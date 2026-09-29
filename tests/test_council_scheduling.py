"""
Scheduling and rate-limiting invariants (plan items S2.3, S2.4, S2.5, S3.4).

These assert behaviour callers depend on — request pacing, seat independence, and that one
slow model cannot set the pace for the whole council — rather than internal call shapes.
"""

import asyncio
import json
import time
from itertools import pairwise
from unittest.mock import AsyncMock, patch

import pytest

from fourtake.client import ModelResponse
from fourtake.config import FourtakeConfig, ProviderConfig
from fourtake.engine import (
    MIN_PER_MODEL_TIMEOUT,
    default_per_model_timeout,
    dispatch_count,
    run_council,
)
from fourtake.rate_limiter import PersistentTokenBucket

# A seat is only filled by a review: findings, or the clean verdict.
REVIEW = "NO_ISSUES_FOUND: Correct, safe, well-structured."


def _config(**overrides) -> FourtakeConfig:
    base = dict(
        providers={"openrouter": ProviderConfig(url="https://x/api", api_key="k")},
        default_models=["auto"],
        rpm_limit=600,  # effectively unthrottled unless a test says otherwise
        min_responses=4,
        timeout=30,
        provider_concurrency={"openrouter": 8},
    )
    base.update(overrides)
    return FourtakeConfig(**base)


def _pool(n: int) -> list[str]:
    return [f"openrouter/m{i}:free" for i in range(n)]


# --------------------------------------------------------------------------------------
# S2.4 — the bucket must serialise waiters, not release them together
# --------------------------------------------------------------------------------------

async def _delays(bucket, n):
    """Delays each of n concurrent acquires would sleep for, without actually sleeping."""
    captured = []
    real_sleep = asyncio.sleep

    async def spy(delay, *a, **kw):
        captured.append(round(delay, 1))

    asyncio.sleep = spy
    try:
        await asyncio.gather(*[bucket.acquire(1) for _ in range(n)])
    finally:
        asyncio.sleep = real_sleep
    return sorted(captured)


@pytest.mark.asyncio
async def test_a_full_council_still_fires_at_once(tmp_path):
    """
    Burst capacity is the point of a bucket. Spacing the first `rpm` requests would serialise
    the council and undo the over-dispatch work.
    """
    bucket = PersistentTokenBucket(rpm=10)
    bucket.state_file = tmp_path / "burst.json"

    assert await _delays(bucket, 4) == []  # nobody slept


@pytest.mark.asyncio
async def test_waiters_past_the_burst_are_spaced_evenly(tmp_path):
    """
    Clamping a negative token balance at a floor made every waiter past that floor compute an
    identical delay and wake together, reinstating the burst further down the queue.
    """
    bucket = PersistentTokenBucket(rpm=10)
    bucket.state_file = tmp_path / "queue.json"

    delays = await _delays(bucket, 26)

    assert len(delays) == 16  # 10 went through on the burst allowance
    # The 26 acquires take real wall-clock time, and each one recomputes its wait against a
    # fresh `now`, so the absolute instants drift by tens of milliseconds under load. Assert
    # the invariant that matters — one token interval between consecutive waiters — rather
    # than exact values, which made this test fail only when the suite ran as a whole.
    gaps = [round(b - a, 1) for a, b in pairwise(delays)]
    assert gaps == pytest.approx([6.0] * 15, abs=0.2)
    assert delays[0] == pytest.approx(6.0, abs=0.2)
    assert len(set(delays)) == len(delays), "no two waiters may wake at the same instant"


@pytest.mark.asyncio
async def test_serialisation_holds_at_any_queue_depth(tmp_path):
    """The old floor meant collisions reappeared past a fixed depth; there is no such depth."""
    bucket = PersistentTokenBucket(rpm=10)
    bucket.state_file = tmp_path / "deep.json"

    delays = await _delays(bucket, 120)
    assert len(set(delays)) == len(delays)


@pytest.mark.asyncio
async def test_acquire_refuses_instead_of_reserving_when_the_wait_is_too_long(tmp_path):
    """
    A caller that will not send must not consume budget: reserving and then abandoning the
    slot starves the callers that would have used it.
    """
    bucket = PersistentTokenBucket(rpm=10)
    bucket.state_file = tmp_path / "refuse.json"
    await _delays(bucket, 12)  # exhaust the burst and queue two

    before = json.loads(bucket.state_file.read_text().strip())["next_available"]
    granted = await bucket.acquire(1, max_wait=0.5)
    after = json.loads(bucket.state_file.read_text().strip())["next_available"]

    assert granted is False
    assert after == before, "a refused acquire must not move the reservation marker"


