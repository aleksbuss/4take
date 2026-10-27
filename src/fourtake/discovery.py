from __future__ import annotations

import logging
import re
from typing import Any

import httpx

from .seed import load as load_seed
from .seed import with_seed
from .telemetry import MIN_SAMPLES_FOR_RANKING, ModelStats, aggregate

logger = logging.getLogger("4take")

# Only for a run that cannot read the catalogue. Unlike discovery this list never updates itself:
# OpenRouter retired nemotron-3-super-120b-a12b:free and it stayed here, a dead seat, until 0.2.6.
FALLBACK_MODELS = [
    "openrouter/cohere/north-mini-code:free",
    "openrouter/poolside/laguna-s-2.1:free",
    "openrouter/nvidia/nemotron-3.5-lightning:free",
    "openrouter/openrouter/free",
    "openrouter/dots-studio/dots-3-note-preview:free",
    "openrouter/nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
]

VERIFIED_FREE_MODELS: set[str] = {fb.split("openrouter/", 1)[-1].lower() for fb in FALLBACK_MODELS}

# Kept only for models the catalog cannot rule out on capabilities alone (access-gated or
# purpose-built classifiers). Everything expressible as a capability is checked against the
# API instead — see is_model_compatible.
INCOMPATIBLE_MODEL_PATTERNS = [
    "thinkingmachines/inkling",  # Requires an agentic harness (HTTP 403)
    "content-safety",  # Moderation classifier, not a reviewer
]

# Per-model capabilities harvested from the OpenRouter catalog during discovery/validation.
# Keyed by bare model id (no "openrouter/" prefix). Populated as a side effect of the
# catalog fetch so the engine can size requests instead of guessing.
# NOTE: superseded by an explicit ModelCandidate type in a later change; kept as a registry
# here to avoid churning the string-based model API in the same commit.
MODEL_LIMITS: dict[str, dict[str, Any]] = {}

# Conservative floor used only when the catalog is unreachable or omits the field.
FALLBACK_CONTEXT_LENGTH = 32000
FALLBACK_MAX_OUTPUT_TOKENS = 8192

# Cap on how many rotation-tail slots one provider may occupy.
MAX_TAIL_MODELS_PER_PROVIDER = 2


def _record_model_limits(model_data: dict[str, Any]) -> None:
    """Cache context/output limits and parameter support for one catalog entry."""
    model_id = model_data.get("id")
    if not model_id:
        return
    top = model_data.get("top_provider") or {}
    supported = set(model_data.get("supported_parameters") or [])
    MODEL_LIMITS[model_id.lower()] = {
        "context_length": top.get("context_length") or model_data.get("context_length"),
        "max_output_tokens": top.get("max_completion_tokens"),
        "supports_reasoning": "reasoning" in supported,
        "supports_include_reasoning": "include_reasoning" in supported,
    }


def get_model_limits(model_id: str) -> dict[str, Any]:
    """
    Capabilities for a model id, with or without the "openrouter/" prefix.
    Falls back to conservative defaults when the catalog was never fetched.
    """
    # Try the id as given before stripping the provider prefix: the catalog contains a model
    # literally named "openrouter/free", and blind stripping turned it into "free", missed the
    # registry, and silently fell back to the conservative 32000 for a 200k-context router.
    lowered = model_id.lower()
    known = MODEL_LIMITS.get(lowered)
    if known is None and lowered.startswith("openrouter/"):
        known = MODEL_LIMITS.get(lowered.split("openrouter/", 1)[1])
    known = known or {}
    return {
        "context_length": known.get("context_length") or FALLBACK_CONTEXT_LENGTH,
        "max_output_tokens": known.get("max_output_tokens") or FALLBACK_MAX_OUTPUT_TOKENS,
        "supports_reasoning": known.get("supports_reasoning", False),
        "supports_include_reasoning": known.get("supports_include_reasoning", False),
    }


