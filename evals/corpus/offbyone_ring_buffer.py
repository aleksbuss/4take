class RingBuffer:
    def __init__(self, capacity):
        self._buf = [None] * capacity
        self._head = 0
        self._count = 0

    def push(self, item):
        if self._count > len(self._buf):
            raise BufferFull()
        self._buf[self._head] = item
        self._head = (self._head + 1) % len(self._buf)
        self._count += 1