@pytest.mark.asyncio
async def test_acquire_grants_when_within_max_wait(tmp_path):
    bucket = PersistentTokenBucket(rpm=10)
    bucket.state_file = tmp_path / "grant.json"
    assert await bucket.acquire(1, max_wait=0.5) is True


# --------------------------------------------------------------------------------------
# S2.3 — every attempt is metered, not just the first
# --------------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_every_retry_spends_a_rate_limit_token():
    """
    Charging only the first attempt let a retry storm bypass the governor: 12 rotated
    models x 5 attempts = 60 unmetered requests.
    """
    from fourtake.client import query_model

    calls = {"tokens": 0, "posts": 0}

    async def acquire():
        calls["tokens"] += 1

    client = AsyncMock()

    def make_429(*a, **kw):
        calls["posts"] += 1
        resp = AsyncMock()
        resp.status_code = 429
        resp.headers = {}
        resp.json = lambda: {"error": {"message": "slow down"}}
        return resp

    client.post.side_effect = make_429

    with patch("fourtake.client.asyncio.sleep", new=AsyncMock()):
        await query_model(
            client, "http://d", "k", "m", "m", "code", max_retries=3, acquire_token=acquire
        )

    assert calls["posts"] == 4  # initial + 3 retries
    assert calls["tokens"] == calls["posts"]


# --------------------------------------------------------------------------------------
# S3.4 — a seat is a distinct reviewer, not a distinct request
# --------------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_router_serving_the_same_model_twice_does_not_fill_two_seats():
    """
    Live evidence: `openrouter/free` was served by `inclusionai/ling-3.0-flash-fin:free`,
    which is itself in the rotation pool. Two copies of one opinion are one opinion.
    """
    served_by = {
        "openrouter/m0:free": "same/model",
        "openrouter/m1:free": "same/model",
        "openrouter/m2:free": "other/model",
    }

    async def fake_query(**kwargs):
        name = kwargs["display_model_name"]
        return ModelResponse(
            model=name,
            content=REVIEW,
            error=None,
            latency_ms=1,
            served_model=served_by.get(name, name),
        )

    with patch("fourtake.engine.query_model", new=fake_query):
        result = await run_council(
            _config(min_responses=2),
            "code",
            model_pool=_pool(3),
            active_count=2,
            use_cache=False,
        )

    seats = {r.served_model for r in result.responses if r.duplicate_of is None}
    duplicates = [r for r in result.responses if r.duplicate_of]

    assert len(seats) == 2, "two distinct reviewers must be seated"
    assert len(duplicates) == 1
    assert duplicates[0].duplicate_of == "same/model"
    # The duplicate is still returned to the caller, just not counted as an independent seat.
    assert duplicates[0].content == REVIEW


@pytest.mark.asyncio
async def test_duplicate_seat_rotates_in_a_replacement():
    launched: list[str] = []
    served_by = {"openrouter/m0:free": "dup", "openrouter/m1:free": "dup"}

    async def fake_query(**kwargs):
        name = kwargs["display_model_name"]
        launched.append(name)
        return ModelResponse(
            model=name, content=REVIEW, error=None, latency_ms=1, served_model=served_by.get(name, name)
        )

    with patch("fourtake.engine.query_model", new=fake_query):
        result = await run_council(
            _config(min_responses=2), "code", model_pool=_pool(4), active_count=2, use_cache=False
        )

    # m0 and m1 collide on "dup", so a third model must be pulled from the pool.
    assert len(launched) > 2
    assert len({r.served_model for r in result.responses if r.duplicate_of is None}) == 2


def _serving(served_by: dict[str, str], slow: frozenset[str] = frozenset()):
    async def fake_query(**kwargs):
        name = kwargs["display_model_name"]
        if name in slow:
            await asyncio.sleep(0.05)
        return ModelResponse(model=name, content=REVIEW, error=None, latency_ms=1,
                             served_model=served_by.get(name, name))
    return fake_query


