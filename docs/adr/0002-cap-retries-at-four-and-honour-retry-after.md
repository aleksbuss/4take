# 2. Cap Retries at Four and Honour Retry-After

Date: 2026-09-08

## Status

Accepted. Supersedes [ADR-0001](0001-use-exponential-backoff-with-10-retries.md). Amended by
[ADR-0003](0003-retry-a-rate-limited-provider-eight-times.md): a 429 is now retried eight
times; the limit of four still applies to every other transient error.

## Context

ADR-0001 specified up to 10 retries. `CHANGELOG.md` and `POST_MORTEMS.md` repeated that
figure. The code has been `max_retries: int = 4` in `fourtake.client` for some time, so three
documents described a policy the system did not implement. An ADR that no longer matches the
code is worse than no ADR: it is a claim a reader has no reason to doubt.

Two further problems were measured in the retry path that ADR-0001 did not anticipate:

- **`Retry-After` was honoured only when it was lenient.** The header was applied only if it
  was under 30 seconds; anything larger was discarded in favour of the exponential delay,
  which is itself capped at 30. Observed: a provider asking for 120s was retried after
  2.3s, 4.9s and 9.6s — up to 50x sooner than instructed, deepening the throttling the
  backoff existed to escape.
- **Retries were not metered.** The cross-process token bucket was charged once per model,
  before the first attempt. The retry loop then issued up to `max_retries` further requests
  against no budget at all. With rotation, a worst case of 12 models x 5 attempts produced
  60 requests from a single invocation, none of them rate limited.

## Decision

- **Max retries stays at 4** (five attempts total). Combined with the backoff below, a single
  model spends at most roughly 45 seconds on retries, which fits inside the per-model timeout
  without starving the rotation pool. Ten retries could exceed the council deadline on its
  own, which is what made the original number unusable in practice.
- **`Retry-After` is authoritative when present.** Both delta-seconds and HTTP-date forms are
  parsed and honoured as given, with no upper clamp.
- **A retry that would overrun the council deadline is not attempted.** `query_model` receives
  the run's monotonic deadline; if `now + delay > deadline` the model fails immediately with
  a explanatory error so the engine can rotate a replacement into the seat instead of
  sleeping through the remaining budget.
- **Exponential backoff with jitter remains the fallback** when no `Retry-After` is supplied:
  `min(2**attempt, 30) * uniform(1.0, 1.5)`.
- **Every attempt is charged to the rate limiter**, not just the first.
- **Malformed 200 responses are transient.** A body with no `choices`, a null `message`, or
  non-JSON content is a provider hiccup and consumes a retry, rather than being reported as a
  permanent failure that costs the model its seat.

## Consequences

- **Positive**: the documented policy and the implemented policy are the same one. Honouring
  `Retry-After` stops the client from amplifying the rate limiting it is reacting to. Metering
  every attempt makes the "cross-process rate limiter" claim in the README true for retries as
  well as first attempts. Failing fast against the deadline converts dead waiting time into a
  rotation opportunity.
- **Negative**: obeying a large `Retry-After` can idle a seat for as long as the provider
  asks. That is bounded by the per-model timeout and by the deadline check, and the engine
  rotates rather than blocking the council.
- **Negative**: charging retries to the bucket makes a degraded run slower under a low
  `rpm_limit`. That is the intended trade: the alternative was an unmetered retry storm.

## Notes

ADR-0001's core reasoning — bounded exponential backoff with jitter, so that concurrently
throttled requests do not resume in lockstep — is retained. Only the retry count and the
handling of provider-supplied delays change.
