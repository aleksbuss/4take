# 4take 🎯

<div align="center">

[![Python](https://img.shields.io/badge/Python-3.10+-3776AB?style=flat&logo=python&logoColor=white)](https://www.python.org/)
[![License: BUSL-1.1](https://img.shields.io/badge/License-BUSL--1.1-yellow.svg)](https://github.com/aleksbuss/4take/blob/main/LICENSE)
[![PyPI](https://img.shields.io/pypi/v/4take)](https://pypi.org/project/4take/)
[![CI Status](https://github.com/aleksbuss/4take/actions/workflows/ci.yml/badge.svg)](https://github.com/aleksbuss/4take/actions)
[![Linted with Ruff](https://img.shields.io/badge/linting-ruff-D7FF64.svg)](https://github.com/astral-sh/ruff)

**Multi-Model AI Council for Code Review.**  
*Four free models from four vendors review your code in one command, and their findings are ranked by how many raised each one.*

On Greptile's own 50-PR bug benchmark, a council of free models found 37 of 50 bugs:  
level with Greptile (41), ahead of Copilot and CodeRabbit, whose reviews date from July 2025. [How it was measured](#-against-the-paid-reviewers-greptiles-benchmark)

[⚡ 30-Second Quickstart](#-30-second-quickstart) •
[🤖 AI Agent & MCP Integration](#-ai-coding-agents--mcp-integration) •
[✨ Features](#-features) •
[📏 Benchmark](#-against-the-paid-reviewers-greptiles-benchmark) •
[🏗️ Architecture](#%EF%B8%8F-how-it-works) •
[💻 CLI Recipes](#-cli-usage--recipes) •
[⚙️ Configuration](#%EF%B8%8F-configuration-file) •
[📊 OpenRouter Limits Guide](#-openrouter-free-tier--rate-limits)

</div>

> **What is free, what is paid.** 4take is source-available under the
> [Business Source License 1.1](https://github.com/aleksbuss/4take/blob/main/LICENSE); the license text decides.
>
> | | |
> | :--- | :--- |
> | **Free to use** | A person using it for their own work, paid or unpaid. A solo developer working alone, as long as it does not run on a client's machines or accounts and no client requires or pays for it. Volunteer projects whose code is public under an OSI-approved license ([details](#-license)). |
> | **Paid** | Organizations: companies, public bodies, universities and non-profits, after a 30-day evaluation. Commercial license: takelicensing@gmail.com. |
> | **Models** | Only OpenRouter's free (`:free`) models. Free has limits: roughly 4 to 8 reviews a day on an OpenRouter account that has never been topped up, roughly 80 to 160 once it has ([limits](#-openrouter-free-tier--rate-limits)). |

---

## ⚡ 30-Second Quickstart

### 1. Install

```bash
# Recommended (Global tool)
uv tool install 4take
# or
pipx install 4take
# or
pip install 4take
```

4take needs **Python 3.10 or newer**. The `python3` that comes with macOS is 3.9, and pip there
fails with `No matching distribution found for 4take`. `uv tool install 4take` fetches a suitable
Python by itself, whatever the system has; to get uv, run
`curl -LsSf https://astral.sh/uv/install.sh | sh` or `brew install uv`.

### 2. Set Your OpenRouter API Key

Get an API key at [OpenRouter](https://openrouter.ai/keys):

```bash
export OPENROUTER_API_KEY="sk-or-v1-..."
```

The environment variable wins over a key in `~/.config/4take/config.json`, so it works even
after `--init-config` has written its placeholder there.

### 3. Run Review

```bash
# Review a single file
cat main.py | 4take

# Review your recent git changes
git diff HEAD~1 | 4take

# Review staged changes before commit
git diff --cached | 4take
```

---

## 🤖 AI Coding Agents & MCP Integration

`4take` works with AI coding agents (Claude Code, Cursor, Antigravity IDE, Windsurf, Cline, Roo Code, Codex) as an MCP server or a CLI: the agent sends its code to the council and gets the findings back ranked by agreement, to show the user.

An agent tends to keep the findings most reviewers agree on and drop the rest, which loses much of what the council catches ([measured](#-read-the-council-yourself)). So the MCP payload opens with every finding and its agreement, for the agent to show you.

```text
                    ┌──────────────────────┐
                    │   AI coding agent    │
                    └──────────┬───────────┘
                               │ 1. sends its code or diff
                               ▼
                    ┌──────────────────────┐
                    │    4take council     │
                    └──────────┬───────────┘
          ┌────────────┬───────┴────┬────────────┐
          ▼            ▼            ▼            ▼
     reviewer 1   reviewer 2   reviewer 3   reviewer 4
     (vendor A)   (vendor B)   (vendor C)   (vendor D)
          └────────────┴───────┬────┴────────────┘
                               │ 2. findings grouped and ranked
                               ▼    by how many raised each one
                    ┌──────────────────────┐
                    │   report_for_user    │
                    │  e.g. "3/4 HIGH ..." │
                    └──────────┬───────────┘
                               │ 3. the agent shows every line and
                               ▼    checks each against the code
                             user
```

### Option A: FastMCP Server (`4take-mcp`)

`4take` includes a native FastMCP server with real-time streaming progress notifications.

It ships as an optional extra, because its transport stack (starlette, uvicorn, pyjwt,
cryptography) triples the size of an install that most CLI users never run a server from:

```bash
pip install '4take[mcp]'        # or: uv tool install '4take[mcp]'
```

<details open>
<summary><b>Claude Desktop Configuration (<code>claude_desktop_config.json</code>)</b></summary>

```json
{
  "mcpServers": {
    "4take": {
      "command": "uvx",
      "args": ["--from", "4take[mcp]", "4take-mcp"],
      "env": {
        "OPENROUTER_API_KEY": "sk-or-v1-..."
      }
    }
  }
}
```
</details>

<details>
<summary><b>Cursor IDE (<code>.cursor/mcp.json</code>)</b></summary>

```json
{
  "mcpServers": {
    "4take": {
      "command": "4take-mcp",
      "env": {
        "OPENROUTER_API_KEY": "sk-or-v1-..."
      }
    }
  }
}
```
</details>

<details>
<summary><b>Antigravity / VS Code / Roo Code / Cline (<code>mcp_config.json</code>)</b></summary>

```json
{
  "mcpServers": {
    "4take": {
      "command": "uvx",
      "args": ["--from", "4take[mcp]", "4take-mcp"],
      "env": {
        "OPENROUTER_API_KEY": "sk-or-v1-..."
      }
    }
  }
}
```
</details>

**MCP Tool Exposed:**
- `get_4take_opinion(code: str, force: bool = False, context_files: list[str] | None = None, context: str | None = None, auto_context: bool = False) -> str`: Queries 4 models in parallel and returns structured JSON with model critiques, latencies, and status. `context_files` are paths the server reads from disk as [reference context](#-giving-the-council-context); `context` is the same as text; `auto_context` lets the server choose it for a diff. The payload opens with `report_for_user`, lines the tool description asks the agent to show you as they are: one per finding, most-agreed first, with how many reviewers raised it (``- 4/4 CRITICAL `search_users` — SQL injection``), under a header giving the [measured calibration](#measuring-the-council). The agent is told to act on findings three or more reviewers raised and to check those one or two raised against the code before accepting or rejecting them, so a single reviewer's find reaches you instead of being dropped unseen. The payload then carries the same `consensus` list as the CLI, plus `status` (`ok` / `degraded` / `error`), `distinct_models` and `min_responses`. `degraded` means fewer independent reviewers answered than were asked for — not that a warning was logged.

---

### Option B: CLI Subprocess for Agents (JSON Output)

AI agents can pipe code directly into `4take` via shell commands and receive clean, parseable JSON:

```bash
cat modified_file.py | 4take --format json --accept-risk --quiet
```

#### JSON Output Schema

```json
{
  "version": "0.2.2",
  "timestamp": "2026-08-16T14:20:00Z",
  "config": {"models_requested": 12, "min_responses": 4, "timeout_seconds": 600, "strict": false},
  "summary": {
    "task_hash": "25d297f773c3",
    "responses_received": 4,
    "distinct_models": 4,
    "responses_failed": 0,
    "total_latency_ms": 3200,
    "context": {"files": ["src/mailer.py", "src/users.py (lines 40-90)"], "tokens": 812, "dropped": []}
  },
  "warning": null,
  "error": null,
  "consensus": [
    {
      "agreement": 3,
      "severity": "CRITICAL",
      "category": "INJECTION",
      "symbol": "search_users",
      "title": "SQL injection via the term parameter",
      "models": ["cohere/north-mini-code:free", "nvidia/nemotron-3-super-120b-a12b:free",
                 "poolside/laguna-s-2.1:free"],
      "severities": {"CRITICAL": 2, "WARNING": 1},
      "disputed": true
    }
  ],
  "responses": [
    {
      "model": "openrouter/nvidia/nemotron-3-super-120b-a12b:free",
      "served_model": "nvidia/nemotron-3-super-120b-a12b:free",
      "served_provider": "NVIDIA",
      "content": "### Code Review\n1. Found SQL Injection on line 8...",
      "finish_reason": "stop",
      "truncated": false,
      "duplicate_of": null,
      "latency_ms": 1150
    },
    {
      "model": "openrouter/poolside/laguna-s-2.1:free",
      "served_model": "poolside/laguna-s-2.1:free",
      "served_provider": "Poolside",
      "content": "### Security & Performance\n1. Use parameterized query for user_id...",
      "finish_reason": "stop",
      "truncated": false,
      "duplicate_of": null,
      "latency_ms": 1420
    }
  ],
  "errors": []
}
```

`consensus` is the list to read first: findings already grouped across models and ranked by
`agreement`, the number of independent reviewers who raised each one. Grouping is lexical,
so an `agreement: 1` entry is not dismissed — it is a finding the others did not word the
same way, or did not see. On the eval corpus an entry all four reviewers raised held a
labelled defect 96% of the time, one raised by two or three 60%, and one raised by a single
reviewer 41%: unanimous agreement is strong evidence, and anything less is a lead to verify.

`category` is the defect class the reviewers declared — one of `INJECTION`, `CONCURRENCY`,
`RESOURCE_LEAK`, `BOUNDS`, `ERROR_HANDLING`, `AUTH_CRYPTO`, `LOGIC`, `STRUCTURE`, `SIMPLICITY` —
and `UNKNOWN` when none did. It is read from the reviews, never guessed from their wording.
It is a label for reading, not evidence: two reviewers filing the same defect choose the same
category only about half the time, so grouping ignores it.

`--strict` narrows `consensus` to findings more than one reviewer raised, plus any `CRITICAL`
finding however few raised it. Agents should normally leave it off: the single-reviewer
entries are the ones they are asked to audit.

A council seats one model per vendor: two models from one vendor share training data, so
their agreement is weaker evidence. A second answer from a seated vendor — or the same model
again, as a router sometimes serves — stays in `responses` with `duplicate_of` naming the seat
it shadows, and does not count towards `agreement`. It never takes a seat, even when no other
vendor answers in time: the council then reports fewer seats (exit code `2`, and `warning`
says how many), and no spare is launched from a vendor that already holds one.

A review lasts as long as the council waits for its seats. Most free models reason before they
answer, and each gets up to 32768 tokens to do it in, so a short file is usually reviewed in about
a minute, while a large diff can take up to `--timeout` (600 s). On a large diff some models
reason without ever reaching an answer and lose their seat at the per-model timeout, and only
three or four free vendors reliably finish one, so the council often seats fewer than four
reviewers (exit code `2`). Measured on 8 real diffs of 3.5–29 KB, each run twice on 8 October:
8 of the 16 councils seated four reviewers, 5 seated three and 3 seated one or two; the median
council took about 8½ minutes, and 6 ran to the 600 s limit. On the 28 short snippets of the eval
corpus the median was about 50 s, and 27 of 28 councils seated four.

### Ready-to-Use Agent Configuration Files:
- **Instructions for any agent**: [`AGENTS.md`](https://github.com/aleksbuss/4take/blob/main/AGENTS.md)
- **Claude Code**: [`CLAUDE.md`](https://github.com/aleksbuss/4take/blob/main/CLAUDE.md)
- **Antigravity Skill**: [`SKILL.md`](https://github.com/aleksbuss/4take/blob/main/SKILL.md)
- **Machine Summary**: [`llms.txt`](https://github.com/aleksbuss/4take/blob/main/llms.txt)

---

## ✨ Features

- 🚀 **One Command**: Pipe a file or a diff into `4take`; four free models review it in parallel.
- 🧠 **Auto-Discovery Ranked on Evidence**: Fetches and filters the free models on OpenRouter, then ranks them by the success rate and median latency they have actually delivered on your machine. The name-based heuristic (parameter count, context window) survives only as the cold-start prior for models you have not run yet.
- 🔎 **Four Review Axes**: Correctness, security, structure and simplicity — not just defects. Every structural finding has to name its cost, so the breadth does not become a flood of taste.
- 🤝 **Consensus Grouping**: Findings several models raised independently are grouped and ranked by how many raised them, so the cross-referencing is done for you rather than left in four parallel reviews.
- 🔄 **Fault-Tolerant Dynamic Rotation**: A rate-limited model (HTTP 429) is retried with exponential backoff; one that keeps failing gives its seat to the next model in a pre-ranked pool of up to 12.
- 🆓 **Free Models Only**: Requests go only to OpenRouter's free (`:free`) models, and a paid model is rejected before anything is sent. Free has limits: roughly 4 to 8 reviews a day on an account that has never been topped up, roughly 80 to 160 once it has ([details](#-openrouter-free-tier--rate-limits)).
- 🛡️ **Secret Redaction**: Automatically scrubs API keys, passwords, and authorization tokens before sending payloads over the wire. The code itself still goes to third-party free endpoints ([privacy](#%EF%B8%8F-privacy--security)).
- 🚦 **Cross-Process Rate Limiter**: Shared file-locked token bucket ensures concurrent terminal sessions or background agents never trigger rate limit bans.
- 🎨 **Terminal and JSON Output**: Markdown rendered in the terminal, or `--format json` for scripts and CI.

---

## 📏 Against the Paid Reviewers: Greptile's Benchmark

Greptile published a benchmark of 50 pull requests from five open-source projects (Cal.com,
Discourse, Grafana, Keycloak, Sentry), each with one labelled bug, together with the comments five
commercial AI reviewers left on them in late July 2025. Fifteen months later 4take reviewed the
same 50 diffs using only OpenRouter's free models (commit 2f07956, 8 October 2026). Its reviews were
graded blind, against a rubric and an analysis written down before the run.

| reviewer | benchmark standard, 50 PRs | strict, 42 verified labels |
| :--- | ---: | ---: |
| **4take, free models** | **37 (74%)** | **30 (71%)** |
| Greptile | 41 | 32 |
| Bugbot | 29 | 25 |
| GitHub Copilot | 26 | 19 |
| CodeRabbit | 22 | 16 |
| Graphite | 3 | 2 |

- **Level with Greptile, not ahead of it.** Neither gap is statistically significant (exact
  McNemar test, paired by PR: p = 0.42 and 0.77). 4take is significantly ahead of Copilot and
  CodeRabbit (p ≤ 0.02), and ahead of Bugbot without significance.
- **What counts.** *Benchmark standard* credits a finding at the right place with a related
  concern, as the benchmark credited the commercial tools; their figures in that column are the
  ones Greptile published in its table. *Strict* credits only a finding that names the labelled
  bug, on the 42 labels an audit did not show to be wrong; the commercial tools' strict figures
  come from a blind re-grade of their published comments.
- **Who graded.** Two Claude models (Opus and Sonnet), each in an isolated session that held only
  the review packets and the rubric, without knowing which run a packet came from. They agreed on
  95 of 100 packets (Cohen's κ 0.88); a third settled the disputed seats.
- **What it measures, and what it does not.** Recall of one labelled bug per PR. A council also
  says a lot more: a median of 17 findings and 8 consensus entries per PR, which the benchmark
  neither credits nor penalises. Read them by agreement (next section).
- **What it is not.** One recording of a stochastic free pool, built and graded by the author of
  4take. A strong signal, not proof. The pool has moved on since: two of the four most-seated
  models of that run (apodex-1.1-mini, ling-3.0-flash-sante) have left the free tier, so the same
  councils cannot be seated again.
- **Fifteen months apart, on public data.** The commercial reviews were written in July 2025 and
  4take's in October 2026, and models improved in between: those tools would probably score higher
  today. The PRs and the benchmark's bug descriptions have been public since 2025, so a model trained
  later may have seen them, and nothing here rules that out. Both effects favour 4take.
- **Where it is weakest.** Large Java diffs: Keycloak 5 of 10. On two of them, and on one Sentry
  diff, only one free vendor reasoned through to an answer. A council took a median of six
  minutes on these diffs and up to the 600 s timeout.

The pre-registrations, the graders' instructions, every departure from the plan and the results
are in [docs/benchmark](https://github.com/aleksbuss/4take/blob/main/docs/benchmark/README.md). The
diffs are other projects' code, so the recordings and the graded packets are not.

---

## 🧪 Read the Council Yourself

**Four free models found more than a commercial model alone.** A
[pre-registered test](https://github.com/aleksbuss/4take/blob/main/docs/benchmark/README.md#does-the-council-help-a-weaker-model)
on the same 50 PRs, in October 2026, gave Gemini 3.8 Flash (thinking high) each diff alone, and
again with the council's findings as notes; three runs each, graded blind:

| | bugs found, of 50 |
| :--- | ---: |
| **the council's findings, read directly** | **37** |
| Gemini 3.8 Flash alone | 24 |
| Gemini 3.8 Flash given the council's findings | 26 |

The council caught 15 bugs the model missed, and the model 2 that the council missed (exact McNemar
p ≈ 0.002). This comparison was not pre-registered, and it credits the council's best reviewer per
PR against the model's majority of three runs. Both sides were recorded in the same month on the
same diffs, so the time gap of the commercial comparison does not arise here, and any exposure of
the PRs in training data applies to both.

**An agent that reads the findings keeps only part of them.** Given the council's findings as
notes, the model caught 26, not 37; against the model alone that is no difference shown (p = 0.69).
In an exploratory breakdown it took up 3 of the 4 bugs only the council caught that three or four
reviewers shared, and none of the 11 that one or two raised: it kept the consensus and dropped the
long tail.

**So 4take puts every finding in front of you.** Over MCP the user sees only what the agent
relays, so the payload opens with `report_for_user`: one line per finding with its agreement
(`3/4`), severity, symbol and title. The tool description asks the agent to show those lines, to
act on agreement 3 or more, and to check agreement 1 or 2 against the code instead of dropping it.
The CLI prints the same ranking to your terminal.

**Agreement tells you where to look first.** On the eval corpus, over three recordings with four
reviewers seated, a finding all four raised held a real, labelled defect 96% of the time; one
raised by two or three held one 60% of the time; one raised by a single reviewer 41%. A single
reviewer's finding is a pointer to check, not a verdict, and four in ten of them are real.

---

## 🔎 What the Council Reviews

The code you pipe in was, more often than not, written by a capable model. Syntax errors and
typos are already gone, and a linter has effectively already run. So the reviewers are asked
for the four things a linter cannot see, in this order:

| Axis | What it catches |
| :--- | :--- |
| **Correctness** | Wrong results, unhandled edge cases, broken invariants, races, resource leaks, error paths that swallow a failure. |
| **Security** | Injection, path traversal, missing authorization, unsafe deserialization, secrets in code or logs, non-constant-time secret comparison. |
| **Structure** | Responsibility in the wrong layer, a boundary that leaks, coupling that makes one behavioural change require edits in several places, logic duplicated where it will drift. |
| **Simplicity** | Indirection with one implementation, a config knob nothing sets, a hand-rolled version of what the stdlib already provides, control flow that can be halved. |

The last two axes are the reason the reviews are worth reading on code that already works,
and they are also the easy way to ruin a review tool. A structural finding is only reported
when the model can name its cost — the change that becomes expensive, the two places that
will drift apart, the code that disappears once it is fixed. "Consider extracting a helper"
is not a finding. An empty review is a valid result.

4take reviews code. A design question or a plan piped in is refused before any request goes
out, because the reviewers are told their input is code and would all answer
`NO_ISSUES_FOUND`. Pass `--force-review` to send one anyway.

---

## 🧭 Giving the Council Context

A diff shows what changed, not what depends on it. A function that now returns `None` where it
used to raise, a route added without the check its neighbours make, a helper that already exists
under another name: none of these is visible in the changed lines alone. Sending the whole
repository is no answer either. It multiplies every request by the number of reviewers, and a
change buried in its surroundings is reviewed worse, not better.

So 4take takes a few related files as **reference context**, which the reviewers read but do not
review:

```bash
git diff -W | 4take --context-file src/mailer.py --context-file src/users.py:40-90
```

- Send what the change touches: each changed function in full (`git diff -W` includes it in the
  diff), its direct callers, the functions it calls, the types, schemas and config it relies on,
  related tests. Most relevant first.
- A file the diff itself changes can be sent too, when the reviewers need the rest of it (the lock
  every other method takes). It is read as it stands after the change and marked that way, so the
  reviewers know the changed code in it is the code under review, not a second copy. The smallest
  free models sometimes report it as defined twice anyway.
- `path:START-END` sends only those lines. Context is capped at 16000 tokens by default
  (`--context-budget`, or `context_budget_tokens` in the config); a file that does not fit is
  left out whole and named, never cut.
- Findings stay on the code under review. A defect the change causes in a caller is reported at
  the changed symbol, with the caller named.
- Context is redacted like the input, and read only from under the working directory (or
  `FOURTAKE_CONTEXT_ROOT`). `.env` files, keys, certificates, `.git/` and `.ssh/` are refused.
- `--dry-run` prints exactly what would be sent, after redaction, and contacts nothing.
- Context is read from disk as it is now: to review `gh pr diff 42`, check out the PR branch first.

### Letting 4take choose

```bash
git diff -W | 4take --auto-context
```

With a diff, `--auto-context` (MCP: `auto_context=true`) finds the context itself, by name:

- code that still uses a definition or a string value the change removes (a renamed function, an
  event name, a cache key);
- callers of the functions the change edits, in files that import their module;
- the rest of a class the change edits, outside the diff (the lock every other method takes);
- where the functions, types and constants the new lines use are defined, and the module they
  read settings from.

It is lexical, names and indentation rather than a parser, so it works the same in every
language and is wrong the way grep is wrong. A name found in more than 25 files is taken for a
word and not followed; docs, lock files, saved diffs, data files, docstrings, comments and imports
never count as a use. It sends at most 8 excerpts, after any `--context-file`, within the same
budget, and `--dry-run` shows what it chose. The JSON summary's `context.auto` and the text
report say why each excerpt was picked. A hunk is found in the file where it now stands, so an
unstaged edit above it does not throw the selection off; a file the diff does not describe (a diff
of two other revisions) is left out rather than excerpted at the wrong lines.

Measured on the 14 cross-file cases of `evals/corpus_ctx`, put in one repository so that every
other case's files are distractors. Offline, every diff selects all of the context chosen for it
by hand and nothing from the other 13 cases. Recorded three times, the councils then named 12 of
12 cross-file defects in every recording, as with hand-picked context; the clean controls drew 3
to 8 findings against 4 to 9, and the median council took 39 s against 38 s
([details](#what-reference-context-adds)). The corpus's context is a few small files that are
easy to find by name, so this shows auto context matching the hand-picked choice on the cases it
was built for, not on a large repository. It stays opt-in: it sends more of your code to the
providers than the diff you piped.

A review without context is sent exactly the prompt it was sent before, so the measurements
below still describe it.

---

## 🏗️ How It Works

```text
                           ┌───────────────────┐
                           │   Host / Caller   │  (Developer, Claude Code, Antigravity, Cursor)
                           └─────────┬─────────┘
                                     │ pipe code / stdin
                                     ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ 4take Core Engine                                                           │
│                                                                             │
│  1. Safety Sanitizer      — Redact secrets & sensitive credentials          │
│  2. Token Estimator       — tiktoken context verification                   │
│  3. Model Auto-Discovery  — Heuristic prior, then measured success rate     │
│  4. Free-Model Guard      — Hard check: only :free models, paid refused     │
│  5. Token Bucket Limiter  — Shared cross-process rate governor              │
│  6. Async Parallel Pool   — Query N models concurrently                     │
│  7. Dynamic Rotation      — Auto-swap failing models on HTTP 429 / errors   │
└─────────────────────────────────────────────────────────────────────────────┘
                                     │
                                     ▼
                      ┌─────────────────────────────┐
                      │   OpenRouter API Gateway    │
                      └──────────────┬──────────────┘
                                     │
           ┌─────────────────────────┼─────────────────────────┐
           ▼                         ▼                         ▼
   NVIDIA Nemotron              Poolside Laguna           Cohere North
   (120B / 550B:free)           (S / XS:free)             (mini-code:free)
                                     │
                                     ▼
                   ┌───────────────────────────────────┐
                   │  Formatted Markdown / JSON Output │
                   └───────────────────────────────────┘
```

---

## 💻 CLI Usage & Recipes

### 1. Review Staged Git Changes Before Committing

```bash
git diff --cached | 4take
```

### 2. Review a Specific GitHub Pull Request

```bash
gh pr diff 42 | 4take
```

### 3. Use Custom Free Models from OpenRouter

```bash
4take --models="nvidia/nemotron-3-super-120b-a12b:free,poolside/laguna-xs-2.1:free,cohere/north-mini-code:free,dots-studio/dots-3-note-preview:free"
```

### 4. Output as JSON for Automation

```bash
cat src/engine.py | 4take --format json --accept-risk > review.json
```

### 5. Save Report to a Markdown File

```bash
cat complex_algorithm.py | 4take --output-file review_report.md
```

### 6. Set Minimum Required Responses

```bash
# Succeed if at least 2 models respond
cat script.py | 4take --min-responses 2
```

### 7. Review a Diff Together with the Code It Touches

```bash
# Callers and types are read as reference context; only the diff is reviewed
git diff -W | 4take --context-file src/mailer.py --context-file src/models.py:10-60

# Or let 4take find them
git diff -W | 4take --auto-context

# See exactly what would be sent, without sending it
git diff -W | 4take --dry-run --context-file src/mailer.py
```

---

## ⚙️ CLI Options Reference

| Option | Env Var | Config Key | Default | Description |
| :--- | :--- | :--- | :--- | :--- |
| `--models` | — | `default_models` | `["auto"]` | Comma-separated list of free OpenRouter models (or `auto`). |
| `--min-responses` | — | `min_responses` | `4` | Minimum successful responses required. |
| `--timeout` | — | `timeout` | `600` | HTTP request timeout in seconds. |
| `--format` | — | — | `text` | Output format: `text` (rendered Markdown) or `json`. |
| `--output-file` | — | — | — | Write output to specified file path. |
| `--quiet` | — | — | `False` | Disable live progress spinners and status logs. |
| `--accept-risk` | `FOURTAKE_ACCEPT_RISK=1` | — | `False` | Silence external LLM privacy warning banner. |
| `--force-review` | — | — | `False` | Review the input even though it does not look like code. |
| `--context-file` | `FOURTAKE_CONTEXT_ROOT` | — | — | Related code the reviewers read but do not review, `PATH[:START-END]`, repeatable, most relevant first. Read only under the working directory, or under `FOURTAKE_CONTEXT_ROOT`. |
| `--auto-context` | — | — | off | With a diff: also send what the change breaks or relies on, chosen by name, after any `--context-file`. See [Letting 4take choose](#letting-4take-choose). |
| `--context-budget` | — | `context_budget_tokens` | `16000` | Most reference context one review may carry, in tokens. Files that do not fit are left out whole and named. |
| `--dry-run` | — | — | `False` | Print exactly what would be sent, after redaction, and exit without contacting any model. |
| `--strict` | — | — | `False` | Report only findings more than one reviewer raised, plus any `CRITICAL` one. Text output also omits the individual reviews and says how many findings it hid; errors are always shown. |
| `--no-strip-secrets`| — | — | `False` | Disable automatic redaction of API keys/passwords. |
| `--show-redactions` | — | — | `False` | List which secret patterns were redacted before sending. |
| `--init-config` | — | — | — | Create template config at `~/.config/4take/config.json`. |
| `--per-model-timeout` | — | — | half of `--timeout`, min 60 | Abandon one model after N seconds and rotate in a replacement. |
| `--no-cache` | — | — | `False` | Bypass the local 24h SHA-256 review cache. |
| `--clear-cache` | — | — | — | Purge `~/.cache/4take/reviews/` and exit. |
| `--no-telemetry` | — | `telemetry` | `False` | Do not record model latency and success rate locally. |
| `--clear-telemetry` | — | — | — | Delete `~/.cache/4take/stats.json` and exit. |
| `--version` | — | — | — | Print `4take` version and exit. |

---

## 🚦 Exit Codes

`stdout` carries only the payload — every log, spinner and warning goes to `stderr`, so
`4take --format json | jq` and `json.loads(stdout)` are safe.

| Code | Meaning |
| :--- | :--- |
| `0` | The council met `--min-responses`. |
| `2` | Partial result: at least one review, fewer than `--min-responses`. See `warning`. |
| `1` | Total failure: no model responded. See `error`. |

---

## ⚙️ Configuration File

Generate a starting configuration file:

```bash
4take --init-config
```

This creates `~/.config/4take/config.json`:

```json
{
  "default_models": [
    "auto"
  ],
  "providers": {
    "openrouter": {
      "api_key": "YOUR_OPENROUTER_API_KEY_HERE"
    }
  },
  "rpm_limit": 10,
  "min_responses": 4,
  "timeout": 600,
  "provider_concurrency": {
    "openrouter": 4
  }
}
```

---

## 📊 OpenRouter Free Tier & Rate Limits

By default, `4take` dynamically discovers and uses OpenRouter's top free models (`:free`). A review
is not four requests. It launches six models at once, four seats and two spares, so that one slow
model cannot stall it, and every retry after a 429 and every replacement for a failed model is one
more request. On recorded runs a review took 6 requests when the pool was quiet and a median of
11 to 12 on a busy day, when about half of them failed:

- **An account that has never been topped up**: OpenRouter limits free-model use to **50 requests
  per day**: about **4 reviews** on a busy day, up to **8** on a quiet one.
- **An account that has been topped up with $10 or more at least once**: the limit rises to **1,000
  requests per day**: about **80 reviews** on a busy day, up to **160** on a quiet one.

OpenRouter sets these limits and can change them. They are why 4take is free to run but not
unlimited.

If a model hits an HTTP 429 rate limit, `4take` retries it up to eight times with exponential
backoff and jitter, then rotates in a candidate from its 12-model backup pool. A model whose
provider refused every request of the last 72 hours is left out of the pool until those refusals
age out. The 429s come from the free pool's global load, not from your own usage, so a quiet hour
is the best way to get full councils.

---

## 🛡️ Privacy & Security

- **Automatic Secret Redaction**: Matches and sanitizes API keys, auth tokens, and credentials from code snippets before sending.
  Known key formats (OpenAI/OpenRouter, Stripe, GitHub, GitLab, Slack, Google, AWS, Groq,
  Telegram, JWTs, PEM keys, credentials in connection strings) are redacted wherever they appear.
  A value assigned to a secret-named key is redacted when it is quoted, or where config puts an
  unquoted one: at the end of its line, or right after a tight `KEY=`. A value code computes —
  `token = await fetchToken()`, `password: string` — is left alone, so the council reviews the
  code as written. It is a safety net, not a guarantee: `--dry-run` shows exactly what would be sent.
- **Privacy Warning Banner**: Alerts users when code leaves local machine (silence with `--accept-risk` or `export FOURTAKE_ACCEPT_RISK=1`).
- **Free endpoints are run by third parties.** Your code goes to OpenRouter and from there to
  whichever provider serves the free model. Providers set their own policies, and some may retain
  prompts or train on them. OpenRouter has account privacy settings for this, separate for free and
  paid models: check them, and the provider's terms, before you send code you cannot share.
  `--dry-run` shows exactly what would be sent. On first use `tiktoken` may also download its
  tokenizer vocabulary from its default host; no code is sent there.
- **Context Length Safeguard**: Estimates input size with `tiktoken` and checks it against each
  model's real context window from the OpenRouter catalog, reserving room for the reply.
- **Reference Context Guard**: Context files are read only from under the project root, after
  symlinks are resolved. Credential files, `.git/`, `.ssh/`, binary and oversized files are
  refused, and everything sent is redacted. In the MCP server the paths come from an agent, so a
  server started from `/` or the home directory reads no paths unless `FOURTAKE_CONTEXT_ROOT` names
  the project.

---

## 🛠️ Development & Testing

```bash
# Clone the repository
git clone https://github.com/aleksbuss/4take.git
cd 4take

# Install dependencies
uv sync --all-extras --dev

# Run test suite
uv run pytest
```

### Measuring the council

The claim that several models find more than one does is measured, not asserted. `evals/`
holds 28 snippets with 37 labelled defects — SQL injection, TOCTOU, off-by-one, resource
leaks, unchecked `None`, races, traversal — plus two deliberately clean controls.

The labels cover defects only, so the eval measures the correctness and security axes and
says nothing about structure or simplicity. The clean controls are clean of *defects*; now
that the reviewers are asked for structure too, a finding raised on one may be legitimate.
That column is still the check on a model rewarded for reporting everything, but it has to
be read rather than counted, and it is expected to be non-zero.

```bash
uv run python evals/score.py          # scores the checked-in recordings, offline and free
uv run python evals/record.py         # re-records them against live models (the only network step)
```

The report gives council recall against the best single model that saw the same corpus,
per-model label precision, noise on the clean controls, the pairwise agreement matrix, how
often a defect several models found lands in one consensus entry, how often two findings one
model wrote separately share an entry, and how often an entry at each `agreement` holds a
labelled defect. The scoring runs in CI against the recorded fixtures, so it stays hermetic
and costs nothing.

Free models are stochastic, so one recording of the corpus is one draw. It is recorded three
times — `evals/recordings` (scored in CI), `evals/runs/2` and `evals/runs/3` — and
`uv run python evals/score.py --runs evals/recordings evals/runs/2 evals/runs/3` prints the
spread:

| measurement | range over 3 recordings |
| :--- | ---: |
| council recall | 97–100% |
| best single-model recall | 91–94% |
| lift from the council | +4 to +6 points |
| findings raised on the two clean controls | 1–7 |
| agreeing models landing in one consensus entry | 56–69% |
| ... or in at least one entry that holds all of them | 81–94% |
| pairs of one model's own findings sharing an entry | 22–42% |

The lift is real and modest, and it held in every recording. Recall alone is easy to game — a
model that reports everything scores well on it — which is what the label-precision and
clean-control columns are there to expose.

The grouping rows are the weak point, and the first two reward merging: an entry that
swallows a whole snippet groups every defect in it. The third row is their check, because a
model that writes two findings mostly means two defects. Grouping was at 74% while the
reviewers were asked for defects alone; asking them for structure and simplicity too widened
the vocabulary they write in, and grouping is lexical, so two models can raise one finding in
different words and still land in two entries. It also used to depend on the order the models
answered in: shuffling one recording's responses moved it anywhere from 44% to 69%. It is now
single linkage, which no order can change, with the threshold raised against the chaining
single linkage brings. Pooled over the three recordings, 61% of agreed defects land in one
entry, as before (62%), and 31% of one model's pairs of findings share an entry, against 44%.
A closed category vocabulary was tried as the signal that does not dilute with wording and
carried little: reviewers filing one labelled defect chose the same category in only 51–59% of
pairs. Used as a veto it split real agreement; used to lower the threshold it merged distinct
defects about as often as agreeing ones. So read an `agreement: 1` entry on your own judgement
rather than as a weak signal.

What an `agreement` value is worth, pooled over the three recordings, in which every council
seated four reviewers (`score.py --runs` prints this table):

| agreement | entries holding a labelled defect |
| :--- | ---: |
| 4, every reviewer | 96% (68 of 71) |
| 3 | 58% (11 of 19) |
| 2 | 62% (16 of 26) |
| 1 | 41% (50 of 123) |

Only unanimous agreement is strong evidence. Two or three reviewers agreeing is a lead to
verify: those rows are small, cannot be told apart, and about four in ten of their entries
held no labelled defect. A single reviewer's finding is right often enough to be judged
rather than dismissed. Every share is a floor, because the snippets hold defects nobody
labelled.

### What reference context adds

The corpus above is self-contained snippets, so it cannot say anything about defects that live
between files. `evals/corpus_ctx` holds 12 of those: a function that now returns `None` to
callers that dereference it, a unit change a watchdog still compares in seconds, an event
renamed under its subscribers, a helper that runs its arguments through a shell. It also holds
three defects visible in the diff itself, to check that context does not cost them, and two
clean controls where the diff looks suspect alone and the context shows it is fine. Each case is
recorded twice, without and with its context, back to back, and once more with the context
`--auto-context` chooses from a repository holding every case's files:

```bash
uv run python evals/record.py --corpus ctx   # 28 councils, roughly 180 requests of free-tier quota
uv run python evals/record.py --corpus ctx --mode auto   # 14 councils, context chosen by --auto-context
uv run python evals/score.py --ctx           # cross-file and local recall, noise and time, side by side
```

Each cross-file defect is counted twice. It is flagged by a finding that warns about it in any
words, since a review of the diff alone can infer that callers may break, and named only by a
finding that names what the context holds, such as the caller a change breaks; a test keeps every
naming keyword out of the reviewed diff. The flagged row is the fair comparison of how many breaks
each mode caught, and the gap between the two rows is what the context adds beyond inference. The
report also counts findings the reviewers located in the context, which the prompt forbids, and
separates a right finding filed under the caller from noise.

The corpus has been recorded three times: `evals/recordings_ctx`, `evals/runs_ctx/2` and
`evals/runs_ctx/3`. `score.py --ctx` given all three directories prints the table below:

| | Without context | With context | Auto context |
|---|---|---|---|
| Cross-file defects flagged | 5–7 of 12 | 12 of 12 in every recording | 12 of 12 in every recording |
| … naming the affected code | 0–1 of 12 | 12 of 12 in every recording | 12 of 12 in every recording |
| Local defects found | 2–3 of 3 | 3 of 3 | 2–3 of 3 |
| Findings on the two clean controls | 4–6 | 4–9 | 3–8 |
| Median council time | 31 s | 38 s | 39 s |

Read it with its limits:
- **Written for this feature.** Every case's context is exactly the right few files. So it
  shows that the mechanism works, not how much it adds to real changes, where the chairman has
  to choose those files.
- **The matcher was corrected after the first recording and frozen before the other two.** The
  corrections are listed under `_about` in `evals/labels_ctx.json`. Before them the first
  recording read 7/12 → 10/12 flagged and 3/12 → 10/12 named.
- **The matcher still errs in favour of the review without context.** Read by hand, each of the
  two later recordings credits that review with one defect it did not flag: a "duplicate check"
  in one, a cache-miss finding in the other. The 2/3 local result is the matcher's miss, not the
  reviewers': they found the defect and located it elsewhere. The one naming without context,
  in recordings 2 and 3, is a guess: "if `run_git` uses `shell=True`".
- **Noise on clean code went up with context, not down.** The controls were built to provoke
  "no input validation" and "no lock" findings that the context disproves. The context removed
  most of those. But when it repeats the file after the change, reviewers take the changed
  method for a second definition of itself: 2 to 5 such findings per recording, most of the
  rise.
- **Marking that file helped the stronger reviewers only.** A context file the diff changes is
  now headed as the file after the change. Across three further recordings of the two cases
  where this applies (`evals/runs_ctx/overlap`), duplicate findings fell from 8 to 5.
  - Every remaining one came from the smallest model seated, `nemotron-3-nano-omni-30b`, which
    made the claim in two of the three.
  - `north-mini-code` made it before the mark, and was seated twice after it without doing so.
  - Such a finding comes from one reviewer, so its agreement is 1.
- **Auto context was recorded days after the other two columns,** from a different free pool,
  so the columns are three separate draws rather than one pool read three ways.
  - Its context is the hand-picked files in 12 cases. In the two where that file is the changed
    one, it sends the file without the lines the diff shows.
  - There, the "method defined twice" claims went from 8 across the hand-picked recordings to 0.
    But `nemotron-3-nano-omni-30b`, which made 7 of the 8, was never seated in that case under
    auto context. `north-mini-code` made the claim once with the whole file, and sat once
    without it and did not. A hint, not a result.
  - Its 2 of 3 local defects is a council that seated one reviewer: six models timed out at once
    and two were rate-limited.

---

## 📄 License

4take is source-available under the [Business Source License 1.1](https://github.com/aleksbuss/4take/blob/main/LICENSE). The license text
decides; this is what it says in plain words:

- **Free for you.** A person using 4take for their own work, paid or unpaid, may use it in full,
  at no cost. So may a self-employed developer who works alone (no employees, no subcontractors,
  no company with staff under their control), in their own name or through a company only they
  own: on software they own, and for clients, as long as it does not run on the client's machines
  or accounts and the client does not ask for it, require it or pay for it.
- **Free for volunteer projects**, in their CI and accounts: public code under an
  OSI-approved license, not owned or controlled by any organization other than a non-profit that
  stewards open source, and not sold or the basis of anything sold by its owners, its maintainers
  or an organization paying people to maintain it. Donations, sponsorship and grants do not make a
  project commercial. Volunteers contributing in their own time are covered as individuals.
- **Organizations need a commercial license:** companies, public bodies, universities and other
  non-profits. That covers anyone using 4take in their work for the organization, and any use the
  organization sets up, requests, requires, pays for or runs (in its CI, on its servers, under its
  accounts), even when a contractor does the running. Reviewing any software with 4take is
  production use, on a laptop or in CI alike. An organization may evaluate 4take for 30
  consecutive days, once for its whole group of companies, and anyone may test and develop 4take
  itself.
- **No hosted service.** Offering 4take or what it does to others as a hosted or managed service
  is not covered by any of the free grants.
- **Every version converts to the Apache License 2.0** four years after it is first published.

For a commercial license, write to takelicensing@gmail.com. The code is open to read, change and
redistribute under the same terms. A pull request can only be merged once its author agrees that
the contribution may be distributed under this license and under commercial ones.