def is_model_compatible(model_id: str, model_data: dict[str, Any] | None = None) -> bool:
    """
    Whether a model can serve as a text code reviewer.

    Capability questions are answered by the catalog rather than by a substring denylist that
    had to be maintained by hand: a reviewer must emit text and accept `max_tokens`. Note the
    output check is equality, not membership — the audio models to exclude advertise
    ["text", "audio"], so `"text" in output_modalities` would let them straight back in.
    The residual denylist covers only what capabilities cannot express.
    """
    clean_id = model_id.lower()
    for pattern in INCOMPATIBLE_MODEL_PATTERNS:
        if pattern in clean_id:
            return False

    if model_data is None:
        return True

    architecture = model_data.get("architecture") or {}
    output_modalities = architecture.get("output_modalities")
    if output_modalities is not None and list(output_modalities) != ["text"]:
        return False

    supported = model_data.get("supported_parameters")
    return supported is None or "max_tokens" in set(supported)


def is_model_free(model_data: dict[str, Any]) -> bool:
    """
    Determines if an OpenRouter model is 100% free based on its ID or pricing structure.
    """
    # `or`, not a default: the catalog sends explicit nulls, and `.get(key, {})` returns them.
    # Missing prices leave the model paid unless its id says `:free`.
    model_id = model_data.get("id") or ""
    pricing = model_data.get("pricing") or {}
    prompt_price = pricing.get("prompt", "-1")
    completion_price = pricing.get("completion", "-1")

    try:
        zero_pricing = float(prompt_price) == 0.0 and float(completion_price) == 0.0
    except (ValueError, TypeError):
        zero_pricing = str(prompt_price) == "0" and str(completion_price) == "0"

    is_free = model_id.endswith(":free") or model_id.lower() in ("openrouter/free", "free") or zero_pricing
    if is_free and model_id:
        VERIFIED_FREE_MODELS.add(model_id.lower())
    return is_free


def is_free_model_id(model_id: str) -> bool:
    """
    Quick check if a model ID represents a guaranteed free model.
    """
    clean_id = model_id.split("openrouter/", 1)[-1].lower() if "openrouter/" in model_id else model_id.lower()
    return (
        clean_id.endswith(":free")
        or clean_id in ("openrouter/free", "free")
        or clean_id in VERIFIED_FREE_MODELS
    )


def get_model_provider(model_id: str) -> str:
    """
    Extract the provider / organization prefix from an OpenRouter model ID.
    """
    if model_id.startswith("openrouter/"):
        model_id = model_id.split("openrouter/", 1)[1]
    if "/" in model_id:
        return model_id.split("/")[0].lower()
    return model_id.lower()


def evaluate_model_intelligence(model_id: str) -> int:
    """
    Prior estimate of a model's code-review fitness, read off its id.

    This is astrology over strings: it cannot tell that a model times out half the time, and
    it scores `nemotron-3-ultra-550b` and `nemotron-3-nano-omni-30b` identically at 65 while
    six of sixteen catalog models score exactly 0. It survives as a **cold-start prior**
    only — see `blend_with_observed`, which takes over as soon as a model has been watched
    often enough for its measured behaviour to mean anything.
    """
    score = 0
    model_id_lower = model_id.lower()

    # 0. High priority for OpenRouter Free Router (routes to MiniMax M3, Tencent Hy3, Nemotron Ultra)
    if model_id_lower in ("openrouter/free", "free"):
        return 55

    # 1. Parameter count evaluation
    if re.search(r"(7[0-9]b|[1-9][0-9]{2,}b|8x[1-9][0-9]*b)", model_id_lower):
        score += 50  # Heavy models (70B, 72B, 104B, 120b, 550b etc.)
    elif re.search(r"(2[0-9]b|3[0-9]b|8x7b)", model_id_lower):
        score += 30  # Medium models (26B, 27B, 30B, 31B, 32B, Mixtral 8x7B)
    elif re.search(r"(\b1[0-9]b)", model_id_lower):
        score += 20  # 10B-19B models
    elif re.search(r"(\b[7-9]b)", model_id_lower):
        score += 10  # 7B-9B models

    # 2. Architecture & Domain evaluation
    if "code" in model_id_lower or "coder" in model_id_lower:
        score += 30  # Specialized code models get priority for code review
    if "reasoning" in model_id_lower:
        score += 20  # Reasoning models
    if "llama-3" in model_id_lower or "qwen-2" in model_id_lower or "deepseek" in model_id_lower:
        score += 25
    elif (
        "mistral" in model_id_lower
        or "gemma" in model_id_lower
        or "nemotron" in model_id_lower
        or "laguna" in model_id_lower
    ):
        score += 15

    # Quality Floor: penalize tiny non-code models (< 7B)
    if re.search(r"(\b[1-6](\.[0-9]+)?b\b)", model_id_lower) and "code" not in model_id_lower:
        score = max(0, score - 30)

    return score


