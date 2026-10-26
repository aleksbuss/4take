# Pre-registration: Greptile benchmark, run 3 on 4take 0.2.0

Written 2026-09-30, before any council of run 3 was recorded. Its sha256 is recorded outside this
file at the same time. Any departure from it will be listed in the report, with its reason.

## Why run 3

Runs 1 and 2 were recorded on pre-release code (JSON `version` 0.1.0, about c640021 and d363a75),
before 0.2.0 seated one model per vendor. Two or three nvidia models held seats in 21 and 29 of
their 50 councils. 0.2.0 cannot seat those councils, so their results (benchmark standard 39 and 38
of 50, strict 27 and 28 of 42; RECHECK_2026-09-30.md) do not describe the shipped tool. Run 3
measures 0.2.0. It is not pooled with runs 1 and 2.

## What is recorded

- Code: commit 706e96c (4take 0.2.0), `.venv/bin/4take`, an editable install of `src/`. `run.py`
  refuses to record if `src/` or `pyproject.toml` differs from the commit, and writes the commit,
  the version, the attempt number and the start time into each fixture's `_eval`.
- Invocation, per case: `review.diff` (byte-identical to `gh pr diff` of the PR on the benchmark's
  fork) piped in as the only input, from the case directory, with
  `--format json --accept-risk --quiet --no-cache --no-telemetry --timeout 600`. No reference
  context, no `--models`: the pool is what 0.2.0 discovers.
- Config (`~/.config/4take/config.json`): `default_models: ["auto"]`, `min_responses: 4`,
  `timeout: 600`, `rpm_limit: 20`, OpenRouter concurrency 4. The OpenRouter account is not on the
  free tier (`is_free_tier: false`).
- Order: the 50 cases sorted by name. Output: `runs/3/<case>.json`.
- Probe before the run: one council on a 10-line snippet seated four vendors (stealth, poolside,
  inclusionai, cohere) in 15 s.

## Which councils count

- Every council that produced a fixture (exit 0 or 2, at least one response, JSON on stdout) is
  kept as recorded, whatever its seat count. Runs 1 and 2 discarded councils of fewer than four
  seats and re-ran them; run 3 does not.
- A case is recorded again only when its council produced no fixture (exit 1, zero responses,
  stdout not JSON, timeout), or when the daily quota ran out during it. Such a fixture goes to
  `runs/3/_discarded/`, and every attempt is logged in `runs/3/attempts.jsonl`.
- A spent daily quota, or two failed or degraded councils in a row, stops the sitting. It resumes
  on another day, skipping the recorded cases.
- Only seated reviews count: a response with `duplicate_of` set holds no seat in 0.2.0 and is not
  graded.

## Labels

Each case's label is the benchmark's `bugDescription` and `filePath` in its `meta.json`
(`cases.json` sha256 69d1785a…b333), with `severityDetail` as context. From the audit
(`audit_blind_run2/LABELS.md`, sha256 abee54fb…686a):

- 42 labels not shown to be wrong: 33 corroborated by two or more tools; keycloak-01 verified on the
  fork; sentry-03, keycloak-02, keycloak-10 and discourse-05 checked real; sentry-05 and
  discourse-09 weak; keycloak-04 and keycloak-05 not pinnable. Graded against the label as written.
- 8 labels shown to be wrong: calcom-02, calcom-04, grafana-02, grafana-03, grafana-08, sentry-01,
  sentry-04, sentry-09. These get no strict verdict. On the benchmark standard they are graded
  against the label as written or the reading the benchmark credited a competitor for, fixed here:
  - calcom-02: a used backup code can be used again, by any path including two concurrent logins
    consuming one code; or CodeRabbit's credited concern at next-auth-options.ts:156, that the
    backup-code check and its invalidation (the used code nulled in the stored list, `indexOf`
    timing) are unsafe.
  - calcom-04: SQL injection in the raw SQL built in insightsBooking.ts; or invalid SQL from that
    construction: `ANY()` with an empty array (Copilot), or NOTHING_CONDITION `(1=0)` composed with
    other conditions (Greptile).
  - grafana-02: permission denials that persist because cache entries do not expire, or a cached
    denial served without re-checking so a new grant is not seen; or Bugbot's credited cache-trust
    asymmetry in service.go, cached grants trusted while cached denials are not.
  - grafana-03: the endpoint* constants used in contextual_logger_middleware.go are undefined and
    the code will not compile (the claim Greptile was credited for; it is false).
  - grafana-08: the double-checked locking in GetWebAssets is incomplete, the cache not re-checked
    after the write lock is taken (all four credited comments); or a deadlock in that locking.
  - sentry-01: the imported OptimizedCursorPaginator does not exist (the claim Greptile was
    credited for; it is false).
  - sentry-04: a crash or None dereference when the github_authenticated_user state is missing;
    a `KeyError` on `metadata["sender"]["login"]` (Bugbot); or that comparison of the state with
    the sender login is not constant-time (Copilot).
  - sentry-09: `queue.ShutDown` or `Queue.shutdown()` does not exist (Greptile, Copilot; false on
    the pinned Python 3.13.1); a defect in the handling of `queue.ShutDown`; or message loss because
    the offset is committed although processing failed.

