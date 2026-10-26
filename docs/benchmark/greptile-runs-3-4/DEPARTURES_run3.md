# Departures from PREREGISTRATION_run3.md

PREREGISTRATION_run3.md (sha256 1d7c970566e685f3be8768f31d3f5c7c098b02247cb8ad70c38bcd128597fe80)
is unchanged. This file lists, as it promises, every departure found, with its reason. It was written
on 2026-10-04 while the blind graders were running, before any grade had been read.

## 1. Sittings resumed sooner than "on another day"

The pre-registration says that a spent quota, or two failed or degraded councils in a row, stops
the sitting, and that it "resumes on another day". `run.py` stopped 7 times by the two-in-a-row
rule (never by a spent quota: `quota_exhausted` is false in all 50 attempts). Three stops were
followed by a resumption on another day or hours later, as planned. Four were not:

| Stop after | Resumed | By |
| :--- | :--- | :--- |
| grafana-01 (2026-10-01 07:23 UTC) | 3.0 h later, same day | scheduled rerun |
| keycloak-04 (2026-10-04 10:36 UTC) | 12 min later | rerun by hand |
| keycloak-06 (2026-10-04 11:03 UTC) | 10 min later | rerun by hand |
| sentry-07 (2026-10-04 11:58 UTC) | at once | a loop of up to four reruns |
| sentry-09 (2026-10-04 12:07 UTC) | at once | the same loop |

Reason: the OpenRouter account has been topped up (1,000 free-model requests a day), so the quota
was not the constraint, and the thin councils came from the cases themselves (keycloak-01 to -06
and sentry-10 were answered only by the stealth and nvidia seats; the others returned empty
responses or 429s, on 1 October and again on 4 October). `run.py` was not changed and its hash is
unchanged.

Direction of the effect: every council is kept as recorded, so recording straight through a thin
pool keeps councils that a pause might have replaced with fuller ones. If anything this lowers
4take's numbers, not raises them. It is a departure all the same.

## 2. Grading logistics (not a departure from the rubric)

- Packets were built by `grading_run3/build_packets.py` and copied, per repository, into isolated
  folders outside the repository tree, each holding only its ten cases, `GRADER.md` and an empty
  `out/`. The seat-to-model key (`key.json`) was in none of them.
- Graders A and B were fresh subagents, each given one repository at a time. They were started with
  the model aliases `opus` and `sonnet`; the exact model version behind an alias is whatever the
  harness resolved it to and is not recorded here.
- Their prompts contained the path of their folder, an instruction to follow `GRADER.md`, and a
  restriction to that folder. They contained nothing from runs 1 and 2, no earlier verdicts and no
  expected result.

## 3. The graders' context held earlier totals (found 2026-10-07)

Graders A, B and C were Claude Code subagents. Such a subagent receives the session's instruction
files as context: the user's `CLAUDE.md` files and this project's memory index. On 2026-10-04 that
index named the totals of runs 1 and 2 (benchmark standard 39 and 38 of 50, strict 27 and 28 of 42),
the published Greptile 41 and Bugbot 29, and the result of an unrelated frontier test; the
repository's `CLAUDE.md` described 4take. No per-case verdict was in it, and the audit of every
grader's tool calls shows none opened a file outside its folder. This still breaks the
pre-registration's "nothing from runs 1 and 2, no earlier verdicts" at the level of totals, and
agreement between A and B cannot rule out a bias they shared. It was found while auditing a later
test, after run 3 had been graded and reported.