@pytest.mark.asyncio
async def test_a_second_model_from_a_seated_vendor_yields_the_seat_to_another_vendor():
    """
    Two models from one vendor share training data, so their agreement is weaker evidence than
    two vendors'. 21 of 28 recorded councils seated such a pair, half of them because
    `openrouter/free` served a vendor that already held a seat.
    """
    served_by = {
        "openrouter/m0:free": "nvidia/nemotron-super:free",
        "openrouter/m1:free": "nvidia/nemotron-nano:free",
        "openrouter/m2:free": "cohere/north:free",
    }
    # Both nvidia models answer first, so without the rule they fill both seats and the
    # council closes before the other vendor is heard.
    slow = frozenset({"openrouter/m2:free"})
    with patch("fourtake.engine.query_model", new=_serving(served_by, slow)):
        result = await run_council(
            _config(min_responses=2), "code", model_pool=_pool(3), active_count=2, use_cache=False
        )

    seated = [r for r in result.responses if r.duplicate_of is None]
    assert sorted(r.served_model.split("/")[0] for r in seated) == ["cohere", "nvidia"]
    (held,) = [r for r in result.responses if r.duplicate_of]
    assert held.served_model.startswith("nvidia/")
    assert held.duplicate_of in {r.served_model for r in seated}
    assert held.content == REVIEW, "the held answer is still returned for reading"


