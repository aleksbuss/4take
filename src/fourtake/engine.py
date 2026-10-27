import asyncio
import math
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

from .cache import get_cached_response, save_cached_response
from .client import ModelResponse, query_model
from .config import FourtakeConfig
from .context import ReviewContext
from .discovery import get_model_limits, get_model_provider, is_free_model_id, is_model_compatible
from .findings import is_review
from .prompts import (
    REVIEWER_SYSTEM_PROMPT,
    REVIEWER_SYSTEM_PROMPT_WITH_CONTEXT,
    build_system_prompt,
    new_review_nonce,
)
from .quota import QuotaGuard, is_openrouter_url
from .rate_limiter import PersistentTokenBucket
from .tokenizer import estimate_tokens

# Output budget bounds. The floor keeps short reviews from being clipped; the ceiling keeps
# latency and provider throttling in check. Both are overridable via config.
MIN_OUTPUT_TOKENS = 2048
MAX_OUTPUT_TOKENS_CEILING = 16384

# A single slow member used to set the pace for the whole council: with
# active_count == min_responses there was no slack, so the run finished when the slowest
# model did. Dispatching a half-cohort of spares lets the fast majority close the council.
OVERDISPATCH_RATIO = 0.5

# A per-model cap exists so one model cannot consume the whole council budget while spares
# are waiting. A flat constant was wrong in both directions: 90s starved models that
# legitimately need minutes on a large input, and was far too generous for a short snippet.
# Half the council deadline always leaves room to rotate a replacement and finish.
PER_MODEL_TIMEOUT_SHARE = 0.5
MIN_PER_MODEL_TIMEOUT = 60.0


def default_per_model_timeout(council_timeout: float) -> float:
    return max(MIN_PER_MODEL_TIMEOUT, council_timeout * PER_MODEL_TIMEOUT_SHARE)


def dispatch_count(min_responses: int, pool_size: int, active_count: int) -> int:
    """How many models to launch up front: enough spares that one straggler cannot stall
    the council, never more than the pool holds."""
    wanted = max(active_count, min_responses + math.ceil(min_responses * OVERDISPATCH_RATIO))
    return max(1, min(wanted, pool_size))


# Room reserved for the system prompt, the <code_to_review> wrapper and tokenizer drift.
# estimate_tokens uses cl100k_base as a proxy for every provider's tokenizer, so the count
# is approximate in both directions.
PROMPT_OVERHEAD_TOKENS = 1200


def model_input_capacity(
    model_id: str,
    input_tokens: int,
    overrides: dict[str, int] | None = None,
    review_tokens: int | None = None,
) -> int:
    """
    How many input tokens this model can actually accept, after reserving room for the
    reply and the prompt scaffolding.

    Previously a flat 32000 was applied to every model. Discovery already fetches each
    model's real context window and then threw it away, so a 198KB diff was refused for
    a model with a 1,000,000-token context. Config overrides still win, for the case where
    a provider advertises more than it honours.

    `review_tokens` is the part of the input the reply is written about. Reference context is
    read, not answered, so it takes room in the window without enlarging the reply reserved.
    """
    override = (overrides or {}).get(model_id)
    limits = get_model_limits(model_id)
    context = override or limits["context_length"]
    reply_basis = input_tokens if review_tokens is None else review_tokens
    reply = compute_output_budget(reply_basis, limits["max_output_tokens"])
    return max(0, context - reply - PROMPT_OVERHEAD_TOKENS)


def filter_models_by_context(
    model_pool: list[str],
    input_tokens: int,
    overrides: dict[str, int] | None = None,
    review_tokens: int | None = None,
) -> tuple[list[str], list[tuple[str, int]]]:
    """Split a pool into models that can hold the input and those that cannot."""
    keep: list[str] = []
    skipped: list[tuple[str, int]] = []
    for model in model_pool:
        capacity = model_input_capacity(model, input_tokens, overrides, review_tokens)
        if input_tokens > capacity:
            skipped.append((model, capacity))
        else:
            keep.append(model)
    return keep, skipped


