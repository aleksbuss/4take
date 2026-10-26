import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("4take")


class ConfigError(ValueError):
    """A config file value that cannot be used. Raised with the key and file path."""


# Known top-level keys and how to check them. Unknown keys are warned about and ignored;
# a known key with an unusable value is a hard error, because silently falling back to a
# default hides a typo that changes behaviour.
_POSITIVE_INT_KEYS = ("rpm_limit", "min_responses", "timeout", "context_budget_tokens")

# How much reference context one review may carry. Several callers and a type or two, not a
# subsystem: the reviewers are free models, and a change buried in its surroundings is reviewed
# worse, not better.
DEFAULT_CONTEXT_BUDGET_TOKENS = 16000
_DICT_KEYS = ("providers", "provider_concurrency", "model_token_limits")
_BOOL_KEYS = ("telemetry",)


def _validate_positive_int(key: str, value: object, path: Path) -> int:
    # bool is an int subclass, and `true` in JSON almost certainly means a mistake here.
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(
            f"{path}: '{key}' must be a positive integer, got {type(value).__name__} ({value!r})"
        )
    if value <= 0:
        raise ConfigError(f"{path}: '{key}' must be greater than 0, got {value}")
    return value


def _validate_bool(key: str, value: object, path: Path) -> bool:
    # "false" and 0 are the two ways people try to turn a flag off in JSON; both are a
    # mistake worth naming rather than a value worth guessing at.
    if not isinstance(value, bool):
        raise ConfigError(f"{path}: '{key}' must be true or false, got {type(value).__name__} ({value!r})")
    return value


def _deep_merge(base: dict, incoming: dict) -> dict:
    """
    Merge nested dicts instead of replacing them.

    A shallow update meant that setting one entry of `model_token_limits` silently dropped
    every built-in entry beside it.
    """
    merged = dict(base)
    for key, value in incoming.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def validate_file_config(file_config: dict, defaults: dict, path: Path) -> dict:
    """Check a parsed config file and deep-merge it onto the defaults."""
    if not isinstance(file_config, dict):
        raise ConfigError(f"{path}: top level must be a JSON object")

    clean: dict = {}
    for key, value in file_config.items():
        if key.startswith("//"):  # comment keys written by --init-config
            continue
        if key not in defaults:
            logger.warning(f"{path}: ignoring unknown config key '{key}'")
            continue
        if key in _POSITIVE_INT_KEYS:
            clean[key] = _validate_positive_int(key, value, path)
        elif key in _BOOL_KEYS:
            clean[key] = _validate_bool(key, value, path)
        elif key in _DICT_KEYS:
            if not isinstance(value, dict):
                raise ConfigError(f"{path}: '{key}' must be an object, got {type(value).__name__}")
            clean[key] = value
        elif key == "default_models":
            if not isinstance(value, list) or not all(isinstance(m, str) for m in value):
                raise ConfigError(f"{path}: 'default_models' must be a list of strings")
            if not value:
                raise ConfigError(f"{path}: 'default_models' must not be empty")
            clean[key] = value
        else:
            clean[key] = value

    return _deep_merge(defaults, clean)


@dataclass
class ProviderConfig:
    url: str
    api_key: str | None


@dataclass
class FourtakeConfig:
    providers: dict[str, ProviderConfig]
    default_models: list[str]
    rpm_limit: int
    min_responses: int
    timeout: int
    provider_concurrency: dict[str, int]
    model_token_limits: dict[str, int] = field(default_factory=dict)
    # Local, code-free record of how each model performed, used to rank the pool. See
    # telemetry.py for exactly what is stored.
    telemetry: bool = True
    context_budget_tokens: int = DEFAULT_CONTEXT_BUDGET_TOKENS


def load_config(cli_overrides: dict) -> FourtakeConfig:
    config_path = Path.home() / ".config" / "4take" / "config.json"

    # Default values
    config_data = {
        "providers": {},
        "default_models": ["auto"],
        "rpm_limit": 10,
        "min_responses": 4,
        "timeout": 600,
        # No cap by default: the council limits itself to the models it plans to launch. A default
        # of 4 held back two of the six launched for four seats, the spares meant to race stragglers.
        "provider_concurrency": {},
        # Per-model context OVERRIDES, empty by default. Real context windows come from the
        # OpenRouter catalog at discovery time. This used to be pre-seeded with a flat 32000
        # for four models, which — once the catalog values started being used — silently
        # overrode them and kept capping 256k-context models at 32k.
        # Set an entry here only to *reduce* a model whose advertised window is not honoured.
        "model_token_limits": {},
        "telemetry": True,
        "context_budget_tokens": DEFAULT_CONTEXT_BUDGET_TOKENS,
    }

    if config_path.exists():
        try:
            with open(config_path, encoding="utf-8") as f:
                file_config = json.load(f)
        except json.JSONDecodeError as e:
            raise ConfigError(f"{config_path}: invalid JSON — {e}") from e
        except OSError as e:
            logger.warning(f"Failed to read config from {config_path}: {e}")
            file_config = None
        if file_config is not None:
            config_data = validate_file_config(file_config, config_data, config_path)

    DEFAULT_PROVIDERS = {"openrouter": "https://openrouter.ai/api/v1/chat/completions"}

    providers = {}
    for name, url in DEFAULT_PROVIDERS.items():
        env_key = f"{name.upper()}_API_KEY"
        api_key = os.environ.get(env_key)
        if name == "openrouter" and not api_key:
            api_key = os.environ.get("FOURTAKE_API_KEY")
        providers[name] = ProviderConfig(url=url, api_key=api_key)

    if "providers" in config_data:
        for name, data in config_data["providers"].items():
            if name in providers:
                providers[name].url = data.get("url", providers[name].url)
                # The environment wins over the file. The other way round, the placeholder that
                # `--init-config` writes shadowed a real OPENROUTER_API_KEY, and the error then
                # told the user to set the very variable they had already set.
                if "api_key" in data and not providers[name].api_key:
                    providers[name].api_key = data["api_key"]
            else:
                providers[name] = ProviderConfig(url=data.get("url", ""), api_key=data.get("api_key"))

    models_str = cli_overrides.get("models")
    if models_str:
        default_models = [m.strip() for m in models_str.split(",") if m.strip()]
    else:
        default_models = config_data["default_models"]

    min_responses = cli_overrides.get("min_responses")
    if min_responses is None:
        min_responses = config_data["min_responses"]

    timeout = cli_overrides.get("timeout")
    if timeout is None:
        timeout = config_data.get("timeout", 600)

    provider_concurrency = config_data.get("provider_concurrency", {})

    return FourtakeConfig(
        providers=providers,
        default_models=default_models,
        rpm_limit=config_data["rpm_limit"],
        min_responses=min_responses,
        timeout=timeout,
        provider_concurrency=provider_concurrency,
        model_token_limits=config_data.get("model_token_limits", {}),
        telemetry=(False if cli_overrides.get("no_telemetry") else bool(config_data.get("telemetry", True))),
        context_budget_tokens=(cli_overrides.get("context_budget") or config_data["context_budget_tokens"]),
    )
