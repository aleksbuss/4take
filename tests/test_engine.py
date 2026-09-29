from unittest.mock import patch

import pytest

from fourtake.config import FourtakeConfig
from fourtake.engine import run_council


@pytest.mark.asyncio
async def test_run_council_all_fail():
    from fourtake.config import ProviderConfig
    config = FourtakeConfig(
        providers={"test": ProviderConfig(url="y", api_key="x")},
        default_models=["test/A"], 
        rpm_limit=10, 
        min_responses=2, 
        timeout=25,
        provider_concurrency={"test": 1}
    )
    with patch("fourtake.engine.query_model") as mock_query:
        from fourtake.client import ModelResponse
        mock_query.return_value = ModelResponse(model="A", content=None, error="fail", latency_ms=10)
        
        result = await run_council(config, "test")
        assert len(result.responses) == 0
        assert len(result.errors) == 1
        assert result.error == "all models failed"

@pytest.mark.asyncio
async def test_run_council_verbose_and_success():
    from fourtake.config import ProviderConfig
    config = FourtakeConfig(
        providers={"openrouter": ProviderConfig(url="y", api_key="x")},
        default_models=["openrouter/A:free", "A:free"], # A will fallback to openrouter
        rpm_limit=10, 
        min_responses=1, 
        timeout=25,
        provider_concurrency={"openrouter": 1}
    )
    with patch("fourtake.engine.query_model") as mock_query:
        from fourtake.client import ModelResponse

        async def echo(**kwargs):
            # Distinct seats must report distinct models, or they are one opinion twice.
            name = kwargs["display_model_name"]
            return ModelResponse(model=name, content="NO_ISSUES_FOUND: good", error=None, latency_ms=10, served_model=name)

        mock_query.side_effect = echo

        result = await run_council(config, "test", verbose=True)
        assert len(result.responses) >= 1
        assert result.error is None
        assert result.warning is None

@pytest.mark.asyncio
async def test_run_council_unknown_provider_and_console():
    from fourtake.config import ProviderConfig
    config = FourtakeConfig(
        providers={"custom": ProviderConfig(url="y", api_key="x")},
        default_models=["unknown/A", "custom/A"],
        rpm_limit=10, 
        min_responses=2, 
        timeout=25,
        provider_concurrency={}
    )
    with patch("fourtake.engine.query_model") as mock_query:
        from fourtake.client import ModelResponse
        mock_query.return_value = ModelResponse(model="custom/A", content="NO_ISSUES_FOUND: good", error=None, latency_ms=10)
        
        from unittest.mock import MagicMock
        console = MagicMock()
        result = await run_council(config, "test", verbose=True, console=console, use_cache=False)
        assert len(result.responses) == 1
        assert len(result.errors) == 1
        assert result.errors[0].error == "Unknown provider"
        assert result.warning == "only 1 distinct of 2 models responded"
        console.print.assert_called()

@pytest.mark.asyncio
async def test_run_council_timeout_and_aclose_error():
    from fourtake.config import ProviderConfig
    config = FourtakeConfig(
        providers={"openrouter": ProviderConfig(url="y", api_key="x")},
        default_models=["openrouter/A"],
        rpm_limit=10, 
        min_responses=1, 
        timeout=0.01, # Super short timeout
        provider_concurrency={}
    )
    with patch("fourtake.engine.query_model") as mock_query:
        import asyncio
        async def slow_query(*args, **kwargs):
            await asyncio.sleep(0.5)
            return None
        mock_query.side_effect = slow_query
        
        with patch("httpx.AsyncClient.aclose", side_effect=asyncio.TimeoutError):
            result = await run_council(config, "test", verbose=True)
            assert len(result.responses) == 0
            assert result.error == "all models failed"

@pytest.mark.asyncio
async def test_run_council_task_exception():
    from fourtake.config import ProviderConfig
    config = FourtakeConfig(
        providers={"openrouter": ProviderConfig(url="y", api_key="x")},
        default_models=["openrouter/A:free"],
        rpm_limit=10, 
        min_responses=1, 
        timeout=25,
        provider_concurrency={}
    )
    with patch("fourtake.engine.query_model", side_effect=Exception("Task Error")):
        result = await run_council(config, "test")
        assert len(result.errors) == 1
        assert result.errors[0].error == "Task Error"

