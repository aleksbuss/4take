import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from fourtake.client import ModelResponse
from fourtake.engine import CouncilResult
from fourtake.mcp_server import get_4take_opinion, main, mcp


@pytest.mark.asyncio
async def test_get_4take_opinion_basic():
    # Mock context
    ctx = MagicMock()
    ctx.request_context.meta.progressToken = "test-token"
    ctx.request_context.session.send_progress_notification = AsyncMock()

    # One seat, and one is what was asked for: the status must be `ok`. Pinning
    # min_responses here also removes this test's dependency on the developer's own config,
    # which decided its outcome before.
    mock_result = CouncilResult(
        responses=[ModelResponse(model="test-model", content="test-content", error=None, latency_ms=100)],
        errors=[],
        warning=None,
        error=None,
        total_latency_ms=100,
    )

    with (
        patch("fourtake.mcp_server.run_council", new_callable=AsyncMock) as mock_run_council,
        patch("fourtake.mcp_server.load_config", new=lambda *a, **k: _config(min_responses=1)),
    ):
        mock_run_council.return_value = mock_result

        result_str = await get_4take_opinion("test code", ctx=ctx)

        # Verify run_council was called
        mock_run_council.assert_called_once()
        kwargs = mock_run_council.call_args.kwargs
        assert kwargs["task_input"] == "test code"

        # Verify progress callback
        cb = kwargs["progress_callback"]
        cb("Test progress", 1, 4)

        # Verify the progress callback actually called the mock context info
        ctx.info.assert_called_with("Test progress")

        # Verify output json
        data = json.loads(result_str)
        assert data["status"] == "ok"
        assert len(data["responses"]) == 1
        assert data["responses"][0]["model"] == "test-model"


@pytest.mark.asyncio
async def test_get_4take_opinion_redacts_secrets():
    """Verify that secrets in code sent to MCP tool are stripped before running council."""
    ctx = MagicMock()
    ctx.request_context = None

    mock_result = CouncilResult(
        responses=[ModelResponse(model="model1", content="Audited", error=None, latency_ms=50)],
        errors=[],
        warning=None,
        error=None,
        total_latency_ms=50,
    )

    raw_code = 'OPENAI_KEY = "sk-12345678901234567890123456789012"\nAWS = "AKIAIOSFODNN7EXAMPLE"'

    with patch("fourtake.mcp_server.run_council", new_callable=AsyncMock) as mock_run_council:
        mock_run_council.return_value = mock_result

        await get_4take_opinion(raw_code, ctx=ctx)

        sanitized_input = mock_run_council.call_args.kwargs["task_input"]
        assert "sk-123456" not in sanitized_input
        assert "AKIAIOSFODNN7EXAMPLE" not in sanitized_input
        assert "***REDACTED***" in sanitized_input


@pytest.mark.asyncio
async def test_mcp_tool_schema_and_listing():
    """Verify that FastMCP registers the get_4take_opinion tool with valid schema."""
    tools = await mcp.list_tools()
    tool_names = [t.name for t in tools]
    assert "get_4take_opinion" in tool_names

    tool = next(t for t in tools if t.name == "get_4take_opinion")
    assert "code" in tool.inputSchema["properties"]
    assert "code" in tool.inputSchema["required"]
    assert "4 independent AI opinions" in tool.description


@pytest.mark.asyncio
async def test_mcp_call_tool_interface():
    """Test calling the tool via FastMCP's internal tool caller."""
    mock_result = CouncilResult(
        responses=[ModelResponse(model="openrouter/m1", content="Clean code", error=None, latency_ms=20)],
        errors=[],
        warning=None,
        error=None,
        total_latency_ms=20,
    )

    with (
        patch("fourtake.mcp_server.run_council", new_callable=AsyncMock) as mock_run_council,
        patch("fourtake.mcp_server.load_config", new=lambda *a, **k: _config(min_responses=1)),
    ):
        mock_run_council.return_value = mock_result

        result = await mcp.call_tool("get_4take_opinion", {"code": "def hello(): pass"})
        text_content = result[0][0]
        data = json.loads(text_content.text)

        assert data["status"] == "ok"
        assert data["responses"][0]["content"] == "Clean code"