def compute_output_budget(
    input_tokens: int, model_max_output: int, ceiling: int = MAX_OUTPUT_TOKENS_CEILING
) -> int:
    """
    Size the completion budget for one model.

    A fixed 4096 was previously used for every request. On reasoning models that budget
    covers thinking *and* the answer, so large inputs came back truncated mid-thought with
    no findings at all. Scale with the input and clamp to what the model actually allows.
    """
    scaled = int(input_tokens * 0.6) + 1024
    upper = min(model_max_output, ceiling)
    # The floor is itself capped by the model. Raising `upper` to the floor instead would
    # guarantee asking a small model for more than it can emit — truncation or a 400.
    floor = min(MIN_OUTPUT_TOKENS, upper)
    return max(floor, min(scaled, upper))


class RateLimitBudgetExceeded(Exception):
    """The rate limiter cannot grant a slot inside the council deadline."""


@dataclass
class ResolvedModel:
    provider_name: str
    url: str
    api_key: str | None
    api_model_name: str
    display_model_name: str


def resolve_model(model_str: str, config: FourtakeConfig) -> ResolvedModel | None:
    if model_str.startswith("openrouter/"):
        actual_model = model_str.split("openrouter/", 1)[1]
        provider = config.providers.get("openrouter")
        if not provider:
            return None
        return ResolvedModel(
            provider_name="openrouter",
            url=provider.url,
            api_key=provider.api_key,
            api_model_name=actual_model,
            display_model_name=model_str,
        )

    if "/" in model_str:
        provider_name, actual_model = model_str.split("/", 1)
        if provider_name in config.providers:
            provider = config.providers[provider_name]
            return ResolvedModel(
                provider_name=provider_name,
                url=provider.url,
                api_key=provider.api_key,
                api_model_name=actual_model,
                display_model_name=model_str,
            )
        elif "openrouter" in config.providers:
            provider = config.providers["openrouter"]
            return ResolvedModel(
                provider_name="openrouter",
                url=provider.url,
                api_key=provider.api_key,
                api_model_name=model_str,
                display_model_name=f"openrouter/{model_str}",
            )
        else:
            return None
    else:
        provider = config.providers.get("openrouter")
        if not provider:
            return None
        return ResolvedModel(
            provider_name="openrouter",
            url=provider.url,
            api_key=provider.api_key,
            api_model_name=model_str,
            display_model_name=f"openrouter/{model_str}",
        )


@dataclass
class CouncilResult:
    responses: list[ModelResponse]
    errors: list[ModelResponse]
    warning: str | None
    error: str | None
    total_latency_ms: int
    # The reference context the council was sent, and what was dropped to fit, for the reader to
    # see what the reviews could and could not have known.
    context: ReviewContext | None = None
    # Every model the code was sent to: seats, spares and replacements, cancelled spares included.
    models_asked: int = 0


def distinct_seats(responses: list[ModelResponse]) -> int:
    """
    Independent reviewers among the responses.

    The council's success threshold is expressed in seats, so anything that reports success
    must count the same way. Counting raw responses lets a router that answered three seats
    with one model look like three independent opinions.
    """
    return len({r.served_model or r.model for r in responses if r.duplicate_of is None})


