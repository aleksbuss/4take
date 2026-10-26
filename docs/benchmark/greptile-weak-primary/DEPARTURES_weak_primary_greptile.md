# Departures from PREREGISTRATION_weak_primary_greptile.md (and its choice file and amendment 1)

## 1. Two senders ran at the same time for four minutes (2026-10-22, 13:36:49-13:41:27 UTC)

**What happened.** The author approved the run in chat and also started the same command,
`send_agy.py --go`, in their own terminal. I started it in the background at the same moment.

Both senders read the same empty `attempts.jsonl` and began the same schedule with 2 requests in
flight each, so every early run was requested twice:
- A/grafana-09.r1 twice, B/grafana-09.r1 twice, A/sentry-06.r1 twice;
- the per-attempt CLI logs, which carry the same names, overwrote each other.

**How it was found.** The two "ok" records for B/grafana-09.r1 have different conversation ids
(2ae5cd15…, 590a2003…). The second answer overwrote the first in `answers/`. I stopped both senders
and their `agy` processes, which ended the four A runs still in flight with result status ERROR,
error "interrupted".

**What was done.** The rule below was fixed without reading the answers' content:
- **B/grafana-09.r1:** the answer on disk is kept, conversation 590a2003…, the second to finish (it
  finished 6 s after the other). Its full event stream is in `answers/raw/`. The other answer,
  2ae5cd15…, is set aside: its stream was overwritten, and its text exists only in Antigravity's
  own transcript. I had looked at the first 600 characters of the kept answer to check its format,
  before the duplicate was noticed.
- **The four interrupted A attempts** were ended by me, not by the model. They are not counted as
  attempts, and those runs will be requested again.
- **Where the records went:** the five set-aside attempt records, the overwritten CLI logs and the
  sitting log moved to `weak_primary/harness_discarded/20261022_duplicate_sender/`. Each record is
  marked with its reason. Nothing was deleted; a byte copy of the original `attempts.jsonl` is in
  the session scratchpad.

**Changes to `send_agy.py`.** The re-hashed file is appended to the .sha256 file. Neither change
affects a request or the model:
- With `--go` it takes an exclusive lock on `weak_primary/.send.lock`, so a second sender refuses to
  start.
- A failed run whose result says "interrupted", or that fails after the sitting was told to stop,
  is logged as `interrupted` and spends no attempt. Ctrl-C, or a stop caused by quota or sign-in,
  must not use up a run's three attempts.

**Not affected:**
- prompts, schedule, model, agent, the arms, grading and analysis;
- no answer was graded or examined for content;
- the extra requests used only subscription quota.

All the conversations involved stay in the author's Antigravity history:
- set aside: 2ae5cd15, 9a773549, a82edfc1, 0574d4b9, 34ca66c7;
- kept: 590a2003.

## 2. A false "sign-in" stop at 2026-10-23 01:09:58 UTC, after 234 answers

**What happened.** A/grafana-03.r3, attempt 3, failed on the output token limit and then hit the
600-second print timeout. The sender tested its sign-in pattern against stderr together with the
result's metadata serialised as JSON. The pattern `\b(401|403)\b` matched the number in
`"duration_seconds": 401.498104`, so the sender logged the attempt as `stop` and ended the sitting.
It let the one request still in flight finish (an answer, A/keycloak-10.r3) and exited at 01:12:54
UTC with 61 runs still to send. Nothing was wrong with the sign-in. The quota pattern had the same
weakness, through a bare `429`.

**What was done.**
- **The attempt.** It is what the rules call a failed attempt, since the model ran out of tokens.
  A correction record was appended to `attempts.jsonl`, attempt 3 marked `failed` with the reason,
  followed by `exhausted`. A/grafana-03.r3 therefore has no answer and is graded as a review with no
  findings, as if the stop had not happened. The `stop` record stays in the log as written.
- **`send_agy.py`.** Both patterns are now tested only against stderr and the result's `error`
  text, never against the metadata. A status code must appear in context ("HTTP", "status" or
  "code" before 401, 403 or 429). The loose `exhausted`, `sign in`, `log in` and `oauth` terms are
  replaced by specific phrases. Before re-hashing, the new patterns were checked against every
  failure text of the run so far: the token-limit message, "The stream was interrupted", the
  timeout line and a plugin OAuth warning. None of them matches, and real quota and sign-in
  messages still do. The fake-agy test passes. The re-hashed file is appended to the .sha256 file.

**Not affected.** No request was changed, no answer was set aside, and no run was given an extra
attempt. The quota rule (wait and stop) did not fire during the run.

## 3. Correction to §1, and the state at the end of the run (2026-10-23, before any grading)