@pytest.mark.asyncio
async def test_full_stdio_mcp_client_session():
    """End-to-end test spawning 4take-mcp over stdio and running JSON-RPC handshake."""
    import sys

    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    # The interpreter running the tests, not `uv run`: that re-syncs the project first, which
    # needs a lockfile and the network, and from an unpacked sdist (no uv.lock) it dropped the
    # mcp extra, so the server died on import and the handshake failed.
    server_params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "fourtake.mcp_entry"],
        env=None,
    )

    async with stdio_client(server_params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            init_result = await session.initialize()
            assert init_result.serverInfo.name == "4take"

            tools_list = await session.list_tools()
            tool_names = [t.name for t in tools_list.tools]
            assert "get_4take_opinion" in tool_names


def test_main():
    with patch("fourtake.mcp_server.mcp.run") as mock_run:
        main()
        mock_run.assert_called_once()


def test_mcp_dunder_main():
    import runpy

    with patch("mcp.server.fastmcp.FastMCP.run") as mock_run:
        runpy.run_module("fourtake.mcp_server", run_name="__main__")
        mock_run.assert_called_once()


@pytest.mark.asyncio
async def test_get_4take_opinion_configured_models_and_token_limits():
    """Test MCP tool with custom configured models and token limit filtering."""
    from fourtake.config import FourtakeConfig, ProviderConfig

    custom_config = FourtakeConfig(
        providers={"openrouter": ProviderConfig(url="https://dummy", api_key="k")},
        default_models=["openrouter/m1:free", "openrouter/m2:free"],
        rpm_limit=10,
        min_responses=1,
        timeout=10,
        provider_concurrency={},
        # Generous overrides: both models can hold the snippet, so the council runs.
        model_token_limits={"openrouter/m1:free": 200_000, "openrouter/m2:free": 200_000},
    )

    ctx = MagicMock()
    ctx.info = AsyncMock()
    ctx.request_context.meta.progressToken = "tok-123"
    ctx.request_context.session.send_progress_notification = AsyncMock()

    mock_result = CouncilResult(
        responses=[ModelResponse(model="openrouter/m1:free", content="OK", error=None, latency_ms=10)],
        errors=[],
        warning=None,
        error=None,
        total_latency_ms=10,
    )

    with patch("fourtake.mcp_server.load_config", return_value=custom_config):
        with patch("fourtake.discovery.validate_configured_models", new_callable=AsyncMock) as mock_val:
            mock_val.return_value = ["openrouter/m1:free", "openrouter/m2:free"]
            with patch("fourtake.mcp_server.run_council", new_callable=AsyncMock) as mock_run:
                mock_run.return_value = mock_result
                res_str = await get_4take_opinion("def long_code_token_exceeded(): pass", ctx=ctx)
                data = json.loads(res_str)
                assert data["status"] == "ok"
                mock_run.assert_called_once()
                # Callback should trigger progress notification
                cb = mock_run.call_args.kwargs["progress_callback"]
                cb("Progress update", 1, 2)
                ctx.request_context.session.send_progress_notification.assert_called_with(
                    progress_token="tok-123", progress=1, total=2
                )


@pytest.mark.asyncio
async def test_mcp_refuses_input_that_exceeds_every_model():
    """
    The MCP tool used to ignore the context limit and dispatch anyway when no model fit,
    while the CLI exited with an error. Both entrypoints must now agree.
    """
    from fourtake.config import FourtakeConfig, ProviderConfig

    tiny = FourtakeConfig(
        providers={"openrouter": ProviderConfig(url="https://dummy", api_key="k")},
        default_models=["openrouter/m1:free"],
        rpm_limit=10,
        min_responses=1,
        timeout=10,
        provider_concurrency={},
        model_token_limits={"openrouter/m1:free": 1},
    )

    with patch("fourtake.mcp_server.load_config", return_value=tiny):
        with patch("fourtake.discovery.validate_configured_models", new_callable=AsyncMock) as mock_val:
            mock_val.return_value = ["openrouter/m1:free"]
            with patch("fourtake.mcp_server.run_council", new_callable=AsyncMock) as mock_run:
                data = json.loads(await get_4take_opinion("def f(): pass", ctx=None))

    assert data["status"] == "error"
    assert "exceeds the usable context" in data["error"]
    mock_run.assert_not_called()


# --------------------------------------------------------------------------------------
# S5 — `mcp` is an optional extra; the entry point must say so, not raise ImportError
# --------------------------------------------------------------------------------------


def test_mcp_entry_point_gives_install_instructions_when_the_extra_is_missing(capsys):
    """
    Importing fourtake.mcp_server at module level would turn a missing extra into a
    traceback. The shim must fail with something a user can act on.
    """
    import builtins

    from fourtake import mcp_entry

    real_import = builtins.__import__

    def blocked(name, *args, **kwargs):
        if name.startswith("mcp") or name == "fourtake.mcp_server" or name.endswith("mcp_server"):
            raise ImportError("No module named 'mcp'", name="mcp")
        return real_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=blocked):
        with pytest.raises(SystemExit) as exc:
            mcp_entry.main()

    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "4take[mcp]" in err
    assert "Traceback" not in err


