from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from fourtake.client import query_model


@pytest.mark.asyncio
async def test_query_model_success():
    client = AsyncMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"choices": [{"message": {"content": "good code"}}]}
    mock_response.raise_for_status = lambda: None
    client.post.return_value = mock_response

    response = await query_model(client, "http://dummy", "key", "modelA", "modelA", "code")
    assert response.error is None
    assert response.content == "good code"


@pytest.mark.asyncio
async def test_query_model_reasoning_without_content_is_not_a_review():
    """
    A reasoning model that returns only chain-of-thought was cut off before answering.
    Substituting the reasoning as the review disguises a truncation as a valid critique.
    """
    client = AsyncMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [
            {
                "message": {"content": "", "reasoning_content": "Deep reasoning analysis"},
                "finish_reason": "length",
            }
        ]
    }
    mock_response.raise_for_status = lambda: None
    client.post.return_value = mock_response

    response = await query_model(
        client, "http://dummy", "key", "modelA", "modelA", "code", max_output_cap=4096
    )
    assert response.content is None
    assert "Truncated while reasoning" in response.error
    assert response.truncated is True
    assert response.finish_reason == "length"
    # Kept for diagnostics, but never presented as the review.
    assert response.reasoning == "Deep reasoning analysis"


@pytest.mark.asyncio
async def test_query_model_reasoning_field_without_content_is_not_a_review():
    client = AsyncMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [{"message": {"content": None, "reasoning": "Alternate reasoning field"}}]
    }
    mock_response.raise_for_status = lambda: None
    client.post.return_value = mock_response

    response = await query_model(client, "http://dummy", "key", "modelA", "modelA", "code")
    assert response.content is None
    assert response.error == "Truncated while reasoning; raise max output tokens"
    assert response.reasoning == "Alternate reasoning field"


@pytest.mark.asyncio
async def test_query_model_empty_response():
    client = AsyncMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"choices": [{"message": {"content": "   "}}]}
    mock_response.raise_for_status = lambda: None
    client.post.return_value = mock_response

    response = await query_model(client, "http://dummy", "key", "modelA", "modelA", "code")
    assert response.error == "Empty response from model"
    assert response.content is None


@pytest.mark.asyncio
async def test_query_model_client_error():
    client = AsyncMock()
    mock_response = MagicMock()
    mock_response.status_code = 404
    mock_response.json.side_effect = Exception("No JSON")
    client.post.return_value = mock_response

    response = await query_model(client, "http://dummy", "key", "modelA", "modelA", "code")
    assert response.error == "Client error: 404"
    assert response.content is None


@pytest.mark.asyncio
async def test_query_model_client_error_with_details():
    client = AsyncMock()
    # 1. error is a dict with message
    mock_resp1 = MagicMock()
    mock_resp1.status_code = 400
    mock_resp1.json.return_value = {"error": {"message": "Invalid prompt format"}}
    client.post.return_value = mock_resp1
    res1 = await query_model(client, "http://dummy", "key", "modelA", "modelA", "code")
    assert res1.error == "Client error (400): Invalid prompt format"

    # 2. error is a string
    mock_resp2 = MagicMock()
    mock_resp2.status_code = 400
    mock_resp2.json.return_value = {"error": "Direct string error"}
    client.post.return_value = mock_resp2
    res2 = await query_model(client, "http://dummy", "key", "modelA", "modelA", "code")
    assert res2.error == "Client error (400): Direct string error"

    # 3. top level message
    mock_resp3 = MagicMock()
    mock_resp3.status_code = 400
    mock_resp3.json.return_value = {"message": "Top-level failure message"}
    client.post.return_value = mock_resp3
    res3 = await query_model(client, "http://dummy", "key", "modelA", "modelA", "code")
    assert res3.error == "Client error (400): Top-level failure message"


@pytest.mark.asyncio
async def test_query_model_insufficient_credits_402():
    client = AsyncMock()
    mock_response = MagicMock()
    mock_response.status_code = 402
    client.post.return_value = mock_response

    response = await query_model(client, "http://dummy", "key", "modelA", "modelA", "code")
    assert response.error == "Insufficient credits / balance on OpenRouter"
    assert response.content is None


@pytest.mark.asyncio
async def test_query_model_retry_after():
    client = AsyncMock()

    fail_response = MagicMock()
    fail_response.status_code = 429
    fail_response.headers = {"Retry-After": "1"}

    success_response = MagicMock()
    success_response.status_code = 200
    success_response.json.return_value = {"choices": [{"message": {"content": "good code"}}]}
    success_response.raise_for_status = lambda: None

    # Fail first time, succeed second time
    client.post.side_effect = [fail_response, success_response]

    response = await query_model(client, "http://dummy", "key", "modelA", "modelA", "code")
    assert response.error is None
    assert response.content == "good code"


@pytest.mark.asyncio
async def test_query_model_timeout_exception():
    client = AsyncMock()
    client.timeout.read = 10
    client.post.side_effect = httpx.TimeoutException("Timeout")

    response = await query_model(client, "http://dummy", "key", "modelA", "modelA", "code")
    assert response.error == "Timeout after 10s"
    assert response.content is None


