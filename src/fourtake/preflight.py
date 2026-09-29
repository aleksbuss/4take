"""
Whether a council can run, and on which models — decided once for both front ends.

The CLI and the MCP server each carried their own copy of this, and the copies drifted three
ways at once: the MCP server sized auto-discovery with no room left for the reply, silently
swapped in the hard-coded fallback pool when every model the user had configured was
rejected, and never checked for an API key, so an agent without one spent a whole council's
requests to learn "Invalid API key".
"""

from __future__ import annotations

from dataclasses import dataclass

from . import discovery
from .config import FourtakeConfig
from .engine import MAX_OUTPUT_TOKENS_CEILING, PROMPT_OVERHEAD_TOKENS, filter_models_by_context

# How many models are seated when the pool is discovered rather than configured.
COUNCIL_SIZE = 4

# What `--init-config` writes into the template. A user who ran it and did not edit the file
# has no key, and reporting that as an authentication failure sends them looking in the wrong
# place entirely.
CONFIG_KEY_PLACEHOLDER = "YOUR_OPENROUTER_API_KEY_HERE"


def missing_api_key(config: FourtakeConfig) -> str | None:
    """
    Why the council cannot authenticate, or None when it can.

    Without this check the commonest first-run mistake cost 52 seconds and twelve requests
    against the shared rate limit, and the message was "Invalid API key" — true of the empty
    string, and useless to someone who never set one.
    """
    provider = config.providers.get("openrouter")
    key = (provider.api_key or "").strip() if provider else ""
    if key and key != CONFIG_KEY_PLACEHOLDER:
        return None
    what = "is still the placeholder" if key == CONFIG_KEY_PLACEHOLDER else "is not set"
    return (
        f"no OpenRouter API key ({what}). Set OPENROUTER_API_KEY in the environment, "
        f"or put it in ~/.config/4take/config.json — run `4take --init-config` to create one. "
        f"Keys are free at https://openrouter.ai/keys"
    )


class NoUsableModels(Exception):
    """No model can run this review. The message says why, for either front end to show."""


@dataclass
class ModelChoice:
    models: list[str]
    active_count: int
    # Dropped because the input does not fit them, with the input tokens each could take.
    skipped: list[tuple[str, int]]
    # Discovery found nothing, so the hard-coded fallback pool is in use.
    used_fallback: bool = False


async def choose_models(
    config: FourtakeConfig, input_tokens: int, review_tokens: int | None = None
) -> ModelChoice:
    """
    The pool a council runs on: discovered or validated, then cut to what can hold the input.

    `input_tokens` is everything sent, reference context included; `review_tokens` is the code the
    reply is about, when that is less. Raises NoUsableModels when nothing is left. Configured
    models are the user's choice, so when every one of them is rejected that is an error to
    report, not a cue to review the code with models the user never picked.
    """
    used_fallback = False
    if config.default_models == ["auto"]:
        # Room for the reply and the prompt scaffolding too, or a model whose window exactly
        # fits the input is selected and then fails.
        required_context = input_tokens + MAX_OUTPUT_TOKENS_CEILING + PROMPT_OVERHEAD_TOKENS
        # Called through the module so the discovery functions can be replaced in one place.
        models = await discovery.get_best_free_models(
            min_context=required_context, count=COUNCIL_SIZE, pool_size=COUNCIL_SIZE * 3
        )
        if not models:
            models, used_fallback = list(discovery.FALLBACK_MODELS), True
        active_count = COUNCIL_SIZE
    else:
        models = await discovery.validate_configured_models(config.default_models)
        if not models:
            raise NoUsableModels(
                "None of the configured OpenRouter models are valid free models. 4take operates "
                "strictly in zero-token-cost mode (only 100% free models are permitted)."
            )
        active_count = len(models)

    usable, skipped = filter_models_by_context(models, input_tokens, config.model_token_limits, review_tokens)
    if not usable:
        roomiest = max((capacity for _, capacity in skipped), default=0)
        raise NoUsableModels(
            f"Input is {input_tokens} tokens, which exceeds the usable context of every available "
            f"free model: the roomiest accepts {roomiest} after reserving room for the reply. "
            f"Send less, or configure a model with a larger context window."
        )
    return ModelChoice(usable, min(active_count, len(usable)), skipped, used_fallback)