**Correction.** §1 says "no answer was graded or examined for content". That is too strong. I have
not graded anything, and I have compared no answer with its label. But I did see the opening of a
few answers:
- the first 600 characters of B/grafana-09.r1, for format;
- the first 300 characters of the final answer in the failed attempt 1 of A/keycloak-05.r1, while
  identifying the tool it had called;
- the opening of seat A of packet W01 (grafana-09), together with its label, while checking the
  built packets.

**The run.** It ended at 03:58:35 UTC: 294 of 300 runs answered, 6 without an answer after 3 failed
attempts, all in arm A (A/calcom-01.r1, A/discourse-09.r2, A/grafana-03.r2, A/grafana-03.r3,
A/keycloak-03.r1, A/keycloak-05.r3).

Failed attempts:
- arm A, 41: 25 on the output token limit, 14 with a tool step, 2 with an interrupted stream;
- arm B, 2: 1 with a tool step, 1 with an interrupted stream.

Median thinking tokens per counted answer: A 51,220, B 32,759. The losses fall on arm A, and under
the pre-registered rule a run without an answer counts as a review with no findings. This is
reported with H1.

**Audit** (`weak_primary/audit_run.py`, `audit.json`):
- every counted answer shows model `gemini-3.8-flash-high`, agent `plain`, and no step beyond the
  input and the answer;
- no attempt shows a gap between wall-clock and monotonic time over 5 s;
- S7: no answer in either arm mentions reviewers, notes, a consensus or a council;
- the final texts of 34 failed attempts were copied to `weak_primary/failed_attempts/`. They are
  outside the primary analysis.

**Packets.** Built by `weak_primary/build_packets.py`: 50 packets W01-W50, seed 5fdb1a44e4a2fd6d,
6 seats each, 6 seats reading "(No answer.)". The builder, `key.json` and a combined digest of the
packets are appended to the .sha256 file. Grading starts only on the author's go-ahead.

## 4. Graders run in a clean environment (2026-10-23, before any grading)

The first isolation probes, at 06:44 and 06:5x UTC, did not reach a model: the `claude` CLI answered
"OAuth session expired". The author signed in again at 06:58 UTC.

The cause of the confusion: `grade.sh` launched `claude -p` from a shell started by the Claude
desktop app, so the grader inherited the app's own session variables (`CLAUDECODE`,
`CLAUDE_CODE_SIMPLE`, `ANTHROPIC_BASE_URL`, `CLAUDE_CODE_SDK_HAS_HOST_AUTH_REFRESH` and others).
Run 4's graders were not meant to see those either.

`grade.sh` now runs each `claude -p` under `env -i` with only `HOME`, `USER`, `LOGNAME`, a minimal
`PATH`, `LANG` and `TERM`. In that environment `claude auth status` reports the author's claude.ai
sign-in. This tightens the isolation the pre-registration asks for, and changes nothing else: the
models, flags, instructions, packets and procedure are the same. The re-hashed `grade.sh` and this
note are appended to the .sha256 file before any grader runs.

## 5. A refusal counted as an answer, found during grading (2026-10-24)

**What happened.** Grader G2's notes on packet W44 (discourse-10) said seat C held "a Gemini
content-filter block message with no review in it". The key maps that seat to B/discourse-10.r1.
Its text, recorded as an answer on 2026-10-22, was Antigravity's notice "This request was blocked by
Gemini's filters. They can occasionally trigger by mistake on safe coding, security, or
biology-related queries. Please try rephrasing your prompt…".

agy reports such a block as a successful turn with that notice as its text. send_agy.py's checks
(exit code, status SUCCESS, no extra steps, non-empty text) all passed. The pre-registration counts a
refusal as a failed attempt, so the run should have been requested again.

**Scope.** All 294 answers were scanned for that notice and for other agy messages (the token-limit
and "stream was interrupted" texts, "Retries remaining", "try rephrasing"). This is the only one.
The 55 short answers all read "No defects found.".

**What was done**, before the Opus graders resumed. They had stopped on 2026-10-23 at the weekly
limit of the author's Claude plan, after G1 35/50 and GH 15/50; G2 had finished.
- **The record.** The block notice and its stream moved to
  `weak_primary/harness_discarded/20261024_refusal/`. A correction record marks attempt 1 of
  B/discourse-10.r1 `failed` (refusal), naming the superseded conversation. `audit_run.py` now leaves
  superseded answers out of its checks.
- **`send_agy.py`.** It now treats a response containing that notice as a refusal, which is a failed
  attempt. The fake-agy test covers this. Its re-hash and this note are appended to the .sha256
  file.
- **The retry.** B/discourse-10.r1 is requested again under the same rules, attempt 2 and if needed
  3, two days after the other runs.
- **The packet.** W44 is rebuilt with the new seat C; every other packet is unchanged by
  construction, and this is checked. The grades already given to W44 (G2's, and G1's or GH's if
  any) are set aside unread, and W44 is graded again by every grader.
