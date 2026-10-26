# AGENTS.md — AI Agent Integration Guide for 4take

This guide specifies how autonomous AI coding agents (Claude Code, Cursor Composer, Antigravity IDE, Windsurf, Codex, Devin, Cline, Roo Code, Aider) should interact with `4take`.

---

## 🎯 Purpose of 4take for AI Agents

When generating complex algorithms, database migrations, security-critical logic, or large refactors, a single LLM is prone to subtle blindspots and confirmation bias. 

`4take` provides you (the agent) with an independent **Council of 4 LLMs** to audit your code in parallel before you present it to the user.

Whether the council improves what a given agent ships has not been measured yet. Treat its output as leads to verify, not verdicts; step 2 of the synthesis framework below gives the measured hit rates.

The council is told its input was written by a capable model, so it is not looking for syntax
or typos. It reviews four axes: **correctness**, **security**, **structure** (responsibility in
the wrong layer, coupling that spreads one change across several files, logic duplicated where
it will drift) and **simplicity** (indirection with one implementation, a hand-rolled stdlib).
A structural finding is only reported when the model can name its concrete cost.

It reviews code. Piping in a design question or a plan is refused before any request goes out;
pass `--force-review` (CLI) or `force=true` (MCP) to override.

---

## 🛠️ Integration Methods

### Method 1: FastMCP Server (`get_4take_opinion`)

If your agent runtime supports the **Model Context Protocol (MCP)**:

- **Server Entrypoint:** `4take-mcp` (or `uvx --from '4take[mcp]' 4take-mcp`). The MCP
  server ships as the `mcp` extra: `pip install '4take[mcp]'`.
