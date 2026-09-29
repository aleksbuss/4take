"""
Cross-process file locking for the shared state under ~/.cache/4take.

Two writers of the same file are two processes, not two threads: several agents run 4take
concurrently against one home directory, and the rate limiter is worthless if each of them
keeps a private copy of the budget. The primitives live here rather than inside one writer
so the next one cannot quietly grow a second, subtly different implementation.
"""

import os
import sys
import time
from pathlib import Path

# The files hold no source code, but os.open() would create them with the process umask,
# leaving them world-readable next to the reviews, which are not.
FILE_MODE = 0o600

_LOCK_ATTEMPTS = 50
_LOCK_RETRY_SECONDS = 0.01


def tighten(path: Path) -> None:
    """Best-effort narrowing of a file created before FILE_MODE was enforced."""
    try:
        path.chmod(FILE_MODE)
    except OSError:
        pass


def acquire(path: Path) -> int:
    """
    Open `path` and hold an exclusive lock on it. Returns the file descriptor.

    The caller is responsible for `release`, and must not sleep while holding the lock: the
    point of the lock is that other processes keep accounting while this one waits.
    """
    fd = os.open(path, os.O_RDWR | os.O_CREAT, FILE_MODE)
    tighten(path)
    for _ in range(_LOCK_ATTEMPTS):
        try:
            _lock(fd)
            return fd
        except OSError:
            time.sleep(_LOCK_RETRY_SECONDS)
    os.close(fd)
    raise TimeoutError(f"Failed to acquire lock on {path} after {_LOCK_ATTEMPTS} retries")


def release(fd: int) -> None:
    """
    Release the lock and close the descriptor.

    Never raises. It is always called from a `finally`, where an exception of its own would
    replace the one the caller was already handling — and `os.close` on a descriptor that is
    somehow already closed raises `OSError: Bad file descriptor`, so the close needs the
    same guard the unlock has.
    """
    try:
        _unlock(fd)
    except OSError:
        pass
    try:
        os.close(fd)
    except OSError:
        pass


# The platform is checked per call rather than bound at import. CI runs on Linux only, so
# binding at import would make the Windows branch unreachable from any test, and locking is
# not on a hot path — a bucket acquire already crosses a thread boundary.
def _lock(fd: int) -> None:
    if sys.platform == "win32":
        import msvcrt

        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(fd: int) -> None:
    if sys.platform == "win32":
        import msvcrt

        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_UN)
