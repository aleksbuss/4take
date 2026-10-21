from unittest.mock import patch

import pytest
import respx

from fourtake.config import FourtakeConfig, ProviderConfig
from fourtake.discovery import (
    FALLBACK_MODELS,
    is_free_model_id,
    is_model_free,
    validate_configured_models,
)
from fourtake.engine import run_council


def test_is_free_model_id():
    # Guaranteed free models
    assert is_free_model_id("meta-llama/llama-3.3-70b-instruct:free") is True
    assert is_free_model_id("openrouter/meta-llama/llama-3.3-70b-instruct:free") is True
    assert is_free_model_id("google/gemma-4-26b-a4b-it:free") is True
    assert is_free_model_id("openrouter/free") is True

    # Check fallback models are recognized as free
    for fb in FALLBACK_MODELS:
        assert is_free_model_id(fb) is True

    # Paid models MUST be False
    assert is_free_model_id("openai/gpt-4o") is False
    assert is_free_model_id("openrouter/openai/gpt-4o") is False
    assert is_free_model_id("anthropic/claude-3.5-sonnet") is False
    assert is_free_model_id("openrouter/anthropic/claude-3.5-sonnet") is False
    assert is_free_model_id("meta-llama/llama-3.3-70b-instruct") is False


def test_is_model_free_pricing():
    # Prompt and completion are 0
    assert is_model_free({"id": "custom/model", "pricing": {"prompt": "0", "completion": "0"}}) is True
    assert is_model_free({"id": "custom/model", "pricing": {"prompt": 0.0, "completion": 0.0}}) is True

    # Model ID ends in :free
    assert is_model_free({"id": "custom/model:free", "pricing": {"prompt": "1", "completion": "1"}}) is True

    # Paid model
    assert (
        is_model_free({"id": "openai/gpt-4o", "pricing": {"prompt": "0.000005", "completion": "0.000015"}})
        is False
    )


@pytest.mark.asyncio
@respx.mock
async def test_validate_configured_models_blocks_paid_models():
    respx.get("https://openrouter.ai/api/v1/models").respond(
        json={
            "data": [
                {
                    "id": "openai/gpt-4o",
                    "pricing": {"prompt": "0.000005", "completion": "0.000015"},
                    "context_length": 128000,
                },
                {
                    "id": "meta-llama/llama-3.3-70b-instruct:free",
                    "pricing": {"prompt": "0", "completion": "0"},
                    "context_length": 65536,
                },
            ]
        }
    )

    # Only paid models configured
    paid_only = ["openrouter/openai/gpt-4o"]
    res = await validate_configured_models(paid_only)
    assert res == []

    # Mixed configuration
    mixed = ["openrouter/openai/gpt-4o", "openrouter/meta-llama/llama-3.3-70b-instruct:free"]
    res = await validate_configured_models(mixed)
    assert res == ["openrouter/meta-llama/llama-3.3-70b-instruct:free"]


@pytest.mark.asyncio
async def test_run_council_pre_flight_blocks_paid_network_calls():
    """Ensure that if a paid model slips through to run_council, query_model is NEVER called."""
    config = FourtakeConfig(
        providers={
            "openrouter": ProviderConfig(
                url="https://openrouter.ai/api/v1/chat/completions", api_key="test-key"
            )
        },
        default_models=["openrouter/openai/gpt-4o"],
        rpm_limit=10,
        min_responses=1,
        timeout=25,
        provider_concurrency={"openrouter": 1},
    )

    with patch("fourtake.engine.query_model") as mock_query:
        result = await run_council(config, "print('hello')", model_pool=["openrouter/openai/gpt-4o"])
        # query_model must NOT have been called over the network!
        mock_query.assert_not_called()
        assert len(result.responses) == 0
        assert len(result.errors) == 1
        assert "ZeroCostViolation" in result.errors[0].error
