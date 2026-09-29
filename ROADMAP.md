# 4take Improvement Roadmap

This document outlines the architectural milestones and feature roadmap for the `4take` project. The plan is divided into logical milestones to allow incremental development.

## Sprint 0: Foundations
**Goal:** Establish basic project infrastructure, testing strategy, and observability before adding new features.
- **Tasks:**
  - Set up CI/CD pipeline (e.g., GitHub Actions) for linting and testing.
  - Establish a testing framework (e.g., `pytest`) and write initial tests.
  - Introduce basic structured logging and error handling.
- **Acceptance Criteria:**
  - CI pipeline passes on every PR.
  - Error logs provide clear debugging info without crashing silently.

## Sprint 1: Safety & Privacy
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
**Goal:** Prevent unhandled `400 Bad Request` errors caused by sending files that are too large.
- **Tasks:**
  - Implement `tiktoken` for accurate token estimation (with a naive `len(text)//4` fallback if unavailable).
  - Store token limits per model in the configuration file and validate against them.
  - Add early-exit logic with a user-friendly error message if input is too large.
- **Acceptance Criteria:**
  - Users see a clear error ("Input exceeds X tokens") instead of an API failure.
  - Token counts are reasonably accurate using a tokenizer rather than just character length.

## Sprint 3: UX & Output Readability
**Goal:** Make the tool's output easy to read in the terminal instead of raw JSON.
- **Tasks:**
  - Add CLI arguments for output formatting: `--format text` (default) and `--format json`.
  - Implement a pretty-printer for Markdown reviews (defining headings, bullet points, code fences).
  - Fix Windows terminal encoding by forcing UTF-8 on `sys.stdout` securely.
- **Acceptance Criteria:**
  - Default output is human-readable formatted Markdown.
  - Windows users do not experience Unicode encoding crashes when printing emojis or special characters.

## Sprint 4: Robustness & Distribution
**Goal:** Global accessibility and model fallback logic.
- **Tasks:**
  - Document installation methods (`uv tool`, `pipx`, etc.) in the README.
  - Validate configured models against OpenRouter API; gracefully handle deprecations or missing models.
  - Improve resilience for rate limits (429s) and network failures during API calls.
- **Acceptance Criteria:**
  - The CLI handles 429s gracefully with exponential backoff (or informs the user).
  - Users have clear instructions on how to install the CLI globally.

## Sprint 5: Engineering Culture & Documentation
**Goal:** Implement professional, open-source-ready documentation practices expected by senior engineering teams.
- **Tasks:**
  - Establish `docs/adr/` directory and log our first ADR (`0001-use-exponential-backoff-with-10-retries.md`) using Michael Nygard's template.
  - Create a structured `CHANGELOG.md` following the "Keep a Changelog" standard.
  - Create a `CONTRIBUTING.md` guide outlining CI status and local setup.
  - Define and enforce "Conventional Commits" v1.0.0 (e.g., via `commitlint`).
- **Acceptance Criteria:**
  - Commits failing the Conventional Commits format are rejected or warned against.
  - All standard OSS documentation files are present and properly formatted.

