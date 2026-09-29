# Post-Mortems & Incident Logs

This document tracks significant failures, bugs, or architectural bottlenecks encountered during the development and usage of `4take`, along with root cause analyses (RCA) and preventive measures.

---

## [2026-09-04] Accidental Paid Model Infiltration & Zero-Cost Guardrail Hardening

**Status:** Resolved
**Impact:** CLI and autonomous AI agents (Claude Code, Cursor, Codex) were observed invoking paid OpenRouter models (Claude 3.5 Sonnet, GPT-4o, Llama 3.3 70B without `:free`), risking unexpected account token expenditure or failing with HTTP 402 Insufficient Balance.

### Background
`4take` is architected as an autonomous multi-model code review council intended to run with zero token costs using OpenRouter's `:free` model tier.

### Root Cause
1. **Config Template Leaks:** `init_config()` in `fourtake.cli` defaulted to a template containing explicit paid models (`openrouter/openai/gpt-4o`, `openrouter/anthropic/claude-3.5-sonnet`, `openrouter/meta-llama/llama-3.3-70b-instruct`).
2. **Missing Price Verification in Model Validation:** `validate_configured_models()` in `fourtake.discovery` only verified model existence against the OpenRouter `/models` catalog ID set, without checking `pricing.prompt` or `pricing.completion`. Paid models were marked valid and dispatched to execution.
3. **Documentation & Agent Skill Prompts:** `README.md`, `llms.txt`, and `AGENTS.md` provided command recipes with explicit paid models, which external LLM agents copied when calling `4take`.
4. **Lack of Pre-Flight Guardrails:** `fourtake.engine` had no pre-flight zero-cost guard, allowing requests to hit paid API endpoints directly.

### Resolution
1. **Zero-Cost Pricing Validation:** `validate_configured_models()` now validates pricing (`prompt == 0`, `completion == 0` or `:free` suffix) and hard-rejects paid models with actionable error diagnostics.
2. **Pre-Flight Network Guard:** `run_council` in `fourtake.engine` verifies all OpenRouter models against `is_free_model_id()` before executing network calls, immediately returning a `ZeroCostViolation` without token spend.
3. **Template & Default Sanitization:** `init_config()` defaults to `["auto"]` with explicit zero-cost notices.
4. **Documentation & Agent Instruction Scrub:** All documentation (`README.md`, `AGENTS.md`, `SKILL.md`, `CLAUDE.md`, `llms.txt`) updated to explicitly mandate 100% free models (`:free` tier) and remove all paid model examples.
5. **Comprehensive Test Suite:** Added `tests/test_zero_cost_enforcement.py` asserting rejection of paid models and zero network invocation.

### Lessons Learned / Preventive Measures
- **Defense in Depth for Cost Invariants:** Never rely solely on user configuration or documentation conventions for critical cost boundaries. Invariants must be enforced at the validation layer AND the network dispatch layer.
- **Agent Prompts are Code:** AI coding agents will verbatim adopt examples from `README.md` or `SKILL.md`. Examples must never contain configurations that violate desired operational boundaries.

---

## [2026-07-23] OpenRouter Free Tier `429 Too Many Requests` Congestion

**Status:** Resolved
**Impact:** CLI and MCP server completely failed to return results when querying free models during peak hours, hanging indefinitely and eventually crashing.

### Background
The application queries up to 4 AI models in parallel using OpenRouter's free tier. The initial configuration assumed models would return results within a few seconds.

### Root Cause
OpenRouter applies strict rate limits (20 Requests Per Minute / 50 Requests Per Day) on free-tier API keys. When multiple requests were dispatched concurrently, the API returned HTTP 429 (Too Many Requests). The initial retry logic was configured for 20 attempts with exponential backoff, which resulted in the script hanging for up to 15-20 minutes trying to bypass a hard rate limit that was mathematically impossible to beat in the short term.

### Resolution
1. **Reduced Max Retries:** Lowered the retry limit from 20 in `fourtake.client`, allowing a "fast fail" instead of an indefinite hang. (The limit was later settled at 4 — see ADR-0002; this entry originally recorded 10, which was never the implemented value.)
2. **Parallel Processing Graceful Degradation:** The engine now correctly captures the 429 error after the retry budget is spent for individual models (like `gemma`) while still returning successful results from the other models that managed to get through.

### Lessons Learned / Preventive Measures
- **Rate Limit Awareness:** Free-tier aggregators are highly volatile. Applications must be designed with the assumption that partial failures *will* happen.
- **Fail Fast:** Exponential backoff must have a reasonable ceiling. Waiting more than 5 minutes for a CLI tool creates poor UX.
- **Future Work (Roadmap):** Implement a local quota tracker to avoid sending requests altogether if the daily 50-request limit is known to be exhausted.

