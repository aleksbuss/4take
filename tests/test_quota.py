"""
OpenRouter's daily free-model allowance: asked before a review, and when a 429 may mean it is spent.

Once the allowance is gone every request answers 429 "Rate limit exceeded", which looks like a busy
provider. 4take used to wait and rotate through the whole pool until its deadline, ten minutes by
default, and end on a list of errors that never said why.
"""

import asyncio
import json
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx

from fourtake import cli, quota
from fourtake.client import query_model
from fourtake.config import FourtakeConfig, ProviderConfig
from fourtake.engine import CouncilResult, run_council
from fourtake.mcp_server import get_4take_opinion
from fourtake.preflight import free_limit_problem
from fourtake.quota import (
    KEY_URL,
    FreeQuota,
    QuotaGuard,
    daily_limit_message,
    describe_wait,
    is_openrouter_url,
    parse_free_quota,
    seconds_until_utc_midnight,
)

# The suite stubs the network read for everyone; the tests that exercise it put the real one back.
REAL_FETCH = quota.fetch_free_quota
CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
EVENING = datetime(2026, 10, 26, 20, 48, tzinfo=timezone.utc)  # 3 h 12 min before the reset


def _key_reply(used, limit):
    return {"data": {"free_model_daily_requests": {"used": used, "limit": limit, "remaining": limit - used}}}


@pytest.fixture
def real_quota(monkeypatch):
    monkeypatch.setattr("fourtake.preflight.fetch_free_quota", REAL_FETCH)
    monkeypatch.setattr("fourtake.quota.fetch_free_quota", REAL_FETCH)


def _config(url=CHAT_URL, key="sk-or-v1-x", **overrides):
    base = dict(
        providers={"openrouter": ProviderConfig(url=url, api_key=key)},
        default_models=["auto"],
        rpm_limit=600,
        min_responses=4,
        timeout=60,
        provider_concurrency={"openrouter": 8},
    )
    base.update(overrides)
    return FourtakeConfig(**base)


# --------------------------------------------------------------------------------------
# Reading the counter
# --------------------------------------------------------------------------------------


def test_the_counter_is_read_from_the_key_reply():
    assert parse_free_quota(_key_reply(90, 1000)) == FreeQuota(used=90, limit=1000, remaining=910)


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"data": {}},
        {"data": {"free_model_daily_requests": None}},
        {"data": {"free_model_daily_requests": {"used": 1}}},
        {"data": {"free_model_daily_requests": {"used": "1", "limit": 50, "remaining": 49}}},
        {"data": {"free_model_daily_requests": {"used": True, "limit": 50, "remaining": 49}}},
        [],
        None,
    ],
)
def test_a_reply_without_a_usable_counter_reads_as_unknown(payload):
    assert parse_free_quota(payload) is None


@pytest.mark.asyncio
@respx.mock
async def test_the_counter_is_fetched_with_the_key_as_a_bearer_token(real_quota):
    route = respx.get(KEY_URL).mock(return_value=httpx.Response(200, json=_key_reply(50, 50)))
    async with httpx.AsyncClient() as client:
        assert await REAL_FETCH(client, "sk-or-v1-abc") == FreeQuota(50, 50, 0)
    assert route.calls.last.request.headers["authorization"] == "Bearer sk-or-v1-abc"


@pytest.mark.asyncio
@respx.mock
@pytest.mark.parametrize(
    "reply",
    [
        httpx.Response(401, json={"error": {"message": "User not found.", "code": 401}}),
        httpx.Response(500),
        httpx.Response(200, text="<html>not json</html>"),
        httpx.Response(200, json={"data": {}}),
        httpx.ConnectError("offline"),
        httpx.ReadTimeout("slow"),
    ],
)
async def test_every_way_the_read_can_fail_reads_as_unknown_and_never_raises(reply):
    if isinstance(reply, Exception):
        respx.get(KEY_URL).mock(side_effect=reply)
    else:
        respx.get(KEY_URL).mock(return_value=reply)
    async with httpx.AsyncClient() as client:
        assert await REAL_FETCH(client, "k") is None


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (CHAT_URL, True),
        ("https://openrouter.ai/api/v1/models", True),
        ("https://proxy.example.com/openrouter.ai/v1", False),
        ("https://x/api", False),
        ("", False),
        (None, False),
    ],
)
def test_only_openrouter_itself_has_the_allowance(url, expected):
    assert is_openrouter_url(url) is expected


