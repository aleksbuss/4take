import threading

class HitCounter:
    def __init__(self):
        self.counts = {}
        self.lock = threading.Lock()

    def hit(self, key):
        if key not in self.counts:
            with self.lock:
                self.counts[key] = 0
        self.counts[key] += 1
        return self.counts[key]
