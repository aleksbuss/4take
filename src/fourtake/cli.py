import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

from rich.console import Console
from rich.logging import RichHandler

from . import __version__
from .config import ConfigError, load_config
from .context import ContextError, ReviewContext, assemble
from .engine import run_council
from .input_kind import looks_like_code
from .output import output_result
from .preflight import CONFIG_KEY_PLACEHOLDER, NoUsableModels, choose_models, missing_api_key
from .prompts import build_system_prompt, new_review_nonce, wrap_input
from .safety import redact_secrets_with_report
from .tokenizer import estimate_tokens

# All diagnostics go to stderr so that stdout carries only the payload.
# RichHandler defaults to a stdout Console, which corrupted `--format json` output.
# markup=False: provider error strings and model ids may contain '[...]' or ':free:',
# which Rich would otherwise interpret as markup/emoji.
_LOG_CONSOLE = Console(stderr=True, emoji=False, highlight=False)
logging.basicConfig(
    level="INFO",
    format="%(message)s",
    datefmt="[%X]",
    handlers=[
        RichHandler(
            console=_LOG_CONSOLE,
            rich_tracebacks=True,
            markup=False,
            show_time=False,
            show_path=False,
        )
    ],
)
# httpx logs every request at INFO; that noise is never part of our contract.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logger = logging.getLogger("4take")

MAX_INPUT_SIZE = 512 * 1024  # 512 KB

# Exit codes (documented in README / AGENTS.md).
EXIT_OK = 0  # met min_responses
EXIT_TOTAL_FAILURE = 1  # no model responded
EXIT_PARTIAL = 2  # at least one response, but fewer than min_responses


def _positive_int(value: str) -> int:
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError(f"must be greater than 0, got {number}")
    return number


def _positive_float(value: str) -> float:
    number = float(value)
    if not number > 0:  # also refuses nan
        raise argparse.ArgumentTypeError(f"must be greater than 0, got {value}")
    return number


def parse_args():
    parser = argparse.ArgumentParser(description="Code review by a council of free LLMs on OpenRouter")
    parser.add_argument("--models", type=str, help="Comma-separated list of models")
    # Positive, as the config file requires: 0 responses counted an empty council as a success, and
    # a timeout of 0 or less set the deadline in the past.
    parser.add_argument("--min-responses", type=_positive_int, help="Minimum responses for success")
    parser.add_argument("--timeout", type=_positive_int, help="Timeout for API requests (seconds)")
    parser.add_argument(
        "--per-model-timeout",
        type=_positive_float,
        help="Abandon a single model after N seconds and rotate in a replacement "
        "(default: half of --timeout, at least 60)",
    )
    parser.add_argument("--output-file", type=str, help="Output JSON file path")
    parser.add_argument(
        "--format", choices=["text", "json"], default="text", help="Output format (default: text)"
    )
    parser.add_argument("--quiet", action="store_true", help="Disable live progress logs")
    parser.add_argument("--version", action="store_true", help="Print version and exit")
    parser.add_argument(
        "--accept-risk",
        action="store_true",
        help="Bypass the privacy warning when sending local code to external LLMs",
    )
    parser.add_argument(
        "--force-review",
        action="store_true",
        help="Review the input even though it does not look like source code",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Report only findings more than one reviewer raised, plus any CRITICAL "
        "finding; text output also omits the individual reviews",
    )
    parser.add_argument(
        "--strip-secrets", action="store_true", help="Deprecated: Secrets are stripped by default now."
    )
    parser.add_argument(
        "--no-strip-secrets",
        action="store_true",
        help="Disable redacting common secrets from input before sending",
    )
    parser.add_argument(
        "--show-redactions",
        action="store_true",
        help="List which secret patterns were redacted from the input",
    )
    parser.add_argument(
        "--init-config",
        action="store_true",
        help="Initialize a template config file at ~/.config/4take/config.json",
    )
    parser.add_argument(
        "--no-cache", action="store_true", help="Bypass reading/writing to the local SHA-256 review cache"
    )
    parser.add_argument(
        "--clear-cache",
        action="store_true",
        help="Clear all stored reviews from ~/.cache/4take/reviews/ and exit",
    )
    parser.add_argument(
        "--no-telemetry",
        action="store_true",
        help="Do not record model latency and success rate to ~/.cache/4take/stats.json",
    )
    parser.add_argument(
        "--clear-telemetry",
        action="store_true",
        help="Delete the recorded model performance history and exit",
    )
    parser.add_argument(
        "--context-file",
        action="append",
        metavar="PATH[:START-END]",
        help="Related code the council reads but does not review: callers, callees, "
        "types, config. Repeatable; most relevant first. Paths must be under "
        "the working directory",
    )
    parser.add_argument(
        "--auto-context",
        action="store_true",
        help="With a diff: also send what the change breaks or relies on, found by "
        "name: callers of what it edits, uses of what it removes, definitions "
        "of what it calls. A few excerpts, within --context-budget",
    )
    parser.add_argument(
        "--context-budget",
        type=_positive_int,
        metavar="TOKENS",
        help="Most reference context one review may carry (default: 16000); files "
        "that do not fit are left out whole and named",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print exactly what would be sent, after redaction, and exit without contacting any model",
    )
    return parser.parse_args()


