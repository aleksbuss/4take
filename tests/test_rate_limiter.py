import time
from unittest.mock import AsyncMock, patch

import pytest

from fourtake.rate_limiter import PersistentTokenBucket


@pytest.mark.asyncio
async def test_persistent_token_bucket(tmp_path):
    bucket = PersistentTokenBucket(rpm=60)
    bucket.state_file = tmp_path / "bucket.json"

    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        await bucket.acquire(1)
        assert mock_sleep.call_count == 0


@pytest.mark.asyncio
async def test_persistent_token_bucket_win32(tmp_path):
    import sys
    from unittest.mock import MagicMock

    bucket = PersistentTokenBucket(rpm=60)
    bucket.state_file = tmp_path / "bucket.json"

    with patch.object(sys, "platform", "win32"):
        import sys as sys_mod

        sys_mod.modules["msvcrt"] = MagicMock()
        try:
            with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                await bucket.acquire(1)
                assert mock_sleep.call_count == 0
        finally:
            del sys_mod.modules["msvcrt"]


@pytest.mark.asyncio
async def test_persistent_token_bucket_win32_lock_fail(tmp_path):
    import sys
    from unittest.mock import MagicMock

    bucket = PersistentTokenBucket(rpm=60)
    bucket.state_file = tmp_path / "bucket.json"

    with patch.object(sys, "platform", "win32"):
        import sys as sys_mod

        msvcrt_mock = MagicMock()
        msvcrt_mock.locking.side_effect = OSError("Lock failed")
        sys_mod.modules["msvcrt"] = msvcrt_mock
        try:
            with patch("time.sleep"), pytest.raises(TimeoutError):
                await bucket.acquire(1)
        finally:
            del sys_mod.modules["msvcrt"]


@pytest.mark.posix_only("fcntl: Windows locks with msvcrt, which these tests cover on every platform")
@pytest.mark.asyncio
async def test_persistent_token_bucket_unix_lock_fail(tmp_path):
    bucket = PersistentTokenBucket(rpm=60)
    bucket.state_file = tmp_path / "bucket.json"

    with patch("fcntl.flock", side_effect=OSError("Lock failed")), patch("time.sleep"):
        with pytest.raises(TimeoutError):
            await bucket.acquire(1)


@pytest.mark.asyncio
async def test_persistent_token_bucket_corrupt_state(tmp_path):
    bucket = PersistentTokenBucket(rpm=60)
    bucket.state_file = tmp_path / "bucket.json"

    # Empty file
    bucket.state_file.write_text("")
    await bucket.acquire(1)

    # Corrupt file
    bucket.state_file.write_text("{bad json")
    await bucket.acquire(1)


@pytest.mark.asyncio
async def test_persistent_token_bucket_time_travel(tmp_path):
    bucket = PersistentTokenBucket(rpm=60)
    bucket.state_file = tmp_path / "bucket.json"

    # Write future time
    import json

    bucket.state_file.write_text(json.dumps({"tokens": 10, "last_update": time.time() + 1000, "rpm": 60}))

    await bucket.acquire(1)


@pytest.mark.asyncio
async def test_a_wait_is_logged_for_debugging_and_counted_but_not_announced(tmp_path, caplog):
    """
    A first review printed fourteen "Rate limit: waiting Ns..." lines (live, 2026-10-27, 254 s).
    Each wait is now a debug line, and the limiter keeps the count and the longest for a summary.
    """
    bucket = PersistentTokenBucket(rpm=60)
    bucket.state_file = tmp_path / "bucket.json"

    with patch("asyncio.sleep", new_callable=AsyncMock), caplog.at_level("DEBUG", logger="4take"):
        for _ in range(63):  # the burst of 60, then three waits
            await bucket.acquire(1)

    waits = [r for r in caplog.records if "Rate limit: waiting" in r.getMessage()]
    assert len(waits) == 3
    assert {r.levelname for r in waits} == {"DEBUG"}
    assert bucket.pauses == 3
    assert 0 < bucket.longest_pause <= 3.0


def test_the_pause_summary_is_one_line_and_absent_when_nothing_waited(tmp_path):
    bucket = PersistentTokenBucket(rpm=10)
    assert bucket.pause_summary() is None

    bucket.pauses, bucket.longest_pause = 1, 4.2
    assert (
        bucket.pause_summary()
        == "[4take] Paused once (longest 4s) to stay under 4take's own limit of 10 requests a minute."
    )

    bucket.pauses, bucket.longest_pause = 14, 19.9
    line = bucket.pause_summary()
    assert "\n" not in line
    assert (
        line
        == "[4take] Paused 14 times (longest 20s) to stay under 4take's own limit of 10 requests a minute."
    )


@pytest.mark.asyncio
async def test_persistent_token_bucket_waits_once_the_burst_is_spent(tmp_path):
    bucket = PersistentTokenBucket(rpm=60)
    bucket.state_file = tmp_path / "bucket.json"

    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        for _ in range(60):  # spend the burst allowance
            await bucket.acquire(1)
        assert mock_sleep.call_count == 0
        await bucket.acquire(1)
        assert mock_sleep.call_count == 1


def test_release_lock_sync_error_handling():
    """A lock that fails to release is swallowed on win32 (msvcrt); runs on every platform."""
    import sys
    from unittest.mock import MagicMock

    from fourtake.rate_limiter import _release_lock_sync

    with patch("sys.platform", "win32"), patch("os.close"), patch("os.lseek"):
        msvcrt_mock = MagicMock()
        msvcrt_mock.locking.side_effect = OSError("Unlock error")
        with patch.dict(sys.modules, {"msvcrt": msvcrt_mock}):
            _release_lock_sync(999)


@pytest.mark.posix_only("fcntl: Windows locks with msvcrt, which these tests cover on every platform")
def test_release_lock_sync_error_handling_with_flock():
    from fourtake.rate_limiter import _release_lock_sync

    with (
        patch("sys.platform", "darwin"),
        patch("os.close"),
        patch("fcntl.flock", side_effect=OSError("Flock error")),
    ):
        _release_lock_sync(999)


@pytest.mark.posix_only("file modes: Windows reports 0o666/0o777 and the profile's ACL guards the file")
def test_state_file_is_not_world_readable(tmp_path):
    """
    The review cache was tightened to 0600, but the bucket state beside it was still created
    with the process umask — 0644 plus a stray exec bit from O_CREAT's 0777 default.
    """
    import asyncio
    import stat

    bucket = PersistentTokenBucket(rpm=20)
    # conftest redirects state_file; point it at a fresh path so creation is exercised.
    bucket.state_file = tmp_path / "fresh-bucket.json"
    asyncio.run(bucket.acquire(1))

    assert bucket.state_file.exists()
    assert stat.S_IMODE(bucket.state_file.stat().st_mode) == 0o600


@pytest.mark.posix_only("file modes: Windows reports 0o666/0o777 and the profile's ACL guards the file")
def test_pre_existing_state_file_is_tightened(tmp_path):
    """os.open's mode argument applies only on creation, so existing files need a chmod."""
    import asyncio
    import stat

    state = tmp_path / "loose-bucket.json"
    state.write_text("{}")
    state.chmod(0o755)

    bucket = PersistentTokenBucket(rpm=20)
    bucket.state_file = state
    asyncio.run(bucket.acquire(1))

    assert stat.S_IMODE(state.stat().st_mode) == 0o600