def test_mcp_entry_point_delegates_when_the_extra_is_present():
    from fourtake import mcp_entry

    with patch("fourtake.mcp_server.main") as run_server:
        mcp_entry.main()
    run_server.assert_called_once()


# --------------------------------------------------------------------------------------
# The MCP verdict must mean the same thing as the CLI exit code
# --------------------------------------------------------------------------------------

from types import SimpleNamespace

from fourtake.config import FourtakeConfig, ProviderConfig
from fourtake.mcp_server import serialise_result


def _config(**overrides) -> FourtakeConfig:
    base = dict(
        providers={"openrouter": ProviderConfig(url="https://x/api", api_key="k")},
        default_models=["auto"],
        rpm_limit=600,
        min_responses=1,
        timeout=30,
        provider_concurrency={"openrouter": 4},
    )
    base.update(overrides)
    return FourtakeConfig(**base)


@pytest.mark.parametrize(
    "seats,min_responses,warning,expected",
    [
        (4, 4, "1 response(s) came from an already-seated model", "ok"),
        (4, 4, None, "ok"),
        (2, 4, "only 2 distinct of 6 models responded", "degraded"),
        (1, 2, None, "degraded"),
    ],
)
def test_status_tracks_the_threshold_not_the_presence_of_a_warning(seats, min_responses, warning, expected):
    """
    Keying `degraded` on `result.warning` marked a healthy council degraded whenever a
    duplicate seat was rotated out — the machinery working — and disagreed with the exit
    code the CLI returns for the identical result.
    """
    from fourtake.client import ModelResponse
    from fourtake.engine import CouncilResult
    from fourtake.output import exit_code_for

    responses = [
        ModelResponse(
            model=f"m{i}:free", content="* [WARNING] x\n", error=None, latency_ms=1, served_model=f"m{i}:free"
        )
        for i in range(seats)
    ]
    result = CouncilResult(responses=responses, errors=[], warning=warning, error=None, total_latency_ms=1)
    config = SimpleNamespace(min_responses=min_responses)

    payload = json.loads(serialise_result(result, config))
    assert payload["status"] == expected
    assert (payload["status"] == "ok") == (exit_code_for(result, config) == 0)


