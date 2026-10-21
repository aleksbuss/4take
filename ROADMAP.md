# 4take Improvement Roadmap

This document outlines the architectural milestones and feature roadmap for the `4take` project. The plan is divided into logical milestones to allow incremental development.

## Sprint 0: Foundations
**Status: done.** CI in `.github/workflows/ci.yml` runs `ruff`, a Conventional Commits check on pull requests, the test suite on Python 3.10-3.13 and a packaging job. Logs go to stderr, stdout carries only the result, and every failed model is reported in `errors`.
**Goal:** Establish basic project infrastructure, testing strategy, and observability before adding new features.
- **Tasks:**
  - Set up CI/CD pipeline (e.g., GitHub Actions) for linting and testing.
  - Establish a testing framework (e.g., `pytest`) and write initial tests.
  - Introduce basic structured logging and error handling.
- **Acceptance Criteria:**
  - CI pipeline passes on every PR.
  - Error logs provide clear debugging info without crashing silently.

## Sprint 1: Safety & Privacy
**Status: done.** The warning banner is bypassed by `--accept-risk` or `FOURTAKE_ACCEPT_RISK`. Secret redaction is on by default (`--no-strip-secrets` turns it off; `--strip-secrets` is kept as a deprecated no-op) and `--show-redactions` lists what was removed.
**Goal:** Prevent sensitive data leaks and ensure users are aware of the risks when sending local code to external LLMs.
- **Tasks:**
  - Add a privacy warning banner that appears unless explicitly bypassed.
  - Implement a `--accept-risk` flag and `FOURTAKE_ACCEPT_RISK` environment variable to bypass the
    warning. (Shipped first as `4TAKE_ACCEPT_RISK`, which no shell can set — a POSIX identifier may
    not begin with a digit. The old name is still read for one release.)
  - Add optional secret-stripping (e.g., using `detect-secrets` or regex) behind a `--strip-secrets` flag.
- **Acceptance Criteria:**
  - CLI displays a clear, colored warning banner by default.
  - Passing `--accept-risk` completely suppresses the banner.
  - `--strip-secrets` redacts common API keys and passwords from the payload before sending.

## Sprint 2: Context Window & Token Limits
**Status: done.** `tokenizer.py` uses `tiktoken` with a character-count fallback; `preflight.py` refuses an input no available model can hold before any request goes out; per-model limits come from the catalog, with `model_token_limits` as an override.
**Goal:** Prevent unhandled `400 Bad Request` errors caused by sending files that are too large.
- **Tasks:**
  - Implement `tiktoken` for accurate token estimation (with a naive `len(text)//4` fallback if unavailable).
  - Store token limits per model in the configuration file and validate against them.
  - Add early-exit logic with a user-friendly error message if input is too large.
- **Acceptance Criteria:**
  - Users see a clear error ("Input exceeds X tokens") instead of an API failure.
  - Token counts are reasonably accurate using a tokenizer rather than just character length.

## Sprint 3: UX & Output Readability
**Status: done.** `--format text` (default) and `--format json`; the text report is rendered with `rich`; `output.py` forces UTF-8 on a Windows stdout.
**Goal:** Make the tool's output easy to read in the terminal instead of raw JSON.
- **Tasks:**
  - Add CLI arguments for output formatting: `--format text` (default) and `--format json`.
  - Implement a pretty-printer for Markdown reviews (defining headings, bullet points, code fences).
  - Fix Windows terminal encoding by forcing UTF-8 on `sys.stdout` securely.
- **Acceptance Criteria:**
  - Default output is human-readable formatted Markdown.
  - Windows users do not experience Unicode encoding crashes when printing emojis or special characters.

## Sprint 4: Robustness & Distribution
**Status: done, except that the package is not on PyPI yet.** The install commands in the README (`uv tool`, `pipx`) work from the first release; `publish.yml` is ready. 429s back off with jitter and honour `Retry-After` ([ADR-0002](docs/adr/0002-cap-retries-at-four-and-honour-retry-after.md)).
**Goal:** Global accessibility and model fallback logic.
- **Tasks:**
  - Document installation methods (`uv tool`, `pipx`, etc.) in the README.
  - Validate configured models against OpenRouter API; gracefully handle deprecations or missing models.
  - Improve resilience for rate limits (429s) and network failures during API calls.
- **Acceptance Criteria:**
  - The CLI handles 429s gracefully with exponential backoff (or informs the user).
  - Users have clear instructions on how to install the CLI globally.

## Sprint 5: Engineering Culture & Documentation
**Status: done.** `docs/adr/` (0001 is superseded by 0002), `CHANGELOG.md`, `CONTRIBUTING.md`, and Conventional Commits enforced on pull requests.
**Goal:** Implement the documentation practices expected by senior engineering teams.
- **Tasks:**
  - Establish `docs/adr/` directory and log our first ADR (`0001-use-exponential-backoff-with-10-retries.md`) using Michael Nygard's template.
  - Create a structured `CHANGELOG.md` following the "Keep a Changelog" standard.
  - Create a `CONTRIBUTING.md` guide outlining CI status and local setup.
  - Define and enforce "Conventional Commits" v1.0.0 (e.g., via `commitlint`).
- **Acceptance Criteria:**
  - Commits failing the Conventional Commits format are rejected or warned against.
  - All standard project documentation files are present and properly formatted.

## Next (after Sprint 5)
**Goal:** put numbers on the shipped release, then publish it.
- **Greptile benchmark: publish the write-up.** Runs 1 and 2 predate the one-seat-per-vendor rule and
  do not describe the shipped tool. Runs 3 and 4 were pre-registered, with the protocol hashed before
  any council was recorded, then recorded on 0.2.0 and graded blind. The write-up is not in the README
  yet; it goes in together with its pre-registration, so the figures can be checked.
- **Does the council help a weaker primary model?** The frontier test (Opus 5.5, 20 bugs) found no
  gain for Opus. A weaker model (Gemini Flash, 20 bugs) was tried once, but its councils seated only
  one or two reviewers because of the empty-response defect fixed in this release, so that run says
  nothing. Needs a pre-registered run with more than 20 bugs, since 20 can only show a large effect.
- **Auto context on by default?** The pre-registered gate is met (named 12 of 12 in three recordings);
  it sends more code to providers, so it stays opt-in until that is decided.
- **First public release:** `SECURITY.md` and a contributor agreement in place, then the first
  publish to PyPI.
