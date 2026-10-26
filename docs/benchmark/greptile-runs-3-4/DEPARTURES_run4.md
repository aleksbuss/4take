# Departures from PREREGISTRATION_run4.md

## 1. Two councils recorded while the Mac slept are recorded again (2026-10-08)

The laptop ran on battery from the start of the run. At 16:25:31 CEST, at 9% charge, its lid was
closed and it entered `Clamshell Sleep` (pmset log); `caffeinate -is` does not prevent that on
battery. It slept until 19:55, waking only for maintenance DarkWakes of 20-45 s every 15 minutes.

Two councils overlapped that sleep. Their clock time and the time the council itself measured
(`_eval.wall_s`, monotonic, which stops during sleep) disagree, while every other council agrees
within a second:

| case | started (CEST) | clock time | wall_s | recorded |
| :--- | :--- | ---: | ---: | :--- |
| keycloak-02 | 16:17:36 | 3291 s | 591 s | 3 seats, exit 2 |
| keycloak-03 | 17:12:29 | 9962 s | 601 s | 1 seat, exit 2; three models "Connection dropped after 4 retries: ReadError" |

A council whose connections were cut by the machine sleeping measures the harness, not the tool,
just as one that ran into the spent daily quota does. Both fixtures were moved to
`runs/4/_discarded/<case>.attempt1.slept.json`, not deleted, and the cases will be recorded again
by the next sitting under the same rules (their attempts.jsonl entries stay, so the new ones are
attempt 2). The check, clock time against `wall_s` for every council, will be repeated at the end
of the run. The charger was connected at about 19:55.

Also observed during that council, not a departure: `inclusionai/ling-3.0-flash-sante:free` now
answers `404 This model is unavailable for free`; it has become a paid model.

## 2. Grading logistics (2026-10-08, not departures from the rubric)

- In two of grader B's ten sessions (P011-P020, P071-P080) Sonnet tried to write its files with a
  shell script, which the isolated session does not allow, and wrote nothing. Those packets were
  given to grader B again in fresh sessions, unseen grades and all; its replies are kept in the
  grader's `replies.log`.
- Two of grader B's quotes were not found verbatim (P023 seat D, P077 seat B). As pre-registered,
  both packets went back to grader B; the first versions are kept in `grading_run4/out_B_rejected/`.
  All 100 packets of A and B then passed the quote check.
- Grader C ruled on all 19 disputed seats in 16 packets. `merge.py check out_C` lists the 84
  packets without a dispute as "no output"; that is expected, as C sees only disputed packets.
- Not pre-registered, reported as context: run 3 without its stealth seat (space-bunny-alpha, since
  withdrawn from the free tier) scores 35/50 on the benchmark standard and 29/42 strict on the clean
  grades.
