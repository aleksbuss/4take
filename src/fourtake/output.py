import hashlib
import json
import os
import sys
import tempfile
from datetime import datetime, timezone

from . import __version__
from .config import FourtakeConfig
from .engine import CouncilResult, distinct_seats
from .findings import Consensus, build_consensus


def _strict_keeps(cluster: Consensus) -> bool:
    """
    What `--strict` still reports: agreed findings, and a critical one however few raised it.

    Grouping is lexical, so a critical defect one reviewer worded differently from the others
    can sit at agreement 1. Dropping it would let `--strict` print a clean-looking report over a
    SQL injection — the one finding an agent is told to act on even from a single model.
    """
    return cluster.agreement > 1 or cluster.severity == "CRITICAL"


def _consensus_entry(c: Consensus) -> list[str]:
    where = f" (at `{c.symbol}`)" if c.symbol else ""
    cat = f" [{c.category.value}]" if getattr(c, "category", None) and c.category.value != "UNKNOWN" else ""
    reviewers = f"{c.agreement} reviewer{'s' if c.agreement != 1 else ''}"
    lines = [f"- **[{c.severity}]**{cat} {c.title}{where} — **{reviewers}**"]
    if c.disputed:
        spread = ", ".join(
            f"{n} said {sev.lower()}" for sev, n in sorted(c.severities.items())
        )
        lines.append(f"  - Severity disputed: {spread}")
    lines.append(f"  - Raised by: {', '.join(f'`{m}`' for m in c.models)}")
    return lines


def _format_consensus(clusters: list[Consensus], strict: bool = False) -> list[str]:
    """
    The section a reader should act on first.

    Four reviews printed side by side left the cross-referencing to the reader, which is the
    one job the council is supposed to do for them. Only findings that more than one
    independent reviewer raised go here; the rest stay in their own model's section, where
    the reader can judge them one at a time.

    `--strict` prints no individual reviews, so this section is all its reader gets: it keeps
    a critical finding one reviewer raised and counts what it hid. A bare "no consensus" line
    would read as a clean bill of health over the very findings it had just dropped.
    """
    agreed = [c for c in clusters if c.agreement > 1]
    critical_solo = [c for c in clusters if strict and c.agreement == 1 and _strict_keeps(c)]
    if not agreed and not strict:
        return []

    lines = ["## Consensus", ""]
    if agreed:
        lines.append(
            f"{len(agreed)} finding{'s' if len(agreed) != 1 else ''} raised independently by more "
            f"than one reviewer, most-agreed first."
        )
        lines.append("")
        for c in agreed:
            lines.extend(_consensus_entry(c))
    else:
        lines.append("No finding was raised by more than one reviewer." if clusters
                     else "No reviewer raised a finding.")
    lines.append("")
    if critical_solo:
        lines.append("### Critical, raised by a single reviewer")
        lines.append("")
        for c in critical_solo:
            lines.extend(_consensus_entry(c))
        lines.append("")
    solo = len(clusters) - len(agreed) - len(critical_solo)
    if solo:
        plural = "s" if solo != 1 else ""
        lines.append(
            f"*{solo} single-reviewer finding{plural} hidden by --strict.*" if strict
            else f"*{solo} further finding{plural} came from a single reviewer "
                 f"only — see the individual reviews below.*"
        )
        lines.append("")
    lines.append("---")
    lines.append("")
    return lines


def _context_lines(result: CouncilResult) -> list[str]:
    """What the reviewers could see beyond the code, and what the budget kept from them."""
    context = getattr(result, "context", None)
    if context is None:
        return []
    lines = []
    if context.pieces:
        # A piece auto context chose says why, so the reader can judge the choice.
        files = ", ".join(f"`{p.label}`" + (f" ({p.why})" if p.why else "") for p in context.pieces)
        lines.append(f"> **Reference context:** {files} ({context.tokens} tokens)")
    if context.dropped:
        lines.append(f"> **Not sent, over the context budget:** {', '.join(f'`{d}`' for d in context.dropped)}")
    return [*lines, ""]