- **Server output:** first `report_for_user`, a list of lines to show the user verbatim or close
  to it: a header with the [calibration](#-the-agent-as-chairman-4-step-synthesis-framework) and
  what follows from it (agreement 3 or more: confirm in the code, then fix; 1 or 2: check against
  the code before accepting or rejecting it), then one line per `consensus` entry, most-agreed
  first, such as ``- 2/4 WARNING `page_size` — Unbounded limit``. The denominator is the number
  of independent reviewers that answered. Show every line, the ones you go on to reject included:
  the user sees only what you relay. Then the same `consensus` list the CLI emits, plus `status`
  (`ok` / `degraded` / `error`), `distinct_models` and `min_responses`. `degraded`
  means fewer independent reviewers answered than were asked for; a `warning` alongside
  `status: ok` is informational, not a failure. Only a review fills a seat: an answer with
  neither a finding nor `NO_ISSUES_FOUND` (a tool call, garbage tokens, reasoning that never
  reached a tag) is counted as a failed model and replaced by a spare.
- **Tool Name:** `get_4take_opinion`
- **Arguments:**
  - `code` (string, required): The source code, git diff, or implementation to review.
  - `force` (boolean, optional, default `false`): Send the input even though it does not look
    like code. Without it, prose is refused rather than dispatched.
  - `context_files` (list of strings, optional): Paths of code related to the change, which the
    reviewers read but do not review. The server reads them from disk, under the project root,
    so you do not paste them. `path:START-END` sends only those lines. See
    [Choosing the context](#-choosing-the-context-the-chairmans-first-job).
  - `context` (string, optional): The same kind of related code as text, when the server cannot
    read your disk.
  - `auto_context` (boolean, optional, default `false`): With a diff, also let the server choose
    context by name: what still uses what the change removes, the callers of what it edits, the
    definitions of what it calls. Sent after `context_files`, within the same budget.
- **Return Value:** JSON string: `report_for_user` first, then the fields above and the
  `responses` array with each model's independent critique and metadata.

---

### Method 2: Shell / CLI Subprocess

If invoking `4take` via bash / shell commands:

```bash
# Recommended command for agents:
cat path/to/file.py | 4take --format json --accept-risk --quiet
```

#### CLI Flags for Agents:
- `--format json`: Ensures machine-readable JSON output on stdout instead of ANSI Markdown.
- `--accept-risk`: Bypasses terminal privacy prompts (or set `FOURTAKE_ACCEPT_RISK=1`).
- `--quiet`: Suppresses live progress logs and diagnostics.
- `--min-responses N`: (Optional) Minimum successful model responses required (default: 4).
- `--per-model-timeout N`: (Optional) Abandon a single model after N seconds and rotate in a
  replacement. Defaults to half of `--timeout` (minimum 60s).
- `--force-review`: (Optional) Review an input that does not look like source code. Without
  it, prose is refused with exit code 1 instead of spending four requests to say nothing.
- `--strict`: (Optional, usually leave off) Narrow `consensus` to findings more than one reviewer
  raised, plus any `CRITICAL` one. Step 3 below needs the single-reviewer entries it drops.
- `--context-file PATH[:START-END]`: (Optional, repeatable, most relevant first) Related code the
  council reads but does not review. Read only under the working directory. See
  [Choosing the context](#-choosing-the-context-the-chairmans-first-job).
- `--auto-context`: (Optional) With a diff, also choose context by name, after any
  `--context-file`. `summary.context.auto` says why each excerpt was chosen.
- `--context-budget N`: (Optional) Cap on reference context in tokens, default 16000. A file that
  does not fit is left out whole and named in `summary.context.dropped`.
- `--dry-run`: Print exactly what would be sent, after redaction, and exit without contacting
  any model.
- `--no-cache` / `--clear-cache`: Bypass or purge the local 24h review cache.
- `--no-telemetry` / `--clear-telemetry`: Skip or erase the local record of model latency and
  success rate. It stores model ids and timings only — never code, reviews or prompts — and
  is what ranks the model pool on evidence instead of on model names.

**stdout carries only the JSON payload.** All logs, progress and warnings go to stderr, so
`json.loads(stdout)` is safe.

#### Exit Codes
| Code | Meaning |
| :--- | :--- |
| `0` | The council met `--min-responses`. |
| `2` | Partial: at least one review, but fewer than `--min-responses`. Check `warning`. |
| `1` | Total failure: no model responded. Check `error`. |

A large diff can take up to `--timeout` (600 s) and often ends with exit code `2` and fewer than
four reviewers: few free models reason through one to an answer. A partial council is still a
usable review; read `agreement` against `summary.distinct_models`.

> **Free models only**: `4take` sends requests only to OpenRouter's free models (`:free` tier) and rejects a paid model before any request goes out. Do not pass `--models` with paid models. Free models are rate-limited: roughly 4 to 8 reviews a day on an OpenRouter account that has never been topped up, roughly 80 to 160 once it has. Do not call `4take` in a loop.

---

## 📜 Output JSON Structure

```json
{
  "version": "0.2.1",
  "timestamp": "2026-08-16T14:20:00Z",
  "summary": {
    "task_hash": "25d297f773c3",
    "responses_received": 4,
    "responses_failed": 0,
    "total_latency_ms": 3200,
    "context": {"files": ["src/mailer.py", "src/users.py (lines 40-90)"], "tokens": 812, "dropped": []}
  },
  "warning": null,
  "error": null,
  "consensus": [
    {
      "agreement": 2,
      "severity": "CRITICAL",
      "category": "INJECTION",
      "symbol": "search_users",
      "title": "SQL injection via the term parameter",
      "models": ["cohere/north-mini-code", "nvidia/nemotron-3-ultra-550b-a55b"],
      "severities": {"CRITICAL": 1, "WARNING": 1},
      "disputed": true
    }
  ],
  "responses": [
    {
      "model": "openrouter/openrouter/free",
      "served_model": "nvidia/nemotron-3-ultra-550b-a55b",
      "served_provider": "NVIDIA",
      "content": "Review text from model...",
      "finish_reason": "stop",
      "truncated": false,
      "duplicate_of": null,
      "latency_ms": 1150
    },
    {
      "model": "openrouter/cohere/north-mini-code:free",
      "served_model": "cohere/north-mini-code",
      "served_provider": "Cohere",
      "content": "Review text from model...",
      "finish_reason": "stop",
      "truncated": false,
      "latency_ms": 1420
    }
  ],
  "errors": [
    {
      "model": "failing/model",
      "error": "Reason for error",
      "latency_ms": 200
    }
  ]
}
```

---

## 🧭 Choosing the Context (the Chairman's first job)

The council sees only what you send. A diff alone hides every defect that lives between files: a
changed return value a caller still relies on, a route without the check its neighbours make, a
helper duplicated under another name. You have already read the surrounding code, so pass it:

1. Each changed function in full: `git diff -W` includes it in the diff itself.
2. Its direct callers, one hop away (grep for the changed names), most relevant first.
3. The functions it calls, and the types, schemas and config it relies on.
4. Related tests, when they encode the contract.

A few files or line ranges, not the repository: the budget is 16000 tokens, and whole files are
dropped past it. Prefer paths (`context_files`, `--context-file`) to pasted text, so you spend
no output tokens on them. Findings stay on the reviewed code; a caller the change breaks is named
in the finding's Issue. `summary.context` (CLI) or `context` (MCP) lists what was sent and what
the budget dropped. A finding that needed a caller you did not send is one the council could not
make.

`auto_context` (`--auto-context`) does steps 2 and 3 the way grep would: callers of the edited
functions that import their module, remaining uses of a removed name or string value, definitions
of what the new lines call, and the rest of an edited class outside the diff. It cannot know what
you know, so it comes after the files you name; name the ones that matter and let it add the rest.
Check `context.auto` for what it chose and why. On the cross-file eval corpus it chose the same
files as a careful chairman, and the council named 12 of 12 cross-file defects in each of three
recordings with it, against 0 or 1 with the diff alone.

---

## 🧠 The Agent as Chairman: 4-Step Synthesis Framework

When you receive the council's reviews:

0. **Check `truncated`**: a response with `truncated: true` was cut off by the output budget
   and is incomplete — weigh it accordingly, and do not treat its silence as "no issues".
1. **Read `consensus` first**: the findings are already grouped across reviewers and ranked
   by `agreement`. It is keyed on `served_model`, not `model`, so a router seating the same
   underlying model twice contributes one voice rather than manufacturing agreement. A council
   also seats one model per vendor while another vendor can answer; a response with
   `duplicate_of` set shadows an existing seat and is not counted — if you re-derive agreement
   from `responses`, skip those.
   `responses[i].content` is still there when you need a finding's full wording.
   `category` is the class the reviewers declared, and `UNKNOWN` when none did — it is read
   from the reviews, never guessed, so an `UNKNOWN` says nothing about the finding itself.
   Reviewers file one defect under the same category only about half the time: treat it as
   a label, never as evidence that two findings are, or are not, the same defect.
2. **Weigh `agreement` as measured**: over three recordings of the eval corpus, every council
   seating four reviewers, this is how often a consensus entry held a labelled defect:
   - `agreement: 4`, every reviewer: 96% (68 of 71). High confidence — confirm it in the code
     and fix it.
   - `agreement` 2 or 3: 60% (27 of 45). Verify before acting: about four in ten held no
     labelled defect. Taken apart, the two rows are too small to tell from each other (2: 62%
     of 26, 3: 58% of 19), so a third reviewer is not a reason to skip the check.
   - `agreement: 1`: 41% (50 of 123). Judge it on its merits (step 3).

   The shares are floors: the snippets hold defects nobody labelled, so an entry that matches
   no label is not proven wrong. Verify a security finding (`INJECTION`, `AUTH_CRYPTO`, or any
   `CRITICAL`) whatever its agreement. `disputed: true` means the reviewers agreed the defect is
   real and differed only on how bad it is — the severity shown is the worst one raised.
3. **Audit Outliers**: Review `agreement: 1` entries. Grouping is lexical, so one of these may be the same defect the others described in different words:
   - If it is a critical security vulnerability (SQLi, auth bypass, secret leak, buffer overrun), investigate and fix it.
   - If it is a structure or simplicity finding, judge it on the cost it names, not on how many
     models raised it. Four reviewers rarely word one design opinion the same way, so agreement
     is a weak signal on these axes and `agreement: 1` is the normal case, not a red flag.
   - If it is subjective stylistic preference or hallucinated API syntax, disregard it.
4. **Apply Fixes & Report**: Implement the corrected code and briefly summarize what the council caught for the user.