@pytest.mark.asyncio
async def test_query_model_connect_error():
    client = AsyncMock()
    client.post.side_effect = httpx.ConnectError("Failed to connect")

    response = await query_model(client, "http://dummy", "key", "modelA", "modelA", "code")
    assert response.error == "Connection failed"
    assert response.content is None


@pytest.mark.asyncio
async def test_query_model_generic_exception():
    client = AsyncMock()
    client.post.side_effect = Exception("Some weird error")

    response = await query_model(client, "http://dummy", "key", "modelA", "modelA", "code")
    assert response.error == "Some weird error"
    assert response.content is None


@pytest.mark.asyncio
async def test_query_model_auth_error():
    client = AsyncMock()
    mock_response = MagicMock()
    mock_response.status_code = 401
    client.post.return_value = mock_response

    response = await query_model(client, "http://dummy", "key", "modelA", "modelA", "code")
    assert response.error == "Invalid API key"
    assert response.content is None


@pytest.mark.asyncio
async def test_query_model_max_retries():
    with patch("asyncio.sleep", new_callable=AsyncMock):
        client = AsyncMock()
        fail_response = MagicMock()
        fail_response.status_code = 500
        client.post.return_value = fail_response

        response = await query_model(client, "http://dummy", "key", "modelA", "modelA", "code")
        assert response.error == "Provider error 500 after 4 retries"
        assert response.content is None


@pytest.mark.asyncio
async def test_query_model_forbidden_403():
    client = AsyncMock()
    mock_response = MagicMock()
    mock_response.status_code = 403
    mock_response.json.return_value = {"error": {"message": "only available on agentic harnesses"}}
    client.post.return_value = mock_response

    response = await query_model(client, "http://dummy", "key", "modelA", "modelA", "code")
    assert "Forbidden (403)" in response.error
    assert "only available on agentic harnesses" in response.error
    assert response.content is None


@pytest.mark.asyncio
async def test_query_model_200_with_error():
    with patch("asyncio.sleep", new_callable=AsyncMock):
        client = AsyncMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = lambda: None
        mock_response.json.return_value = {
            "error": {
                "code": 502,
                "message": "Upstream error from Nvidia: Service temporarily overloaded"
            }
        }
        client.post.return_value = mock_response

        response = await query_model(client, "http://dummy", "key", "modelA", "modelA", "code")
        assert "Upstream provider error" in response.error
        assert "Service temporarily overloaded" in response.error
        assert response.content is None



def _ok_client():
    client = AsyncMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"choices": [{"message": {"content": "NO_ISSUES_FOUND: ok"}}]}
    mock_response.raise_for_status = lambda: None
    client.post.return_value = mock_response
    return client


@pytest.mark.asyncio
async def test_reference_context_is_sent_before_the_code_under_review():
    client = _ok_client()
    await query_model(client, "http://dummy", "key", "m", "m", "x = 1", nonce="n1",
                      context_block="--- svc.py ---\ny = 2\n")
    user = client.post.call_args.kwargs["json"]["messages"][1]["content"]
    assert user.index("<reference_context_n1>") < user.index("<code_to_review_n1>")
    assert "--- svc.py ---" in user


@pytest.mark.asyncio
async def test_without_context_the_request_body_is_unchanged():
    from fourtake.prompts import wrap_code

    client = _ok_client()
    await query_model(client, "http://dummy", "key", "m", "m", "x = 1", nonce="n1")
    user = client.post.call_args.kwargs["json"]["messages"][1]["content"]
    assert user == wrap_code("x = 1", "n1")


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [httpx.ReadError(""), httpx.RemoteProtocolError(""), httpx.WriteError("")],
                         ids=["read", "remote-protocol", "write"])
async def test_a_dropped_connection_is_retried(error):
    """
    A connection dropped mid-exchange is the network, not the model. It was reported at once, and
    with httpx's empty message: 12 blank errors in one recording, each costing a seat.
    """
    ok = MagicMock()
    ok.status_code = 200
    ok.raise_for_status = lambda: None
    ok.json.return_value = {"choices": [{"message": {"content": "NO_ISSUES_FOUND: ok"}, "finish_reason": "stop"}]}
    client = AsyncMock()
    client.post.side_effect = [error, ok]
    with patch("asyncio.sleep", new_callable=AsyncMock):
        response = await query_model(client, "http://dummy", "key", "modelA", "modelA", "code")
    assert response.error is None
    assert response.content == "NO_ISSUES_FOUND: ok"
    assert client.post.call_count == 2


@pytest.mark.asyncio
async def test_a_connection_that_keeps_dropping_says_what_happened():
    client = AsyncMock()
    client.post.side_effect = httpx.RemoteProtocolError("")
    with patch("asyncio.sleep", new_callable=AsyncMock):
        response = await query_model(client, "http://dummy", "key", "modelA", "modelA", "code")
    assert response.error == "Connection dropped after 4 retries: RemoteProtocolError"
    assert client.post.call_count == 5


@pytest.mark.asyncio
async def test_an_error_with_no_message_is_named_by_its_type():
    client = AsyncMock()
    client.post.side_effect = RuntimeError()
    response = await query_model(client, "http://dummy", "key", "modelA", "modelA", "code")
    assert response.error == "RuntimeError"