@pytest.mark.asyncio
async def test_run_council_rotation_on_failure():
    """When a model fails, the engine should rotate in a replacement from the pool."""
    from fourtake.client import ModelResponse
    from fourtake.config import ProviderConfig
    config = FourtakeConfig(
        providers={"openrouter": ProviderConfig(url="y", api_key="x")},
        default_models=["openrouter/A:free"],  # not used when model_pool is provided
        rpm_limit=10, 
        min_responses=2, 
        timeout=25,
        provider_concurrency={}
    )

    call_count = 0
    async def mock_query_side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        model_name = kwargs.get("display_model_name", f"model-{call_count}")
        # First call fails, second and third succeed
        if call_count == 1:
            return ModelResponse(model=model_name, content=None, error="Rate limited", latency_ms=10)
        return ModelResponse(model=model_name, content=f"NO_ISSUES_FOUND: review from {model_name}", error=None, latency_ms=10)

    with patch("fourtake.engine.query_model", side_effect=mock_query_side_effect):
        model_pool = ["openrouter/model-A:free", "openrouter/model-B:free", "openrouter/model-C:free"]
        result = await run_council(config, "test", model_pool=model_pool, active_count=2)
        
        # model-A fails -> model-C rotates in
        # model-B succeeds, model-C succeeds -> 2 responses = min_responses
        assert len(result.responses) == 2
        assert len(result.errors) == 1
        assert result.error is None
        assert result.warning is None

@pytest.mark.asyncio
async def test_run_council_rotation_pool_exhausted():
    """When all models in the pool fail, the engine should report all models failed."""
    from fourtake.client import ModelResponse
    from fourtake.config import ProviderConfig
    config = FourtakeConfig(
        providers={"openrouter": ProviderConfig(url="y", api_key="x")},
        default_models=["openrouter/A:free"],
        rpm_limit=10, 
        min_responses=2, 
        timeout=25,
        provider_concurrency={}
    )

    with patch("fourtake.engine.query_model") as mock_query:
        mock_query.return_value = ModelResponse(model="X", content=None, error="Provider down", latency_ms=10)
        
        model_pool = ["openrouter/A:free", "openrouter/B:free", "openrouter/C:free"]
        result = await run_council(config, "test", model_pool=model_pool, active_count=2)
        
        # All 3 models fail, pool exhausted
        assert len(result.responses) == 0
        assert len(result.errors) == 3  # A, B, C all failed
        assert result.error == "all models failed"


def test_resolve_model_provider_missing():
    from fourtake.engine import resolve_model
    config = FourtakeConfig(
        providers={},
        default_models=[],
        rpm_limit=10,
        min_responses=1,
        timeout=10,
        provider_concurrency={}
    )
    # openrouter prefix with missing provider
    assert resolve_model("openrouter/test:free", config) is None
    # plain model with missing provider
    assert resolve_model("plain-model", config) is None
    # unknown slash provider with missing openrouter fallback
    assert resolve_model("custom/model", config) is None

    # unknown slash provider WITH openrouter fallback
    from fourtake.config import ProviderConfig
    config_openrouter = FourtakeConfig(
        providers={"openrouter": ProviderConfig(url="https://openrouter.ai/api", api_key="k")},
        default_models=[],
        rpm_limit=10,
        min_responses=1,
        timeout=10,
        provider_concurrency={}
    )
    resolved = resolve_model("meta-llama/llama-3.3-70b:free", config_openrouter)
    assert resolved is not None
    assert resolved.provider_name == "openrouter"
    assert resolved.display_model_name == "openrouter/meta-llama/llama-3.3-70b:free"


@pytest.mark.asyncio
async def test_run_council_empty_model_pool():
    config = FourtakeConfig(
        providers={},
        default_models=[],
        rpm_limit=10,
        min_responses=1,
        timeout=10,
        provider_concurrency={}
    )
    result = await run_council(config, "code", model_pool=[])
    assert result.error == "model_pool is empty"
    assert result.responses == []


@pytest.mark.asyncio
async def test_run_council_cache_hit():
    from fourtake.cache import save_cached_response
    from fourtake.client import ModelResponse
    from fourtake.config import ProviderConfig
    from fourtake.prompts import REVIEWER_SYSTEM_PROMPT

    config = FourtakeConfig(
        providers={"openrouter": ProviderConfig(url="https://dummy", api_key="test-key")},
        default_models=["openrouter/cached-model:free"],
        rpm_limit=10,
        min_responses=1,
        timeout=10,
        provider_concurrency={}
    )

    task_input = "def cached_fn(): pass"
    model_name = "openrouter/cached-model:free"
    cached_resp = ModelResponse(model=model_name, content="NO_ISSUES_FOUND: cached audit", error=None, latency_ms=150)
    save_cached_response(task_input, model_name, cached_resp, REVIEWER_SYSTEM_PROMPT)

    result = await run_council(config, task_input, model_pool=[model_name], use_cache=True)
    assert len(result.responses) == 1
    assert result.responses[0].content == "NO_ISSUES_FOUND: cached audit"
    assert result.responses[0].latency_ms == 0


