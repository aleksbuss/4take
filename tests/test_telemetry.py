"""
Telemetry and evidence-based model ranking (plan item S6).

The file these tests exercise is written into the user's home directory on every run, so
what it must never contain is asserted as carefully as what it must.
"""

import json
import time
from unittest.mock import patch

import pytest

from fourtake import telemetry
from fourtake.client import ModelResponse
from fourtake.discovery import blend_with_observed, evaluate_model_intelligence
from fourtake.telemetry import (
    MAX_EVENTS,
    MIN_SAMPLES_FOR_RANKING,
    REFUSAL_WINDOW_S,
    ModelStats,
    aggregate,
    clear,
    record,
)


def _ok(model, latency_ms=1000, served=None):
    return ModelResponse(
        model=model, content="review", error=None, latency_ms=latency_ms, served_model=served or model
    )


def _failed(model, error, latency_ms=90000):
    return ModelResponse(model=model, content=None, error=error, latency_ms=latency_ms)


# --------------------------------------------------------------------------------------
# What is stored, and what must never be
# --------------------------------------------------------------------------------------


def test_the_review_and_the_code_never_reach_the_file(tmp_path):
    path = tmp_path / "stats.json"
    record([_ok("a:free")], [_failed("b:free", "boom: def secret_function(password)")], path=path)

    text = path.read_text()
    assert "review" not in text
    assert "secret_function" not in text
    assert "password" not in text

    (event, _) = json.loads(text)["events"]
    assert set(event) == {"model", "served_model", "ok", "latency_ms", "error_code", "finish_reason", "ts"}


@pytest.mark.posix_only("file modes: Windows reports 0o666/0o777 and the profile's ACL guards the file")
def test_the_file_is_not_readable_by_other_users(tmp_path):
    path = tmp_path / "stats.json"
    record([_ok("a:free")], [], path=path)
    assert oct(path.stat().st_mode & 0o777) == "0o600"


@pytest.mark.parametrize(
    "error,expected",
    [
        ("Reasoned through the whole 32768-token budget without answering", "reasoning_loop"),
        ("Per-model timeout after 90s", "per_model_timeout"),
        ("Request timed out", "timeout"),
        ("HTTP 429: rate limit exceeded", "rate_limited"),
        ("HTTP 503 Service Unavailable", "http_5xx"),
        ("Provider returned malformed JSON", "malformed_response"),
        ("connection reset by peer", "connection"),
        ("something nobody anticipated", "other"),
        (None, None),
    ],
)
def test_error_text_is_reduced_to_a_low_cardinality_code(error, expected):
    """Storing provider prose verbatim would turn a metrics file into someone else's log."""
    assert telemetry._error_code(error) == expected


def test_history_is_capped_so_the_file_cannot_grow_without_bound(tmp_path):
    path = tmp_path / "stats.json"
    record([_ok(f"m{i}:free") for i in range(MAX_EVENTS + 250)], [], path=path)
    events = json.loads(path.read_text())["events"]
    assert len(events) == MAX_EVENTS
    assert events[-1]["model"].endswith(f"m{MAX_EVENTS + 249}:free"), "the newest are kept"


def test_a_write_failure_never_costs_the_caller_a_review(tmp_path):
    path = tmp_path / "stats.json"
    with patch("fourtake.telemetry.filelock.acquire", side_effect=OSError("disk full")):
        assert record([_ok("a:free")], [], path=path) == 0
    assert not path.exists()


def test_a_corrupt_file_is_replaced_rather_than_raising(tmp_path):
    path = tmp_path / "stats.json"
    path.write_text("{ this is not json")
    assert record([_ok("a:free")], [], path=path) == 1
    assert len(json.loads(path.read_text())["events"]) == 1


def test_clear_removes_the_history_and_reports_whether_it_had_to(tmp_path):
    path = tmp_path / "stats.json"
    record([_ok("a:free")], [], path=path)
    assert clear(path) is True
    assert clear(path) is False


def test_the_temporary_file_is_not_left_behind(tmp_path):
    """The write is atomic-replace, so a crash must not litter the cache with fragments."""
    path = tmp_path / "stats.json"
    record([_ok("a:free")], [], path=path)
    assert not any(p.name.startswith(".stats-") for p in tmp_path.iterdir())


# --------------------------------------------------------------------------------------
# Aggregation
# --------------------------------------------------------------------------------------


def test_aggregate_separates_success_rate_from_latency(tmp_path):
    path = tmp_path / "stats.json"
    record([_ok("fast:free", 500), _ok("fast:free", 700), _ok("fast:free", 900)], [], path=path)
    record([], [_failed("flaky:free", "timeout")], path=path)
    record([_ok("flaky:free", 4000)], [], path=path)

    stats = aggregate(path)
    assert stats["fast:free"] == ModelStats(attempts=3, successes=3, p50_latency_ms=700)
    assert stats["flaky:free"].success_rate == 0.5
    assert stats["flaky:free"].p50_latency_ms == 4000, "a timeout's latency is not a latency"


