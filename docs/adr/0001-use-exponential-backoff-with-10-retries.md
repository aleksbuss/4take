# 1. Use Exponential Backoff with 10 Retries

Date: 2026-07-23

## Status

Superseded by [ADR-0002](0002-cap-retries-at-four-and-honour-retry-after.md).

The retry count in this ADR (10) was never implemented; the code has used 4. ADR-0002
records the actual policy and adds `Retry-After` handling and deadline-aware fail-fast.

## Context

The 4take CLI queries multiple LLMs concurrently via OpenRouter. Free-tier accounts on OpenRouter have strict rate limits (HTTP 429 Too Many Requests). When querying 4 models simultaneously, it is common to hit concurrency limits or rpm (requests per minute) limits, leading to transient failures. 

We need a resilient way to handle these transient failures without crashing the entire council request, as having some models fail reduces the value of the tool.

## Decision

We will implement an exponential backoff strategy with up to 10 retries for HTTP 429 (Too Many Requests) and HTTP 5xx (Server Error) responses.

- **Initial Delay**: Based on the retry attempt number (`min(2^attempt, 30)` seconds).
- **Jitter**: A random multiplier between 1.0 and 1.5 is applied to the delay to prevent thundering herd problems.
- **Retry-After Header**: If the provider returns a `Retry-After` header with a specific integer value, we respect it if it is reasonable (under 30 seconds), otherwise we cap it or fall back to our exponential delay.
- **Max Retries**: We cap the retries at 10 to ensure the CLI doesn't hang indefinitely. 
- **Timeouts**: Total execution time is bounded by the global `timeout` configuration (default 600 seconds).

## Consequences

- **Positive**: High success rate for completing the council request even when hitting strict rate limits on free tiers. The jitter prevents all concurrently blocked requests from retrying at the exact same moment.
- **Negative**: The user might experience long wait times if the rate limits are severe. To mitigate this, the progress indicator will continue to show activity, and the global timeout will eventually kill the operation if it takes too long.