def test_the_mcp_tool_carries_the_consensus_block():
    """AGENTS.md tells an agent to read `consensus` first; over MCP it was never sent."""
    from fourtake.client import ModelResponse
    from fourtake.engine import CouncilResult

    body = (
        "* [CRITICAL] [INJECTION] SQL injection (at search_users)\n"
        "  - Issue: term is concatenated into the query.\n"
    )
    undeclared = "* [WARNING] Unbounded limit (at page_size)\n  - Issue: limit is never capped.\n"
    result = CouncilResult(
        responses=[
            ModelResponse(
                model="a:free", content=body + undeclared, error=None, latency_ms=1, served_model="a:free"
            ),
            ModelResponse(model="b:free", content=body, error=None, latency_ms=1, served_model="b:free"),
        ],
        errors=[],
        warning=None,
        error=None,
        total_latency_ms=1,
    )
    payload = json.loads(serialise_result(result, SimpleNamespace(min_responses=2)))

    assert payload["consensus"][0]["agreement"] == 2
    assert payload["consensus"][0]["symbol"] == "search_users"
    # The same `category` the CLI emits: declared by the reviewers, UNKNOWN when none did.
    assert [c["category"] for c in payload["consensus"]] == ["INJECTION", "UNKNOWN"]


# --------------------------------------------------------------------------------------
# A non-code input is refused before any request goes out
# --------------------------------------------------------------------------------------

_PROSE = (
    "We want to move notification delivery out of the request path. Today the handler\n"
    "blocks on the provider call, so a slow provider becomes a slow API for everyone.\n"
    "Should the worker be a separate deployment or a thread in the API process?\n"
)


@pytest.mark.asyncio
async def test_prose_is_refused_without_dispatching():
    ctx = MagicMock()
    with patch("fourtake.mcp_server.run_council", new_callable=AsyncMock) as mock_run_council:
        payload = json.loads(await get_4take_opinion(_PROSE, ctx=ctx))

    mock_run_council.assert_not_called()
    assert payload["status"] == "error"
    assert "does not look like source code" in payload["error"]
    assert payload["responses"] == []


@pytest.mark.asyncio
async def test_force_sends_prose_anyway():
    ctx = MagicMock()
    ctx.request_context.meta.progressToken = None
    mock_result = CouncilResult(
        responses=[ModelResponse(model="test-model", content="test-content", error=None, latency_ms=100)],
        errors=[],
        warning=None,
        error=None,
        total_latency_ms=100,
    )
    # Discovery is imported inside the tool, so it is patched at its source module or the
    # test reaches OpenRouter for a model list it never uses.
    with (
        patch("fourtake.mcp_server.run_council", new_callable=AsyncMock) as mock_run_council,
        patch("fourtake.mcp_server.load_config", new=lambda *a, **k: _config(min_responses=1)),
        patch("fourtake.discovery.get_best_free_models", new=AsyncMock(return_value=["a:free", "b:free"])),
    ):
        mock_run_council.return_value = mock_result
        payload = json.loads(await get_4take_opinion(_PROSE, ctx=ctx, force=True))

    mock_run_council.assert_called_once()
    assert payload["status"] != "error"


# --------------------------------------------------------------------------------------
# Reference context through the tool
# --------------------------------------------------------------------------------------

_CHANGED = "def rename(uid):\n    user = find_user(uid)\n    return user.email\n"


async def _call_with(context_files=None, context=None, code=_CHANGED, auto_context=False):
    ctx = MagicMock()
    ctx.request_context = None
    ok = CouncilResult(
        responses=[ModelResponse(model="m", content="NO_ISSUES_FOUND: ok", error=None, latency_ms=1)],
        errors=[],
        warning=None,
        error=None,
        total_latency_ms=1,
    )
    with (
        patch("fourtake.mcp_server.run_council", new_callable=AsyncMock) as mock_run,
        patch("fourtake.mcp_server.load_config", new=lambda *a, **k: _config(min_responses=1)),
        patch("fourtake.discovery.get_best_free_models", new=AsyncMock(return_value=["a:free"])),
    ):
        mock_run.return_value = ok
        payload = json.loads(
            await get_4take_opinion(
                code, ctx=ctx, context_files=context_files, context=context, auto_context=auto_context
            )
        )
    return payload, mock_run


