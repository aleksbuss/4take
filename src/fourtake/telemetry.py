"""
Local record of how each model actually performed.

The model pool is ranked by a heuristic over model *names* — parameter counts and vendor
substrings scraped out of the id. That heuristic cannot see that a model times out half the
time, and it scores `nemotron-3-ultra-550b` and `nemotron-3-nano-omni-30b` identically. The
only way to rank on evidence is to keep some.

What is stored: model id, whether the call succeeded, how long it took, the error code, the
finish reason, a timestamp. What is never stored: the code under review, the reviews, the
prompt, or anything derived from them. The file is local, 0600, capped, and switchable off
with `--no-telemetry`.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from . import filelock

logger = logging.getLogger("4take")

# Enough history for the ranking to be stable across a few weeks of use, small enough that
# the file stays a few hundred KB and is rewritten in one atomic replace.
MAX_EVENTS = 2000

# Below this many observations a model's measured rate is noise, so the name heuristic stays
# in charge. Four council seats per run means this is roughly two or three runs of exposure.
MIN_SAMPLES_FOR_RANKING = 8


@dataclass(frozen=True)
class ModelStats:
    """Observed behaviour of one model id, aggregated over the stored history."""

    attempts: int
    successes: int
    p50_latency_ms: int

    @property
    def success_rate(self) -> float:
        return self.successes / self.attempts if self.attempts else 0.0

    @property
    def confident(self) -> bool:
        return self.attempts >= MIN_SAMPLES_FOR_RANKING


def stats_path() -> Path:
    return Path.home() / ".cache" / "4take" / "stats.json"


def _lock_path(path: Path) -> Path:
    # A separate lock file, because the data file is replaced rather than written in place:
    # a process holding a lock on the old inode would not exclude anyone from the new one.
    return path.with_suffix(".lock")


# Ordered: the first match wins, so the specific label precedes the general one.
_ERROR_PATTERNS = (
    ("per-model timeout", "per_model_timeout"),
    ("timeout", "timeout"),
    ("timed out", "timeout"),
    ("rate limit", "rate_limited"),
    ("429", "rate_limited"),
    ("truncat", "truncated"),
    ("empty", "empty_response"),
    ("malformed", "malformed_response"),
    ("unauthor", "auth"),
    ("forbidden", "auth"),
    ("connect", "connection"),
)

_HTTP_STATUS_RE = re.compile(r"\b([1-5]\d{2})\b")


def _error_code(error: str | None) -> str | None:
    """
    A coarse, low-cardinality label for why a call failed.

    Free-text error strings carry model ids, urls and provider prose; storing them would turn
    a metrics file into an unbounded log of someone else's messages.
    """
    if not error:
        return None
    lowered = error.lower()
    for needle, code in _ERROR_PATTERNS:
        if needle in lowered:
            return code
    status = _HTTP_STATUS_RE.search(error)
    if status:
        return f"http_{status.group(1)[0]}xx"
    return "other"


def _event(response, ok: bool) -> dict:
    return {
        "model": response.model,
        "served_model": response.served_model,
        "ok": ok,
        "latency_ms": int(response.latency_ms or 0),
        "error_code": _error_code(getattr(response, "error", None)),
        "finish_reason": getattr(response, "finish_reason", None),
        "ts": int(time.time()),
    }


def record(responses, errors, path: Path | None = None) -> int:
    """
    Append one event per dispatched model. Returns how many events were written.

    Telemetry must never be the reason a review is lost, so every failure here is logged at
    debug level and swallowed.
    """
    path = path or stats_path()
    # A cache hit made no request. Recording it would credit the model with a 0 ms success it
    # never delivered, and re-running one file — what an agent iterating on a fix does all
    # day — would pile up dozens of fabricated observations for the same four models.
    events = [_event(r, ok=True) for r in responses if not getattr(r, "from_cache", False)]
    events += [_event(e, ok=False) for e in errors if not getattr(e, "from_cache", False)]
    if not events:
        return 0

    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = filelock.acquire(_lock_path(path))
        try:
            stored = _read(path)
            stored.extend(events)
            _atomic_write(path, stored[-MAX_EVENTS:])
        finally:
            filelock.release(fd)
    except Exception as exc:  # pragma: no cover - defensive, exercised via monkeypatch
        logger.debug("telemetry write failed: %s", exc)
        return 0
    return len(events)


def _read(path: Path) -> list[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    events = data.get("events") if isinstance(data, dict) else data
    return [e for e in events if isinstance(e, dict)] if isinstance(events, list) else []


def _atomic_write(path: Path, events: list[dict]) -> None:
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".stats-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"version": 1, "events": events}, fh)
        os.chmod(tmp, filelock.FILE_MODE)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def aggregate(path: Path | None = None) -> dict[str, ModelStats]:
    """Observed success rate and median latency per model id."""
    events = _read(path or stats_path())
    by_model: dict[str, list[dict]] = {}
    for e in events:
        model = e.get("model")
        if isinstance(model, str) and model:
            by_model.setdefault(model, []).append(e)

    stats: dict[str, ModelStats] = {}
    for model, rows in by_model.items():
        successes = [r for r in rows if r.get("ok")]
        # Latency of a failure is the latency of a timeout, which says nothing about how fast
        # the model answers; only successful calls go into the median.
        latencies = sorted(int(r.get("latency_ms") or 0) for r in successes)
        p50 = latencies[len(latencies) // 2] if latencies else 0
        stats[model] = ModelStats(attempts=len(rows), successes=len(successes), p50_latency_ms=p50)
    return stats


def clear(path: Path | None = None) -> bool:
    """Delete the history. Returns whether a file was removed."""
    path = path or stats_path()
    try:
        path.unlink()
        return True
    except OSError:
        return False
