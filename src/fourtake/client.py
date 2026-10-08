import asyncio
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

from .prompts import REVIEWER_SYSTEM_PROMPT, wrap_input


@dataclass
class ModelResponse:
    model: str
    content: str | None
    error: str | None
    latency_ms: int
    # Which model/provider actually served the request. Differs from `model` whenever a
    # router such as "openrouter/free" is used, and is what consensus must be keyed on.
    served_model: str | None = None
    served_provider: str | None = None
    finish_reason: str | None = None
    truncated: bool = False
    # Chain-of-thought, when a provider returns it. Diagnostics only — never a review.
    reasoning: str | None = None
    # Set when another seat was already served by this same underlying model.
    duplicate_of: str | None = None
    # True when this came from the local review cache rather than from the provider. No
    # request was made, so it is not an observation of how the model behaves.
    from_cache: bool = False


DEFAULT_MAX_OUTPUT_TOKENS = 8192

# Share of the completion budget a reasoning model may spend on thinking, and the floor of
# answer space that share may never eat into. A bare `max(1024, share)` inverted the intent at
# small budgets: at the 2048 floor it handed reasoning 50%, not 40%.
REASONING_BUDGET_SHARE = 0.4
MIN_ANSWER_TOKENS = 1024
MIN_REASONING_TOKENS = 256

# Room a reasoning model thinks in on its first attempt. Most free providers ignore the
# reasoning cap (Nvidia honours it), so the budget sized for the answer left nothing: measured
# on 2026-10-07, ling and apodex thought for 2-7k tokens on a short snippet and 15-26k on a
# 5-30 KB diff before answering. Free models are metered per request, not per token, so the
# ceiling costs only a model that spends it.
REASONING_OUTPUT_TOKENS = 32768


def reasoning_budget(total_tokens: int) -> int:
    """Thinking allowance that can never crowd out the findings."""
    share = int(total_tokens * REASONING_BUDGET_SHARE)
    return max(MIN_REASONING_TOKENS, min(share, total_tokens - MIN_ANSWER_TOKENS))


class MalformedCompletion(Exception):
    """A 200 response whose body cannot be read as a completion. Treated as transient."""


def _retry_after_seconds(response: httpx.Response) -> float | None:
    """
    Parse a Retry-After header: delta-seconds or an HTTP-date. Returns None when absent
    or unparseable. The value is honoured as given — previously anything >= 30s was
    silently discarded in favour of a shorter exponential delay, so the client retried
    far sooner than the provider asked and deepened the throttling it was backing off from.
    """
    raw = response.headers.get("Retry-After")
    if not raw:
        return None
    raw = raw.strip()
    try:
        return max(0.0, float(int(raw)))
    except ValueError:
        pass
    try:
        from email.utils import parsedate_to_datetime

        target = parsedate_to_datetime(raw)
        if target is None:
            return None
        if target.tzinfo is None:
            target = target.replace(tzinfo=timezone.utc)
        return max(0.0, (target - datetime.now(timezone.utc)).total_seconds())
    except Exception:
        return None


def _parse_completion(data: object) -> tuple[str | None, str | None, str | None]:
    """
    Extract (content, reasoning, finish_reason) from a chat-completion body.

    Raises MalformedCompletion for shapes that indicate a provider hiccup rather than a
    real answer, so the caller can retry instead of reporting a raw Python exception
    ("list index out of range") as a permanent failure.
    """
    if not isinstance(data, dict):
        raise MalformedCompletion(f"expected a JSON object, got {type(data).__name__}")

    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        raise MalformedCompletion("provider returned no choices")

    choice = choices[0]
    if not isinstance(choice, dict):
        raise MalformedCompletion("malformed choice entry")

    # A null message is a provider hiccup (retryable); a present-but-empty one is a real
    # empty answer and is classified further up.
    message = choice.get("message")
    if not isinstance(message, dict):
        raise MalformedCompletion("malformed message entry")

    finish_reason = choice.get("finish_reason")
    raw_content = message.get("content")
    content = str(raw_content) if raw_content and str(raw_content).strip() else None

    raw_reasoning = message.get("reasoning_content") or message.get("reasoning")
    reasoning = str(raw_reasoning) if raw_reasoning and str(raw_reasoning).strip() else None

    return content, reasoning, (str(finish_reason) if finish_reason else None)


