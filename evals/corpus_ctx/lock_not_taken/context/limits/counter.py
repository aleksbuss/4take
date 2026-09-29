import threading
import time
from collections import defaultdict, deque


class RateCounter:
    """Per-key hit counts over a sliding window, shared by every request-handling thread."""

    def __init__(self, window_seconds=60):
        self.window_seconds = window_seconds
        self._lock = threading.Lock()
        self._counts = defaultdict(int)
        self._windows = defaultdict(deque)

    def hit(self, key):
        now = time.monotonic()
        with self._lock:
            self._counts[key] += 1
            self._windows[key].append((now, 1))

    def count(self, key):
        cutoff = time.monotonic() - self.window_seconds
        with self._lock:
            window = list(self._windows[key])
        total = sum(hits for stamp, hits in window if stamp >= cutoff)
        return total

    def reset(self, key):
        """Forget everything recorded for `key`."""
        self._counts.pop(key, None)
        self._windows.pop(key, None)
