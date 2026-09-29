"""
Contract regression tests.

Every case here corresponds to a defect that the pre-existing suite could not catch despite
100% statement and branch coverage, because it asserted what the code did rather than what
callers depend on.
"""

import io
import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from rich.console import Console

from fourtake.client import ModelResponse, _parse_completion, _retry_after_seconds, query_model
from fourtake.config import FourtakeConfig, ProviderConfig
from fourtake.engine import MAX_OUTPUT_TOKENS_CEILING, MIN_OUTPUT_TOKENS, CouncilResult, compute_output_budget
from fourtake.output import exit_code_for


def _config(**overrides) -> FourtakeConfig:
    base = dict(
        providers={"openrouter": ProviderConfig(url="https://x/api", api_key="k")},
        default_models=["auto"],
        rpm_limit=20,
        min_responses=4,
        timeout=600,
        provider_concurrency={"openrouter": 4},
    )
    base.update(overrides)
    return FourtakeConfig(**base)


# --------------------------------------------------------------------------------------
# S1.1 — stdout must carry only the payload
# --------------------------------------------------------------------------------------

def test_json_format_stdout_is_parseable(capsys):
    """
    `4take --format json` is the documented integration path for agents. httpx logs at INFO
    and RichHandler used to write them to stdout, so json.loads(stdout) raised.
    """
    result = CouncilResult(
        responses=[ModelResponse("openrouter/m:free", "finding", None, 10)],
        errors=[],
        warning=None,
        error=None,
        total_latency_ms=10,
    )

    async def fake_council(*args, **kwargs):
        # Reproduce the exact noise that used to corrupt stdout.
        logging.getLogger("httpx").info("HTTP Request: GET https://openrouter.ai/api/v1/models")
        logging.getLogger("4take").warning("Skipping model 'x:free': limit exceeded")
        return result

    argv = ["4take", "--format", "json", "--accept-risk", "--quiet"]
    with patch("sys.argv", argv), \
         patch("fourtake.cli.read_stdin", return_value="print('hi')"), \
         patch("fourtake.cli.run_council", new=fake_council), \
         patch("fourtake.discovery.get_best_free_models", new=AsyncMock(return_value=["openrouter/m:free"])):
        with pytest.raises(SystemExit):
            from fourtake.cli import main

            main()

    captured = capsys.readouterr()
    payload = json.loads(captured.out)  # must not raise
    assert payload["responses"][0]["model"] == "openrouter/m:free"
    assert "HTTP Request" not in captured.out


def test_httpx_info_logs_are_suppressed():
    import fourtake.cli

    assert logging.getLogger("httpx").level == logging.WARNING
    assert logging.getLogger("httpcore").level == logging.WARNING


def test_log_console_targets_stderr_with_markup_disabled():
    """
    RichHandler defaults to a stdout Console — that is what corrupted --format json.
    Asserted on the module's Console rather than the root handlers, because pytest's
    logging plugin swaps handlers out during a run.
    """
    import fourtake.cli

    console = fourtake.cli._LOG_CONSOLE
    assert console.stderr is True
    assert console.is_jupyter is False
    # Emoji substitution turned "...:free: <error>" into "...🆓 <error>".
    buf = io.StringIO()
    probe = Console(file=buf, width=200, emoji=console._emoji, highlight=False)
    probe.print("m:free: boom", markup=False)
    assert ":free" in buf.getvalue() and "🆓" not in buf.getvalue()


# --------------------------------------------------------------------------------------
# S1.2 — Rich must not rewrite model ids
# --------------------------------------------------------------------------------------

def test_free_suffix_is_not_turned_into_an_emoji():
    """':free' followed by ':' formed ':free:', which Rich replaced with the 🆓 emoji."""
    buf = io.StringIO()
    console = Console(file=buf, width=200, force_terminal=False)
    model = "openrouter/google/gemma-4-26b-a4b-it:free"

    console.print(f"[4take] x {model}: Provider error 429", highlight=False, markup=False, emoji=False)

    out = buf.getvalue()
    assert ":free" in out
    assert "🆓" not in out