async def run_council(
    config: FourtakeConfig,
    task_input: str,
    model_pool: list[str] | None = None,
    active_count: int | None = None,
    verbose: bool = False,
    console: Any = None,
    progress_callback: Callable[[str, int, int], None] | None = None,
    use_cache: bool = True,
    per_model_timeout: float | None = None,
    context: ReviewContext | None = None,
) -> CouncilResult:
    """
    Run the council of models with dynamic rotation, per-request rate limiting, and local SHA caching.

    Args:
        config: The loaded FourtakeConfig.
        task_input: The user's input text to review.
        model_pool: Full list of candidate models for rotation. Defaults to config.default_models.
        active_count: How many models to run concurrently. Defaults to len(model_pool).
        verbose: If True, print progress messages.
        console: Rich Console instance for stderr output.
        progress_callback: Optional callback for progress updates.
        use_cache: If True, check and save to local SHA-256 cache.
        per_model_timeout: Hard per-model wall-clock cap in seconds; a model that exceeds it
            is abandoned and a replacement is rotated in. Defaults to half the council
            timeout (floored at 60s), so there is always budget left to rotate.
        context: Reference context sent beside the code, consulted but never reviewed. None, or
            a context whose every piece was dropped to fit the budget, sends exactly what a
            review without context sends.

    Returns:
        CouncilResult with responses, errors, and metadata.
    """
    start_time = time.monotonic()

    if model_pool is None:
        model_pool = list(config.default_models)
    if not model_pool:
        return CouncilResult(
            responses=[],
            errors=[],
            warning=None,
            error="model_pool is empty",
            total_latency_ms=0,
            context=context,
        )

    # Deduplicate candidates on their RESOLVED identity, preserving order. Deduplicating the
    # raw strings let aliases through — "openrouter/x:free" and "x:free" name one model but
    # are two strings, so the same reviewer occupied two seats and its opinion counted twice.
    deduped_pool: list[str] = []
    seen_identity: set[str] = set()
    for m in model_pool:
        resolved = resolve_model(m, config)
        identity = resolved.display_model_name if resolved else m
        if identity not in seen_identity:
            seen_identity.add(identity)
            deduped_pool.append(m)
    model_pool = deduped_pool

    if active_count is None:
        active_count = len(model_pool)
    active_count = max(1, min(active_count, len(model_pool)))

    def _log(msg: str):
        if console:
            # markup/emoji off: model ids end in ':free', and "…:free: <error>" would be
            # rendered by Rich as the 🆓 emoji; provider errors may contain '[...]'.
            console.print(msg, highlight=False, markup=False, emoji=False)
        else:
            print(msg, file=sys.stderr)

    if verbose:
        _log("[4take] Loading config...")
        _log(f"[4take] Models: {', '.join(model_pool[:active_count])}")
        if len(model_pool) > active_count:
            _log(f"[4take] Rotation pool: {len(model_pool) - active_count} backup model(s) available")

    # The reply is sized on the code under review alone: reference context is read, not answered.
    input_tokens = estimate_tokens(task_input)
    context_block = context.block() if context is not None else None
    # One nonce for the whole council so every member sees identical framing. The cache is
    # keyed on the un-nonced template, or every lookup would miss.
    review_nonce = new_review_nonce()
    rendered_system_prompt = build_system_prompt(review_nonce, with_context=context_block is not None)
    prompt_identity = REVIEWER_SYSTEM_PROMPT_WITH_CONTEXT if context_block else REVIEWER_SYSTEM_PROMPT
    context_digest = context.digest if context_block and context is not None else ""
    if per_model_timeout is None:
        per_model_timeout = default_per_model_timeout(config.timeout)

    bucket = PersistentTokenBucket(rpm=config.rpm_limit)

    planned_launch = dispatch_count(config.min_responses, len(model_pool), active_count)
    limits = httpx.Limits(
        max_connections=max(8, planned_launch + 4),
        max_keepalive_connections=max(4, planned_launch),
    )
    timeout = httpx.Timeout(config.timeout, connect=5.0)

    headers = {
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/aleksbuss/4take",
        "X-Title": "4take",
    }

    deadline = start_time + config.timeout
    guard: QuotaGuard | None = None

    async with httpx.AsyncClient(limits=limits, timeout=timeout, headers=headers) as client:
        openrouter = config.providers.get("openrouter")
        if openrouter is not None and openrouter.api_key and is_openrouter_url(openrouter.url):
            guard = QuotaGuard(client, openrouter.api_key)
        semaphores = {name: asyncio.Semaphore(limit) for name, limit in config.provider_concurrency.items()}
        # A provider without a configured cap is held to what the council plans to have in flight:
        # rotation replaces a finished model, so no more than `planned_launch` ever run at once.
        default_semaphore = asyncio.Semaphore(max(1, planned_launch))

        async def wrapped_query(model_str: str) -> ModelResponse:
            resolved = resolve_model(model_str, config)
            if not resolved:
                return ModelResponse(model=model_str, content=None, error="Unknown provider", latency_ms=0)

            # Pre-flight Zero-Cost Guard: strictly prevent network calls to paid/unverified models
            if resolved.provider_name == "openrouter" and not is_free_model_id(resolved.api_model_name):
                return ModelResponse(
                    model=resolved.display_model_name,
                    content=None,
                    error=f"ZeroCostViolation: Model '{resolved.display_model_name}' is not verified as free. 4take operates strictly in zero-token-cost mode.",
                    latency_ms=0,
                )

            # 1. Check local SHA-256 cache
            if use_cache:
                cached = get_cached_response(
                    task_input, resolved.display_model_name, prompt_identity, context_digest=context_digest
                )
                # A non-review cached before seats checked for one would be replayed forever.
                if cached is not None and is_review(cached.content):
                    return cached

            # 2. The rate limiter is charged inside query_model, once per attempt, so
            # that retries are metered too.
            async def _acquire_slot() -> None:
                # Waiting past the council deadline for a rate-limit slot is dead time; refuse
                # so the seat can be rotated. acquire() declines without reserving, so the
                # abandoned slot is not taken from callers that would have used it.
                remaining = max(0.0, deadline - time.monotonic())
                if not await bucket.acquire(1, max_wait=remaining):
                    raise RateLimitBudgetExceeded(
                        f"rate-limit wait exceeds the remaining {remaining:.0f}s budget"
                    )

            sem = semaphores.get(resolved.provider_name, default_semaphore)
            limits = get_model_limits(resolved.api_model_name)
            model_max_output = limits["max_output_tokens"]
            try:
                resp = await asyncio.wait_for(
                    query_model(
                        client=client,
                        provider_url=resolved.url,
                        api_key=resolved.api_key,
                        api_model_name=resolved.api_model_name,
                        display_model_name=resolved.display_model_name,
                        prompt=task_input,
                        system_prompt=rendered_system_prompt,
                        nonce=review_nonce,
                        semaphore=sem,
                        max_tokens=compute_output_budget(input_tokens, model_max_output),
                        max_output_cap=model_max_output,
                        supports_reasoning=limits["supports_reasoning"],
                        deadline=deadline,
                        acquire_token=_acquire_slot,
                        context_block=context_block,
                        quota_guard=guard if resolved.provider_name == "openrouter" else None,
                    ),
                    timeout=per_model_timeout,
                )
                if use_cache and resp.error is None and is_review(resp.content):
                    save_cached_response(
                        task_input, resolved.display_model_name, resp, prompt_identity, context_digest
                    )
                return resp
            except (asyncio.TimeoutError, TimeoutError):
                return ModelResponse(
                    model=resolved.display_model_name,
                    content=None,
                    error=f"Exceeded per-model timeout of {per_model_timeout:.0f}s",
                    latency_ms=int(per_model_timeout * 1000),
                )
            except Exception as e:
                return ModelResponse(
                    model=resolved.display_model_name, content=None, error=str(e), latency_ms=0
                )

        # --- Rotation logic ---
        launch_count = planned_launch
        pool_index = launch_count
        initial_models = model_pool[:pool_index]
        if verbose and launch_count > active_count:
            _log(
                f"[4take] Over-dispatching {launch_count} models for {config.min_responses} seats "
                f"so one slow model cannot stall the council"
            )

        task_to_model: dict[asyncio.Task, str] = {}
        pending: set[asyncio.Task] = set()
        limit_announced = False

        for m in initial_models:
            t = asyncio.create_task(wrapped_query(m), name=f"4take-{m}")
            pending.add(t)
            task_to_model[t] = m

        all_launched = set(initial_models)
        responses: list[ModelResponse] = []
        errors: list[ModelResponse] = []
        # Seats keyed by the model that ACTUALLY answered. A router such as "openrouter/free"
        # can resolve two seats to one underlying model, and two copies of one opinion are
        # one opinion — the whole premise is independence.
        seen_served: set[str] = set()
        # One seat per vendor. Two models from one vendor share training data and habits, so
        # their agreement is weaker evidence than two vendors' — yet 21 of 28 recorded councils
        # seated such a pair, half of them because `openrouter/free` served a vendor that
        # already held a seat. Letting a second answer take the seat when no other vendor
        # answered in time filled 21 of 50 councils on a public benchmark with two or three
        # nvidia models, 5 of them stealth + three nemotrons. A second answer from a seated
        # vendor is kept for reading and never seated.
        seated_vendor: dict[str, str] = {}
        held_same_vendor: list[ModelResponse] = []

        def seats_filled() -> int:
            return len(seen_served)

        def rotate_in(reason: str) -> bool:
            """Bring the next pooled model into a freed seat. Shared by the failure and
            duplicate paths so both stay in step."""
            nonlocal pool_index
            # A model from a vendor that already holds a seat can never take one, so launching
            # it would only spend a request while the seat stays empty.
            while (
                pool_index < len(model_pool) and get_model_provider(model_pool[pool_index]) in seated_vendor
            ):
                pool_index += 1
            if pool_index >= len(model_pool) or seats_filled() >= config.min_responses:
                return False
            replacement = model_pool[pool_index]
            pool_index += 1
            all_launched.add(replacement)
            new_task = asyncio.create_task(wrapped_query(replacement), name=f"4take-{replacement}")
            pending.add(new_task)
            task_to_model[new_task] = replacement
            if verbose:
                _log(f"[4take] ↻ Rotating in {replacement} ({reason})")
            return True

        try:
            if progress_callback:
                try:
                    progress_callback(f"Querying {len(initial_models)} models...", 0, config.min_responses)
                except Exception:
                    pass

            while seats_filled() < config.min_responses and pending:
                remaining_timeout = max(0.0, deadline - time.monotonic())
                if remaining_timeout <= 0:
                    if verbose:
                        _log(f"[4take] ✗ Cumulative timeout of {config.timeout}s reached.")
                    break

                done, pending = await asyncio.wait(
                    pending, return_when=asyncio.FIRST_COMPLETED, timeout=remaining_timeout
                )

                if not done:
                    if verbose:
                        _log(f"[4take] ✗ Timeout of {config.timeout}s reached.")
                    break

                # Replacements are chosen only once the whole batch has been read. Tasks that
                # finish together (cache hits, pre-flight refusals) come back as a set, iterated
                # in memory-address order; rotating on a failure before the batch's seats were
                # recorded launched a spare from a vendor about to be seated — a request that
                # could never take the seat.
                # The batch is read in pool order, which is rank order, so when two answers in
                # it contend for one seat — one vendor, or one served model — the better-ranked
                # model takes it. Read in set order, a fully cached rerun could seat the other
                # one and change the agreement counts.
                freed: list[str] = []
                for task in sorted(done, key=lambda t: model_pool.index(task_to_model[t])):
                    model_id = task_to_model.get(task, "unknown")
                    try:
                        result = task.result()
                        if result.error is None:
                            seat = result.served_model or result.model
                            if not is_model_compatible(seat):
                                # A router can seat a model the pool itself would never
                                # select. Observed live: `openrouter/free` was served by a
                                # moderation classifier, which answered "User Safety: safe"
                                # and filled a council seat with a non-review that counted
                                # as a success. Treat it as a soft failure, like a duplicate.
                                # Only the id-based checks apply here — the catalog entry for
                                # whoever the router picked was never fetched — so this
                                # catches the known-unfit models, not every unfit model.
                                result.error = f"served by an incompatible model ({seat})"
                                errors.append(result)
                                if verbose:
                                    _log(
                                        f"[4take] ✗ {result.model} was served by {seat}, "
                                        f"which cannot review code"
                                    )
                                freed.append(f"incompatible seat {seat}")
                            elif not is_review(result.content):
                                # Neither a finding nor the clean verdict: a tool call, `<unk>`
                                # tokens, reasoning that never reached a tag. Seated, it read as
                                # a reviewer who looked and found nothing.
                                result.error = "answered without a review: no finding and no NO_ISSUES_FOUND"
                                errors.append(result)
                                if verbose:
                                    _log(f"[4take] ✗ {result.model} answered without a review")
                                freed.append(f"no review from {seat}")
                            elif seat in seen_served:
                                # Keep the response — the user paid for it and may want to
                                # read it — but it does not fill a seat, and a fresh model
                                # is rotated in exactly as if this one had failed.
                                result.duplicate_of = seat
                                responses.append(result)
                                if verbose:
                                    _log(f"[4take] ⧉ {result.model} was served by {seat}, already seated")
                                freed.append(f"duplicate of {seat}")
                            elif get_model_provider(seat) in seated_vendor:
                                vendor = get_model_provider(seat)
                                held_same_vendor.append(result)
                                if verbose:
                                    _log(
                                        f"[4take] ⧉ {result.model} was served by {seat}; "
                                        f"{vendor} already holds a seat"
                                    )
                                freed.append(f"{vendor} already seated")
                            else:
                                seen_served.add(seat)
                                seated_vendor[get_model_provider(seat)] = seat
                                responses.append(result)
                                if verbose:
                                    _log(
                                        f"[4take] ✓ {result.model} responded ({result.latency_ms / 1000.0:.1f}s)"
                                    )
                                if progress_callback:
                                    try:
                                        progress_callback(
                                            f"{result.model} responded", seats_filled(), config.min_responses
                                        )
                                    except Exception:
                                        pass
                        elif guard is not None and result.error == guard.message:
                            # The day's allowance is spent. Said once below, not once per model, and
                            # no spare is launched: it would meet the same answer.
                            pass
                        else:
                            errors.append(result)
                            if verbose:
                                _log(f"[4take] ✗ {result.model}: {result.error}")
                            freed.append(f"replacing {result.model}")

                    except Exception as e:
                        err_resp = ModelResponse(model=model_id, content=None, error=str(e), latency_ms=0)
                        errors.append(err_resp)
                        freed.append(f"replacing {model_id}")

                if guard is not None and guard.tripped:
                    # Requests OpenRouter already accepted still return answers, so they are waited
                    # for; what stops is everything new, since a spare would meet the same 429.
                    if verbose and not limit_announced:
                        limit_announced = True
                        _log(
                            "[4take] ✗ OpenRouter's free-model limit for today is used up; "
                            "waiting only for requests already in flight"
                        )
                    freed = []

                for reason in freed:
                    rotate_in(reason)

                if seats_filled() >= config.min_responses:
                    if verbose:
                        _log(
                            f"[4take] ✓ min_responses={config.min_responses} reached, cancelling remaining..."
                        )
                    break

        finally:
            for t in pending:
                if not t.done():
                    t.cancel()
                    if seats_filled() < config.min_responses:
                        model_name = task_to_model.get(t, "unknown")
                        errors.append(
                            ModelResponse(
                                model=model_name,
                                content=None,
                                error="Cancelled or timed out",
                                latency_ms=0,
                            )
                        )

            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

    # Independence is required: a council short of vendors reports fewer seats rather than
    # counting one vendor twice. The held answers stay in the result for the reader.
    for held in held_same_vendor:
        held.duplicate_of = seated_vendor[get_model_provider(held.served_model or held.model)]
        responses.append(held)

    total_latency_ms = int((time.monotonic() - start_time) * 1000)

    if verbose:
        pauses = bucket.pause_summary()
        if pauses:
            _log(pauses)
        _log(
            f"[4take] Done. {len(responses)} responses, {len(errors)} error(s), {total_latency_ms / 1000.0:.1f}s total."
        )

    if guard is not None and guard.tripped and not responses:
        # Nothing was reviewed, and the reason is one sentence, not the dozen errors that restate it.
        return CouncilResult(
            responses=[],
            errors=[e for e in errors if e.error != guard.message],
            warning=None,
            error=guard.message,
            total_latency_ms=total_latency_ms,
            context=context,
            models_asked=len(all_launched),
        )

    duplicates = sum(1 for r in responses if r.duplicate_of)
    notes = []
    if guard is not None and guard.tripped:
        notes.append(f"stopped early: {guard.message}")
    if duplicates:
        notes.append(f"{duplicates} response(s) came from an already-seated model or vendor")
    if len(seen_served) >= config.min_responses:
        return CouncilResult(
            responses=responses,
            errors=errors,
            warning="; ".join(notes) or None,
            error=None,
            total_latency_ms=total_latency_ms,
            context=context,
            models_asked=len(all_launched),
        )
    elif len(responses) > 0:
        return CouncilResult(
            responses=responses,
            errors=errors,
            warning="; ".join(
                [f"only {len(seen_served)} distinct of {len(all_launched)} models responded", *notes]
            ),
            error=None,
            total_latency_ms=total_latency_ms,
            context=context,
            models_asked=len(all_launched),
        )
    else:
        return CouncilResult(
            responses=[],
            errors=errors,
            warning=None,
            error="all models failed",
            total_latency_ms=total_latency_ms,
            context=context,
            models_asked=len(all_launched),
        )
