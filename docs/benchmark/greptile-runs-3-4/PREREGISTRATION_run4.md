# Pre-registration: Greptile benchmark, run 4 on the reasoning fix

Written 2026-10-08, before any council of run 4 was recorded. Its sha256 is recorded outside this
file at the same time. Any departure from it will be listed in DEPARTURES_run4.md, with its reason.
Everything not stated here is as in PREREGISTRATION_run3.md (sha256 1d7c9705…fe80).

## Why run 4

Run 3 measured 4take 0.2.0 (706e96c) while free reasoning models came back empty: their providers
ignore the reasoning cap and the 2048-4096-token budget left no room for an answer, so councils on
diffs seated 1-2 vendors (found 2026-10-07, after run 3 was recorded). Run 3's graders also ran as
subagents that had the earlier totals in their context (DEPARTURES_run3.md §3). Run 4 measures the
fix and grades both runs again, cleanly and blind to the run.

## What is recorded

- Code: commit 2f07956 on branch `fix/reasoning-room` (`__version__` still 0.2.0):
  - a reasoning model gets up to 32768 output tokens; reasoning is never switched off, and a model
    that thinks through its whole budget gives up its seat to another model;
  - a model whose provider refused every request of the last 72 hours (at least 8) is left out of
    the pool; on the history at the start of the run that is the two gemma models only;
  - a 429 is retried up to 8 times instead of 4.
  `run.py` refuses to record if `src/` or `pyproject.toml` differs from the commit and writes the
  commit, version, attempt and start time into each fixture's `_eval`.
- Invocation, config, case order and labels: as in run 3 (`--format json --accept-risk --quiet
  --no-cache --no-telemetry --timeout 600`, `review.diff` as the only input, no reference context,
  no `--models`, `min_responses: 4`, `rpm_limit: 20`, OpenRouter concurrency 4, account not on the
  free tier). Output: `runs/4/<case>.json`.
- `--no-telemetry` means the run adds nothing to the history; the pool is ranked from the history
  as it stood when the run began.
- Diagnostics only: a `sitecustomize` hook on `PYTHONPATH` (`hook/sitecustomize.py`) appends, for
  every HTTP request, the model, `max_tokens`, the `reasoning` parameter, status, finish reason and
  token counts to `runs/4/requests.jsonl`. It changes no request. Like the rest of this directory
  it is not committed.
- Probe before the run: the 8 frontier diffs, twice on 2026-10-08 on this code's predecessors
  (b9c9d4e, then c88e761): 8 of 16 councils seated four vendors, 5 three, 3 one or two.

## Which councils count

As in run 3: every council that produced a fixture is kept as recorded, whatever its seat count; a
case is recorded again only when its council produced no fixture, or when the daily quota ran out
during it; only seated reviews are graded.

Departure from run 3, declared here: a sitting that stops on two impaired councils in a row (exit 2
with a provider error) is resumed automatically after 15 minutes, on the same day. On diffs a
council short of four seats, with some 429s on the way, is now the ordinary outcome (8 of the 16
probe councils), not a sign that the provider is down, so the run 3 rule would stop nearly every
sitting. A sitting that stops on a spent daily quota resumes 5 minutes after the reset (00:00
UTC). A driver loop does both and logs every stop and resume; it gives up after 6 sittings in a
row that record nothing.

## Rubric

Identical to run 3: HIT, PARTIAL, MISS per seat; a case's verdict is its best seat; strict = HIT
on the 42 labels not shown to be wrong; benchmark standard = HIT or PARTIAL on all 50, with the 8
bad labels graded against the readings fixed in PREREGISTRATION_run3.md.

## Grading

- Starts only after all 50 cases of run 4 are recorded.
- Run 3 and run 4 are graded together: 100 packets (50 cases x 2 runs), built by script exactly as
  in run 3 (label, readings for the 8, PR title, `review.diff`, every seated review's full content,
  seats shown as Seat A, B, ... in a per-packet random order). Packets carry random ids; which run a
  packet comes from, and the model names, stay in a key outside the packets. The packet order is
  shuffled with a recorded seed.
- Two independent graders, grader A Claude Opus 5.5 and grader B Claude Sonnet 5.5, each run as
  `claude -p` from an empty directory under /private/tmp, so that no CLAUDE.md, memory or earlier
  verdict is in its context. Each sees only the packets and the rubric and grades every seat HIT,
  PARTIAL or MISS, with a verbatim quote of at most 40 words and a one-sentence reason for every
  HIT or PARTIAL.
- Quotes are checked by script against the seat's content (whitespace normalised); a quote that is
  not there sends the packet back to the same grader.
- Where A and B agree on every seat of a packet, that is the verdict. Otherwise a third Opus 5.5
  grader, isolated the same way, reads the packet and both graders' grades, quotes and reasons
  (unnamed) and rules on each disputed seat.
- These clean grades of run 3 replace its earlier ones for every comparison made here; the earlier
  grades are reported beside them.

## Analysis

Primary (run 4):
- Strict on the 42.
- Benchmark standard on the 50.

Secondary:
- Run 4 against run 3, paired by case, both on the clean grades: exact two-sided McNemar, strict on
  the 42 and benchmark standard on the 50.
- Seats and vendors per council, council wall time, and for each seated review whether its answer
  came after reasoning (from the request log); councils in which a model reasoned through its whole
  budget.
- Cases caught by two or more seats of different vendors.
- Agreement between graders A and B (Cohen's kappa on packet verdicts) and the lower bound that
  credits a case only where both do.

Comparisons with the other tools, as in run 3: exact two-sided McNemar, paired by case, strict on
the 42 against the blind strict grades of the competitors (Greptile 32, Bugbot 25, Copilot 19,
CodeRabbit 16, Graphite 2) and benchmark standard on the 50 against the published credits
(Greptile 41, Bugbot 29, Copilot 26, CodeRabbit 22, Graphite 3); Wilson 95% intervals for 4take.

To be disclosed with the results: one recording is one draw from a stochastic free pool; the pool
differs from run 3 (the stealth model space-bunny-alpha, ling-3.0-flash-vl and nex-n2.5 are no
longer free; apodex-1.1-mini is new); the graders are Claude models that can tell the reviews come
from an AI council.
