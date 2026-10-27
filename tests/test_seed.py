"""
The evidence a new machine's ranking starts from (fourtake/seed.py).

A new machine has no telemetry, so the name heuristic alone seated its first councils. On
2026-10-27 it put nemotron-3-ultra-550b first, which failed in 26 of 28 recorded councils. These tests
pin what the shipped seed may contain, how much it may weigh, and that the machine's own history
is added to it rather than swapped for it. The suite runs with the seed switched off (conftest.py);
these tests load it through fourtake.seed.
"""

import json
import pathlib
from datetime import datetime, timezone

import pytest
import respx

from fourtake import seed
from fourtake.discovery import blend_with_observed, evaluate_model_intelligence, get_best_free_models
from fourtake.telemetry import MIN_SAMPLES_FOR_RANKING, ModelStats

ULTRA = "openrouter/nvidia/nemotron-3-ultra-550b-a55b:free"
NANO = "openrouter/nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free"


def _seed_text(models: dict, generated: str = "2026-10-27") -> str:
    return json.dumps(
        {"generated": generated, "source": "test", "window": None, "events": 0, "models": models}
    )


def _ts(day: str) -> float:
    return datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()


# --------------------------------------------------------------------------------------
# What the seed weighs
# --------------------------------------------------------------------------------------


def test_a_seeded_model_counts_as_at_most_eight_attempts_at_its_own_rate():
    text = _seed_text(
        {
            NANO: {"attempts": 195, "successes": 179, "p50_latency_ms": 32900},
            ULTRA: {"attempts": 25, "successes": 0, "p50_latency_ms": 0},
        }
    )
    loaded = seed.load(text, now=_ts("2026-10-28"))
    assert loaded[NANO] == ModelStats(attempts=MIN_SAMPLES_FOR_RANKING, successes=7, p50_latency_ms=32900)
    assert loaded[ULTRA].attempts == MIN_SAMPLES_FOR_RANKING and loaded[ULTRA].successes == 0
    assert not any(s.unavailable for s in loaded.values())


@pytest.mark.parametrize(
    "row",
    [
        {"attempts": 4, "successes": 4, "p50_latency_ms": 1000},  # an anecdote
        {"attempts": 10, "successes": 11, "p50_latency_ms": 1000},  # impossible
        {"attempts": "many", "successes": 1, "p50_latency_ms": 1000},
        {"attempts": 10, "successes": 5},
    ],
)
def test_a_row_too_thin_or_malformed_is_left_out(row):
    assert seed.load(_seed_text({NANO: row}), now=_ts("2026-10-28")) == {}


def test_a_seed_older_than_sixty_days_is_ignored():
    text = _seed_text({NANO: {"attempts": 195, "successes": 179, "p50_latency_ms": 32900}}, "2026-08-01")
    assert seed.load(text, now=_ts("2026-09-29")) != {}
    assert seed.load(text, now=_ts("2026-10-01")) == {}


@pytest.mark.parametrize("text", ["", "not json", "[]", '{"generated": "yesterday", "models": {}}'])
def test_an_unreadable_seed_is_no_seed(text):
    assert seed.load(text, now=_ts("2026-10-28")) == {}


# --------------------------------------------------------------------------------------
# How it meets this machine's history
# --------------------------------------------------------------------------------------


def test_the_seed_fills_in_only_what_this_machine_has_not_run_and_adds_to_the_rest():
    local = {NANO: ModelStats(attempts=3, successes=3, p50_latency_ms=20000, unavailable=False)}
    sown = {
        NANO: ModelStats(attempts=8, successes=7, p50_latency_ms=32900),
        ULTRA: ModelStats(attempts=8, successes=0, p50_latency_ms=0),
    }
    merged = seed.with_seed(local, sown)
    assert merged[ULTRA] == sown[ULTRA]
    assert merged[NANO] == ModelStats(attempts=11, successes=10, p50_latency_ms=20000)


def test_availability_is_this_machines_alone_and_so_is_its_latency_once_it_has_one():
    local = {ULTRA: ModelStats(attempts=9, successes=0, p50_latency_ms=0, unavailable=True)}
    merged = seed.with_seed(local, {ULTRA: ModelStats(attempts=8, successes=2, p50_latency_ms=40000)})
    assert merged[ULTRA].unavailable
    assert merged[ULTRA].p50_latency_ms == 40000  # no answer here yet, so the seed's median stands


