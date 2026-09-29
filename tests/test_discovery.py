import pytest
import respx

from fourtake.discovery import evaluate_model_intelligence, get_best_free_models


def test_evaluate_model_intelligence():
    # Test parameter parsing
    assert evaluate_model_intelligence("some-model-70b") >= 50
    assert evaluate_model_intelligence("some-model-32b") >= 30
    assert evaluate_model_intelligence("some-model-14b") >= 20
    assert evaluate_model_intelligence("some-model-8b") >= 10

    # Test architecture boosts
    # llama-3 -> 25, 70b -> 50 = 75
    assert evaluate_model_intelligence("llama-3-70b") == 75
    # qwen-2 -> 25, 72b -> 50 = 75
    assert evaluate_model_intelligence("qwen-2-72b") == 75
    # gemma -> 15, 9b -> 10 = 25
    assert evaluate_model_intelligence("gemma-2-9b-it") == 25

@pytest.mark.asyncio
@respx.mock
async def test_get_best_free_models():
    # Mock OpenRouter API response
    respx.get("https://openrouter.ai/api/v1/models").respond(json={
        "data": [
            {
                "id": "bad/model-1",
                "pricing": {"prompt": "1", "completion": "1"}, # not free
                "context_length": 8192
            },
            {
                "id": "good/model-llama-3-8b:free",
                "pricing": {"prompt": "0", "completion": "0"},
                "context_length": 8192
            },
            {
                "id": "good/model-qwen-2-72b:free",
                "pricing": {"prompt": "0", "completion": "0"},
                "context_length": 32000
            },
            {
                "id": "good/model-tiny:free",
                "pricing": {"prompt": "0", "completion": "0"},
                "context_length": 2048 # Too small context
            }
        ]
    })
    
    # Require 4000 context
    models = await get_best_free_models(min_context=4000, count=2)
    assert len(models) == 2
    # qwen-2-72b has higher score than llama-3-8b, so it should be first
    assert models[0] == "openrouter/good/model-qwen-2-72b:free"
    assert models[1] == "openrouter/good/model-llama-3-8b:free"

@pytest.mark.asyncio
@respx.mock
async def test_get_best_free_models_error():
    # Mock error
    respx.get("https://openrouter.ai/api/v1/models").respond(500)
    models = await get_best_free_models(min_context=4000, count=2)
    assert models == [] # Should fail gracefully

def test_get_model_provider():
    from fourtake.discovery import get_model_provider
    assert get_model_provider("openrouter/nvidia/nemotron-70b-instruct:free") == "nvidia"
    assert get_model_provider("openrouter/google/gemma-4-31b-it:free") == "google"
    assert get_model_provider("qwen/qwen-2.5-coder-32b-instruct:free") == "qwen"
    assert get_model_provider("custom-model") == "custom-model"

@pytest.mark.asyncio
@respx.mock
async def test_get_best_free_models_provider_diversity():
    respx.get("https://openrouter.ai/api/v1/models").respond(json={
        "data": [
            {
                "id": "nvidia/model-120b:free",
                "pricing": {"prompt": "0", "completion": "0"},
                "context_length": 32000
            },
            {
                "id": "nvidia/model-70b:free",
                "pricing": {"prompt": "0", "completion": "0"},
                "context_length": 32000
            },
            {
                "id": "google/gemma-27b:free",
                "pricing": {"prompt": "0", "completion": "0"},
                "context_length": 32000
            },
            {
                "id": "qwen/qwen-32b:free",
                "pricing": {"prompt": "0", "completion": "0"},
                "context_length": 32000
            }
        ]
    })
    
    models = await get_best_free_models(min_context=4000, count=2, pool_size=2)
    assert len(models) == 2
    # First round should pick one from top 2 providers (nvidia, google) and break
    assert models[0] == "openrouter/nvidia/model-120b:free"
    assert models[1] == "openrouter/google/gemma-27b:free"

@pytest.mark.asyncio
@respx.mock
async def test_validate_configured_models_rejects_paid():
    from fourtake.discovery import validate_configured_models

    respx.get("https://openrouter.ai/api/v1/models").respond(json={
        "data": [
            {
                "id": "openai/gpt-4o",
                "pricing": {"prompt": "0.000005", "completion": "0.000015"},
                "context_length": 128000
            },
            {
                "id": "anthropic/claude-3.5-sonnet",
                "pricing": {"prompt": "0.000003", "completion": "0.000015"},
                "context_length": 200000
            },
            {
                "id": "meta-llama/llama-3.3-70b-instruct:free",
                "pricing": {"prompt": "0", "completion": "0"},
                "context_length": 65536
            }
        ]
    })

    configured = [
        "openrouter/openai/gpt-4o",
        "openrouter/anthropic/claude-3.5-sonnet",
        "openrouter/meta-llama/llama-3.3-70b-instruct:free"
    ]

    validated = await validate_configured_models(configured)
    # Paid models must be completely filtered out
    assert "openrouter/openai/gpt-4o" not in validated
    assert "openrouter/anthropic/claude-3.5-sonnet" not in validated
    assert validated == ["openrouter/meta-llama/llama-3.3-70b-instruct:free"]


