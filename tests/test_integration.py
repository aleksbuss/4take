import asyncio
import json
import os
from tempfile import NamedTemporaryFile
from unittest.mock import MagicMock, patch

import httpx
import pytest
import respx

from fourtake.cli import main, read_stdin
from fourtake.config import FourtakeConfig
from fourtake.engine import run_council
from fourtake.output import output_result


@pytest.fixture
def mock_config():
    from fourtake.config import ProviderConfig
    providers = {
        "openrouter": ProviderConfig(url="https://test.openrouter.ai/api/v1/chat/completions", api_key="sk-test")
    }
    return FourtakeConfig(
        providers=providers,
        default_models=["openrouter/m1:free", "openrouter/m2:free", "openrouter/m3:free", "openrouter/m4:free"],
        rpm_limit=60,
        min_responses=2,
        timeout=1,
        provider_concurrency={"openrouter": 4}
    )

@pytest.fixture
def clean_env():
    with patch.dict(os.environ, {"FOURTAKE_API_KEY": "sk-test"}, clear=True):
        yield

@pytest.mark.asyncio
@respx.mock
async def test_council_all_models_success(mock_config):
    # All models succeed
    respx.post("https://test.openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "NO_ISSUES_FOUND: ok"}}]})
    )
    
    result = await run_council(mock_config, "code", verbose=False)
    
    assert len(result.responses) >= mock_config.min_responses
    assert len(result.errors) == 0
    assert result.error is None
    assert result.warning is None
    assert all(r.content == "NO_ISSUES_FOUND: ok" for r in result.responses)

def test_cli_empty_stdin_exits_with_error(clean_env):
    with patch("sys.stdin.isatty", return_value=False), patch("sys.stdin.read", return_value="   \n  "):
        with patch("sys.argv", ["4take"]):
            with pytest.raises(SystemExit) as exc:
                main()
            assert exc.value.code == 1

def test_cli_large_stdin_truncated_to_limit():
    mock_stdin = MagicMock()
    mock_stdin.isatty.return_value = False
    mock_stdin.buffer.read.return_value = b"A" * (512 * 1024 + 100)
    
    with patch("sys.stdin", mock_stdin):
        data = read_stdin()
        assert len(data) == 512 * 1024

@pytest.mark.asyncio
@respx.mock
async def test_council_handles_invalid_api_key_401(mock_config):
    respx.post("https://test.openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(401, json={"error": "invalid"})
    )
    
    result = await run_council(mock_config, "code", verbose=False)
    
    assert len(result.responses) == 0
    assert len(result.errors) == 4
    assert result.error == "all models failed"
    assert all(e.error == "Invalid API key" for e in result.errors)

@pytest.mark.asyncio
@respx.mock
async def test_council_handles_all_models_rate_limited_429(mock_config):
    # Models return 429, fallbacks also return 429
    respx.post("https://test.openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(429, json={"error": "rate limit"})
    )
    
    # Needs a smaller sleep for tests
    with patch("asyncio.sleep", return_value=None):
        result = await run_council(mock_config, "code", verbose=False)

    assert len(result.responses) == 0
    assert len(result.errors) == 4
    assert result.error == "all models failed"
    # mock_config.timeout is 1s, so the first backoff already exceeds the council deadline.
    # Failing fast frees the seat for rotation instead of sleeping past the budget.
    assert all(
        "exceeds the remaining budget" in e.error
        or "after 5 retries" in e.error
        or "Provider error 429" in e.error
        for e in result.errors
    )


@pytest.mark.asyncio
@respx.mock
async def test_council_429_exhausts_retries_when_budget_allows(mock_config):
    """With a generous deadline the client must actually spend its retries."""
    mock_config.timeout = 600
    route = respx.post("https://test.openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(429, json={"error": "rate limit"})
    )

    with patch("asyncio.sleep", return_value=None):
        result = await run_council(mock_config, "code", verbose=False)

    assert result.error == "all models failed"
    assert all("after 4 retries" in e.error or "Provider error 429" in e.error for e in result.errors)
    # 4 seats, 1 initial attempt + 4 retries each.
    assert route.call_count >= 20