# --------------------------------------------------------------------------------------
# What is said
# --------------------------------------------------------------------------------------


def test_the_wait_until_the_reset_is_counted_to_the_midnight_in_utc():
    assert seconds_until_utc_midnight(EVENING) == 3 * 3600 + 12 * 60
    # The same instant in another zone is the same wait.
    plus_three = EVENING.astimezone(timezone(timedelta(hours=3)))
    assert seconds_until_utc_midnight(plus_three) == 3 * 3600 + 12 * 60


@pytest.mark.parametrize(
    ("seconds", "text"),
    [
        (11520, "3 h 12 min"),
        (7200, "2 h"),
        (2820, "47 min"),
        (61, "2 min"),
        (20, "1 min"),
        (0, "under a minute"),
        (3570, "1 h"),
    ],
)
def test_the_wait_reads_in_hours_and_minutes_rounded_up(seconds, text):
    assert describe_wait(seconds) == text


def test_the_message_for_an_account_that_never_bought_credits_says_what_lifts_the_limit():
    text = daily_limit_message(FreeQuota(used=50, limit=50, remaining=0), now=EVENING)
    assert "used up: 50 of 50 requests" in text
    assert "00:00 UTC" in text and "3 h 12 min" in text
    assert "never bought credits gets 50" in text and "$10 or more gets 1000" in text
    assert "https://openrouter.ai/settings/credits" in text
    assert "6 to 12 requests" in text


def test_the_message_for_an_account_that_already_has_the_higher_limit_only_says_to_wait():
    text = daily_limit_message(FreeQuota(used=1000, limit=1000, remaining=0), now=EVENING)
    assert "used up: 1000 of 1000 requests" in text
    assert "already has the higher limit" in text
    assert "settings/credits" not in text


# --------------------------------------------------------------------------------------
# Before the council
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_a_spent_allowance_stops_the_review_before_anything_is_sent(real_quota):
    respx.get(KEY_URL).mock(return_value=httpx.Response(200, json=_key_reply(50, 50)))
    problem = await free_limit_problem(_config())
    assert problem and "used up: 50 of 50" in problem


@pytest.mark.asyncio
@respx.mock
async def test_an_allowance_with_requests_left_does_not_stop_it(real_quota):
    respx.get(KEY_URL).mock(return_value=httpx.Response(200, json=_key_reply(10, 50)))
    assert await free_limit_problem(_config()) is None


@pytest.mark.asyncio
@respx.mock
async def test_an_unknown_allowance_does_not_stop_it(real_quota):
    respx.get(KEY_URL).mock(return_value=httpx.Response(500))
    assert await free_limit_problem(_config()) is None


@pytest.mark.asyncio
@respx.mock
async def test_nothing_is_asked_without_a_key_or_for_another_host(real_quota):
    route = respx.get(KEY_URL).mock(return_value=httpx.Response(200, json=_key_reply(50, 50)))
    assert await free_limit_problem(_config(key="")) is None
    assert await free_limit_problem(_config(url="https://proxy.example.com/v1/chat")) is None
    assert not route.called


def test_the_cli_stops_on_a_spent_allowance_before_discovering_models(capsys, caplog):
    with (
        patch("sys.argv", ["4take", "--quiet"]),
        patch("fourtake.cli.read_stdin", return_value="def add(a, b):\n    return a - b\n"),
        patch("fourtake.cli.free_limit_problem", new=AsyncMock(return_value="limit used up for today")),
        patch("fourtake.cli.choose_models") as discover,
        patch("fourtake.cli.run_council") as council,
    ):
        with pytest.raises(SystemExit) as exc:
            cli.main()
    assert exc.value.code == 1
    discover.assert_not_called()
    council.assert_not_called()
    assert "limit used up for today" in caplog.text