def format_markdown(
    result: CouncilResult, config: FourtakeConfig, task_input: str, strict: bool = False
) -> str:
    lines = []
    lines.append(f"# 4take Council Review (Task Hash: {hashlib.sha256(task_input.encode()).hexdigest()[:12]})")
    lines.append("")
    lines.extend(_context_lines(result))

    if result.warning:
        lines.append(f"> **Warning:** {result.warning}")
        lines.append("")

    # With no responses there is nothing to agree on; a consensus line there would read as a
    # clean result over a run in which every model failed. The errors section says what happened.
    if result.responses:
        lines.extend(_format_consensus(build_consensus(result.responses), strict=strict))
        if strict:
            lines.append("*Individual reviews omitted by --strict; run without it to read them.*")
            lines.append("")

    reviews = [] if strict else result.responses
    for r in reviews:
        header = f"## Model: `{r.model}` (Latency: {r.latency_ms/1000:.1f}s)"
        lines.append(header)
        if r.served_model and r.served_model != r.model.split("openrouter/", 1)[-1]:
            lines.append(f"*Served by `{r.served_model}`*")
        notes = []
        if r.truncated:
            notes.append("**Truncated** — this review was cut off and is incomplete.")
        if r.duplicate_of:
            notes.append(f"**Duplicate seat** — `{r.duplicate_of}` already holds this seat "
                         f"(the same model, or another from its vendor); counts once.")
        if notes:
            lines.append("")
            lines.extend(f"> {n}" for n in notes)
        lines.append("")
        if r.content is not None:
            lines.append(r.content.strip())
        else:
            lines.append(f"[Error from model {r.model}]")
        lines.append("")
        lines.append("---")
        lines.append("")

    # Printed under --strict too: it filters findings, not failures.
    if result.errors:
        lines.append("## Errors")
        lines.append("")
        for e in result.errors:
            lines.append(f"- **`{e.model}`**: {e.error} ({e.latency_ms/1000:.1f}s)")
            
    return "\n".join(lines).strip()

def output_result(
    result: CouncilResult,
    output_file: str | None,
    config: FourtakeConfig,
    task_input: str,
    output_format: str = "text",
    strict: bool = False,
) -> int:
    if output_format == "text":
        out_str = format_markdown(result, config, task_input, strict=strict)
    else:
        clusters = build_consensus(result.responses)
        if strict:
            clusters = [c for c in clusters if _strict_keeps(c)]
        data = {
        "version": __version__,
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "config": {
            # The models the council asked, not the length of the configured list: that is
            # ["auto"], and every auto council reported asking one model.
            "models_requested": result.models_asked,
            "min_responses": config.min_responses,
            "timeout_seconds": config.timeout,
            "strict": strict,
        },
        "summary": {
            "task_hash": hashlib.sha256(task_input.encode()).hexdigest()[:12],
            "responses_received": len(result.responses),
            "distinct_models": distinct_seats(result.responses),
            "responses_failed": len(result.errors),
            "total_latency_ms": result.total_latency_ms,
            # Files the reviewers read beside the code, and those the budget left out: a finding
            # that needed a caller nobody sent is one the council could not have made.
            "context": result.context.summary() if getattr(result, "context", None) else None,
        },
        "warning": result.warning,
        "error": result.error,
        # Ranked by how many independent reviewers raised each defect. An agent reading this
        # should weight `agreement` far above any single model's confidence.
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
            for c in clusters
        ],
        "responses": [
            {
                "model": r.model,
                # Differs from `model` when a router served the request; consensus must be
                # keyed on this, not on what was asked for.
                "served_model": r.served_model,
                "served_provider": r.served_provider,
                "content": r.content,
                "finish_reason": r.finish_reason,
                "truncated": r.truncated,
                # Set when the answer does not count as an independent seat: the same model
                # again, or another from a vendor already seated. The MCP payload always had
                # it; without it here the eval scorer counted such answers as extra reviewers.
                "duplicate_of": r.duplicate_of,
                "latency_ms": r.latency_ms
            } for r in result.responses
        ],
        "errors": [
            {
                "model": e.model,
                "error": e.error,
                "latency_ms": e.latency_ms
            } for e in result.errors
        ]
    }
    
    if output_format == "json":
        out_str = json.dumps(data, indent=2, ensure_ascii=False)
    
    # Configure stdout for UTF-8 on Windows
    if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding='utf-8')
        except Exception:
            pass
    
    if output_file:
        out_path = os.path.abspath(output_file)
        out_dir = os.path.dirname(out_path)
        os.makedirs(out_dir, exist_ok=True)

        fd, temp_path = tempfile.mkstemp(dir=out_dir)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(out_str)

        os.replace(temp_path, out_path)
        print(out_path)
    else:
        print(out_str)

    return exit_code_for(result, config)


def exit_code_for(result: CouncilResult, config: FourtakeConfig) -> int:
    """
    0 = the council met min_responses, 2 = partial (some responses, below the threshold),
    1 = total failure. Callers (agents, CI) need to tell these apart.
    """
    if not result.responses:
        return 1
    required = getattr(config, "min_responses", 1) or 1
    # Seats, not responses: the engine's own threshold is in seats, and duplicates must not
    # promote a degraded run to a clean exit.
    return 0 if distinct_seats(result.responses) >= required else 2