def test_one_more_failure_here_does_not_lift_a_model_the_seed_had_sunk():
    """
    If a local attempt replaced the seed, ultra's evidence would shrink from eight failures to one
    and the name heuristic would lift it back above the free router, a model from another vendor:
    first in line again to replace any seat that fails. Added to the seed, it stays below.
    """
    router = "openrouter/openrouter/free"
    sown = seed.load(
        _seed_text(
            {
                ULTRA: {"attempts": 25, "successes": 0, "p50_latency_ms": 0},
                router: {"attempts": 226, "successes": 120, "p50_latency_ms": 21000},
            }
        ),
        now=_ts("2026-10-28"),
    )
    one_failure = {ULTRA: ModelStats(attempts=1, successes=0, p50_latency_ms=0)}

    def score(model, stats):
        bare = model.removeprefix("openrouter/")
        return blend_with_observed(bare, evaluate_model_intelligence(bare), stats)

    added = seed.with_seed(one_failure, sown)
    replaced = {**sown, **one_failure}
    assert score(ULTRA, added) < score(router, added)
    assert score(ULTRA, replaced) > score(router, replaced), "the alternative this rule exists to avoid"


# --------------------------------------------------------------------------------------
# What a build may put in it
# --------------------------------------------------------------------------------------


def test_a_build_keeps_three_numbers_per_live_free_model_and_nothing_else():
    stats = {
        NANO: ModelStats(attempts=195, successes=179, p50_latency_ms=32900),
        "openrouter/openrouter/free": ModelStats(attempts=226, successes=120, p50_latency_ms=21000),
        "openrouter/nvidia/nemotron-3-super-120b-a12b:free": ModelStats(
            attempts=228, successes=176, p50_latency_ms=34000
        ),  # retired: no longer in the catalogue
        "openrouter/recursive/flint-v1.0:free": ModelStats(attempts=4, successes=1, p50_latency_ms=1),
    }
    live = {NANO.removeprefix("openrouter/"), "openrouter/free", "recursive/flint-v1.0:free"}
    events = [
        {"ts": _ts("2026-09-09"), "model": NANO, "ok": True, "error_code": None},
        {"ts": _ts("2026-10-26"), "model": NANO, "ok": False, "error_code": "timeout"},
    ]
    built = seed.build(stats, live, events, "2026-10-27")

    assert set(built) == {"generated", "source", "window", "events", "models"}
    assert built["window"] == {"from": "2026-09-09", "to": "2026-10-26"}
    assert set(built["models"]) == {NANO, "openrouter/openrouter/free"}
    assert built["models"][NANO] == {"attempts": 195, "successes": 179, "p50_latency_ms": 32900}
    assert "timeout" not in json.dumps(built)


# --------------------------------------------------------------------------------------
# The file the package ships
# --------------------------------------------------------------------------------------

SHIPPED = pathlib.Path(seed.__file__).with_name(seed.SEED_FILE)


def test_the_shipped_seed_holds_only_what_the_build_writes():
    data = json.loads(SHIPPED.read_text(encoding="utf-8"))
    assert set(data) == {"generated", "source", "window", "events", "models"}
    assert data["models"], "an empty seed ships nothing"
    for model, row in data["models"].items():
        assert set(row) == set(seed.FIELDS), model
        assert all(isinstance(v, int) for v in row.values()), model
        assert row["attempts"] >= seed.MIN_ATTEMPTS and 0 <= row["successes"] <= row["attempts"], model
        # A seed must never introduce a paid model: free ids, or the free router.
        assert model.startswith("openrouter/") and (
            model.endswith(":free") or model == "openrouter/openrouter/free"
        )


def test_the_shipped_seed_loads_on_the_day_it_was_built():
    data = json.loads(SHIPPED.read_text(encoding="utf-8"))
    loaded = seed.load(now=_ts(data["generated"]))
    assert set(loaded) == set(data["models"])


# --------------------------------------------------------------------------------------
# Who a new machine's first council seats
# --------------------------------------------------------------------------------------


def _free(model_id: str) -> dict:
    return {"id": model_id, "pricing": {"prompt": "0", "completion": "0"}, "context_length": 262144}


@pytest.mark.asyncio
@respx.mock
async def test_a_new_machine_seats_what_answered_rather_than_what_has_the_biggest_name(monkeypatch):
    respx.get("https://openrouter.ai/api/v1/models").respond(
        json={"data": [_free(m.removeprefix("openrouter/")) for m in (ULTRA, NANO)]}
    )
    sown = seed.load(
        _seed_text(
            {
                ULTRA: {"attempts": 25, "successes": 0, "p50_latency_ms": 0},
                NANO: {"attempts": 195, "successes": 179, "p50_latency_ms": 32900},
            }
        ),
        now=_ts("2026-10-28"),
    )
    monkeypatch.setattr("fourtake.discovery.load_seed", lambda: sown)

    # Both are nvidia, so one is seated and the other waits in the tail: the order is the point.
    assert (await get_best_free_models(min_context=4000, count=1))[0] == NANO
    # `{}` still means the name heuristic alone, which puts the 550B first.
    assert (await get_best_free_models(min_context=4000, count=1, stats={}))[0] == ULTRA
