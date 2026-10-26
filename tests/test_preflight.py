"""
The pre-flight step both front ends share: whether a council can run, and on which models.

The CLI and the MCP server used to carry separate copies of it, and the copies disagreed in
three places. These tests pin the shared behaviour, and the MCP cases pin that the server
really goes through it.
"""

import json
from unittest.mock import AsyncMock, patch

import pytest

from fourtake.config import FourtakeConfig, ProviderConfig
from fourtake.engine import MAX_OUTPUT_TOKENS_CEILING, PROMPT_OVERHEAD_TOKENS
from fourtake.preflight import NoUsableModels, choose_models, missing_api_key


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


@pytest.mark.asyncio
async def test_discovery_leaves_room_for_the_reply():
    """The MCP server asked discovery for the bare input size, so a model that fit exactly was chosen and then failed."""
    discover = AsyncMock(return_value=["openrouter/a:free"])
    with patch("fourtake.discovery.get_best_free_models", new=discover):
        await choose_models(_config(), 1000)
    assert (
        discover.call_args.kwargs["min_context"] == 1000 + MAX_OUTPUT_TOKENS_CEILING + PROMPT_OVERHEAD_TOKENS
    )


@pytest.mark.asyncio
async def test_rejected_configured_models_are_an_error_not_a_silent_swap():
    """The MCP server used to review with the hard-coded fallback pool the user never picked."""
    with patch("fourtake.discovery.validate_configured_models", new=AsyncMock(return_value=[])):
        with pytest.raises(NoUsableModels, match="configured"):
            await choose_models(_config(default_models=["openrouter/paid-model"]), 10)


@pytest.mark.asyncio
async def test_an_empty_discovery_falls_back_and_says_so():
    with patch("fourtake.discovery.get_best_free_models", new=AsyncMock(return_value=[])):
        choice = await choose_models(_config(), 10)
    assert choice.used_fallback
    assert choice.models


@pytest.mark.asyncio
async def test_an_input_no_model_can_hold_is_refused_with_the_numbers():
    config = _config(default_models=["openrouter/m1:free"], model_token_limits={"openrouter/m1:free": 1})
    with patch(
        "fourtake.discovery.validate_configured_models", new=AsyncMock(return_value=["openrouter/m1:free"])
    ):
        with pytest.raises(NoUsableModels, match="exceeds the usable context"):
            await choose_models(config, 500)


def test_a_real_key_is_not_reported_missing():
    assert missing_api_key(_config()) is None
    assert "not set" in missing_api_key(
        _config(providers={"openrouter": ProviderConfig(url="u", api_key="")})
    )


@pytest.mark.asyncio
async def test_the_mcp_tool_refuses_without_a_key_before_dispatching():
    """The CLI stopped before dispatch without a key; the MCP server spent a council to learn it."""
    from fourtake.mcp_server import get_4take_opinion

    no_key = _config(providers={"openrouter": ProviderConfig(url="u", api_key=None)})
    with (
        patch("fourtake.mcp_server.load_config", return_value=no_key),
        patch("fourtake.mcp_server.run_council", new_callable=AsyncMock) as run,
    ):
        payload = json.loads(await get_4take_opinion("def f(): pass", ctx=None))
    run.assert_not_called()
    assert payload["status"] == "error"
    assert "the OpenRouter API key is not set" in payload["error"]


@pytest.mark.asyncio
async def test_the_mcp_tool_refuses_when_every_configured_model_is_rejected():
    from fourtake.mcp_server import get_4take_opinion

    with (
        patch(
            "fourtake.mcp_server.load_config", return_value=_config(default_models=["openrouter/paid-model"])
        ),
        patch("fourtake.discovery.validate_configured_models", new=AsyncMock(return_value=[])),
        patch("fourtake.mcp_server.run_council", new_callable=AsyncMock) as run,
    ):
        payload = json.loads(await get_4take_opinion("def f(): pass", ctx=None))
    run.assert_not_called()
    assert payload["status"] == "error"
    assert "configured" in payload["error"]