def read_stdin() -> str:
    if sys.stdin.isatty():
        logger.error("Error: no input piped. Usage: echo 'your code' | 4take")
        sys.exit(1)

    # Read bytes and decode leniently. `sys.stdin.read()` decodes with surrogateescape, so a
    # single stray byte — a latin-1 file, a binary blob piped by mistake — survived the read
    # and then killed the run much later with "utf-8 codec can't encode ... surrogates not
    # allowed" and no review. A file with one bad byte is still a file worth reviewing.
    buffer = getattr(sys.stdin, "buffer", None)
    if buffer is None:  # a replaced stdin in an embedding context
        raw = sys.stdin.read(MAX_INPUT_SIZE + 1).encode("utf-8", "replace")
    else:
        raw = buffer.read(MAX_INPUT_SIZE + 1)

    if len(raw) > MAX_INPUT_SIZE:
        logger.warning(f"Warning: input truncated to {MAX_INPUT_SIZE // 1024} KB")
        raw = raw[:MAX_INPUT_SIZE]

    return raw.decode("utf-8", "replace")


def _tighten_config_permissions(config_dir: Path, config_path: Path) -> None:
    """Narrow an existing directory too: `mkdir(mode=...)` applies only on creation."""
    for path, mode in ((config_dir, 0o700), (config_path, 0o600)):
        try:
            path.chmod(mode)
        except OSError:
            pass