# A median as slow as this costs a model the full latency penalty. Free endpoints answer in
# 30-120s, so the scale is set by what is actually observed, not by what feels fast.
SLOW_P50_MS = 120_000
LATENCY_PENALTY_POINTS = 25
SUCCESS_POINTS = 100


def _observed(model_id: str, stats: dict[str, ModelStats] | None) -> ModelStats | None:
    if not stats:
        return None
    # Discovery names models bare; telemetry records them as dispatched, with the prefix.
    return stats.get(model_id) or stats.get(f"openrouter/{model_id}")


def blend_with_observed(model_id: str, prior: int, stats: dict[str, ModelStats] | None) -> float:
    """
    Combine the name heuristic with what the model has actually done.

    The weight on evidence grows with the number of observations, so one unlucky timeout
    cannot demote a good model and a model nobody has run yet is not punished for it. With
    `MIN_SAMPLES_FOR_RANKING` observations the two halves count equally; by three times that
    the measurements decide.
    """
    observed = _observed(model_id, stats)
    if observed is None or not observed.attempts:
        return float(prior)

    latency_penalty = LATENCY_PENALTY_POINTS * min(1.0, observed.p50_latency_ms / SLOW_P50_MS)
    measured = SUCCESS_POINTS * observed.success_rate - latency_penalty
    weight = observed.attempts / (observed.attempts + MIN_SAMPLES_FOR_RANKING)
    return (1.0 - weight) * prior + weight * measured


