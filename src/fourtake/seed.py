"""
The evidence a new machine starts from, so its first councils are not seated by model names alone.

Ranking blends a name heuristic with what each model actually did on this machine (telemetry.py),
and a new machine has no history. On 2026-10-27, with no history, the heuristic seated
nemotron-3-ultra-550b first; it failed with an Nvidia 500 in 26 of 28 councils, and the two gemma-4
models it seated next timed out 38 times, so the median council took 186 s against 63 s for the
four models that did answer. The package therefore ships the maintainer's telemetry reduced to three
numbers per model (`seed_stats.json`, built by `scripts/build_seed_stats.py` before a release), and
each model's own history on this machine is added to it.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from importlib import resources

from .telemetry import MIN_SAMPLES_FOR_RANKING, ModelStats

SEED_FILE = "seed_stats.json"
# Past this the free pool has moved on, and the name heuristic is the honest fallback: a forgotten
# refresh then ranks as 4take did before the seed existed, not on evidence about another pool.
MAX_AGE_DAYS = 60
# Fewer attempts than this is an anecdote, not evidence.
MIN_ATTEMPTS = 5
FIELDS = ("attempts", "successes", "p50_latency_ms")
_PREFIX = "openrouter/"


def _generated_at(data: dict) -> float:
    return datetime.strptime(data["generated"], "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()


def load(text: str | None = None, now: float | None = None) -> dict[str, ModelStats]:
    """
    The shipped seed, keyed like telemetry, or {} when it is missing, unreadable or stale.

    Each model counts as at most MIN_SAMPLES_FOR_RANKING attempts, at its own success rate: as much
    weight as the name heuristic, and no more, so a handful of runs on this machine outweigh it. A
    seed never marks a model unavailable; that takes refusals seen here in the last 72 hours.
    """
    try:
        raw = (
            text
            if text is not None
            else resources.files("fourtake").joinpath(SEED_FILE).read_text(encoding="utf-8")
        )
        data = json.loads(raw)
        generated = _generated_at(data)
        models = data["models"]
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return {}
    if (time.time() if now is None else now) - generated > MAX_AGE_DAYS * 86400:
        return {}
    seed: dict[str, ModelStats] = {}
    for model, row in models.items():
        try:
            attempts, successes, p50 = (int(row[f]) for f in FIELDS)
        except (KeyError, TypeError, ValueError):
            continue
        if attempts < MIN_ATTEMPTS or not 0 <= successes <= attempts:
            continue
        counted = min(attempts, MIN_SAMPLES_FOR_RANKING)
        seed[model] = ModelStats(
            attempts=counted, successes=round(successes * counted / attempts), p50_latency_ms=p50
        )
    return seed


def with_seed(local: dict[str, ModelStats], seed: dict[str, ModelStats]) -> dict[str, ModelStats]:
    """
    This machine's history with the seed added to it, model by model.

    Added, not replaced: if one local attempt replaced the seed, a model the seed had sunk would
    climb straight back with the next failure, its evidence cut from eight attempts to one.
    Availability is this machine's alone, and so is the median latency once it has an answer.
    """
    merged = dict(local)
    for model, s in seed.items():
        mine = local.get(model)
        if mine is None:
            merged[model] = s
            continue
        merged[model] = ModelStats(
            attempts=mine.attempts + s.attempts,
            successes=mine.successes + s.successes,
            p50_latency_ms=mine.p50_latency_ms if mine.successes else s.p50_latency_ms,
            unavailable=mine.unavailable,
        )
    return merged


def build(stats: dict[str, ModelStats], live_free: set[str], events: list[dict], generated: str) -> dict:
    """
    The seed file's content: three numbers for each model the catalogue still lists as free.

    `stats` is telemetry.aggregate(), keyed by the dispatched id (`openrouter/<catalogue id>`);
    `live_free` holds catalogue ids. Nothing else of an event survives: no timestamps, no error
    text, nothing about what was reviewed.
    """
    timestamps = sorted(e["ts"] for e in events if isinstance(e.get("ts"), (int, float)))

    def day(ts: float) -> str:
        return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")

    models = {
        model: {"attempts": s.attempts, "successes": s.successes, "p50_latency_ms": s.p50_latency_ms}
        for model, s in sorted(stats.items())
        if model.removeprefix(_PREFIX) in live_free and s.attempts >= MIN_ATTEMPTS
    }
    return {
        "generated": generated,
        "source": "the maintainer's 4take telemetry: per model, requests sent, requests answered with a "
        "review, and the median latency of an answer",
        "window": {"from": day(timestamps[0]), "to": day(timestamps[-1])} if timestamps else None,
        "events": len(events),
        "models": models,
    }
