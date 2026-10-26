# 3. Retry a Rate-Limited Provider Eight Times

Date: 2026-10-08

## Status

Accepted. Amends [ADR-0002](0002-cap-retries-at-four-and-honour-retry-after.md) for HTTP 429
only; every other part of ADR-0002 stands.

## Context

ADR-0002 capped retries at four for every transient error. The request logs of 7 and 8 October
showed that a 429 from a free provider is often temporary: a retry after Poolside's 429 got
through in 21-50% of cases, so giving up after four retries still cost laguna about one seat in
four. A seat lost that way is replaced by a spare, but the spares are the weaker models, and on
a large diff there is often no spare that finishes.

Some providers never relent: not one of 69 retries after Gemma's 429 got through. Waiting longer
on such a provider only burns the per-model timeout, so it needs a different remedy than more
retries.

## Decision

- **A 429 is retried up to eight times** (`RATE_LIMIT_RETRIES` in `fourtake.client`), with the
  same backoff as before: `Retry-After` when the provider sends it, otherwise
  `min(2**attempt, 30) * uniform(1.0, 1.5)`.
- **A 5xx, a timeout or a malformed reply keeps the limit of four.** Those are not signs that
  the provider will answer if asked again later.
- **A provider that refuses everything is left out of the pool by telemetry**, not waited on: a
  model whose provider refused every request in the last 72 hours, at least 8 of them, is
  skipped until the refusals age out.
- Everything else in ADR-0002 is unchanged: `Retry-After` is authoritative, a retry that would
  overrun the council deadline is not attempted, and every attempt is charged to the rate
  limiter.

## Consequences

- **Positive**: fewer seats lost to a provider that was only briefly throttled.
- **Negative**: without `Retry-After`, eight retries add up to about 2 to 3 minutes of backoff.
  That fits inside the default per-model timeout (300 s, half of the 600 s `--timeout`), and
  the deadline check still stops a retry that would overrun the council.
- **Negative**: one throttled seat can now cost up to nine requests instead of five, and every
  one counts against OpenRouter's daily free-model limit (50 requests on an account that has
  never been topped up).