def test_bracketed_provider_error_does_not_raise_markup_error():
    buf = io.StringIO()
    console = Console(file=buf, width=200, force_terminal=False)
    console.print("[4take] x m:free: upstream said [closed]", highlight=False, markup=False, emoji=False)
    assert "[closed]" in buf.getvalue()


# --------------------------------------------------------------------------------------
# S1.4 — exit codes distinguish success, degradation and failure
# --------------------------------------------------------------------------------------

@pytest.mark.parametrize(
    "n_responses,min_responses,expected",
    [(4, 4, 0), (5, 4, 0), (1, 4, 2), (3, 4, 2), (0, 4, 1)],
)
def test_exit_codes(n_responses, min_responses, expected):
    result = CouncilResult(
        responses=[ModelResponse(f"m{i}", "c", None, 1) for i in range(n_responses)],
        errors=[],
        warning=None,
        error=None,
        total_latency_ms=1,
    )
    assert exit_code_for(result, _config(min_responses=min_responses)) == expected


# --------------------------------------------------------------------------------------
# S0.1 — output budget scales with input and respects the model's own ceiling
# --------------------------------------------------------------------------------------

def test_output_budget_never_below_floor():
    assert compute_output_budget(10, 65536) == MIN_OUTPUT_TOKENS


def test_output_budget_scales_with_input():
    small = compute_output_budget(1_000, 65536)
    large = compute_output_budget(20_000, 65536)
    assert large > small
    assert large <= MAX_OUTPUT_TOKENS_CEILING


def test_output_budget_respects_model_ceiling():
    """A model that only allows 4096 output tokens must never be asked for more."""
    assert compute_output_budget(50_000, 4096) <= 4096


def test_output_budget_never_exceeds_global_ceiling():
    assert compute_output_budget(10_000_000, 460_800) == MAX_OUTPUT_TOKENS_CEILING


# --------------------------------------------------------------------------------------
# S0.3 — truncation is retried once, then reported honestly
# --------------------------------------------------------------------------------------

def _resp(payload, status=200):
    m = MagicMock()
    m.status_code = status
    m.json.return_value = payload
    m.raise_for_status = lambda: None
    m.headers = {}
    return m


@pytest.mark.asyncio
async def test_truncated_response_retries_with_a_larger_budget():
    client = AsyncMock()
    truncated = _resp({"choices": [{"message": {"content": "partial"}, "finish_reason": "length"}]})
    complete = _resp({"choices": [{"message": {"content": "full review"}, "finish_reason": "stop"}]})
    client.post.side_effect = [truncated, complete]

    r = await query_model(
        client, "http://d", "k", "m", "m", "code", max_tokens=2048, max_output_cap=32768
    )

    assert r.content == "full review"
    assert r.truncated is False
    assert client.post.call_count == 2
    first_budget = client.post.call_args_list[0].kwargs["json"]["max_tokens"]
    second_budget = client.post.call_args_list[1].kwargs["json"]["max_tokens"]
    assert second_budget == first_budget * 2


@pytest.mark.asyncio
async def test_truncation_is_reported_when_the_budget_cannot_grow():
    client = AsyncMock()
    client.post.return_value = _resp(
        {"choices": [{"message": {"content": "partial"}, "finish_reason": "length"}]}
    )

    r = await query_model(client, "http://d", "k", "m", "m", "code", max_tokens=4096, max_output_cap=4096)

    assert r.truncated is True
    assert r.finish_reason == "length"
    assert r.content == "partial"


