# Amendment 2 to PREREGISTRATION_weak_primary_greptile.md: one declared sensitivity analysis

Written 2026-10-23, on the author's instruction, after the 300 requests and before any grading. No
grader has run. The only things I have seen of any answer's content are the three openings listed in
DEPARTURES_weak_primary_greptile.md §3. Its sha256 is appended to
`PREREGISTRATION_weak_primary_greptile.sha256`.

## Why

Six runs ended without an answer after three failed attempts, all of them in arm A. Arm A also had 41
failed attempts against arm B's 2, mostly on the output token limit, and its median thinking was 51k
tokens against B's 33k (DEPARTURES §3). Under the pre-registered rule a run without an answer is
graded as a review with no findings. Some of any difference between B and A may therefore come from
A not finishing rather than from A finishing with fewer catches.

Not finishing is a real cost of reviewing alone, and the primary analysis keeps it. This analysis
shows how much of H1 rests on it.

## S9 (sensitivity, declared after the run and before grading)

H1 repeated on the cases where every run of both arms has an answer. This excludes the five cases
with a run without an answer: calcom-01, discourse-09, grafana-03, keycloak-03 and keycloak-05. That
leaves 45 cases on the benchmark standard, and 38 on the strict standard, the 42 labels less
calcom-01, discourse-09, keycloak-03 and keycloak-05 (grafana-03 is one of the eight wrong labels).

Everything else is as in H1:
- an arm's verdict is the majority of its three runs;
- the test is exact two-sided McNemar, paired by case;
- the grades are those of the pre-registered grading, with no separate grading.

## How it is read

- S9 is reported beside H1, labelled "sensitivity, declared after the run, before grading". It does
  not replace H1 and has no claim rule of its own. The conclusion rules of the pre-registration
  apply to H1 only.
- If S9 and H1 differ in direction or in reaching p < 0.05, both are reported, together with the
  number of the five excluded cases that H1 counted as caught by B but not by A, or the reverse.
- The 34 failed attempts whose final text was copied to `weak_primary/failed_attempts/` are not
  graded and enter no analysis.

Nothing else changes: the hypotheses, the grading, the secondary analyses S1-S8 and the rules.