def _extract_error_message(response: httpx.Response) -> str | None:
    try:
        err_json = response.json()
        if isinstance(err_json, dict):
            err_val = err_json.get("error")
            if isinstance(err_val, dict):
                msg = err_val.get("message")
                return str(msg) if msg else None
            elif isinstance(err_val, str):
                return err_val
            top_msg = err_json.get("message")
            return str(top_msg) if top_msg else None
    except Exception:
        pass
    return None


async def query_model(
    client: httpx.AsyncClient,
    provider_url: str,
    api_key: str | None,
    api_model_name: str,
    display_model_name: str,
    prompt: str,
    system_prompt: str = REVIEWER_SYSTEM_PROMPT,
    max_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
    semaphore: asyncio.Semaphore | None = None,
    max_retries: int = 4,
    max_output_cap: int | None = None,
    supports_reasoning: bool = False,
    deadline: float | None = None,
    acquire_token: Callable[[], Awaitable[None]] | None = None,
    nonce: str | None = None,
    context_block: str | None = None,
) -> ModelResponse:
    start_time = time.monotonic()

    # On reasoning models max_tokens budgets thinking AND the answer together, so an
    # under-sized budget yields a response truncated mid-thought with no findings at all.
    # `max_tokens` is sized for the answer; a reasoning model gets room to think on top.
    answer_tokens = max_tokens
    current_max_tokens = (
        max(max_tokens, min(REASONING_OUTPUT_TOKENS, max_output_cap or REASONING_OUTPUT_TOKENS))
        if supports_reasoning
        else max_tokens
    )

    def build_payload() -> dict:
        body = {
            "model": api_model_name,
            "messages": [
                {
                    "role": "system",
                    "content": system_prompt,
                },
                {
                    "role": "user",
                    # Nonce-suffixed delimiters: a fixed tag could be closed from inside the
                    # input, turning the rest of the snippet into instructions.
                    "content": wrap_input(prompt, nonce, context_block),
                },
            ],
            "max_tokens": current_max_tokens,
            "temperature": 0.3,
        }
        if supports_reasoning:
            # `exclude` only hides the chain of thought — those tokens are still generated
            # and still spend the budget. The cap is sized on the answer: a provider that
            # honours it thinks briefly, as it did when the evals were recorded, and the room
            # above it is for the providers that do not.
            body["reasoning"] = {
                "max_tokens": reasoning_budget(answer_tokens),
                "exclude": True,
            }
        return body

    payload = build_payload()
    grew_budget = False

    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    attempt = 0
    while True:
        try:
            # Every attempt is a real request upstream, so every attempt must pay the
            # rate limiter. Charging only the first one let a retry storm bypass the
            # governor entirely: 12 rotated models x 5 attempts = 60 unmetered requests.
            if acquire_token is not None:
                await acquire_token()

            if semaphore:
                async with semaphore:
                    response = await client.post(provider_url, json=payload, headers=headers)
            else:
                response = await client.post(provider_url, json=payload, headers=headers)

            latency = int((time.monotonic() - start_time) * 1000)

            # 1. Fatal / Non-retryable client errors (0 retries, immediate fail)
            if 400 <= response.status_code < 500 and response.status_code != 429:
                err_msg = _extract_error_message(response)
                if response.status_code == 401:
                    return ModelResponse(
                        model=display_model_name,
                        content=None,
                        error="Invalid API key",
                        latency_ms=latency,
                    )
                if response.status_code == 403:
                    msg = err_msg or "Access forbidden"
                    return ModelResponse(
                        model=display_model_name,
                        content=None,
                        error=f"Forbidden (403): {msg}",
                        latency_ms=latency,
                    )
                if response.status_code == 402:
                    return ModelResponse(
                        model=display_model_name,
                        content=None,
                        error="Insufficient credits / balance on OpenRouter",
                        latency_ms=latency,
                    )
                if err_msg:
                    return ModelResponse(
                        model=display_model_name,
                        content=None,
                        error=f"Client error ({response.status_code}): {err_msg}",
                        latency_ms=latency,
                    )
                return ModelResponse(
                    model=display_model_name,
                    content=None,
                    error=f"Client error: {response.status_code}",
                    latency_ms=latency,
                )

            # 2. Transient errors: 429 (Rate limit) or 5xx (Server error)
            if response.status_code == 429 or response.status_code >= 500:
                if attempt >= max_retries:
                    err_msg = _extract_error_message(response)
                    detail = f": {err_msg}" if err_msg else ""
                    return ModelResponse(
                        model=display_model_name,
                        content=None,
                        error=f"Provider error {response.status_code} after {attempt} retries{detail}",
                        latency_ms=latency,
                    )

                attempt += 1

                header_delay = _retry_after_seconds(response)
                if header_delay is not None:
                    delay = header_delay
                else:
                    delay = min(2**attempt, 30) * random.uniform(1.0, 1.5)

                # Sleeping past the council deadline helps nobody: fail now so the engine
                # can rotate a different model into this seat.
                if deadline is not None and time.monotonic() + delay > deadline:
                    return ModelResponse(
                        model=display_model_name,
                        content=None,
                        error=(
                            f"Rate limited ({response.status_code}); "
                            f"retry-after {delay:.0f}s exceeds the remaining budget"
                        ),
                        latency_ms=latency,
                    )

                # Semaphore is released while sleeping so other models can proceed.
                await asyncio.sleep(delay)
                continue

            response.raise_for_status()
            try:
                data = response.json()
            except Exception:
                # HTML error pages and truncated bodies are transient provider faults.
                if attempt < max_retries:
                    attempt += 1
                    await asyncio.sleep(min(2**attempt, 30) * random.uniform(1.0, 1.5))
                    continue
                return ModelResponse(
                    model=display_model_name,
                    content=None,
                    error="Malformed response from provider: body is not JSON",
                    latency_ms=latency,
                )

            # 3. Check for OpenRouter returning HTTP 200 with an error object
            if isinstance(data, dict) and "error" in data:
                err_val = data["error"]
                err_code = err_val.get("code", 500) if isinstance(err_val, dict) else 500
                err_msg = err_val.get("message") if isinstance(err_val, dict) else str(err_val)

                is_transient = (
                    err_code in (429, 500, 502, 503, 504)
                    or "overload" in str(err_msg).lower()
                    or "rate" in str(err_msg).lower()
                )

                if is_transient and attempt < max_retries:
                    attempt += 1
                    delay = min(2**attempt, 30) * random.uniform(1.0, 1.5)
                    await asyncio.sleep(delay)
                    continue

                return ModelResponse(
                    model=display_model_name,
                    content=None,
                    error=f"Upstream provider error: {err_msg}",
                    latency_ms=latency,
                )

            try:
                content, reasoning, finish_reason = _parse_completion(data)
            except MalformedCompletion as exc:
                # A malformed 200 is a provider hiccup, not a permanent defect: retry it
                # instead of burning the model's seat in the council.
                if attempt < max_retries:
                    attempt += 1
                    await asyncio.sleep(min(2**attempt, 30) * random.uniform(1.0, 1.5))
                    continue
                return ModelResponse(
                    model=display_model_name,
                    content=None,
                    error=f"Malformed response from provider: {exc}",
                    latency_ms=latency,
                )

            served_model = data.get("model") if isinstance(data, dict) else None
            served_provider = data.get("provider") if isinstance(data, dict) else None
            truncated = finish_reason == "length"

            # A reasoning model that thought through all that room is looping, not short of
            # space: on diffs cohere and laguna-s ran to 32768 with nothing to show, and a
            # bigger budget only makes the council wait longer. The seat goes to another
            # model that still reasons, not to this one with its reasoning switched off.
            if content is None and truncated and supports_reasoning:
                return ModelResponse(
                    model=display_model_name,
                    content=None,
                    error=f"Reasoned through the whole {current_max_tokens}-token budget without answering",
                    latency_ms=latency,
                    served_model=served_model,
                    served_provider=served_provider,
                    finish_reason=finish_reason,
                    truncated=truncated,
                    reasoning=reasoning,
                )

            # Grow the budget once before accepting a truncated answer. Reasoning models
            # routinely spend the whole allowance thinking and return nothing usable.
            if truncated and not grew_budget:
                ceiling = max_output_cap or (current_max_tokens * 2)
                bigger = min(current_max_tokens * 2, ceiling)
                if bigger > current_max_tokens:
                    current_max_tokens = bigger
                    payload = build_payload()
                    grew_budget = True
                    continue

            if content is None:
                # Empty content with reasoning present means the model was cut off while
                # thinking. Returning the chain-of-thought as if it were a review hides that.
                detail = (
                    "Truncated while reasoning; raise max output tokens"
                    if reasoning
                    else "Empty response from model"
                )
                return ModelResponse(
                    model=display_model_name,
                    content=None,
                    error=detail,
                    latency_ms=latency,
                    served_model=served_model,
                    served_provider=served_provider,
                    finish_reason=finish_reason,
                    truncated=truncated,
                    reasoning=reasoning,
                )

            return ModelResponse(
                model=display_model_name,
                content=content,
                error=None,
                latency_ms=latency,
                served_model=served_model,
                served_provider=served_provider,
                finish_reason=finish_reason,
                truncated=truncated,
                reasoning=reasoning,
            )

        except httpx.TimeoutException:
            latency = int((time.monotonic() - start_time) * 1000)
            timeout_val = getattr(client.timeout, "read", "unknown")
            return ModelResponse(
                model=display_model_name, content=None, error=f"Timeout after {timeout_val}s", latency_ms=latency
            )

        except httpx.ConnectError:
            latency = int((time.monotonic() - start_time) * 1000)
            return ModelResponse(model=display_model_name, content=None, error="Connection failed", latency_ms=latency)

        except (httpx.ReadError, httpx.WriteError, httpx.RemoteProtocolError) as e:
            # A connection dropped mid-exchange is the network, not the model, and it passes: retry
            # it as a 5xx is retried. Unretried it cost the seat, with httpx's empty message — 12
            # blank errors in one recording. A refused connection (above) is not retried: with no
            # network, retrying only makes every model take longer to fail.
            delay = min(2 ** (attempt + 1), 30) * random.uniform(1.0, 1.5)
            if attempt < max_retries and (deadline is None or time.monotonic() + delay <= deadline):
                attempt += 1
                await asyncio.sleep(delay)
                continue
            latency = int((time.monotonic() - start_time) * 1000)
            return ModelResponse(
                model=display_model_name, content=None,
                error=f"Connection dropped after {attempt} retries: {_describe(e)}", latency_ms=latency,
            )

        except Exception as e:
            latency = int((time.monotonic() - start_time) * 1000)
            return ModelResponse(model=display_model_name, content=None, error=_describe(e), latency_ms=latency)


def _describe(error: Exception) -> str:
    """The error's message, or its type when it has none: several httpx errors carry no message."""
    text = str(error).strip()
    if not text:
        return type(error).__name__
    # An httpx message ("Server disconnected") reads as nothing without the error it belongs to.
    return f"{type(error).__name__}: {text}" if isinstance(error, httpx.HTTPError) else text