@pytest.mark.asyncio
async def test_run_council_progress_callback_and_verbose_rotation():
    from fourtake.client import ModelResponse
    from fourtake.config import ProviderConfig

    config = FourtakeConfig(
        providers={"openrouter": ProviderConfig(url="https://dummy", api_key="test-key")},
        default_models=["openrouter/model-1:free"],
        rpm_limit=10,
        min_responses=2,
        timeout=10,
        provider_concurrency={}
    )

    progress_events = []
    def on_progress(msg, current, total):
        progress_events.append((msg, current, total))

    call_count = 0
    async def side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        model = kwargs.get("display_model_name", f"m-{call_count}")
        if call_count == 1:
            raise RuntimeError("Task crashed")
        elif call_count == 2:
            return ModelResponse(model=model, content=None, error="Rate limited", latency_ms=10)
        else:
            return ModelResponse(model=model, content="NO_ISSUES_FOUND: approved", error=None, latency_ms=10)

    with patch("fourtake.engine.query_model", side_effect=side_effect):
        pool = ["openrouter/m1:free", "openrouter/m2:free", "openrouter/m3:free", "openrouter/m4:free"]
        result = await run_council(
            config,
            "test_code",
            model_pool=pool,
            active_count=2,
            verbose=True,
            progress_callback=on_progress,
            use_cache=False
        )
        assert len(progress_events) >= 1
        assert len(result.responses) >= 1


@pytest.mark.asyncio
async def test_run_council_progress_callback_error_resilience():
    from fourtake.client import ModelResponse
    from fourtake.config import ProviderConfig

    config = FourtakeConfig(
        providers={"openrouter": ProviderConfig(url="https://dummy", api_key="test-key")},
        default_models=["openrouter/model-1:free"],
        rpm_limit=10,
        min_responses=1,
        timeout=10,
        provider_concurrency={}
    )

    def faulty_callback(msg, cur, tot):
        raise ValueError("Callback crashed")

    with patch("fourtake.engine.query_model") as mock_q:
        mock_q.return_value = ModelResponse(model="openrouter/model-1:free", content="NO_ISSUES_FOUND: ok", error=None, latency_ms=10)
        result = await run_council(config, "test", model_pool=["openrouter/model-1:free"], progress_callback=faulty_callback)
        assert len(result.responses) == 1


@pytest.mark.asyncio
async def test_run_council_wait_timeout():
    import asyncio

    from fourtake.config import ProviderConfig

    config = FourtakeConfig(
        providers={"openrouter": ProviderConfig(url="https://dummy", api_key="test-key")},
        default_models=["openrouter/model-1:free"],
        rpm_limit=10,
        min_responses=2,
        timeout=10,
        provider_concurrency={}
    )

    real_task = asyncio.create_task(asyncio.sleep(5))
    try:
        with patch("asyncio.wait", return_value=(set(), {real_task})):
            result = await run_council(config, "code", model_pool=["openrouter/model-1:free"], verbose=True)
            assert len(result.responses) == 0
            assert result.error == "all models failed"
    finally:
        real_task.cancel()


@pytest.mark.asyncio
async def test_run_council_task_result_exception_triggers_rotation():
    import asyncio
    from unittest.mock import MagicMock

    from fourtake.config import ProviderConfig

    config = FourtakeConfig(
        providers={"openrouter": ProviderConfig(url="https://dummy", api_key="test-key")},
        default_models=["openrouter/m1:free", "openrouter/m2:free"],
        rpm_limit=10,
        min_responses=1,
        timeout=10,
        provider_concurrency={}
    )

    orig_create_task = asyncio.create_task

    call_count = 0
    def mock_create_task(coro, name=None):
        nonlocal call_count
        call_count += 1
        t = orig_create_task(coro, name=name)
        if call_count == 1:
            # First task's result raises
            t.result = MagicMock(side_effect=RuntimeError("Direct task failure"))
        return t

    with patch("asyncio.create_task", side_effect=mock_create_task):
        with patch("fourtake.engine.query_model") as mock_q:
            from fourtake.client import ModelResponse
            mock_q.return_value = ModelResponse(model="openrouter/m2:free", content="NO_ISSUES_FOUND: success", error=None, latency_ms=10)
            result = await run_council(config, "code", model_pool=["openrouter/m1:free", "openrouter/m2:free"], active_count=1, verbose=True)
            assert len(result.errors) >= 1
            assert "Direct task failure" in result.errors[0].error