async def get_best_free_models(
    min_context: int = 4000,
    count: int = 4,
    pool_size: int | None = None,
    timeout: int = 10,
    stats: dict | None = None,
) -> list[str]:
    """
    Fetches the best free models from OpenRouter that satisfy the minimum context length.

    Args:
        min_context: Minimum context window size in tokens.
        count: How many models the caller intends to run concurrently (used for default pool_size).
        pool_size: Total number of models to return (for runtime rotation). Defaults to count * 3.
        timeout: HTTP request timeout in seconds.
        stats: Observed per-model behaviour. Defaults to the local telemetry history with the
            shipped seed added to it (seed.py); pass `{}` to rank on the name heuristic alone.

    Returns:
        A list of model IDs sorted by intelligence score, up to pool_size entries.
    """
    if pool_size is None:
        pool_size = count * 3

    if stats is None:
        stats = with_seed(aggregate(), load_seed())

    url = "https://openrouter.ai/api/v1/models"

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(url)
            response.raise_for_status()
            models_data = response.json().get("data", [])
    except Exception as e:
        logger.warning(f"Failed to fetch models from OpenRouter: {e}")
        return []

    free_models = []

    for model in models_data:
        # One entry with a null field must cost that entry, not the discovery of every other one.
        model_id = model.get("id") or ""
        if not model_id:
            continue  # nothing to request it by: it used to join the pool as "openrouter/"
        context_length = model.get("context_length") or 0
        _record_model_limits(model)

        # Filter strictly for free, compatible models satisfying minimum context length
        if is_model_free(model) and is_model_compatible(model_id, model) and context_length >= min_context:
            observed = _observed(model_id, stats)
            if observed is not None and observed.unavailable:
                logger.debug("skipping %s: its provider refused every recent request", model_id)
                continue
            prior = evaluate_model_intelligence(model_id)

            free_models.append(
                {
                    "id": model_id,
                    "context_length": context_length,
                    "score": blend_with_observed(model_id, prior, stats),
                    "prior": prior,
                }
            )
    # Sort by score descending, then context descending
    free_models.sort(key=lambda x: (x["score"], x["context_length"]), reverse=True)

    # Telemetry silently reorders the pool once a model has enough history, so leave a trace
    # of why: without it, "why did my usual model stop being picked?" has no answer anywhere.
    # Ranked by how far evidence moved each model, not by final position — a model demoted
    # out of the pool is exactly the one the question is about, and slicing the pool first
    # hid it.
    if logger.isEnabledFor(logging.DEBUG):
        moved = sorted(
            (m for m in free_models if abs(m["score"] - m["prior"]) >= 1.0),
            key=lambda m: abs(m["score"] - m["prior"]),
            reverse=True,
        )
        for m in moved[:10]:
            logger.debug(
                "ranking: %s prior %d -> %.1f from observed history", m["id"], m["prior"], m["score"]
            )

    # Group models by provider to ensure diversity across organizations
    by_provider = {}
    for m in free_models:
        prov = get_model_provider(m["id"])
        by_provider.setdefault(prov, []).append(m)

    # Sort providers by the highest score of their top model
    sorted_providers = sorted(
        by_provider.keys(),
        key=lambda p: (by_provider[p][0]["score"], by_provider[p][0]["context_length"]),
        reverse=True,
    )

    # The seated cohort gets one model per provider, best first, so the council is made of
    # genuinely independent voices.
    selected: list[dict] = []
    taken: set[str] = set()
    for prov in sorted_providers:
        if len(selected) >= count:
            break
        best = by_provider[prov][0]
        selected.append(best)
        taken.add(best["id"])

    # The rotation tail is ordered by quality, not by round-robin. Round-robin across the
    # whole pool pushed a 120B model to position 10, behind a 2.6B one at position 9, so a
    # failure was replaced by the worst candidate available rather than the next best.
    # A per-provider cap keeps one provider's outage from draining the tail.
    per_provider_tail: dict[str, int] = {}
    for m in free_models:
        if len(selected) >= pool_size:
            break
        if m["id"] in taken:
            continue
        prov = get_model_provider(m["id"])
        if per_provider_tail.get(prov, 0) >= MAX_TAIL_MODELS_PER_PROVIDER:
            continue
        per_provider_tail[prov] = per_provider_tail.get(prov, 0) + 1
        selected.append(m)
        taken.add(m["id"])

    return [f"openrouter/{m['id']}" for m in selected]


async def validate_configured_models(models: list[str], timeout: int = 10) -> list[str]:
    """
    Validates configured OpenRouter models against the API to ensure they are available AND 100% free.
    Any paid model is rejected to guarantee zero token expenditure.
    Non-OpenRouter models are checked against free naming conventions.
    """
    url = "https://openrouter.ai/api/v1/models"

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(url)
            response.raise_for_status()
            available_models_data = response.json().get("data", [])
    except Exception as e:
        logger.warning(
            f"Failed to fetch models from OpenRouter for validation: {e}. Enforcing strict :free suffix check."
        )
        return [m for m in models if is_free_model_id(m)]

    model_map = {m.get("id"): m for m in available_models_data if m.get("id")}
    for m in available_models_data:
        _record_model_limits(m)

    valid_models = []
    for original_m in models:
        model_id = (
            original_m.split("openrouter/", 1)[1] if original_m.startswith("openrouter/") else original_m
        )
        if model_id in model_map:
            model_info = model_map[model_id]
            if is_model_free(model_info):
                valid_models.append(original_m)
            else:
                logger.error(
                    f"Zero-Cost Violation: Model '{original_m}' is a PAID model. "
                    f"4take operates strictly in zero-token-cost mode. Rejecting '{original_m}'."
                )
        else:
            if is_free_model_id(model_id):
                logger.warning(
                    f"Warning: Free model '{original_m}' not found in current OpenRouter catalog. Keeping with warning."
                )
                valid_models.append(original_m)
            else:
                logger.error(
                    f"Zero-Cost Violation: Model '{original_m}' is not a verified free model. "
                    f"4take operates strictly in zero-token-cost mode. Rejecting '{original_m}'."
                )

    return valid_models
