import hashlib
import json
import logging
import os
import time
from pathlib import Path

from .client import ModelResponse

logger = logging.getLogger("4take")

CACHE_DIR_MODE = 0o700
CACHE_FILE_MODE = 0o600
DEFAULT_TTL_SECONDS = 86400  # 24 hours

# Entries are keyed by a content hash, so an expired one is almost never looked up again and
# lazy TTL alone never reclaims anything: the directory grew without bound. These caps are
# enforced on write.
MAX_CACHE_ENTRIES = 2000
MAX_CACHE_BYTES = 200 * 1024 * 1024


_tightened_once = False


def _tighten_existing_entries(cache_dir: Path) -> int:
    """
    One-time sweep over entries written before permissions were tightened.

    New entries are created 0600, but files already on disk keep the mode they were written
    with — and they hold the reviewed source. Runs once per process, not on every save.
    """
    fixed = 0
    for entry in cache_dir.glob("*.json"):
        try:
            if entry.stat().st_mode & 0o077:
                entry.chmod(CACHE_FILE_MODE)
                fixed += 1
        except OSError:
            pass
    if fixed:
        logger.debug(f"Tightened permissions on {fixed} pre-existing cache entr(ies)")
    return fixed


def get_cache_dir() -> Path:
    global _tightened_once

    cache_dir = Path.home() / ".cache" / "4take" / "reviews"
    # mode= applies only to the final component; parents are created with the process umask,
    # so tighten them explicitly. Cached entries contain the reviewed source.
    cache_dir.mkdir(parents=True, exist_ok=True, mode=CACHE_DIR_MODE)
    for path in (cache_dir, cache_dir.parent):
        try:
            path.chmod(CACHE_DIR_MODE)
        except OSError:
            pass

    if not _tightened_once:
        _tightened_once = True
        _tighten_existing_entries(cache_dir)

    return cache_dir


def _evict_if_over_budget(cache_dir: Path) -> int:
    """Drop the least recently modified entries until the cache is back inside its caps."""
    try:
        entries = [(f, f.stat()) for f in cache_dir.glob("*.json")]
    except OSError:
        return 0

    total_bytes = sum(st.st_size for _, st in entries)
    if len(entries) <= MAX_CACHE_ENTRIES and total_bytes <= MAX_CACHE_BYTES:
        return 0

    entries.sort(key=lambda pair: pair[1].st_mtime)
    removed = 0
    for path, st in entries:
        if len(entries) - removed <= MAX_CACHE_ENTRIES and total_bytes <= MAX_CACHE_BYTES:
            break
        try:
            path.unlink()
            total_bytes -= st.st_size
            removed += 1
        except OSError:
            pass
    if removed:
        logger.debug(f"Evicted {removed} cache entr(ies) to stay within budget")
    return removed


def compute_cache_key(
    task_input: str, model_id: str, system_prompt: str = "", context_digest: str = ""
) -> str:
    hasher = hashlib.sha256()
    hasher.update(task_input.strip().encode("utf-8"))
    hasher.update(b"\x00")
    hasher.update(model_id.strip().encode("utf-8"))
    hasher.update(b"\x00")
    hasher.update(system_prompt.strip().encode("utf-8"))
    # Only when there is context: the same code reviewed against other context is another review,
    # while every key written without context stays valid.
    if context_digest:
        hasher.update(b"\x00")
        hasher.update(context_digest.encode("utf-8"))
    return hasher.hexdigest()


def get_cached_response(
    task_input: str,
    model_id: str,
    system_prompt: str = "",
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    context_digest: str = "",
) -> ModelResponse | None:
    cache_key = compute_cache_key(task_input, model_id, system_prompt, context_digest)
    cache_file = get_cache_dir() / f"{cache_key}.json"

    if not cache_file.exists():
        return None

    try:
        with open(cache_file, encoding="utf-8") as f:
            data = json.load(f)

        cached_time = data.get("timestamp", 0)
        if time.time() - cached_time > ttl_seconds:
            # Expired
            try:
                cache_file.unlink(missing_ok=True)
            except OSError:
                pass
            return None

        content = data.get("content")
        if not content:
            return None

        return ModelResponse(
            model=model_id,
            content=content,
            error=None,
            latency_ms=0,  # 0ms cache hit
            from_cache=True,
            # Provenance and completeness must survive the cache. Without them a truncated
            # review came back looking complete, and seat de-duplication had nothing to key
            # on, so a cache hit silently undid both guarantees.
            served_model=data.get("served_model"),
            served_provider=data.get("served_provider"),
            finish_reason=data.get("finish_reason"),
            truncated=bool(data.get("truncated", False)),
        )
    except Exception as e:
        logger.debug(f"Cache read error for key {cache_key}: {e}")
        return None


def save_cached_response(
    task_input: str, model_id: str, response: ModelResponse, system_prompt: str = "", context_digest: str = ""
) -> None:
    if response.error is not None or not response.content:
        return

    cache_key = compute_cache_key(task_input, model_id, system_prompt, context_digest)
    cache_file = get_cache_dir() / f"{cache_key}.json"

    data = {
        "model": model_id,
        "content": response.content,
        "timestamp": time.time(),
        "original_latency_ms": response.latency_ms,
        "served_model": response.served_model,
        "served_provider": response.served_provider,
        "finish_reason": response.finish_reason,
        "truncated": response.truncated,
    }

    try:
        temp_file = cache_file.with_suffix(".tmp")
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        # 0600 before the rename: the file holds the reviewed source and the critique, and
        # the default umask would leave it world-readable on a shared machine.
        os.chmod(temp_file, CACHE_FILE_MODE)
        os.replace(temp_file, cache_file)
        _evict_if_over_budget(cache_file.parent)
    except Exception as e:
        logger.debug(f"Cache write error for key {cache_key}: {e}")


def clear_cache() -> int:
    cache_dir = get_cache_dir()
    count = 0
    for f in cache_dir.glob("*.json"):
        try:
            f.unlink()
            count += 1
        except OSError:
            pass
    return count
