import asyncio
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path

# Shared with the telemetry writer; see filelock.py for why they are not defined here. The
# aliases keep the historical names this module's call sites and tests already use.
from .filelock import acquire as _acquire_lock_sync
from .filelock import release as _release_lock_sync

logger = logging.getLogger("4take")


@dataclass
class BucketState:
    tokens: float
    last_update: float
    rpm: int
    # Absolute time before which the next request must not be sent. Serialisation is expressed
    # as a reservation in TIME rather than as negative tokens: clamping the token balance at a
    # floor made every waiter past that floor compute the same delay and wake together —
    # exactly the burst being prevented, just moved further down the queue.
    next_available: float = 0.0


class PersistentTokenBucket:
    def __init__(self, rpm: int = 10):
        self.rpm = rpm
        self.state_file = Path.home() / ".cache" / "4take" / "bucket.json"
        self.state_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # What this process waited, for one summary line. Several seats wait at once, so the
        # waits do not add up to elapsed time and only their count and the longest are kept.
        self.pauses = 0
        self.longest_pause = 0.0

    def pause_summary(self) -> str | None:
        """One line saying the limiter held requests back, or None when it never did."""
        if not self.pauses:
            return None
        times = "once" if self.pauses == 1 else f"{self.pauses} times"
        return (
            f"[4take] Paused {times} (longest {self.longest_pause:.0f}s) to stay under "
            f"4take's own limit of {self.rpm} requests a minute."
        )

    def _sync_acquire_tokens(self, tokens: int, max_wait: float | None = None) -> float:
        import os

        fd = _acquire_lock_sync(self.state_file)
        wait_time = 0.0
        try:
            # Read state
            try:
                os.lseek(fd, 0, os.SEEK_SET)
                content = os.read(fd, 4096).decode("utf-8").strip("\x00").strip()
                if not content:
                    raise ValueError("Empty file")
                data = json.loads(content)
                state = BucketState(
                    tokens=float(data.get("tokens", self.rpm)),
                    last_update=float(data.get("last_update", time.time())),
                    rpm=int(data.get("rpm", self.rpm)),
                    next_available=float(data.get("next_available", 0.0)),
                )
            except Exception:
                state = BucketState(tokens=float(self.rpm), last_update=time.time(), rpm=self.rpm)

            # Refill
            now = time.time()
            elapsed = now - state.last_update
            if elapsed < 0:
                elapsed = 0
            added_tokens = elapsed * (self.rpm / 60.0)
            state.tokens = min(float(self.rpm), state.tokens + added_tokens)
            state.last_update = now
            state.rpm = self.rpm

            # A token bucket expressed as a time reservation (the GCRA formulation).
            #
            # `next_available` is allowed to lag behind `now` by up to one full bucket, which
            # is what preserves burst capacity: an idle bucket lets `rpm` requests through
            # immediately, exactly as a token balance would. Past that, each caller takes the
            # next free instant and pushes the marker forward, so waiters are spaced by the
            # token interval however deep the queue gets.
            #
            # The previous formulation clamped a negative token balance at a floor, which made
            # every waiter beyond that floor compute an identical delay and wake together —
            # reinstating the burst it was meant to prevent, just further down the queue.
            per_token = 60.0 / self.rpm
            # Burst tolerance: how far the theoretical arrival time may run ahead of `now`
            # before anyone actually waits. `rpm - 1` intervals reproduces a bucket of
            # capacity `rpm` — an idle limiter still lets a full council fire at once.
            tolerance = max(0, self.rpm - 1) * per_token
            arrival = max(state.next_available, now)
            wait_time = max(0.0, arrival - tolerance - now)

            if max_wait is not None and wait_time > max_wait:
                # Refuse rather than reserve: a caller that will not send must not consume
                # budget, or abandoned work starves the callers that would have used it.
                wait_time = -1.0
            else:
                state.next_available = arrival + tokens * per_token
                state.tokens = max(0.0, state.tokens - tokens)

            # Write state
            os.lseek(fd, 0, os.SEEK_SET)
            data_out = json.dumps(
                {
                    "tokens": state.tokens,
                    "last_update": state.last_update,
                    "rpm": state.rpm,
                    "next_available": state.next_available,
                }
            ).encode("utf-8")
            padded_data = data_out.ljust(4096, b" ")
            os.write(fd, padded_data)
        finally:
            _release_lock_sync(fd)

        return wait_time

    async def acquire(self, tokens: int = 1, max_wait: float | None = None) -> bool:
        """
        Reserve capacity for `tokens` requests.

        The reservation is committed under the file lock; the wait happens outside it so other
        processes keep accounting while this one sleeps. Returns False without reserving
        anything when the required wait exceeds `max_wait`, so the caller can fail fast and
        let a replacement be rotated in instead of sleeping through its budget.
        """
        wait_time = await asyncio.to_thread(self._sync_acquire_tokens, tokens, max_wait)
        if wait_time < 0:
            return False
        if wait_time > 0:
            # One line per wait filled a first review with a dozen of them: 14 in a four-minute
            # run, none of them anything a reader could act on. The engine prints a summary.
            logger.debug("Rate limit: waiting %.1fs...", wait_time)
            self.pauses += 1
            self.longest_pause = max(self.longest_pause, wait_time)
            await asyncio.sleep(wait_time)
        return True