def test_aggregate_of_a_missing_file_is_empty(tmp_path):
    assert aggregate(tmp_path / "absent.json") == {}


def _refused(model):
    return _failed(model, "Provider error 429 after 4 retries: Provider returned error", latency_ms=500)


def test_a_model_its_provider_refuses_every_time_is_unavailable(tmp_path):
    """gemma-4-31b: 27 of 27 requests in a week came back 429."""
    path = tmp_path / "stats.json"
    record([], [_refused("gone:free")] * MIN_SAMPLES_FOR_RANKING, path=path)

    assert aggregate(path)["gone:free"].unavailable


def test_a_rate_limited_model_that_still_answers_sometimes_stays_available(tmp_path):
    """Poolside: about one 429 retry in three gets through, and laguna is worth the seat."""
    path = tmp_path / "stats.json"
    record([], [_refused("busy:free")] * 10, path=path)
    record([_ok("busy:free")], [], path=path)

    assert not aggregate(path)["busy:free"].unavailable


def test_a_model_that_answered_badly_is_not_unavailable(tmp_path):
    """
    An empty answer is the model, or 4take's budget, not the provider refusing: laguna-s came
    back empty 19 times in a week until the reasoning budget was fixed, then answered.
    """
    path = tmp_path / "stats.json"
    record([], [_failed("thinker:free", "Empty response from model")] * 12, path=path)
    record([], [_refused("thinker:free")] * 9, path=path)

    assert not aggregate(path)["thinker:free"].unavailable


def test_refusals_older_than_the_window_are_forgiven(tmp_path):
    path = tmp_path / "stats.json"
    record([], [_refused("gone:free")] * 20, path=path)

    later = time.time() + REFUSAL_WINDOW_S + 60
    assert not aggregate(path, now=later)["gone:free"].unavailable


# --------------------------------------------------------------------------------------
# Ranking: the heuristic is a prior, the measurements are the evidence
# --------------------------------------------------------------------------------------


def test_an_unobserved_model_keeps_its_prior():
    assert blend_with_observed("nvidia/nemotron-3-ultra-550b:free", 65, {}) == 65.0


def test_one_bad_run_does_not_demote_a_model():
    """Otherwise a single provider hiccup permanently buries the best model in the pool."""
    stats = {"m:free": ModelStats(attempts=1, successes=0, p50_latency_ms=0)}
    assert blend_with_observed("m:free", 65, stats) > 55


def test_sustained_failure_does_demote_it():
    stats = {"m:free": ModelStats(attempts=40, successes=2, p50_latency_ms=0)}
    assert blend_with_observed("m:free", 65, stats) < 20


def test_a_reliable_small_model_overtakes_an_unreliable_large_one():
    """
    The exact defect the heuristic cannot see: it reads parameter counts out of the name and
    ranked a 2.6B model 10th while a 550B model that mostly fails stayed at the top.
    """
    small, large = "liquid/lfm-2.5-2.6b:free", "nvidia/nemotron-3-ultra-550b:free"
    stats = {
        small: ModelStats(attempts=30, successes=30, p50_latency_ms=8_000),
        large: ModelStats(attempts=30, successes=3, p50_latency_ms=100_000),
    }
    small_score = blend_with_observed(small, evaluate_model_intelligence(small), stats)
    large_score = blend_with_observed(large, evaluate_model_intelligence(large), stats)
    assert small_score > large_score


def test_a_slow_model_ranks_below_a_fast_one_with_the_same_success_rate():
    stats = {
        "quick:free": ModelStats(attempts=30, successes=30, p50_latency_ms=2_000),
        "slow:free": ModelStats(attempts=30, successes=30, p50_latency_ms=180_000),
    }
    assert blend_with_observed("quick:free", 0, stats) > blend_with_observed("slow:free", 0, stats)


def test_telemetry_records_models_with_the_prefix_discovery_omits():
    """Discovery names models bare; the engine dispatches them prefixed. Both must resolve."""
    stats = {"openrouter/vendor/m:free": ModelStats(attempts=30, successes=0, p50_latency_ms=0)}
    assert blend_with_observed("vendor/m:free", 65, stats) < 30


def test_a_cache_hit_is_not_an_observation(tmp_path):
    """
    A cached review made no request. Counting it would credit the model with a 0 ms success
    it never delivered, and an agent re-running one file all day would manufacture dozens of
    observations for the same four models and hand them the top of the ranking.
    """
    path = tmp_path / "stats.json"
    live = _ok("live:free", 1500)
    cached = ModelResponse(
        model="cached:free",
        content="review",
        error=None,
        latency_ms=0,
        served_model="cached:free",
        from_cache=True,
    )

    assert record([live, cached], [], path=path) == 1
    assert set(aggregate(path)) == {"live:free"}


def test_releasing_a_lock_never_masks_the_error_being_handled():
    """
    `release` is only ever called from a `finally`. An exception raised there would replace
    the one the caller was already handling, and `os.close` on an already-closed descriptor
    raises `OSError: Bad file descriptor`.
    """
    from fourtake import filelock

    filelock.release(999)  # must not raise