@pytest.mark.asyncio
async def test_the_mcp_tool_refuses_with_the_reason_in_the_report_for_the_user():
    ctx = AsyncMock()
    ctx.request_context = None
    with (
        patch("fourtake.mcp_server.load_config", new=lambda *a, **k: _config()),
        patch(
            "fourtake.mcp_server.free_limit_problem", new=AsyncMock(return_value="limit used up for today")
        ),
        patch("fourtake.mcp_server.run_council", new_callable=AsyncMock) as council,
    ):
        payload = json.loads(await get_4take_opinion("def f():\n    return 1\n", ctx=ctx))
    council.assert_not_called()
    assert payload["status"] == "error"
    assert "limit used up for today" in payload["error"]
    assert "limit used up for today" in payload["report_for_user"][0]


# --------------------------------------------------------------------------------------
# During the council: a 429 that is the allowance running out
# --------------------------------------------------------------------------------------


def _rate_limited():
    return httpx.Response(
        429,
        headers={"X-RateLimit-Limit": "50", "X-RateLimit-Remaining": "0"},
        json={
            "error": {
                "code": 429,
                "message": "Rate limit exceeded",
                "metadata": {"error_type": "rate_limit_exceeded"},
            }
        },
    )


@pytest.mark.asyncio
async def test_the_guard_asks_once_for_any_number_of_concurrent_429s():
    asked = []

    async def fetch(client, key):
        asked.append(key)
        await asyncio.sleep(0.01)
        return FreeQuota(50, 50, 0)

    guard = QuotaGuard(httpx.AsyncClient(), "k")
    with patch("fourtake.quota.fetch_free_quota", new=fetch):
        answers = await asyncio.gather(*(guard.exhausted() for _ in range(8)))
        assert all(answers) and guard.tripped
        assert await guard.exhausted() is True
    assert len(asked) == 1
    assert "used up: 50 of 50" in guard.message


@pytest.mark.asyncio
async def test_not_spent_is_remembered_briefly_and_asked_again_after():
    now = [0.0]
    answers = [FreeQuota(10, 50, 40), FreeQuota(50, 50, 0)]

    async def fetch(client, key):
        return answers.pop(0)

    guard = QuotaGuard(httpx.AsyncClient(), "k", recheck_after=15.0, clock=lambda: now[0])
    with patch("fourtake.quota.fetch_free_quota", new=fetch):
        assert await guard.exhausted() is False
        now[0] = 10.0
        assert await guard.exhausted() is False, "inside the window, no new question"
        assert len(answers) == 1
        now[0] = 16.0
        assert await guard.exhausted() is True, "the allowance ran out mid-run"


@pytest.mark.asyncio
async def test_an_unknown_answer_never_trips_the_guard():
    guard = QuotaGuard(httpx.AsyncClient(), "k")
    with patch("fourtake.quota.fetch_free_quota", new=AsyncMock(return_value=None)):
        assert await guard.exhausted() is False
    assert guard.message is None


@pytest.mark.asyncio
@respx.mock
async def test_a_429_that_is_the_spent_allowance_is_not_waited_out(real_quota):
    respx.post(CHAT_URL).mock(return_value=_rate_limited())
    respx.get(KEY_URL).mock(return_value=httpx.Response(200, json=_key_reply(50, 50)))
    async with httpx.AsyncClient() as client:
        guard = QuotaGuard(client, "k")
        with patch("fourtake.client.asyncio.sleep", new=AsyncMock(side_effect=AssertionError("waited"))):
            response = await query_model(client, CHAT_URL, "k", "m:free", "m:free", "code", quota_guard=guard)
    assert response.content is None
    assert response.error == guard.message and "used up: 50 of 50" in response.error


@pytest.mark.asyncio
@respx.mock
async def test_a_429_with_requests_left_is_retried_as_before(real_quota):
    respx.post(CHAT_URL).mock(
        side_effect=[
            _rate_limited(),
            httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": "NO_ISSUES_FOUND: fine"}, "finish_reason": "stop"}]
                },
            ),
        ]
    )
    respx.get(KEY_URL).mock(return_value=httpx.Response(200, json=_key_reply(10, 50)))
    slept = AsyncMock()
    async with httpx.AsyncClient() as client:
        guard = QuotaGuard(client, "k")
        with patch("fourtake.client.asyncio.sleep", new=slept):
            response = await query_model(client, CHAT_URL, "k", "m:free", "m:free", "code", quota_guard=guard)
    assert response.error is None and response.content.startswith("NO_ISSUES_FOUND")
    slept.assert_awaited_once()