@pytest.mark.asyncio
@respx.mock
async def test_council_partial_failure_with_warning(mock_config):
    # 1 succeeds, 3 fail
    def side_effect(request):
        req_json = json.loads(request.content)
        if req_json["model"] == "m1:free":
            return httpx.Response(200, json={"choices": [{"message": {"content": "NO_ISSUES_FOUND: ok"}}]})
        return httpx.Response(500)

    respx.post("https://test.openrouter.ai/api/v1/chat/completions").mock(side_effect=side_effect)
    
    result = await run_council(mock_config, "code", verbose=False)
    
    assert len(result.responses) == 1
    assert len(result.errors) == 3
    assert result.warning == "only 1 distinct of 4 models responded"
    assert result.error is None

@pytest.mark.asyncio
@respx.mock
async def test_council_handles_timeout(mock_config):
    mock_config.timeout = 1
    
    async def slow_response(request):
        await asyncio.sleep(2.0)
        return httpx.Response(200, json={"choices": [{"message": {"content": "NO_ISSUES_FOUND: ok"}}]})
        
    respx.post("https://test.openrouter.ai/api/v1/chat/completions").mock(side_effect=slow_response)
    
    result = await run_council(mock_config, "code", verbose=False)
    
    assert len(result.responses) == 0
    assert len(result.errors) == 4
    assert result.error == "all models failed"
    # httpx.TimeoutException gets mapped to a timeout error in client.py

def test_output_result_writes_json_to_file(mock_config):
    from fourtake.client import ModelResponse
    from fourtake.engine import CouncilResult
    
    res = CouncilResult(
        responses=[ModelResponse("m1", "ok", None, 10)],
        errors=[],
        warning=None,
        error=None,
        total_latency_ms=100
    )
    
    with NamedTemporaryFile(delete=False) as f:
        tmp = f.name
        
    try:
        output_result(res, tmp, mock_config, "test code input", output_format="json")
        
        with open(tmp) as f:
            data = json.load(f)
            
        assert data["summary"]["responses_received"] == 1
        assert len(data["summary"]["task_hash"]) == 12
    finally:
        os.unlink(tmp)

@pytest.mark.asyncio
@respx.mock
async def test_council_provider_diversity_with_fallback(mock_config):
    # Set up a pool of 6 models: 4 diverse first, then 2 backups from the same providers
    mock_config.default_models = [
        "openrouter/nvidia/model-1:free",
        "openrouter/google/model-1:free",
        "openrouter/qwen/model-1:free",
        "openrouter/meta/model-1:free",
        "openrouter/nvidia/model-2:free",
        "openrouter/google/model-2:free",
    ]
    mock_config.min_responses = 4
    
    def side_effect(request):
        req_json = json.loads(request.content)
        model = req_json["model"]
        # qwen and meta models fail
        if "qwen" in model or "meta" in model:
            return httpx.Response(500, json={"error": "provider down"})
        # nvidia and google models succeed
        return httpx.Response(200, json={"choices": [{"message": {"content": f"NO_ISSUES_FOUND: ok from {model}"}}]})

    respx.post("https://test.openrouter.ai/api/v1/chat/completions").mock(side_effect=side_effect)
    
    result = await run_council(mock_config, "code", verbose=False)
    
    # We should receive 4 successful responses: nvidia/model-1, google/model-1, nvidia/model-2, google/model-2
    assert len(result.responses) == 4
    assert result.error is None
    responded_models = {r.model for r in result.responses}
    assert "openrouter/nvidia/model-1:free" in responded_models
    assert "openrouter/google/model-1:free" in responded_models
    assert "openrouter/nvidia/model-2:free" in responded_models
    assert "openrouter/google/model-2:free" in responded_models