## Rubric

Graded per seat, then per case. A seat's grade is the best grade among its findings.

- HIT: a finding names the labelled defect: the same location (file or function) and the same
  mechanism, so that a developer reading only that finding would find this bug.
- PARTIAL: the right location and a related concern, but the mechanism is wrong, vague or generic
  (for example "consider try/catch" on an unhandled rejection; "check for null" without naming
  which value is null and why).
- MISS: anything else, including a different bug on the same line.

A case's verdict is the best seat grade. Strict = HIT, read on the 42 only. Benchmark standard =
HIT or PARTIAL, read on all 50. This is the rubric of the run-2 blind re-grade
(`audit_blind_run2/RUBRIC.md`), with the bad labels now 8 and graded as above.

## Grading

- Grading starts only after all 50 cases are recorded.
- Packets, one per case, built by a script from `runs/3`: the label (and, for the 8, the readings
  above), the PR title, `review.diff`, and every seated review's full `content` as returned. The
  markdown output prints that content, so it is what a user reads. Reviews are shown as Seat A-D
  in a per-case random order; the key to the model names stays out of the packets. Truncated
  reviews are included as they are. No competitor comments beyond the readings above, nothing from
  runs 1 and 2, no earlier verdicts.
- Two independent graders, fresh sessions that see only the packets and this rubric: grader A,
  Claude Opus 5.5, and grader B, Claude Sonnet 5.5. Each grades every seat of every case HIT,
  PARTIAL or MISS, and gives, for every HIT or PARTIAL, a verbatim quote of at most 40 words and a
  one-sentence reason.
- Every quote is checked by script against that seat's content (whitespace normalised). A quote
  that is not there sends the case back to the same grader.
- Where A and B agree on every seat of a case, that is the verdict. Where they differ on any seat,
  a third fresh Opus 5.5 grader reads the packet and both graders' grades, quotes and reasons
  (unnamed) and rules on each disputed seat.

## Analysis

Primary:
- Strict on the 42 labels not shown to be wrong.
- Benchmark standard on all 50.

Secondary:
- Benchmark standard on the 42.
- Cases still caught without the stealth seat (space-bunny-alpha).
- Cases caught by two or more seats, each a different vendor in 0.2.0 (vendor = the prefix of
  `served_model`).
- Seats and vendors per council; findings and consensus entries per PR; seat latency.
- Agreement between graders A and B (Cohen's kappa on case verdicts), and a lower bound that
  credits a case only where both graders do.

Comparisons:
- Exact two-sided McNemar, paired by case: strict on the 42 against the blind strict grades of the
  competitors (`audit_blind_run2/verdicts.jsonl`: Greptile 32, Bugbot 25, Copilot 19, CodeRabbit 16,
  Graphite 2); benchmark standard on 50 against the published credits (Greptile 41, Bugbot 29,
  Copilot 26, CodeRabbit 22, Graphite 3; the case table, not the page's "Copilot 54%" headline).
- Wilson 95% intervals for 4take's rates.
- Run 3 against runs 1 and 2, per case: descriptive, not pooled.

To be disclosed with the results: one recording is one draw from a stochastic free pool whose
models differ from runs 1 and 2; the stealth model's identity is unknown; the graders are Claude
models that can tell the reviews come from an AI council, though not how earlier runs were graded.
