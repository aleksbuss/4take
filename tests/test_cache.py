import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from fourtake.cache import (
    clear_cache,
    compute_cache_key,
    get_cache_dir,
    get_cached_response,
    save_cached_response,
)
from fourtake.client import ModelResponse


def test_compute_cache_key_deterministic():
    k1 = compute_cache_key("def foo(): pass", "openrouter/model-a", "system prompt")
    k2 = compute_cache_key("def foo(): pass", "openrouter/model-a", "system prompt")
    k3 = compute_cache_key("def bar(): pass", "openrouter/model-a", "system prompt")
    assert k1 == k2
    assert k1 != k3
    assert len(k1) == 64  # sha256 hex


def test_get_cache_dir_creation(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    # Call the original get_cache_dir
    import fourtake.cache

    real_fn = fourtake.cache.__dict__.get("_orig_get_cache_dir", get_cache_dir)
    # If monkeypatched in conftest, invoke directly with temporary home
    cache_dir = real_fn()
    assert cache_dir.exists()
    assert "reviews" in str(cache_dir)


def test_cache_save_and_get(tmp_path, monkeypatch):
    monkeypatch.setattr("fourtake.cache.get_cache_dir", lambda: tmp_path)

    input_text = "def calculate(x): return x * 2"
    model = "openrouter/test-model"
    prompt = "Review this code"

    # Initially empty
    assert get_cached_response(input_text, model, prompt) is None

    # Save response
    resp = ModelResponse(
        model=model,
        content="Looks clean and correct.",
        error=None,
        latency_ms=1200,
    )
    save_cached_response(input_text, model, resp, prompt)

    # Retrieve response
    cached = get_cached_response(input_text, model, prompt)
    assert cached is not None
    assert cached.model == model
    assert cached.content == "Looks clean and correct."
    assert cached.latency_ms == 0
    assert cached.error is None


def test_cache_save_skipped_on_error_or_empty():
    resp_err = ModelResponse(model="m", content="some content", error="Error message", latency_ms=100)
    save_cached_response("code", "m", resp_err)
    assert get_cached_response("code", "m") is None

    resp_empty = ModelResponse(model="m", content=None, error=None, latency_ms=100)
    save_cached_response("code", "m", resp_empty)
    assert get_cached_response("code", "m") is None


def test_cache_expiration(tmp_path, monkeypatch):
    monkeypatch.setattr("fourtake.cache.get_cache_dir", lambda: tmp_path)

    input_text = "x = 1"
    model = "test-model"

    resp = ModelResponse(model=model, content="Short snippet", error=None, latency_ms=500)
    save_cached_response(input_text, model, resp)

    # Valid with 10s TTL
    assert get_cached_response(input_text, model, ttl_seconds=10) is not None

    # Expired with negative TTL
    assert get_cached_response(input_text, model, ttl_seconds=-1) is None


def test_cache_corrupt_file_handling(tmp_path, monkeypatch):
    monkeypatch.setattr("fourtake.cache.get_cache_dir", lambda: tmp_path)
    key = compute_cache_key("bad_code", "m", "")
    corrupt_file = tmp_path / f"{key}.json"
    corrupt_file.write_text("NOT_JSON_DATA{{{")

    assert get_cached_response("bad_code", "m", "") is None


def test_cache_empty_content_in_file(tmp_path, monkeypatch):
    monkeypatch.setattr("fourtake.cache.get_cache_dir", lambda: tmp_path)
    key = compute_cache_key("code", "m", "")
    cache_file = tmp_path / f"{key}.json"
    cache_file.write_text(json.dumps({"timestamp": time.time(), "content": ""}))

    assert get_cached_response("code", "m", "") is None


def test_cache_expiration_unlink_oserror(tmp_path, monkeypatch):
    monkeypatch.setattr("fourtake.cache.get_cache_dir", lambda: tmp_path)
    resp = ModelResponse(model="m", content="content", error=None, latency_ms=100)
    save_cached_response("code", "m", resp)

    with patch.object(Path, "unlink", side_effect=OSError("Permission denied")):
        assert get_cached_response("code", "m", ttl_seconds=-1) is None


def test_cache_save_write_error(tmp_path, monkeypatch):
    monkeypatch.setattr("fourtake.cache.get_cache_dir", lambda: tmp_path)
    resp = ModelResponse(model="m", content="content", error=None, latency_ms=100)

    with patch("os.replace", side_effect=OSError("Disk full")):
        save_cached_response("code", "m", resp)
        # Verify it handled exception gracefully


def test_clear_cache(tmp_path, monkeypatch):
    monkeypatch.setattr("fourtake.cache.get_cache_dir", lambda: tmp_path)

    input_text1 = "code1"
    input_text2 = "code2"

    resp = ModelResponse(model="m", content="c", error=None, latency_ms=100)
    save_cached_response(input_text1, "m", resp)
    save_cached_response(input_text2, "m", resp)

    assert len(list(tmp_path.glob("*.json"))) == 2
    cleared = clear_cache()
    assert cleared == 2
    assert len(list(tmp_path.glob("*.json"))) == 0


def test_clear_cache_unlink_error(tmp_path, monkeypatch):
    monkeypatch.setattr("fourtake.cache.get_cache_dir", lambda: tmp_path)
    resp = ModelResponse(model="m", content="c", error=None, latency_ms=100)
    save_cached_response("code", "m", resp)

    with patch.object(Path, "unlink", side_effect=OSError("Busy")):
        cleared = clear_cache()
        assert cleared == 0


# --------------------------------------------------------------------------------------
# S4.3 — cached reviews contain source code: tighten permissions and bound growth
# --------------------------------------------------------------------------------------

import stat as _stat


@pytest.mark.posix_only("file modes: Windows reports 0o666/0o777 and the profile's ACL guards the file")
def test_cache_files_are_not_world_readable(monkeypatch, tmp_path):
    import fourtake.cache as cache_mod
    from fourtake.client import ModelResponse

    # conftest replaces get_cache_dir with a plain tmp dir; the permission logic lives in
    # the real one, so restore it and point HOME at the tmp tree instead.
    monkeypatch.setattr(cache_mod, "get_cache_dir", cache_mod._orig_get_cache_dir)
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    cache_dir = cache_mod.get_cache_dir()
    cache_mod.save_cached_response("secret source", "m:free", ModelResponse("m:free", "review", None, 1))

    entry = next(cache_dir.glob("*.json"))
    assert _stat.S_IMODE(entry.stat().st_mode) == 0o600
    # mode= on mkdir only applies to the final component; the parent was left at 0755.
    assert _stat.S_IMODE(cache_dir.stat().st_mode) == 0o700
    assert _stat.S_IMODE(cache_dir.parent.stat().st_mode) == 0o700


def test_cache_evicts_oldest_entries_when_over_the_entry_cap(monkeypatch, tmp_path):
    """
    TTL is only checked on read, and the key is a content hash, so an expired entry is
    almost never looked up again: without a cap the directory grew forever.
    """
    import fourtake.cache as cache_mod
    from fourtake.client import ModelResponse

    monkeypatch.setattr(cache_mod, "get_cache_dir", cache_mod._orig_get_cache_dir)
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setattr(cache_mod, "MAX_CACHE_ENTRIES", 5)
    cache_dir = cache_mod.get_cache_dir()

    for i in range(25):
        cache_mod.save_cached_response(f"code-{i}", "m:free", ModelResponse("m:free", "r", None, 1))

    assert len(list(cache_dir.glob("*.json"))) <= 5


@pytest.mark.posix_only("file modes: Windows reports 0o666/0o777 and the profile's ACL guards the file")
def test_pre_existing_cache_entries_are_tightened_once(monkeypatch, tmp_path):
    """
    Entries written before the permission fix keep their old mode, and they contain the
    reviewed source. Swept once per process rather than on every save.
    """
    import fourtake.cache as cache_mod

    monkeypatch.setattr(cache_mod, "get_cache_dir", cache_mod._orig_get_cache_dir)
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setattr(cache_mod, "_tightened_once", False)

    reviews = tmp_path / ".cache" / "4take" / "reviews"
    reviews.mkdir(parents=True)
    stale = reviews / "deadbeef.json"
    stale.write_text("{}")
    stale.chmod(0o644)

    cache_mod.get_cache_dir()
    assert _stat.S_IMODE(stale.stat().st_mode) == 0o600


def test_a_key_without_context_is_the_key_it_always_was():
    """Entries written before context existed must still be found."""
    import hashlib

    from fourtake.cache import compute_cache_key

    old = hashlib.sha256()
    for i, part in enumerate((b"code", b"m", b"prompt")):
        if i:
            old.update(b"\x00")
        old.update(part)
    assert compute_cache_key("code", "m", "prompt") == old.hexdigest()
    assert compute_cache_key("code", "m", "prompt", context_digest="") == old.hexdigest()


def test_the_same_code_with_other_context_is_another_review():
    from fourtake.cache import compute_cache_key

    plain = compute_cache_key("code", "m", "prompt")
    one = compute_cache_key("code", "m", "prompt", context_digest="d1")
    two = compute_cache_key("code", "m", "prompt", context_digest="d2")
    assert len({plain, one, two}) == 3