def init_config():
    config_dir = Path.home() / ".config" / "4take"
    # This file is where the tool tells users to put their API key, so it is created with the
    # same permissions as the cache and the rate-limiter state beside it. The default umask
    # left it 0644 in a 0755 directory: a credential readable by every account on the box.
    config_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    config_path = config_dir / "config.json"

    if config_path.exists():
        logger.error(f"Error: Config file already exists at {config_path}")
        sys.exit(1)

    template = {
        "//": "4take Zero-Cost Configuration File. Documentation: https://github.com/aleksbuss/4take",
        "//notice": "4take operates strictly in 100% free mode (zero token cost). Only models ending in ':free' or 'auto' are permitted.",
        "default_models": ["auto"],
        "providers": {"openrouter": {"api_key": CONFIG_KEY_PLACEHOLDER}},
        "rpm_limit": 10,
        "min_responses": 4,
        "timeout": 600,
        "//telemetry": "Local record of model latency and success rate in ~/.cache/4take/stats.json, used to rank the model pool on evidence. Stores model ids and timings only, never code. Set to false to disable.",
        "telemetry": True,
    }

    fd = os.open(config_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(template, f, indent=4)
    _tighten_config_permissions(config_dir, config_path)

    logger.info(f"Created template config file at {config_path}")
    logger.info("Edit this file to add your API keys and custom models!")
    sys.exit(0)


def render_dry_run(task_input: str, review_context: ReviewContext | None, output_format: str) -> str:
    """
    Exactly what a council would be sent, built by the same functions the engine uses.

    The nonce is drawn fresh, as it is for a real council; everything else — the redactions, the
    context rules, the order of the blocks — is what the models would read.
    """
    block = review_context.block() if review_context is not None else None
    nonce = new_review_nonce()
    system = build_system_prompt(nonce, with_context=block is not None)
    user = wrap_input(task_input, nonce, block)
    if output_format == "json":
        return json.dumps(
            {
                "system": system,
                "user": user,
                "review_tokens": estimate_tokens(task_input),
                "context": review_context.summary() if review_context is not None else None,
            },
            indent=2,
            ensure_ascii=False,
        )
    return f"=== system ===\n{system}\n\n=== user ===\n{user}"


def require_api_key(config) -> None:
    """Fail before dispatching anything when there is no key to dispatch with."""
    problem = missing_api_key(config)
    if problem:
        logger.error(f"Error: {problem}")
        sys.exit(1)


def accept_risk_enabled(args) -> bool:
    """
    True if the privacy warning should be suppressed.

    FOURTAKE_ACCEPT_RISK is the supported name. The legacy 4TAKE_ACCEPT_RISK is still read
    for one release, but it cannot be set from bash or zsh at all: POSIX shell identifiers
    may not start with a digit, so `export 4TAKE_ACCEPT_RISK=1` is a syntax error.
    """
    if getattr(args, "accept_risk", False):
        return True
    if os.environ.get("FOURTAKE_ACCEPT_RISK") == "1":
        return True
    if os.environ.get("4TAKE_ACCEPT_RISK") == "1":
        logger.warning(
            "4TAKE_ACCEPT_RISK is deprecated and unsettable from bash/zsh; "
            "use FOURTAKE_ACCEPT_RISK=1 instead."
        )
        return True
    return False


def main() -> None:
    args = parse_args()

    # --quiet must silence diagnostics too, not just the engine's progress lines. Set the
    # level on every run rather than only lowering it: `main()` mutates a module-level
    # logger, so a one-way change leaks into any later call in the same process.
    logger.setLevel(logging.WARNING if getattr(args, "quiet", False) else logging.INFO)

    if getattr(args, "version", False):
        print(f"4take {__version__}")
        sys.exit(0)

    if getattr(args, "init_config", False):
        init_config()

    if getattr(args, "clear_cache", False):
        from .cache import clear_cache

        count = clear_cache()
        logger.info(f"Cleared {count} cached review(s).")
        sys.exit(0)

    if getattr(args, "clear_telemetry", False):
        from .telemetry import clear, stats_path

        removed = clear()
        logger.info(
            f"Cleared model performance history at {stats_path()}."
            if removed
            else "No model performance history to clear."
        )
        sys.exit(0)

    try:
        CONFIG_KEYS = {"models", "min_responses", "timeout", "no_telemetry", "context_budget"}
        cli_overrides = {k: v for k, v in vars(args).items() if k in CONFIG_KEYS and v is not None}
        config = load_config(cli_overrides)
        dry_run = getattr(args, "dry_run", False)
        if not dry_run:
            # A dry run sends nothing, so it has nothing to authenticate.
            require_api_key(config)

        task_input = read_stdin()
        if not task_input.strip():
            logger.error("Error: empty stdin. Usage: echo 'code' | 4take")
            sys.exit(1)

        # A design question used to reach the council and come back as four copies of
        # NO_ISSUES_FOUND, because the reviewers were told their input was code to audit.
        # Refusing here says so once instead of spending four requests to say nothing.
        if not getattr(args, "force_review", False) and not looks_like_code(task_input):
            logger.error(
                "Error: this input does not look like source code, a diff, or config. "
                "4take reviews code: every model would answer NO_ISSUES_FOUND. "
                "Pipe a file, a function, or `git diff` — or pass --force-review to send it anyway."
            )
            sys.exit(1)

        strip_secrets = not getattr(args, "no_strip_secrets", False)
        redactions: dict[str, int] = {}
        # Marking context reads the diff's headers as they were piped: a redaction that happened to
        # touch one must not unmark a file. Nothing of this copy is sent.
        piped = task_input
        if strip_secrets:
            task_input, redactions = redact_secrets_with_report(task_input)
        # Read after the prose check, so a refused input costs no walk over the disk.
        review_context, context_redactions = assemble(
            getattr(args, "context_file", None),
            config.context_budget_tokens,
            strip_secrets=strip_secrets,
            reviewed=piped,
            auto=getattr(args, "auto_context", False),
        )
        for name, count in context_redactions.items():
            redactions[name] = redactions.get(name, 0) + count
        if redactions:
            total = sum(redactions.values())
            # Say what was removed: a broad pattern that eats real code degrades the
            # review, and a silent redaction gives no way to notice.
            if getattr(args, "show_redactions", False):
                detail = ", ".join(f"{name} x{n}" for name, n in sorted(redactions.items()))
                logger.info(f"Redacted {total} secret(s) before sending: {detail}")
            else:
                logger.info(
                    f"Redacted {total} secret(s) before sending "
                    f"(--show-redactions for detail, --no-strip-secrets to disable)"
                )
        if review_context is not None and review_context.dropped:
            logger.warning(
                f"Context over the {config.context_budget_tokens}-token budget, not sent: "
                f"{', '.join(review_context.dropped)}"
            )

        if dry_run:
            print(render_dry_run(task_input, review_context, getattr(args, "format", "text")))
            sys.exit(0)

        review_tokens = estimate_tokens(task_input)
        estimated_tokens = review_tokens + (review_context.tokens if review_context is not None else 0)

        console = Console(stderr=True)
        doing = (
            "Discovering top free OpenRouter models..."
            if config.default_models == ["auto"]
            else "Validating configured models..."
        )
        with console.status(f"[bold cyan]{doing}[/bold cyan]", spinner="dots"):
            try:
                choice = asyncio.run(choose_models(config, estimated_tokens, review_tokens))
            except NoUsableModels as e:
                logger.error(f"Error: {e}")
                sys.exit(1)
        if choice.used_fallback:
            logger.warning("Warning: Auto-discovery failed or no models found. Using fallback models.")
        for model, capacity in choice.skipped:
            logger.warning(
                f"Skipping model '{model}': input is {estimated_tokens} tokens but it can "
                f"accept {capacity} after reserving room for the reply."
            )
        model_pool, active_count = choice.models, choice.active_count

        if not accept_risk_enabled(args):
            console.print(
                "[bold yellow]WARNING: You are sending local content to external LLM providers.[/bold yellow]"
            )
            console.print(
                "[yellow]Free models are served by third parties, and some may keep prompts or train on them. "
                "`--dry-run` shows exactly what would be sent.[/yellow]"
            )
            console.print(
                "[yellow]Pass `--accept-risk` or set `FOURTAKE_ACCEPT_RISK=1` to silence this warning.[/yellow]\n"
            )

        use_cache = not getattr(args, "no_cache", False)

        with console.status(
            f"[bold cyan]Gathering opinions from {active_count} models...[/bold cyan]", spinner="dots"
        ):
            is_verbose = not getattr(args, "quiet", False)
            result = asyncio.run(
                run_council(
                    config,
                    task_input,
                    model_pool=model_pool,
                    active_count=active_count,
                    verbose=is_verbose,
                    console=console,
                    use_cache=use_cache,
                    per_model_timeout=getattr(args, "per_model_timeout", None),
                    context=review_context,
                )
            )

        if config.telemetry:
            # After the council, before the output: a review must never be lost to a metrics
            # write, so `record` swallows its own failures and returns 0.
            from .telemetry import record

            record(result.responses, result.errors)

        exit_code = output_result(
            result,
            getattr(args, "output_file", None),
            config,
            task_input,
            getattr(args, "format", "text"),
            strict=getattr(args, "strict", False),
        )
        sys.exit(exit_code)
    except ConfigError as e:
        # Name the file and the key rather than surfacing a TypeError from deep inside.
        logger.error(f"Config error: {e}")
        sys.exit(1)
    except ContextError as e:
        logger.error(f"Context error: {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        logger.error("Error: Process interrupted by user.")
        sys.exit(130)
    except Exception as e:
        logger.error(f"Error: An unexpected error occurred: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