@pytest.mark.asyncio
async def test_a_vendor_never_holds_two_seats_even_when_no_other_vendor_answers():
    """
    Seating a second answer from one vendor when nobody else answered in time filled 21 of 50
    councils on a public benchmark with two or three nvidia models. The council now reports
    the seat as missing instead of counting one vendor twice.
    """
    served_by = {"openrouter/m0:free": "nvidia/a:free", "openrouter/m1:free": "nvidia/b:free"}
    with patch("fourtake.engine.query_model", new=_serving(served_by)):
        result = await run_council(
            _config(min_responses=2), "code", model_pool=_pool(2), active_count=2, use_cache=False
        )

    seated = [r for r in result.responses if r.duplicate_of is None]
    assert len(seated) == 1 and seated[0].served_model.startswith("nvidia/")
    (held,) = [r for r in result.responses if r.duplicate_of]
    assert held.duplicate_of == seated[0].served_model
    assert result.error is None
    assert "only 1 distinct" in result.warning


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_first", [False, True], ids=["seat-read-first", "failure-read-first"])
async def test_no_replacement_is_launched_from_a_vendor_already_seated(failure_first):
    """
    A spare from a seated vendor could never take the seat; the next vendor's model goes.

    m0's failure and m1's seat finish together, and `asyncio.wait` returns them as a set
    iterated in memory-address order. Rotating on the failure before the seat was read
    launched the nvidia spare on some runs and not others, so both orders are pinned here.
    The failure ranks first in the pool, so reading the batch in pool order reads it first.
    """
    real_wait = asyncio.wait
    batches: list[list[str]] = []

    async def wait_in_pinned_order(tasks, **kwargs):
        done, pending = await real_wait(tasks, **kwargs)
        ordered = sorted(done, key=lambda t: t.get_name(), reverse=not failure_first)
        batches.append([t.get_name() for t in ordered])
        return ordered, pending

    launched: list[str] = []
    served_by = {
        "openrouter/m0:free": "bad/x:free",      # fails, so a replacement is rotated in
        "openrouter/m1:free": "nvidia/a:free",
        "openrouter/m2:free": "nvidia/b:free",   # same vendor as the seat: must be skipped
        "openrouter/m3:free": "cohere/c:free",
    }

    async def fake_query(**kwargs):
        name = kwargs["display_model_name"]
        launched.append(name)
        if name.endswith("m0:free"):
            return ModelResponse(model=name, content=None, error="Provider error 429", latency_ms=1)
        return ModelResponse(model=name, content=REVIEW, error=None, latency_ms=1,
                             served_model=served_by[name])

    pool = ["openrouter/m0:free", "openrouter/m1:free", "openrouter/nvidia/b:free", "openrouter/m3:free"]
    served_by["openrouter/nvidia/b:free"] = "nvidia/b:free"
    with patch("fourtake.engine.query_model", new=fake_query), \
         patch("fourtake.engine.dispatch_count", return_value=2), \
         patch.object(asyncio, "wait", new=wait_in_pinned_order):
        result = await run_council(
            _config(min_responses=2), "code", model_pool=pool, active_count=2, use_cache=False
        )

    assert len(batches[0]) == 2, "the seat and the failure must be read in one batch"
    assert "openrouter/nvidia/b:free" not in launched
    assert sorted(r.served_model for r in result.responses if r.duplicate_of is None) == [
        "cohere/c:free", "nvidia/a:free"
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("m1_served_by", ["nvidia/b:free", "nvidia/a:free"],
                         ids=["same-vendor", "same-served-model"])
@pytest.mark.parametrize("reverse", [False, True], ids=["pool-order", "reverse-order"])
async def test_a_contested_seat_goes_to_the_earlier_pool_entry_in_either_batch_order(m1_served_by, reverse):
    """
    Answers that return without awaiting — cache hits above all — land in one batch, and
    `asyncio.wait` returns it as a set iterated in memory-address order. When two of them
    contend for one seat, whichever was read first took it, so a fully cached rerun could seat
    the other model and change agreement counts. The pool is ranked best first; its earlier
    entry takes the seat.
    """
    real_wait = asyncio.wait
    batches: list[list[str]] = []

    async def wait_in_pinned_order(tasks, **kwargs):
        done, pending = await real_wait(tasks, **kwargs)
        ordered = sorted(done, key=lambda t: t.get_name(), reverse=reverse)
        batches.append([t.get_name() for t in ordered])
        return ordered, pending

    served_by = {
        "openrouter/m0:free": "nvidia/a:free",
        "openrouter/m1:free": m1_served_by,
        "openrouter/m2:free": "cohere/c:free",   # rotated in for the seat m1 could not take
    }
    with patch("fourtake.engine.query_model", new=_serving(served_by)), \
         patch("fourtake.engine.dispatch_count", return_value=2), \
         patch.object(asyncio, "wait", new=wait_in_pinned_order):
        result = await run_council(
            _config(min_responses=2), "code", model_pool=_pool(3), active_count=2, use_cache=False
        )

    assert len(batches[0]) == 2, "both contenders must be read in one batch"
    assert {r.model for r in result.responses if r.duplicate_of is None} == {
        "openrouter/m0:free", "openrouter/m2:free"
    }
    (held,) = [r for r in result.responses if r.duplicate_of]
    assert held.model == "openrouter/m1:free"
    assert held.duplicate_of == "nvidia/a:free"


@pytest.mark.asyncio
async def test_pool_aliases_resolving_to_one_model_get_one_seat():
    """"openrouter/x:free" and "x:free" name one model; they used to occupy two seats."""
    seen: list[str] = []

    async def fake_query(**kwargs):
        seen.append(kwargs["display_model_name"])
        return ModelResponse(
            model=kwargs["display_model_name"], content=REVIEW, error=None, latency_ms=1
        )

    with patch("fourtake.engine.query_model", new=fake_query):
        await run_council(
            _config(min_responses=1),
            "code",
            model_pool=["openrouter/A:free", "A:free"],
            active_count=2,
            use_cache=False,
        )

    assert seen == ["openrouter/A:free"]


# --------------------------------------------------------------------------------------
# S2.5 — one slow model must not set the pace for the council
# --------------------------------------------------------------------------------------

@pytest.mark.parametrize(
    "min_responses,pool,active,expected",
    [
        (4, 12, 4, 6),   # 4 seats + 2 spares
        (4, 5, 4, 5),    # pool smaller than the ideal cohort
        (1, 12, 1, 2),
        (2, 2, 2, 2),
        (4, 12, 8, 8),   # explicit active_count wins when larger
    ],
)
def test_dispatch_count_adds_spares_without_exceeding_the_pool(min_responses, pool, active, expected):
    assert dispatch_count(min_responses, pool, active) == expected


@pytest.mark.asyncio
async def test_council_does_not_wait_for_the_slowest_model():
    """
    Live baseline: two members answered in 1.7s and 6.2s but the run took 157s because a
    third took 156.9s and there was no slack. Spares must let the fast majority close it.
    """
    async def fake_query(**kwargs):
        name = kwargs["display_model_name"]
        if name == "openrouter/m0:free":
            await asyncio.sleep(30)  # the straggler
        return ModelResponse(model=name, content=REVIEW, error=None, latency_ms=1, served_model=name)

    started = time.monotonic()
    with patch("fourtake.engine.query_model", new=fake_query):
        result = await run_council(
            _config(min_responses=3), "code", model_pool=_pool(8), active_count=3, use_cache=False
        )
    elapsed = time.monotonic() - started

    assert len(result.responses) >= 3
    assert elapsed < 5, f"council waited {elapsed:.1f}s on a straggler it had spares for"


@pytest.mark.asyncio
async def test_per_model_timeout_abandons_a_hung_model():
    """
    The cap only has to bite when the hung model is actually needed — with spares available
    the council closes first, which is the better outcome and is covered above. Here the
    pool is exhausted, so without the cap the run would block until the global timeout.
    """
    async def fake_query(**kwargs):
        name = kwargs["display_model_name"]
        if name == "openrouter/m0:free":
            await asyncio.sleep(60)
        return ModelResponse(model=name, content=REVIEW, error=None, latency_ms=1, served_model=name)

    started = time.monotonic()
    with patch("fourtake.engine.query_model", new=fake_query):
        result = await run_council(
            _config(min_responses=2, timeout=30),
            "code",
            model_pool=_pool(2),  # no spares: m0 hangs, m1 answers, both seats are needed
            active_count=2,
            use_cache=False,
            per_model_timeout=0.2,
        )
    elapsed = time.monotonic() - started

    timed_out = [e for e in result.errors if "per-model timeout" in (e.error or "")]
    assert timed_out, f"expected a per-model timeout, got {[e.error for e in result.errors]}"
    assert elapsed < 5, "the cap must fire long before the 30s council deadline"
    assert result.warning and "distinct" in result.warning


@pytest.mark.parametrize(
    "council_timeout,expected",
    [(600, 300.0), (120, 60.0), (30, 60.0), (1000, 500.0)],
)
def test_per_model_timeout_tracks_the_council_budget(council_timeout, expected):
    """
    A flat 90s cap killed models that legitimately needed minutes on a large input — seen
    live, where every member was abandoned mid-review. Half the deadline adapts and still
    guarantees room to rotate.
    """
    assert default_per_model_timeout(council_timeout) == expected
    assert default_per_model_timeout(council_timeout) >= MIN_PER_MODEL_TIMEOUT


@pytest.mark.asyncio
async def test_a_router_seating_a_model_that_cannot_review_does_not_fill_a_seat():
    """
    Observed live in the eval corpus: `openrouter/free` was served by
    `nvidia/nemotron-3.5-content-safety:free`, a moderation classifier. It answered
    "User Safety: safe" and that non-review counted as one of four independent opinions.
    """
    served_by = {
        "openrouter/m0:free": "nvidia/nemotron-3.5-content-safety:free",
        "openrouter/m1:free": "good-a/reviewer:free",
        "openrouter/m2:free": "good-b/reviewer:free",
    }

    async def fake_query(**kwargs):
        name = kwargs["display_model_name"]
        served = served_by.get(name, name)
        return ModelResponse(
            model=name, content="User Safety: safe" if "content-safety" in served else REVIEW,
            error=None, latency_ms=1, served_model=served,
        )

    with patch("fourtake.engine.query_model", new=fake_query):
        result = await run_council(
            _config(min_responses=2), "code", model_pool=_pool(3), active_count=2,
            use_cache=False,
        )

    seats = {r.served_model for r in result.responses if r.duplicate_of is None}
    assert "nvidia/nemotron-3.5-content-safety:free" not in seats
    assert len(seats) == 2, "a fresh model must be rotated into the rejected seat"
    assert any("incompatible" in (e.error or "") for e in result.errors)


@pytest.mark.parametrize("answer", [
    # Each transcribed from a seat a public benchmark's councils filled.
    "<|tool_call_start|>[read_file(path='models/Constants.java', offset=200, limit=20)]<|tool_call_end|>",
    "<unk><unk><unk><unk><unk> ( 1-",
    "We are given a diff that shows changes in multiple files. We are to review the added lines.",
    "* [WARNING] LOGIC",
])
@pytest.mark.asyncio
async def test_an_answer_that_is_not_a_review_does_not_fill_a_seat(answer):
    """
    12 of 200 seats on the benchmark went to answers with neither a finding nor the clean
    verdict, and each read as a reviewer that had looked and found nothing: the council was
    three reviewers wearing four seats.
    """
    async def fake_query(**kwargs):
        name = kwargs["display_model_name"]
        return ModelResponse(model=name, content=answer if name.startswith("openrouter/m0") else REVIEW,
                             error=None, latency_ms=1, served_model=name.replace("openrouter/", "v-"))

    with patch("fourtake.engine.query_model", new=fake_query):
        result = await run_council(
            _config(min_responses=2), "code", model_pool=_pool(3), active_count=2, use_cache=False,
        )

    seats = {r.served_model for r in result.responses if r.duplicate_of is None}
    assert "v-m0:free" not in seats
    assert len(seats) == 2, "a spare must be rotated into the seat the non-review left"
    assert [e.error for e in result.errors] == ["answered without a review: no finding and no NO_ISSUES_FOUND"]


@pytest.mark.asyncio
async def test_findings_and_the_clean_verdict_both_fill_a_seat():
    finding = "* [WARNING] [LOGIC] Off by one (at pack)\n  - Issue: the last byte is dropped\n"

    async def fake_query(**kwargs):
        name = kwargs["display_model_name"]
        return ModelResponse(model=name, content=finding if name.endswith("m0:free") else REVIEW,
                             error=None, latency_ms=1, served_model=name.replace("openrouter/", "v-"))

    with patch("fourtake.engine.query_model", new=fake_query):
        result = await run_council(
            _config(min_responses=2), "code", model_pool=_pool(2), active_count=2, use_cache=False,
        )

    assert len(result.responses) == 2 and result.errors == []


@pytest.mark.asyncio
async def test_a_non_review_is_neither_cached_nor_replayed_from_the_cache():
    calls = []

    async def fake_query(**kwargs):
        calls.append(kwargs["display_model_name"])
        return ModelResponse(model=kwargs["display_model_name"], content="<unk><unk>",
                             error=None, latency_ms=1, served_model="v/only")

    with patch("fourtake.engine.save_cached_response") as save, \
         patch("fourtake.engine.get_cached_response",
               return_value=ModelResponse(model="openrouter/m0:free", content="<unk>", error=None,
                                          latency_ms=1)), \
         patch("fourtake.engine.query_model", new=fake_query):
        await run_council(_config(min_responses=1), "code", model_pool=_pool(1), active_count=1,
                          use_cache=True)

    assert calls == ["openrouter/m0:free"], "a cached non-review must not be replayed"
    save.assert_not_called()


def _counting_in_flight(peak: list[int]):
    """A fake query that holds the semaphore the engine hands it, as the real client does."""
    in_flight = 0

    async def fake_query(**kwargs):
        nonlocal in_flight
        async with kwargs["semaphore"]:
            in_flight += 1
            peak[0] = max(peak[0], in_flight)
            await asyncio.sleep(0.02)
            in_flight -= 1
        name = kwargs["display_model_name"]
        return ModelResponse(model=name, content=REVIEW, error=None, latency_ms=1, served_model=name)
    return fake_query


@pytest.mark.asyncio
async def test_every_model_the_council_launches_is_in_flight_at_once_by_default():
    """
    A council launches spares so one straggler cannot stall it — six models for four seats.
    A default cap of four in flight held two of them back, their per-model clock running, so the
    spares that were meant to race the stragglers queued behind them.
    """
    peak = [0]
    with patch("fourtake.engine.query_model", new=_counting_in_flight(peak)):
        await run_council(_config(provider_concurrency={}), "code", model_pool=_pool(6), active_count=4, use_cache=False)
    assert peak[0] == 6


@pytest.mark.asyncio
async def test_a_configured_concurrency_limit_is_still_kept():
    peak = [0]
    with patch("fourtake.engine.query_model", new=_counting_in_flight(peak)):
        await run_council(_config(provider_concurrency={"openrouter": 2}), "code", model_pool=_pool(6),
                          active_count=4, use_cache=False)
    assert peak[0] == 2


def test_no_concurrency_cap_is_configured_by_default(tmp_path, monkeypatch):
    from fourtake.config import load_config

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-test")
    assert load_config({}).provider_concurrency == {}


@pytest.mark.asyncio
async def test_the_result_counts_every_model_the_council_asked():
    """
    `models_requested` in the JSON output was the length of the configured list, which is
    ["auto"]: every auto council reported asking one model. It counts spares and replacements,
    including a spare cancelled once the seats filled.
    """
    served_by = {f"openrouter/m{i}:free": f"vendor{i}/x:free" for i in range(6)}
    with patch("fourtake.engine.query_model", new=_serving(served_by, slow=frozenset({"openrouter/m5:free"}))):
        result = await run_council(_config(), "code", model_pool=_pool(6), active_count=4, use_cache=False)
    assert result.models_asked == 6
