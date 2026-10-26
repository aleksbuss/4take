import asyncio
import json
from typing import Annotated

from mcp.server.fastmcp import Context, FastMCP
from pydantic import Field

from .config import load_config
from .context import ContextError, assemble
from .engine import distinct_seats, run_council
from .findings import build_consensus
from .input_kind import looks_like_code
from .preflight import NoUsableModels, choose_models, missing_api_key
from .safety import redact_secrets
from .tokenizer import estimate_tokens

mcp = FastMCP("4take")

# asyncio only keeps a weak reference to a running task, so a bare create_task() can be
# garbage-collected mid-execution and the notification silently never arrives. Hold a strong
# reference until the task completes.
_background_tasks: set[asyncio.Task] = set()


def _fire_and_forget(coro) -> None:
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


def _refused(error: str) -> str:
    """The payload for a review that was never dispatched: the same shape as a failed council."""
    return json.dumps(
        {
            "status": "error",
            "error": error,
            "warning": None,
            "responses_received": 0,
            "responses": [],
            "errors": [],
        },
        ensure_ascii=False,
        indent=2,
    )


_CONTEXT_FILES_HELP = (
    "Paths of code related to the change, which the reviewers read but do not review: each "
    "changed function in full, its direct callers, the functions it calls, the types, schemas "
    "and config it relies on, related tests. Most relevant first; a few files, not the "
    "repository. `path:START-END` sends only those lines. The server reads them from disk, "
    "under the project root, so there is no need to paste them."
)
_AUTO_CONTEXT_HELP = (
    "With a diff: also send the code the change breaks or relies on, found by name — callers of "
    "what it edits, remaining uses of what it removes, definitions of what it calls. A few "
    "excerpts after any context_files, within the same budget. Files you name are sent first; "
    "name the ones you know matter."
)
_CONTEXT_HELP = (
    "The same kind of related code as text, for code the server cannot read from disk. Read, not reviewed."
)


@mcp.tool()
async def get_4take_opinion(
    code: str,
    ctx: Context,
    force: bool = False,
    context_files: Annotated[list[str] | None, Field(description=_CONTEXT_FILES_HELP)] = None,
    context: Annotated[str | None, Field(description=_CONTEXT_HELP)] = None,
    auto_context: Annotated[bool, Field(description=_AUTO_CONTEXT_HELP)] = False,
) -> str:
    """
    Get 4 independent AI opinions on a piece of code.
    Use this tool whenever the user wants code reviewed or audited by multiple models.
    Pass source, a function, or a unified diff — a design question is refused, because the
    reviewers are told their input is code and would all answer NO_ISSUES_FOUND. Set force
    to send a non-code input anyway.
    Pass `context_files` with a diff: a defect between files — a changed return value a caller
    still relies on, a route missing the check its neighbours make — is invisible without them.
    """
    # Refuse before dispatching, for the reason the CLI refuses: four requests spent to
    # produce four copies of the empty-review sentinel is a worse answer than saying so.
    if not force and not looks_like_code(code):
        return _refused(
            "Input does not look like source code, a diff, or config. 4take reviews code; "
            "every model would answer NO_ISSUES_FOUND. Pass source or a diff, or set "
            "force=true to send it anyway."
        )

    config = load_config({})
    problem = missing_api_key(config)
    if problem:
        return _refused(problem)
    sanitized_code = redact_secrets(code)
    try:
        # The same read-redact-fit path the CLI takes, so the refusals cannot drift apart.
        # `code` as sent, not `sanitized_code`: marking reads diff headers, which a redaction must
        # not be able to rewrite, and it sends nothing.
        review_context, _ = assemble(
            context_files,
            config.context_budget_tokens,
            inline=context,
            reviewed=code,
            auto=auto_context,
        )
    except ContextError as e:
        return _refused(f"Context error: {e}")

    def on_progress(msg: str, completed: int, total: int):
        try:
            if ctx:
                _fire_and_forget(ctx.info(msg))
                if (
                    ctx.request_context
                    and ctx.request_context.meta
                    and ctx.request_context.meta.progressToken
                ):
                    _fire_and_forget(
                        ctx.request_context.session.send_progress_notification(
                            progress_token=ctx.request_context.meta.progressToken,
                            progress=completed,
                            total=total,
                        )
                    )
        except Exception:
            pass  # Ignore progress errors to not crash the main tool

    review_tokens = estimate_tokens(sanitized_code)
    total_tokens = review_tokens + (review_context.tokens if review_context is not None else 0)
    try:
        choice = await choose_models(config, total_tokens, review_tokens)
    except NoUsableModels as e:
        return _refused(str(e))

    result = await run_council(
        config=config,
        task_input=sanitized_code,
        model_pool=choice.models,
        active_count=choice.active_count,
        verbose=False,
        console=None,
        progress_callback=on_progress,
        context=review_context,
    )

    if config.telemetry:
        # As in the CLI: agents reach 4take through this tool at least as often, and a pool
        # ranked only on CLI runs is ranked on a fraction of the evidence. Never raises.
        from .telemetry import record

        record(result.responses, result.errors)

    return serialise_result(result, config)


def serialise_result(result, config) -> str:
    """
    The MCP tool's output contract, extracted so it can be asserted without running a
    council. Both the status rule and the consensus block are things agents rely on.
    """
    # "Success" used to be reported even when every model failed, because result.error was
    # never serialised. Callers are agents: they must be able to tell failure from success.
    #
    # Degradation is measured against the threshold, not against the presence of a warning.
    # Keying it on `result.warning` marked a healthy run degraded whenever a duplicate seat
    # had been rotated out — the machinery working as designed — and made this verdict
    # disagree with the exit code the CLI returns for the very same result.
    seats = distinct_seats(result.responses)
    if result.error or not result.responses:
        status = "error"
    elif seats < config.min_responses:
        status = "degraded"
    else:
        status = "ok"

    return json.dumps(
        {
            "status": status,
            "error": result.error,
            "warning": result.warning,
            "responses_received": len(result.responses),
            "distinct_models": seats,
            "min_responses": config.min_responses,
            "total_latency_ms": result.total_latency_ms,
            # What the reviewers read beside the code, and what the budget left out of it.
            "context": result.context.summary() if getattr(result, "context", None) else None,
            # The same ranked grouping the CLI emits. Agents reach 4take through this tool at
            # least as often as through the CLI, and AGENTS.md tells them to read consensus
            # first — which they could not, because it was never sent.
            "consensus": [
                {
                    "agreement": c.agreement,
                    "severity": c.severity,
                    "category": c.category.value if getattr(c, "category", None) else "UNKNOWN",
                    "symbol": c.symbol,
                    "title": c.title,
                    "models": c.models,
                    "severities": c.severities,
                    "disputed": c.disputed,
                }
                for c in build_consensus(result.responses)
            ],
            "responses": [
                {
                    "model": r.model,
                    "served_model": r.served_model,
                    "served_provider": r.served_provider,
                    "content": r.content,
                    "error": r.error,
                    "finish_reason": r.finish_reason,
                    "truncated": r.truncated,
                    "duplicate_of": r.duplicate_of,
                    "latency_ms": r.latency_ms,
                }
                for r in result.responses
            ],
            "errors": [
                {"model": e.model, "error": e.error, "latency_ms": e.latency_ms} for e in result.errors
            ],
        },
        ensure_ascii=False,
        indent=2,
    )


def main():
    mcp.run()


if __name__ == "__main__":
    main()
