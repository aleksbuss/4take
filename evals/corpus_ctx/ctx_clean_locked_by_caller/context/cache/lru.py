import threading
from collections import OrderedDict


class LRUCache:
    """Thread-safe LRU cache. Public methods hold self._lock; private helpers expect it held."""

    def __init__(self, capacity):
        self.capacity = capacity
        self._lock = threading.Lock()
        self._items = OrderedDict()

    def put(self, key, value):
        with self._lock:
            self._items[key] = value
            self._items.move_to_end(key)
            if len(self._items) > self.capacity:
                self._evict_oldest()

    def get(self, key):
        with self._lock:
            if key not in self._items:
                return None
            self._items.move_to_end(key)
            return self._items[key]

    def _evict_oldest(self):
        """Drop the least recently used entry."""
        if self._items:
            self._items.popitem(last=False)