@pytest.mark.asyncio
@respx.mock
async def test_a_council_that_meets_the_spent_allowance_ends_at_once_with_one_clear_error(real_quota):
    posts = respx.post(CHAT_URL).mock(return_value=_rate_limited())
    respx.get(KEY_URL).mock(return_value=httpx.Response(200, json=_key_reply(50, 50)))
    pool = [f"openrouter/m{i}:free" for i in range(8)]
    started = time.monotonic()
    result = await run_council(_config(), "def f():\n    return 1\n", model_pool=pool, active_count=4)
    assert time.monotonic() - started < 5, "no backoff, no rotation through the pool"
    assert isinstance(result, CouncilResult)
    assert result.responses == [] and result.errors == []
    assert result.error and "used up: 50 of 50" in result.error
    assert posts.call_count <= 6, "only the models launched at the start were asked, once each"


@pytest.mark.asyncio
@respx.mock
async def test_a_council_with_some_answers_before_the_limit_keeps_them_and_says_it_stopped(real_quota):
    review = httpx.Response(
        200,
        json={
            "model": "vendor-a/m:free",
            "choices": [{"message": {"content": "NO_ISSUES_FOUND: fine"}, "finish_reason": "stop"}],
        },
    )

    def reply(request):
        return review if json.loads(request.content)["model"] == "m0:free" else _rate_limited()

    respx.post(CHAT_URL).mock(side_effect=reply)
    respx.get(KEY_URL).mock(return_value=httpx.Response(200, json=_key_reply(50, 50)))
    pool = [f"openrouter/m{i}:free" for i in range(8)]
    result = await run_council(_config(), "def f():\n    return 1\n", model_pool=pool, active_count=4)
    assert len(result.responses) == 1
    assert result.error is None
    assert "stopped early" in result.warning and "used up: 50 of 50" in result.warning


@pytest.mark.asyncio
@respx.mock
async def test_a_busy_provider_is_still_rotated_around_when_the_allowance_is_fine(real_quota):
    posts = respx.post(CHAT_URL).mock(return_value=_rate_limited())
    respx.get(KEY_URL).mock(return_value=httpx.Response(200, json=_key_reply(5, 50)))
    config = _config(timeout=3)
    with patch("fourtake.client.asyncio.sleep", new=AsyncMock()):
        result = await run_council(
            config,
            "def f():\n    return 1\n",
            model_pool=[f"openrouter/m{i}:free" for i in range(4)],
            active_count=4,
        )
    assert result.error == "all models failed"
    assert posts.call_count > 4, "retried: nothing said the allowance was spent"


def test_the_text_report_names_the_reason_when_nothing_was_reviewed():
    from fourtake.output import format_markdown

    result = CouncilResult(
        responses=[], errors=[], warning=None, error="limit used up for today", total_latency_ms=10
    )
    text = format_markdown(result, _config(), "code")
    assert "> **Error:** limit used up for today" in text


def test_the_cli_prints_the_reason_a_review_never_happened(caplog):
    result = CouncilResult(
        responses=[], errors=[], warning=None, error="limit used up for today", total_latency_ms=10
    )
    with (
        patch("sys.argv", ["4take", "--quiet", "--accept-risk"]),
        patch("fourtake.cli.read_stdin", return_value="def add(a, b):\n    return a - b\n"),
        patch(
            "fourtake.cli.choose_models",
            new=AsyncMock(
                return_value=type(
                    "C",
                    (),
                    {
                        "models": ["openrouter/m:free"],
                        "active_count": 1,
                        "skipped": [],
                        "used_fallback": False,
                    },
                )()
            ),
        ),
        patch("fourtake.cli.run_council", new=AsyncMock(return_value=result)),
    ):
        with pytest.raises(SystemExit) as exc:
            cli.main()
    assert exc.value.code == 1
    assert "limit used up for today" in caplog.text