def test_is_model_free_non_numeric_pricing():
    from fourtake.discovery import is_model_free
    # Pricing fields that raise ValueError on float() but match "0" as str
    model_data = {
        "id": "custom/model",
        "pricing": {"prompt": "0", "completion": "0"}
    }
    assert is_model_free(model_data) is True

    # Pricing with non-numeric, non-zero string
    model_data2 = {
        "id": "custom/model-paid",
        "pricing": {"prompt": "custom_rate", "completion": "custom_rate"}
    }
    assert is_model_free(model_data2) is False


@pytest.mark.asyncio
@respx.mock
async def test_validate_configured_models_network_error():
    from fourtake.discovery import validate_configured_models
    respx.get("https://openrouter.ai/api/v1/models").respond(500)

    models = [
        "openrouter/meta-llama/llama-3.3-70b:free",
        "openrouter/openai/gpt-4o"
    ]
    validated = await validate_configured_models(models)
    assert validated == ["openrouter/meta-llama/llama-3.3-70b:free"]


@pytest.mark.asyncio
@respx.mock
async def test_validate_configured_models_unlisted_catalog():
    from fourtake.discovery import validate_configured_models
    # OpenRouter returns empty catalog
    respx.get("https://openrouter.ai/api/v1/models").respond(json={"data": []})

    models = [
        "openrouter/unlisted-model:free",
        "openrouter/unlisted-paid-model"
    ]
    validated = await validate_configured_models(models)
    assert validated == ["openrouter/unlisted-model:free"]


def test_is_model_compatible_residual_denylist():
    """Only what capabilities cannot express stays on the denylist."""
    from fourtake.discovery import is_model_compatible
    assert is_model_compatible("thinkingmachines/inkling-small:free") is False  # 403 harness-gated
    assert is_model_compatible("nvidia/nemotron-3.5-content-safety:free") is False  # classifier
    assert is_model_compatible("openrouter/free") is True
    assert is_model_compatible("poolside/laguna-s-2.1:free") is True
    assert is_model_compatible("cohere/north-mini-code:free") is True


def test_is_model_compatible_uses_catalog_capabilities():
    from fourtake.discovery import is_model_compatible

    text_model = {
        "id": "cohere/north-mini-code:free",
        "architecture": {"output_modalities": ["text"]},
        "supported_parameters": ["max_tokens", "temperature"],
    }
    assert is_model_compatible(text_model["id"], text_model) is True

    # Live catalog: the audio models advertise ["text", "audio"]. Equality is required —
    # a membership test ('"text" in output_modalities') would admit them.
    audio_model = {
        "id": "google/lyria-3-pro-preview",
        "architecture": {"output_modalities": ["text", "audio"]},
        "supported_parameters": ["max_tokens"],
    }
    assert is_model_compatible(audio_model["id"], audio_model) is False
    assert "text" in audio_model["architecture"]["output_modalities"]  # the trap, documented

    no_max_tokens = {
        "id": "some/model:free",
        "architecture": {"output_modalities": ["text"]},
        "supported_parameters": ["temperature"],
    }
    assert is_model_compatible(no_max_tokens["id"], no_max_tokens) is False

    # A catalog entry missing the fields must not crash or be rejected outright.
    sparse = {"id": "some/other:free"}
    assert is_model_compatible(sparse["id"], sparse) is True


def test_evaluate_model_intelligence_openrouter_free_and_code():
    assert evaluate_model_intelligence("openrouter/free") == 55
    assert evaluate_model_intelligence("some-code-model") >= 30




@pytest.mark.asyncio
@respx.mock
async def test_one_catalog_entry_with_null_fields_does_not_stop_discovery():
    """
    The catalog is someone else's JSON. One entry with `"pricing": null` or
    `"context_length": null` raised out of discovery, and the run ended with "an unexpected
    error" instead of reviewing with the other models.
    """
    respx.get("https://openrouter.ai/api/v1/models").respond(json={
        "data": [
            {"id": "odd/priceless", "pricing": None, "context_length": 8192},
            {"id": "odd/windowless:free", "pricing": {"prompt": "0", "completion": "0"},
             "context_length": None},
            {"id": None, "pricing": {"prompt": "0", "completion": "0"}, "context_length": 8192},
            {"id": "good/model-qwen-2-72b:free", "pricing": {"prompt": "0", "completion": "0"},
             "context_length": 32000},
        ]
    })
    models = await get_best_free_models(min_context=4000, count=1, stats={})
    assert models == ["openrouter/good/model-qwen-2-72b:free"]


def test_null_pricing_is_not_free_unless_the_id_says_so():
    from fourtake.discovery import is_model_free

    assert not is_model_free({"id": "vendor/paid-model", "pricing": None})
    assert is_model_free({"id": "vendor/model:free", "pricing": None})