@pytest.mark.asyncio
async def test_run_council_cumulative_deadline_exceeded():
    from fourtake.config import ProviderConfig

    config = FourtakeConfig(
        providers={"openrouter": ProviderConfig(url="https://dummy", api_key="test-key")},
        default_models=["openrouter/m1:free"],
        rpm_limit=10,
        min_responses=2,
        timeout=10,
        provider_concurrency={}
    )

    import time
    start = time.monotonic()
    calls = 0
    def fake_monotonic():
        nonlocal calls
        calls += 1
        # First call is start_time, second call in loop exceeds deadline
        if calls == 1:
            return start
        return start + 1000.0

    with patch("fourtake.engine.time.monotonic", side_effect=fake_monotonic):
        result = await run_council(config, "code", model_pool=["openrouter/m1:free"], verbose=True)
        assert result.error == "all models failed"







# --------------------------------------------------------------------------------------
# Reference context
# --------------------------------------------------------------------------------------


def _one_seat_config():
    from fourtake.config import ProviderConfig

    return FourtakeConfig(
        providers={"openrouter": ProviderConfig(url="https://dummy", api_key="k")},
        default_models=["openrouter/a:free"],
        rpm_limit=600,
        min_responses=1,
        timeout=10,
        provider_concurrency={},
    )


def _capturing(calls):
    from fourtake.client import ModelResponse

    async def capture(**kwargs):
        calls.append(kwargs)
        name = kwargs["display_model_name"]
        return ModelResponse(model=name, content="NO_ISSUES_FOUND: ok", error=None, latency_ms=1,
                             served_model=name)

    return capture


def _review_context():
    from fourtake.context import ContextPiece, fit_to_budget

    return fit_to_budget([ContextPiece("svc.py", "def helper(x):\n    return x * 2\n" * 200)], 100_000)


@pytest.mark.asyncio
async def test_context_is_sent_with_its_rules_and_does_not_grow_the_reply():
    calls = []
    review = _review_context()
    with patch("fourtake.engine.query_model", side_effect=_capturing(calls)):
        plain = await run_council(_one_seat_config(), "def f(): pass", use_cache=False)
        with_context = await run_council(_one_seat_config(), "def f(): pass", use_cache=False, context=review)

    assert calls[0]["context_block"] is None
    assert "reference_context" not in calls[0]["system_prompt"]
    assert "--- svc.py ---" in calls[1]["context_block"]
    assert "reference_context_" in calls[1]["system_prompt"]
    # The reply is about the code under review; the context is read, not answered.
    assert calls[0]["max_tokens"] == calls[1]["max_tokens"]
    assert plain.context is None
    assert with_context.context is review


@pytest.mark.asyncio
async def test_a_context_dropped_whole_by_the_budget_sends_nothing_extra():
    from fourtake.context import ContextPiece, fit_to_budget

    calls = []
    empty = fit_to_budget([ContextPiece("big.py", "x = 1\n" * 500)], budget_tokens=5)
    with patch("fourtake.engine.query_model", side_effect=_capturing(calls)):
        result = await run_council(_one_seat_config(), "def f(): pass", use_cache=False, context=empty)
    assert calls[0]["context_block"] is None
    assert "reference_context" not in calls[0]["system_prompt"]
    # ... but the reader is still told what was left out.
    assert result.context.dropped == ("big.py",)


@pytest.mark.asyncio
async def test_a_review_cached_without_context_is_not_served_for_one_with_it():
    from fourtake.cache import save_cached_response
    from fourtake.client import ModelResponse
    from fourtake.prompts import REVIEWER_SYSTEM_PROMPT

    model = "openrouter/a:free"
    save_cached_response("def f(): pass", model,
                         ModelResponse(model=model, content="NO_ISSUES_FOUND: stale review", error=None, latency_ms=5),
                         REVIEWER_SYSTEM_PROMPT)
    calls = []
    with patch("fourtake.engine.query_model", side_effect=_capturing(calls)):
        result = await run_council(_one_seat_config(), "def f(): pass", model_pool=[model],
                                   use_cache=True, context=_review_context())
    assert len(calls) == 1
    assert result.responses[0].content != "NO_ISSUES_FOUND: stale review"

    # ... and the review made with context is cached under a key of its own.
    calls.clear()
    with patch("fourtake.engine.query_model", side_effect=_capturing(calls)):
        again = await run_council(_one_seat_config(), "def f(): pass", model_pool=[model],
                                  use_cache=True, context=_review_context())
    assert calls == []
    assert again.responses[0].from_cache


def test_reference_context_takes_room_without_growing_the_reserved_reply():
    from fourtake.engine import model_input_capacity

    everything_reviewed = model_input_capacity("x/unknown:free", 20_000)
    mostly_context = model_input_capacity("x/unknown:free", 20_000, review_tokens=2_000)
    assert mostly_context > everything_reviewed
