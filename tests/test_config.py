import json
import os
from unittest.mock import mock_open, patch

import pytest

from fourtake.config import load_config


def test_load_config_missing_api_key():
    with patch.dict(os.environ, {}, clear=True), patch("pathlib.Path.exists", return_value=False):
        config = load_config({})
        assert config.providers["openrouter"].api_key is None

def test_load_config_with_api_key():
    with patch.dict(os.environ, {"FOURTAKE_API_KEY": "sk-test"}, clear=True):
        with patch("pathlib.Path.exists", return_value=False):
            config = load_config({"models": "test-model"})
            assert config.providers["openrouter"].api_key == "sk-test"
            assert config.default_models == ["test-model"]

def test_load_config_with_valid_file():
    mock_json = '{"min_responses": 2, "providers": {"openrouter": {"api_key": "file_key"}, "new_prov": {"url": "http://new"}}}'
    with patch.dict(os.environ, {}, clear=True), patch("pathlib.Path.exists", return_value=True):
        with patch("builtins.open", mock_open(read_data=mock_json)):
            config = load_config({})
            assert config.min_responses == 2
            assert config.providers["openrouter"].api_key == "file_key"
            assert "new_prov" in config.providers
            assert config.providers["new_prov"].url == "http://new"


@pytest.mark.parametrize("file_key", ["YOUR_OPENROUTER_API_KEY_HERE", "sk-or-v1-from-the-file"])
def test_the_environment_key_wins_over_the_file(file_key):
    """
    `--init-config` writes a placeholder key, and the file used to win: a user who then set
    OPENROUTER_API_KEY, exactly as the error message told them to, still got the error.
    """
    mock_json = json.dumps({"providers": {"openrouter": {"api_key": file_key}}})
    with patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-or-v1-from-the-env"}, clear=True), \
         patch("pathlib.Path.exists", return_value=True), \
         patch("builtins.open", mock_open(read_data=mock_json)):
        config = load_config({})
    assert config.providers["openrouter"].api_key == "sk-or-v1-from-the-env"

def test_load_config_rejects_invalid_json():
    """
    Silently falling back to defaults changed the models, the threshold and the timeout with
    no indication that the user's file had been ignored.
    """
    from fourtake.config import ConfigError

    with patch("pathlib.Path.exists", return_value=True):
        with patch("builtins.open", mock_open(read_data="{invalid_json")):
            with pytest.raises(ConfigError) as exc:
                load_config({})
    assert "invalid JSON" in str(exc.value)
    assert "config.json" in str(exc.value)


# --------------------------------------------------------------------------------------
# S4.4 — config validation: unknown keys warn, bad values fail with the key and file named
# --------------------------------------------------------------------------------------

def _load_with(tmp_path, monkeypatch, payload):
    import json as _json

    cfg_dir = tmp_path / ".config" / "4take"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "config.json").write_text(_json.dumps(payload))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    return load_config({})


@pytest.mark.parametrize(
    "payload,needle",
    [
        ({"rpm_limit": 0}, "greater than 0"),
        ({"rpm_limit": None}, "positive integer"),
        ({"rpm_limit": "20"}, "positive integer"),
        ({"rpm_limit": True}, "positive integer"),
        ({"min_responses": -1}, "greater than 0"),
        ({"timeout": 0}, "greater than 0"),
        ({"default_models": []}, "must not be empty"),
        ({"default_models": "auto"}, "list of strings"),
        ({"providers": "openrouter"}, "must be an object"),
    ],
)
def test_bad_values_fail_with_a_pointed_message(tmp_path, monkeypatch, payload, needle):
    """These used to raise ZeroDivisionError/TypeError deep in the engine, or pass silently."""
    from fourtake.config import ConfigError

    with pytest.raises(ConfigError) as exc:
        _load_with(tmp_path, monkeypatch, payload)
    message = str(exc.value)
    assert needle in message
    assert "config.json" in message
    assert next(iter(payload)) in message


def test_unknown_keys_warn_but_do_not_fail(tmp_path, monkeypatch, caplog):
    with caplog.at_level("WARNING"):
        config = _load_with(tmp_path, monkeypatch, {"rpm_limit": 15, "typo_key": 1})
    assert config.rpm_limit == 15
    assert "typo_key" in caplog.text


def test_comment_keys_from_init_config_are_not_reported(tmp_path, monkeypatch, caplog):
    with caplog.at_level("WARNING"):
        config = _load_with(tmp_path, monkeypatch, {"//": "notes", "//notice": "x", "timeout": 42})
    assert config.timeout == 42
    assert "//" not in caplog.text


def test_nested_dicts_are_deep_merged(tmp_path, monkeypatch):
    """Setting one entry used to drop every sibling that came from the defaults."""
    from fourtake.config import _deep_merge

    merged = _deep_merge(
        {"model_token_limits": {"a": 1, "b": 2}, "rpm_limit": 10},
        {"model_token_limits": {"b": 99, "c": 3}},
    )
    assert merged["model_token_limits"] == {"a": 1, "b": 99, "c": 3}
    assert merged["rpm_limit"] == 10


def test_the_context_budget_has_a_default_and_a_command_line_override():
    with patch.dict(os.environ, {}, clear=True), patch("pathlib.Path.exists", return_value=False):
        assert load_config({}).context_budget_tokens == 16000
        assert load_config({"context_budget": 4000}).context_budget_tokens == 4000


def test_the_context_budget_in_the_file_is_checked_like_the_other_counts():
    from fourtake.config import ConfigError

    with patch.dict(os.environ, {}, clear=True), patch("pathlib.Path.exists", return_value=True):
        with patch("builtins.open", mock_open(read_data='{"context_budget_tokens": 8000}')):
            assert load_config({}).context_budget_tokens == 8000
        with patch("builtins.open", mock_open(read_data='{"context_budget_tokens": 0}')), \
             pytest.raises(ConfigError, match="context_budget_tokens"):
            load_config({})