@pytest.mark.asyncio
async def test_reasoning_budget_requested_only_when_supported():
    client = AsyncMock()
    client.post.return_value = _resp({"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]})

    await query_model(
        client, "http://d", "k", "m", "m", "code", max_tokens=10_000, supports_reasoning=True
    )
    reasoning = client.post.call_args.kwargs["json"]["reasoning"]
    # Bounded, not merely hidden: `exclude` alone still lets thinking consume the budget.
    assert reasoning["exclude"] is True
    assert reasoning["max_tokens"] == 4000
    assert reasoning["max_tokens"] < client.post.call_args.kwargs["json"]["max_tokens"]

    client.post.reset_mock()
    await query_model(client, "http://d", "k", "m", "m", "code", supports_reasoning=False)
    assert "reasoning" not in client.post.call_args.kwargs["json"]


# --------------------------------------------------------------------------------------
# S2.2 — malformed 200 bodies are transient, not raw Python errors
# --------------------------------------------------------------------------------------

@pytest.mark.parametrize(
    "body",
    [
        {"choices": []},
        {"choices": [None]},
        {"choices": [{"message": None}]},
        [1, 2, 3],
        {},
    ],
)
def test_parse_completion_rejects_malformed_shapes(body):
    from fourtake.client import MalformedCompletion

    with pytest.raises(MalformedCompletion):
        _parse_completion(body)


@pytest.mark.asyncio
async def test_malformed_body_is_retried_then_reported_readably():
    client = AsyncMock()
    client.post.return_value = _resp({"choices": []})

    with patch("fourtake.client.asyncio.sleep", new=AsyncMock()):
        r = await query_model(client, "http://d", "k", "m", "m", "code", max_retries=2)

    assert r.content is None
    assert "Malformed response from provider" in r.error
    assert "list index out of range" not in (r.error or "")
    assert client.post.call_count == 3  # initial + 2 retries


@pytest.mark.asyncio
async def test_non_json_body_is_retried_not_reported_as_a_parse_error():
    client = AsyncMock()
    bad = MagicMock()
    bad.status_code = 200
    bad.json.side_effect = ValueError("Expecting value: line 1 column 1 (char 0)")
    bad.raise_for_status = lambda: None
    bad.headers = {}
    client.post.return_value = bad

    with patch("fourtake.client.asyncio.sleep", new=AsyncMock()):
        r = await query_model(client, "http://d", "k", "m", "m", "code", max_retries=1)

    assert "body is not JSON" in r.error
    assert client.post.call_count == 2


# --------------------------------------------------------------------------------------
# S2.1 — Retry-After is honoured as given
# --------------------------------------------------------------------------------------

def test_retry_after_parses_delta_seconds():
    r = MagicMock()
    r.headers = {"Retry-After": "120"}
    assert _retry_after_seconds(r) == 120.0


def test_retry_after_parses_http_date():
    from datetime import datetime, timedelta, timezone
    from email.utils import format_datetime

    r = MagicMock()
    r.headers = {"Retry-After": format_datetime(datetime.now(timezone.utc) + timedelta(seconds=90))}
    value = _retry_after_seconds(r)
    assert value is not None and 60 <= value <= 120


def test_retry_after_absent_or_garbage_returns_none():
    r = MagicMock()
    r.headers = {}
    assert _retry_after_seconds(r) is None
    r.headers = {"Retry-After": "soon-ish"}
    assert _retry_after_seconds(r) is None


@pytest.mark.asyncio
async def test_large_retry_after_is_obeyed_not_shortened():
    """The client used to discard any Retry-After >= 30s and retry ~10x sooner."""
    client = AsyncMock()
    limited = MagicMock()
    limited.status_code = 429
    limited.headers = {"Retry-After": "120"}
    limited.json.return_value = {"error": {"message": "slow down"}}
    ok = _resp({"choices": [{"message": {"content": "done"}, "finish_reason": "stop"}]})
    client.post.side_effect = [limited, ok]

    slept = []

    async def spy(d):
        slept.append(d)

    with patch("fourtake.client.asyncio.sleep", new=spy):
        r = await query_model(client, "http://d", "k", "m", "m", "code", max_retries=2)

    assert r.content == "done"
    assert slept == [120.0]


@pytest.mark.asyncio
async def test_retry_is_skipped_when_it_would_pass_the_deadline():
    import time

    client = AsyncMock()
    limited = MagicMock()
    limited.status_code = 429
    limited.headers = {"Retry-After": "300"}
    limited.json.return_value = {"error": {"message": "slow down"}}
    client.post.return_value = limited

    r = await query_model(
        client, "http://d", "k", "m", "m", "code", max_retries=4, deadline=time.monotonic() + 5
    )

    assert "exceeds the remaining budget" in r.error
    assert client.post.call_count == 1


# --------------------------------------------------------------------------------------
# S3.4 — the model that actually served the request is recorded
# --------------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_served_model_and_provider_are_captured():
    """With a router such as openrouter/free the requested id is not the reviewing model."""
    client = AsyncMock()
    client.post.return_value = _resp(
        {
            "model": "nvidia/nemotron-3-ultra-550b-a55b",
            "provider": "NVIDIA",
            "choices": [{"message": {"content": "review"}, "finish_reason": "stop"}],
        }
    )

    r = await query_model(client, "http://d", "k", "openrouter/free", "openrouter/openrouter/free", "code")

    assert r.model == "openrouter/openrouter/free"
    assert r.served_model == "nvidia/nemotron-3-ultra-550b-a55b"
    assert r.served_provider == "NVIDIA"


# --------------------------------------------------------------------------------------
# S1.3 — MCP must not report failure as success
# --------------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_mcp_reports_error_when_every_model_fails():
    import fourtake.mcp_server as mcp_server

    failed = CouncilResult(
        responses=[],
        errors=[ModelResponse("m:free", None, "Provider error 429", 10)],
        warning=None,
        error="all models failed",
        total_latency_ms=1234,
    )

    async def fake_council(**kwargs):
        return failed

    async def fake_validate(models, timeout=10):
        return ["openrouter/m:free"]

    with patch("fourtake.mcp_server.load_config", return_value=_config(default_models=["openrouter/m:free"])), \
         patch("fourtake.mcp_server.run_council", new=fake_council), \
         patch("fourtake.discovery.validate_configured_models", new=fake_validate):
        data = json.loads(await mcp_server.get_4take_opinion("code", ctx=None))

    assert data["status"] == "error"
    assert data["error"] == "all models failed"
    assert data["responses_received"] == 0


@pytest.mark.asyncio
async def test_mcp_reports_degraded_on_partial_council():
    import fourtake.mcp_server as mcp_server

    partial = CouncilResult(
        responses=[ModelResponse("m:free", "finding", None, 10)],
        errors=[],
        warning="only 1 of 4 models responded",
        error=None,
        total_latency_ms=99,
    )

    async def fake_council(**kwargs):
        return partial

    async def fake_validate(models, timeout=10):
        return ["openrouter/m:free"]

    with patch("fourtake.mcp_server.load_config", return_value=_config(default_models=["openrouter/m:free"])), \
         patch("fourtake.mcp_server.run_council", new=fake_council), \
         patch("fourtake.discovery.validate_configured_models", new=fake_validate):
        data = json.loads(await mcp_server.get_4take_opinion("code", ctx=None))

    assert data["status"] == "degraded"
    assert data["warning"] == "only 1 of 4 models responded"


# --------------------------------------------------------------------------------------
# S5 — the accept-risk environment variable must be settable from a real shell
# --------------------------------------------------------------------------------------

def test_fourtake_accept_risk_env_var(monkeypatch):
    from fourtake.cli import accept_risk_enabled

    args = MagicMock()
    args.accept_risk = False

    monkeypatch.delenv("FOURTAKE_ACCEPT_RISK", raising=False)
    monkeypatch.delenv("4TAKE_ACCEPT_RISK", raising=False)
    assert accept_risk_enabled(args) is False

    monkeypatch.setenv("FOURTAKE_ACCEPT_RISK", "1")
    assert accept_risk_enabled(args) is True


def test_legacy_accept_risk_env_var_still_read(monkeypatch):
    from fourtake.cli import accept_risk_enabled

    args = MagicMock()
    args.accept_risk = False
    monkeypatch.delenv("FOURTAKE_ACCEPT_RISK", raising=False)
    monkeypatch.setenv("4TAKE_ACCEPT_RISK", "1")
    assert accept_risk_enabled(args) is True


# --------------------------------------------------------------------------------------
# Self-audit regressions: defects introduced by the fixes above, caught by auditing them
# --------------------------------------------------------------------------------------

def test_cache_preserves_truncation_and_provenance():
    """
    Adding fields to ModelResponse without teaching the cache about them meant a truncated
    review came back from cache looking complete, and seat de-duplication had nothing to
    key on — silently undoing both guarantees on the most common code path.
    """
    import fourtake.cache as cache  # conftest already isolates the cache directory

    original = ModelResponse(
        model="openrouter/openrouter/free",
        content="partial review",
        error=None,
        latency_ms=900,
        served_model="inclusionai/ling-3.0-flash-fin:free",
        served_provider="InclusionAI",
        finish_reason="length",
        truncated=True,
    )
    cache.save_cached_response("code", original.model, original)
    restored = cache.get_cached_response("code", original.model)

    assert restored.truncated is True
    assert restored.finish_reason == "length"
    assert restored.served_model == "inclusionai/ling-3.0-flash-fin:free"
    assert restored.served_provider == "InclusionAI"


def test_exit_code_counts_seats_not_responses():
    """Three responses from one router-backed model are one opinion, not three."""
    from fourtake.engine import distinct_seats

    responses = [
        ModelResponse("openrouter/free", "r", None, 1, served_model="same/model"),
        ModelResponse("openrouter/a:free", "r", None, 1, served_model="same/model", duplicate_of="same/model"),
        ModelResponse("openrouter/b:free", "r", None, 1, served_model="same/model", duplicate_of="same/model"),
    ]
    result = CouncilResult(responses=responses, errors=[], warning="dupes", error=None, total_latency_ms=1)

    assert distinct_seats(responses) == 1
    assert exit_code_for(result, _config(min_responses=3)) == 2  # degraded, not success
    assert exit_code_for(result, _config(min_responses=1)) == 0


def test_text_output_discloses_truncation_and_duplicates():
    """The default format hid both, so a CLI user could not tell a review was cut off."""
    from fourtake.output import format_markdown

    result = CouncilResult(
        responses=[
            ModelResponse("openrouter/m:free", "half a review", None, 10, truncated=True, finish_reason="length"),
            ModelResponse(
                "openrouter/openrouter/free", "same again", None, 10,
                served_model="other/model", duplicate_of="other/model",
            ),
        ],
        errors=[],
        warning=None,
        error=None,
        total_latency_ms=10,
    )
    text = format_markdown(result, _config(), "code")

    assert "Truncated" in text
    assert "Duplicate seat" in text
    assert "other/model" in text


@pytest.mark.asyncio
async def test_mcp_output_carries_provenance_fields():
    """The MCP tool's schema must match the CLI's; AGENTS.md documents served_model."""
    import fourtake.mcp_server as mcp_server

    result = CouncilResult(
        responses=[
            ModelResponse(
                "openrouter/openrouter/free", "review", None, 10,
                served_model="nvidia/nemotron", served_provider="NVIDIA",
                finish_reason="stop", truncated=False,
            )
        ],
        errors=[],
        warning=None,
        error=None,
        total_latency_ms=10,
    )

    async def fake_council(**kwargs):
        return result

    async def fake_validate(models, timeout=10):
        return ["openrouter/m:free"]

    with patch("fourtake.mcp_server.load_config", return_value=_config(default_models=["openrouter/m:free"])), \
         patch("fourtake.mcp_server.run_council", new=fake_council), \
         patch("fourtake.discovery.validate_configured_models", new=fake_validate):
        data = json.loads(await mcp_server.get_4take_opinion("code", ctx=None))

    r = data["responses"][0]
    assert r["served_model"] == "nvidia/nemotron"
    assert r["served_provider"] == "NVIDIA"
    assert r["truncated"] is False
    assert data["distinct_models"] == 1


# --------------------------------------------------------------------------------------
# S6 — the consensus block is a documented contract that agents read first
# --------------------------------------------------------------------------------------

def test_json_carries_consensus_ranked_by_agreement(capsys):
    """
    `consensus` is what AGENTS.md and CLAUDE.md tell an agent to read before `responses`, so
    its shape is a contract: agreement counted in distinct reviewers, most-agreed first.
    """
    sqli = ("* [CRITICAL] SQL injection via term (at search_users)\n"
            "  - Issue: term is concatenated into the query, so a quote ends the literal.\n")
    sqli_other_words = ("* [WARNING] Unparameterised query (at search_users)\n"
                        "  - Issue: user input is concatenated into the SQL query.\n")
    solo = ("* [WARNING] Unbounded limit (at search_users)\n"
            "  - Issue: limit is interpolated without validation.\n")

    result = CouncilResult(
        responses=[
            ModelResponse("openrouter/a:free", sqli, None, 10, served_model="a:free"),
            ModelResponse("openrouter/b:free", sqli_other_words + solo, None, 10, served_model="b:free"),
        ],
        errors=[], warning=None, error=None, total_latency_ms=10,
    )

    async def fake_council(*args, **kwargs):
        return result

    argv = ["4take", "--format", "json", "--accept-risk", "--quiet"]
    with patch("sys.argv", argv), \
         patch("fourtake.cli.read_stdin", return_value="code"), \
         patch("fourtake.cli.run_council", new=fake_council), \
         patch("fourtake.discovery.get_best_free_models", new=AsyncMock(return_value=["openrouter/a:free"])):
        with pytest.raises(SystemExit):
            from fourtake.cli import main

            main()

    consensus = json.loads(capsys.readouterr().out)["consensus"]
    assert [c["agreement"] for c in consensus] == sorted(
        [c["agreement"] for c in consensus], reverse=True
    ), "most-agreed first"

    top = consensus[0]
    assert set(top) == {"agreement", "severity", "category", "symbol", "title", "models", "severities",
                        "disputed"}
    assert top["agreement"] == 2
    assert top["models"] == ["a:free", "b:free"]
    assert top["severity"] == "CRITICAL", "the cluster takes the worst severity raised"
    assert top["disputed"] is True
    assert top["symbol"] == "search_users"
    # The solo finding is still reported; it is ranked below, not dropped.
    assert any(c["agreement"] == 1 for c in consensus)


def test_a_duplicate_seat_does_not_manufacture_agreement(capsys):
    """A router seating one model twice is one opinion, in the consensus block too."""
    body = ("* [CRITICAL] SQL injection (at f)\n"
            "  - Issue: input is concatenated into the query.\n")
    result = CouncilResult(
        responses=[
            ModelResponse("openrouter/free", body, None, 10, served_model="x:free"),
            ModelResponse("openrouter/x:free", body, None, 10, served_model="x:free",
                          duplicate_of="x:free"),
        ],
        errors=[], warning=None, error=None, total_latency_ms=10,
    )

    async def fake_council(*args, **kwargs):
        return result

    argv = ["4take", "--format", "json", "--accept-risk", "--quiet"]
    with patch("sys.argv", argv), \
         patch("fourtake.cli.read_stdin", return_value="code"), \
         patch("fourtake.cli.run_council", new=fake_council), \
         patch("fourtake.discovery.get_best_free_models", new=AsyncMock(return_value=["openrouter/free"])):
        with pytest.raises(SystemExit):
            from fourtake.cli import main

            main()

    payload = json.loads(capsys.readouterr().out)
    consensus = payload["consensus"]
    assert [c["agreement"] for c in consensus] == [1]
    assert consensus[0]["models"] == ["x:free"]
    # The field that says so reaches the JSON too, as it always did over MCP: without it a
    # consumer re-deriving agreement from `responses` counted the duplicate as a reviewer.
    assert [r["duplicate_of"] for r in payload["responses"]] == [None, "x:free"]


def test_strict_mode_suppresses_solitary_findings(capsys):
    """In --strict mode, a non-critical finding only one reviewer raised is suppressed from consensus."""
    body_a = "* [CRITICAL] Bug A (at f)\n  - Issue: race condition\n"
    body_b = "* [WARNING] Bug B (at g)\n  - Issue: only one model saw this\n"
    result = CouncilResult(
        responses=[
            ModelResponse("m1:free", body_a + body_b, None, 10),
            ModelResponse("m2:free", body_a, None, 10),
        ],
        errors=[],
        warning=None,
        error=None,
        total_latency_ms=10,
    )

    async def fake_council(*args, **kwargs):
        return result

    argv = ["4take", "--format", "json", "--accept-risk", "--quiet", "--strict"]
    with patch("sys.argv", argv), \
         patch("fourtake.cli.read_stdin", return_value="code"), \
         patch("fourtake.cli.run_council", new=fake_council), \
         patch("fourtake.discovery.get_best_free_models", new=AsyncMock(return_value=["m1:free", "m2:free"])):
        with pytest.raises(SystemExit):
            from fourtake.cli import main

            main()

    payload = json.loads(capsys.readouterr().out)
    consensus = payload["consensus"]
    assert len(consensus) == 1
    assert consensus[0]["agreement"] == 2
    assert consensus[0]["title"] == "Bug A"


def test_strict_mode_keeps_a_critical_finding_a_single_reviewer_raised(capsys):
    """
    Dropping every agreement-1 entry would let one model's SQL injection vanish and the output
    read as clean. Grouping is lexical: a critical finding stays however few raised it.
    """
    sqli = ("* [CRITICAL] SQL injection via term (at search_users)\n"
            "  - Issue: term is concatenated into the query\n")
    minor = ("* [SUGGESTION] Row formatting duplicated (at format_row)\n"
             "  - Issue: both handlers format rows by hand and will drift\n")
    result = CouncilResult(
        responses=[
            ModelResponse("m1:free", sqli + minor, None, 10),
            ModelResponse("m2:free", "NO_ISSUES_FOUND: Correct, safe.", None, 10),
        ],
        errors=[], warning=None, error=None, total_latency_ms=10,
    )

    async def fake_council(*args, **kwargs):
        return result

    argv = ["4take", "--format", "json", "--accept-risk", "--quiet", "--strict"]
    with patch("sys.argv", argv), \
         patch("fourtake.cli.read_stdin", return_value="code"), \
         patch("fourtake.cli.run_council", new=fake_council), \
         patch("fourtake.discovery.get_best_free_models", new=AsyncMock(return_value=["m1:free", "m2:free"])):
        with pytest.raises(SystemExit):
            from fourtake.cli import main

            main()

    consensus = json.loads(capsys.readouterr().out)["consensus"]
    assert [(c["severity"], c["agreement"], c["title"]) for c in consensus] == [
        ("CRITICAL", 1, "SQL injection via term")
    ]


def test_strict_text_output_keeps_errors_and_counts_what_it_hid():
    """
    A strict report that stopped before the errors section, or printed a bare "no consensus"
    line, would read as clean over a finding it had just hidden and over a run in which every
    model failed.
    """
    from fourtake.output import format_markdown

    solo = ("* [WARNING] Sleeps after the final attempt (at retry)\n"
            "  - Issue: the last failure still waits before raising\n")
    failed = ModelResponse("c:free", None, "HTTP 429 rate limited", 900)
    result = CouncilResult(
        responses=[
            ModelResponse("a:free", solo, None, 10),
            ModelResponse("b:free", "NO_ISSUES_FOUND: Correct, safe.", None, 10),
        ],
        errors=[failed], warning=None, error=None, total_latency_ms=10,
    )
    text = format_markdown(result, _config(), "code", strict=True)
    assert "1 single-reviewer finding hidden by --strict" in text
    assert "Sleeps after the final attempt" not in text, "the individual reviews are omitted"
    assert "## Errors" in text and "c:free" in text

    everything_failed = CouncilResult(
        responses=[], errors=[failed], warning=None, error="all models failed", total_latency_ms=900,
    )
    text = format_markdown(everything_failed, _config(), "code", strict=True)
    assert "## Consensus" not in text, "no consensus verdict over a run with no reviews"
    assert "c:free" in text

