"""
OpenRouter's daily allowance for free models, read before it is spent and when a 429 may mean it is.

An account that has never bought credits gets 50 free-model requests a day, one that has bought $10
or more gets 1000, and the day ends at 00:00 UTC. When the allowance is gone every request answers
429 "Rate limit exceeded", which looks exactly like a provider that is merely busy. 4take then waited
and rotated through the whole model pool until its deadline, ten minutes by default, and ended on a
list of errors that never said why. `GET /api/v1/key` reports `free_model_daily_requests`, so the
question can be asked, for nothing, and answered in a sentence.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

import httpx

KEY_URL = "https://openrouter.ai/api/v1/key"
CREDITS_URL = "https://openrouter.ai/settings/credits"
# What OpenRouter documents for accounts without and with at least $10 of purchased credits.
FREE_TIER_DAILY_LIMIT = 50
TOPPED_UP_DAILY_LIMIT = 1000
# A reply that finds the allowance spent says so for this long without asking again.
RECHECK_AFTER_S = 15.0
REQUEST_TIMEOUT_S = 5.0


@dataclass(frozen=True)
class FreeQuota:
    used: int
    limit: int
    remaining: int


def is_openrouter_url(url: str | None) -> bool:
    """Only OpenRouter itself has the allowance; a configured proxy or another host does not."""
    return urlparse(url or "").hostname == "openrouter.ai"


def parse_free_quota(payload: Any) -> FreeQuota | None:
    """The counter out of a `/key` reply, or None when the account has none (exempt accounts) or it is malformed."""
    try:
        counter = payload["data"]["free_model_daily_requests"]
        used, limit, remaining = counter["used"], counter["limit"], counter["remaining"]
    except (KeyError, TypeError):
        return None
    if not all(isinstance(n, int) and not isinstance(n, bool) for n in (used, limit, remaining)):
        return None
    return FreeQuota(used=used, limit=limit, remaining=remaining)


async def fetch_free_quota(client: httpx.AsyncClient, api_key: str) -> FreeQuota | None:
    """
    How much of today's free-model allowance is left, or None when that cannot be told.

    Never raises and never blocks a review on doubt: no network, a rejected key or an odd reply all
    read as "unknown", and the review goes ahead as it did before this check existed. The call asks
    about the key, not a model, so it spends none of the allowance it reports.
    """
    try:
        response = await client.get(
            KEY_URL, headers={"Authorization": f"Bearer {api_key}"}, timeout=REQUEST_TIMEOUT_S
        )
        if not response.is_success:
            return None
        return parse_free_quota(response.json())
    except (httpx.HTTPError, ValueError):
        return None


def seconds_until_utc_midnight(now: datetime) -> int:
    now = now.astimezone(timezone.utc)
    midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(0, int((midnight - now).total_seconds()))


def describe_wait(seconds: int) -> str:
    hours, rest = divmod(max(0, seconds), 3600)
    minutes = -(-rest // 60)  # a started minute counts, so "0 min" is never shown while time remains
    if minutes == 60:
        hours, minutes = hours + 1, 0
    if hours:
        return f"{hours} h {minutes} min" if minutes else f"{hours} h"
    return f"{minutes} min" if minutes else "under a minute"


def daily_limit_message(quota: FreeQuota, now: datetime | None = None) -> str:
    """What to tell a person whose free-model allowance for the day is spent."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    wait = describe_wait(seconds_until_utc_midnight(now))
    reset = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    local = reset.astimezone()
    when = "00:00 UTC"
    if local.utcoffset() != timedelta(0):
        when += f" ({local:%H:%M} your time)"
    text = (
        f"OpenRouter's free-model limit for today is used up: {quota.used} of {quota.limit} requests. "
        f"It resets at {when}, in {wait}."
    )
    if quota.limit <= FREE_TIER_DAILY_LIMIT:
        text += (
            f" An OpenRouter account that has never bought credits gets {FREE_TIER_DAILY_LIMIT} free-model "
            f"requests a day; one that has bought $10 or more gets {TOPPED_UP_DAILY_LIMIT}. "
            f"Add credits at {CREDITS_URL} to raise it now."
        )
    else:
        text += " This account already has the higher limit, so the only way on is to wait for the reset."
    return text + " A review takes 6 to 12 requests."


class QuotaGuard:
    """
    Asks, on a 429, whether the daily allowance is what ran out, once for the whole council.

    The first 429 of a run starts the question and every other waits for the same answer. "Not spent"
    is remembered for `recheck_after` seconds, since the allowance can run out mid-run; "spent" is
    final, and `message` then holds what to tell the user.
    """

    def __init__(
        self,
        client: httpx.AsyncClient,
        api_key: str,
        recheck_after: float = RECHECK_AFTER_S,
        clock=time.monotonic,
    ) -> None:
        self._client = client
        self._api_key = api_key
        self._recheck_after = recheck_after
        self._clock = clock
        self._lock = asyncio.Lock()
        self._checked_at: float | None = None
        self.message: str | None = None

    @property
    def tripped(self) -> bool:
        return self.message is not None

    async def exhausted(self) -> bool:
        if self.message is not None:
            return True
        async with self._lock:
            if self.message is not None:
                return True
            if self._checked_at is not None and self._clock() - self._checked_at < self._recheck_after:
                return False
            quota = await fetch_free_quota(self._client, self._api_key)
            self._checked_at = self._clock()
            if quota is not None and quota.remaining <= 0:
                self.message = daily_limit_message(quota)
            return self.message is not None