@pytest.mark.asyncio
async def test_a_context_file_the_reviewed_diff_changes_is_marked(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "users.py").write_text("def find_user(uid):\n    return None\n")
    (tmp_path / "api.py").write_text("from users import find_user\n")
    diff = (
        "--- a/users.py\n+++ b/users.py\n@@ -1,2 +1,2 @@\n def find_user(uid):\n"
        "-    raise LookupError(uid)\n+    return None\n"
    )
    _, mock_run = await _call_with(context_files=["users.py", "api.py"], code=diff)
    assert [p.changed for p in mock_run.call_args.kwargs["context"].pieces] == [True, False]


@pytest.mark.asyncio
async def test_context_files_are_read_by_the_server_and_sent(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "users.py").write_text("def find_user(uid):\n    return None\n")
    payload, mock_run = await _call_with(context_files=["users.py"])
    context = mock_run.call_args.kwargs["context"]
    assert [p.label for p in context.pieces] == ["users.py"]
    assert payload["status"] == "ok"


@pytest.mark.asyncio
async def test_inline_context_is_sent_without_touching_the_disk(monkeypatch):
    """A server started from `/` cannot read paths, and must still take pasted context."""
    from pathlib import Path

    monkeypatch.chdir(Path("/"))
    _, mock_run = await _call_with(context="def find_user(uid):\n    return None\n")
    context = mock_run.call_args.kwargs["context"]
    assert "find_user" in context.block()


@pytest.mark.asyncio
async def test_a_refused_context_file_refuses_the_review(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("OPENROUTER_API_KEY=sk-or-v1-secret\n")
    payload, mock_run = await _call_with(context_files=[".env"])
    mock_run.assert_not_called()
    assert payload["status"] == "error"
    assert ".env" in payload["error"]


@pytest.mark.asyncio
async def test_auto_context_is_chosen_by_the_server(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "users.py").write_text("def find_user(uid):\n    return None\n")
    (tmp_path / "api.py").write_text("from users import find_user\n\nx = find_user(1).email\n")
    diff = (
        "--- a/users.py\n+++ b/users.py\n@@ -1,2 +1,2 @@\n def find_user(uid):\n"
        "-    raise LookupError(uid)\n+    return None\n"
    )
    _, mock_run = await _call_with(code=diff, auto_context=True)
    context = mock_run.call_args.kwargs["context"]
    assert [p.label for p in context.pieces] == ["api.py"]
    assert context.summary()["auto"] == {"api.py": "uses find_user"}


@pytest.mark.asyncio
async def test_the_tool_schema_describes_the_context_parameters():
    tools = await mcp.list_tools()
    schema = next(t for t in tools if t.name == "get_4take_opinion").inputSchema
    files = schema["properties"]["context_files"]
    assert "callers" in files["description"]
    assert "context" in schema["properties"]
    assert "context_files" not in schema.get("required", [])


def test_the_payload_reports_the_context_and_what_was_left_out():
    from fourtake.context import ContextPiece, fit_to_budget
    from fourtake.mcp_server import serialise_result

    fitted = fit_to_budget(
        [ContextPiece("users.py", "def f():\n    pass\n"), ContextPiece("big.py", "x = 1\n" * 3000)],
        budget_tokens=200,
    )
    result = CouncilResult(
        responses=[], errors=[], warning=None, error="all models failed", total_latency_ms=1, context=fitted
    )
    payload = json.loads(serialise_result(result, _config()))
    assert payload["context"] == {"files": ["users.py"], "tokens": fitted.tokens, "dropped": ["big.py"]}
