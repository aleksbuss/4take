# Benchmark records

The benchmark figures in the [README](https://github.com/aleksbuss/4take#-against-the-paid-reviewers-greptiles-benchmark)
come from two experiments on Greptile's 50-PR bug benchmark. This folder holds the documents behind
them, unchanged: the plan written before each run, every departure from it, the instructions the
graders worked from, and the results as the analysis scripts printed them.

**What "pre-registered" means here.** Each plan was written and hashed on the author's machine
before its run. The repository was private then, so there is no public timestamp. The hashes in the
tables below are the ones recorded at the time, and every file here is byte-identical to the one
that was hashed; `shasum -a 256 */*` in this folder checks it. A file marked "after" was written
during or after its run and was not hashed in advance.

## Runs 3 and 4: 4take against the commercial reviewers

Greptile published 50 pull requests from Cal.com, Discourse, Grafana, Keycloak and Sentry, each
with one labelled bug, together with the comments five commercial AI reviewers left on them. 4take
reviewed each PR's diff using only OpenRouter's free models. Run 3 recorded 4take 0.2.0 at commit
706e96c (30 September to 4 October 2026). Run 4 recorded the reasoning fix at commit 2f07956
(8 October), which went into 0.2.0 before release, and graded both runs again, blind to which run a
packet came from.

| | benchmark standard, 50 PRs | strict, 42 verified labels |
| :--- | ---: | ---: |
| **run 4** (the README's figures) | **37** | **30** |
| run 3, on the same clean grading | 37 | 31 |
| Greptile | 41 | 32 |

- Graders: Claude Opus 5.5 and Claude Sonnet 5.5, each in an isolated `claude -p` session that held
  only the packets and the instructions. They agreed on 95 of 100 packets (Cohen's κ 0.88). A third
  session (Opus) ruled on the 19 disputed seats.
- Departures, in short. Run 3 resumed sittings sooner than planned after its stop rule fired. Its
  first graders were subagents whose context held the totals of earlier runs, which is why run 4
  graded both runs again. In run 4, two councils overlapped the laptop going to sleep and were
  recorded again.
- Fifteen months apart, on public data. The benchmark's pull requests were opened, and the
  commercial reviewers left their comments, on 26 to 29 July 2025 (the creation dates of the PRs in
  Greptile's forks). 4take's runs are from October 2026. Models improved in between, so the
  commercial tools would probably score higher today. The PRs and the benchmark's bug descriptions
  have been public since 2025, so a model trained later may have seen them, and nothing here rules
  that out. Both effects favour 4take; neither was part of the pre-registration.

| file | what it is | sha256, first 16 | hashed |
| :--- | :--- | :--- | :--- |
| [PREREGISTRATION_run3.md](greptile-runs-3-4/PREREGISTRATION_run3.md) | plan, rubric and analysis | `1d7c970566e685f3` | 2026-09-30 |
| [PREREGISTRATION_run4.md](greptile-runs-3-4/PREREGISTRATION_run4.md) | what run 4 changes | `5e0d7fadafc18673` | 2026-10-08 |
| [DEPARTURES_run3.md](greptile-runs-3-4/DEPARTURES_run3.md) | departures from the plan | `c5d2ed3ff022c751` | after |
| [DEPARTURES_run4.md](greptile-runs-3-4/DEPARTURES_run4.md) | departures from the plan | `c494ea81456d3be3` | after |
| [GRADER.md](greptile-runs-3-4/GRADER.md) | instructions for graders A and B | `4e64a28d508b245e` | after |
| [GRADER_C.md](greptile-runs-3-4/GRADER_C.md) | instructions for the tie-breaker | `3d7d707f856fad64` | after |
| [RESULTS_run4.txt](greptile-runs-3-4/RESULTS_run4.txt) | results of both runs | `c0fecd1c0b78e5da` | after |
| [RESULTS_run3.txt](greptile-runs-3-4/RESULTS_run3.txt) | run 3's first grading | `4a41635038bc5ad4` | after |

## Does the council help a weaker model?

Gemini 3.8 Flash, thinking set to high, reviewed each diff alone (arm A) and again with run 4's
council output as notes (arm B). Each arm ran three times per PR, and a PR counts as caught when
two of the three runs caught it. The 300 requests ran on 22 and 23 October 2026 and were graded
blind by isolated Claude sessions.

| | benchmark standard, 50 PRs | strict, 42 verified labels |
| :--- | ---: | ---: |
| A: Flash alone | 24 | 20 |
| B: Flash with the council's notes | 26 (p = 0.69) | 22 (p = 0.63) |
| the council's output, read directly | 37 | 30 |

- The primary hypothesis, B above A, was not shown. Without the five PRs where a run of arm A got
  no answer (the sensitivity analysis of amendment 2), the arms tie: 23 and 23, 20 and 19.
- The council read directly scored above Flash with notes: 37 against 26 (p = 0.007). The
  comparison favours the council, since it credits the council's best reviewer per PR against the
  model's majority of three runs.
- Against Flash alone the council scored 37 against 24: 15 bugs only the council caught, 2 only the
  model (exact McNemar p ≈ 0.002; strict 30 against 20, 12 and 2, p ≈ 0.013). This comparison was
  not pre-registered; it follows from S1 in the results, with the same asymmetry.
- Flash took up 3 of the 15 bugs only the council had caught and lost 2 of the 24 it caught alone.
  The notes did not add false findings: 0.15 per review alone, 0.13 with notes.
- Departures, in short: two senders ran at once and the duplicates were discarded; a duration of
  401 s was read as an HTTP 401 and stopped a sitting; the graders needed a cleaner environment;
  a content-filter notice was first counted as an answer, then retried. The requests went through
  the Antigravity CLI instead of OpenRouter (amendment 1).

| file | what it is | sha256, first 16 | hashed |
| :--- | :--- | :--- | :--- |
| [PREREGISTRATION_weak_primary_greptile.md](greptile-weak-primary/PREREGISTRATION_weak_primary_greptile.md) | plan, hypotheses and analysis | `5fdb1a44e4a2fd6d` | 2026-10-22 |
| […_choice.md](greptile-weak-primary/PREREGISTRATION_weak_primary_greptile_choice.md) | the model and council chosen | `d7dcb9401a1375b8` | 2026-10-22 |
| […_amendment1_agy.md](greptile-weak-primary/PREREGISTRATION_weak_primary_greptile_amendment1_agy.md) | the route through the Antigravity CLI | `82c813f6629eaff0` | 2026-10-22 |
| […_amendment2_sensitivity.md](greptile-weak-primary/PREREGISTRATION_weak_primary_greptile_amendment2_sensitivity.md) | the sensitivity analysis, declared before grading | `4cbc2f9b9e6fd3d2` | 2026-10-23 |
| [GRADER.md](greptile-weak-primary/GRADER.md) | instructions for the two graders | `e7f7bff5ad31832b` | 2026-10-22 |
| [GRADER_C.md](greptile-weak-primary/GRADER_C.md) | instructions for the tie-breaker | `3a38d92211d65517` | 2026-10-22 |
| [GRADER_H.md](greptile-weak-primary/GRADER_H.md) | instructions for counting false findings | `e858dac95453749b` | 2026-10-22 |
| [DEPARTURES_weak_primary_greptile.md](greptile-weak-primary/DEPARTURES_weak_primary_greptile.md) | departures, §1 to §5 | `3adda5fbccc008c6` | as each was written |
| [RESULTS_weak_primary_greptile.txt](greptile-weak-primary/RESULTS_weak_primary_greptile.txt) | every pre-registered analysis | `71a55fe1a08c1988` | when grading ended |

## What is not here

- The 50 diffs, their labels and the commercial reviewers' comments. They are Greptile's dataset,
  made of other projects' code.
- The council recordings, the model answers and the graded packets. They quote those diffs.
- The scripts that recorded, built the packets and ran the statistics. They are tied to that local
  dataset; the tests themselves (exact McNemar, Wilson intervals, Cohen's κ) are standard.

The documents name these files by their local paths, such as `runs/4/<case>.json` or
`weak_primary/send_agy.py`. Those paths are on the author's machine, not in this repository.
