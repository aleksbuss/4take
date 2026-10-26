# Pre-registration: does the council's output help a weaker primary model on the Greptile 50?

Written 2026-10-22, before any request of this experiment was sent to any model. Its sha256, and
those of the files it pins, are recorded outside this file at the same time, in
`PREREGISTRATION_weak_primary_greptile.sha256`. Any departure will be listed in
`DEPARTURES_weak_primary_greptile.md`, with its reason. Where this file is silent, the rules of
PREREGISTRATION_run4.md (sha256 5e0d7fad…17ca) and PREREGISTRATION_run3.md (1d7c9705…fe80) apply.
Directory: `evals/external/greptile/weak_primary/` (git-ignored, like the rest of `evals/external/`).

## Question, and what is known so far

Does a weaker model that reviews a pull request's diff catch more of the labelled bugs when it also
reads 4take's council output than when it reviews alone?

- On the 50 Greptile cases the free council of run 4 (commit 2f07956, recorded 2026-10-08) caught
  37/50 on the benchmark standard and 30/42 strict (grading_run4/final.json). Level with Greptile,
  ahead of Copilot and CodeRabbit. That measures the council as a reviewer, not as help to another.
- Opus 5.5 as the primary (frontier corpus, 20 bugs, 2026-09-28): 15/20 alone, 14/20 with the
  council. No gain shown; councils were degraded.
- Gemini 3.8 Flash as the primary (same 20 bugs, 2026-10-07): 3/20 alone, 3/20 with the council.
  Void as a test of the council: its councils seated 1-2 reviewers, and Flash was at a floor.
- Whether the council helps a weaker primary is therefore unmeasured. It is what the author
  experiences in daily use with Gemini Flash, and what a claim that 4take helps weaker models would
  rest on. This test reuses the Greptile 50 so that the council's own catch is already known.

## Hypotheses

- **H1 (primary).** For the primary model M (named in the choice file, below), arm B catches more
  of the labelled bugs than arm A. Two co-primary endpoints, each an exact two-sided McNemar test
  paired by case: the benchmark standard on all 50 cases, and strict on the 42 labels not shown to
  be wrong. Null: the notes change nothing. The reverse direction is tested by the same test.
- **Secondary** (S1-S8 under Analysis): what the council could have added (A or C against A), B
  against the council alone, uptake and loss, false findings per review (harm), and checks.
- **HP (separate family, optional).** ProTake's claim that a frontier council beats a frontier
  model reviewing alone, here: the ProTake council against Opus 5.5 alone on the same 50 cases. It
  runs only on its own go-ahead, after H1 is graded, and is never pooled with H1 (section "ProTake").

## Choices fixed before the first request

Two choices are left to the author. Both are fixed in `PREREGISTRATION_weak_primary_greptile_choice.md`,
written and hashed (its sha256 appended to the .sha256 file) before the first request; no request is
sent without it. The choice is made before any data of this experiment exists, so it cannot be
fitted to the result.

1. **The primary model M**, one of:
   - **M1, Gemini 3.8 Flash** (recommended): the model the author actually uses as a primary with
     4take; reasoning at the high setting, as in the 2026-10-07 Flash sessions.
   - **M2, Claude Haiku 4.5**: the H1 model of evals/frontier/PREREG_weak_primary.md; no money on
     the `claude -p` route; the same model family as the graders.
   - **M3, nvidia nemotron-3-super-120b-a12b:free**: no money, free-tier quota; it holds a seat in
     37 of the 50 run-4 councils, so in arm B it reads its own earlier review.
   Only M's B against A is H1. A second candidate run later is a separate, secondary hypothesis with
   its own report, never pooled with H1.
2. **The council output C**: C1 (recommended) reuse `runs/4` as recorded; or C2 re-record the 50
   councils first (protocol in "Option C2"). Under C2 the arm-B prompts are rebuilt from the new
   councils and the new manifest's sha256 goes into the choice file before any request.

The choice file also names the route (one per model, for all requests) and the exact model id.

## Material

- Cases and labels: the 50 cases of `cases.json` (sha256 69d1785a…b333), each case's `meta.json`;
  42 labels not shown to be wrong; the 8 wrong labels (calcom-02, calcom-04, grafana-02, grafana-03,
  grafana-08, sentry-01, sentry-04, sentry-09) scored on the benchmark standard only, against the
  label as written or the readings fixed in PREREGISTRATION_run3.md (`WRONG` in
  grading_run3/build_packets.py, sha256 c3f6b9ad…09d0). Rubric: audit_blind_run2/RUBRIC.md
  (efb508a9…6e5b) as worded in PREREGISTRATION_run3.md.
- Diffs: each case's `review.diff`, the only input the council saw (combined digest in the .sha256
  file).
- Council output (C1): `runs/4/<case>.json` (combined digest in the .sha256 file; per-fixture hashes
  in weak_primary/manifest.json). Seats {4: 37, 3: 9, 2: 1, 1: 3}.
- The council's grades: grading_run4/final.json (7dec6cc3…c99e), entries with run "4": bench 37/50,
  strict 30/42, graded blind by isolated Opus 5.5 / Sonnet 5.5 graders, kappa 0.88.

**Disclosure about C1.** `runs/4` is one draw from the free pool of 2026-10-08. Two of its four most
seated models have left the free catalogue since: apodex-1.1-mini (34 seats) and
inclusionai/ling-3.0-flash-sante (28 seats), 62 of its 180 seats; the catalogue checked 2026-10-21
has neither (nor space-bunny-alpha, gone since run 3), and has minimax, xingchen and others instead.
Under C1, H1 measures the effect of *the council as recorded on 2026-10-08*, not of the council a
user would get today, and every report of it says so. C1 is recommended because it needs no
free-tier quota, its council is already graded blind, and the notes are fixed files anyone can
re-read; re-recording (C2) would measure today's pool at the price of about 600 free requests, a
fresh council grading, and a draw that may again be degraded.

## Arms

For each of the 50 cases and each arm, three independent requests (runs 1-3) to M; 300 requests.

- **A, alone:** the system prompt and the arm-A prompt: the task, the answer format and the diff.
- **B, with notes:** the same, plus, after the diff, the council's output for that case: its
  `consensus` list (JSON, as recorded, model names included) and every seated review's `content`
  as "Reviewer 1..n" (n = seats), with a note where a review was truncated; headed "Other reviewers'
  notes. They may be wrong" and closed by "Check each note against the code before relying on it.
  Your answer is your own list of defects. Do not mention these notes or the other reviewers in your
  answer." (The last sentence is new against evals/frontier: it keeps graders blind to the arm.)
- **C, council alone:** the run-4 council (C1) as already graded; not re-requested.
- No tools and no repository in either arm: the council saw only the diff too. No PR title.

The prompts are built by `weak_primary/build_prompts.py build` into `weak_primary/prompts/{A,B}/<case>.txt`,
with every prompt's sha256 in `weak_primary/manifest.json`; the script, the manifest and the system
prompt are hashed in the .sha256 file. `build_prompts.py check` must pass before the first request.
A prompt is sent byte for byte as built; the three runs of a case and arm send the same prompt.

## Requests

- Routes, for whichever model is chosen (one route for all its requests):
  - M1: OpenRouter `google/gemini-3.8-flash` (or the id the choice file names), chat completions,
    `messages = [system, user]`, `reasoning: {"effort": "high"}`, `max_tokens` 32768, no
    temperature; or the Google AI Studio API with `thinkingLevel` high and `maxOutputTokens` 32768.
  - M2: `claude -p --model claude-haiku-4-5 --tools "" --system-prompt "<system>" --strict-mcp-config
    --output-format json --no-session-persistence`, prompt on stdin, from an empty directory under
    /private/tmp with a neutral name (no CLAUDE.md above it, no memory); or the Anthropic API with
    the same system prompt, `max_tokens` 16000 and thinking left at the model's default.
  - M3: OpenRouter `nvidia/nemotron-3-super-120b-a12b:free`, `messages = [system, user]`,
    `max_tokens` 32768, no reasoning parameter, no temperature.
- Order: `build_prompts.py schedule` draws it from this file's sha256 (first 16 hex digits): run 1
  for all 50 cases, then run 2, then run 3; within a run the cases in a shuffled order, and within a
  case A and B in a shuffled order, so that time of day and provider drift fall on both arms alike.
  At most 4 requests in flight.
- Failures: rate limits (429) and server errors are retried with back-off and do not count as
  attempts. An attempt fails on a transport error, a timeout (600 s), a refusal, or an answer with
  no text (a model that reasoned through its budget). A request gets at most 3 attempts; after 3
  failed attempts that run has no answer, is graded as a review with no findings, and is reported.
  An answer cut off at the token limit is kept as returned and flagged. A run that produced an
  answer is never requested again. A spent daily quota pauses the sitting until the reset.
- Logging: the final answer text to `answers/<arm>/<case>.r<run>.txt`, the full response (usage,
  finish reason, served model or provider) to `answers/raw/`, every attempt to `attempts.jsonl`.
- The sender script is written before the first request, does only what this section says, and its
  sha256 is appended to the .sha256 file before it runs. Nothing is sent before the author's
  go-ahead.

## Grading

Starts only after all 300 answers exist (or have exhausted their attempts).

- Packets: one per case, 50 in all, built by `weak_primary/build_packets.py` (written before grading
  and hashed into the .sha256 file first) as grading_run4/build_packets.py built run 4's: case,
  PR title, label, labelled file, severity, the diff's path, and for the 8 wrong labels the readings;
  then the six answers of the case (A runs 1-3, B runs 1-3) as Seat A-F in an order shuffled per
  packet (seed: sha256 of "wpgrading:" + packet id), each as returned, stripped, with a note when it
  was cut off and "(No answer.)" when the run has none. Packet ids W01-W50 are assigned by shuffling
  the cases with the seed above. Nothing in a packet names an arm, a model, a council or 4take; the
  key goes to `weak_primary/key.json`, which graders are not given.
- Three graders as in run 4, each `claude -p --model <model> --strict-mcp-config --allowedTools Read
  Write`, the task on stdin, run from an empty, neutrally named directory under /private/tmp holding
  only the instructions, the packets and the diffs: G1 Claude Opus 5.5 and G2 Claude Sonnet 5.5
  grade every seat with `weak_primary/GRADER.md` (5 packets per fresh session; graded packets are
  skipped on a rerun); G3 Claude Opus 5.5 rules on every seat where G1 and G2 differ, with
  `weak_primary/GRADER_C.md`, seeing both grades, quotes and reasons as "Grader 1/2" in a shuffled
  order. Before grading, one session per grader directory is asked what it knows of 4take and what
  instructions it has beyond the message; grading starts only if it knows nothing and has none (the
  run-3 subagent graders had the project's memory in context; this check replaces that mistake).
- Quotes are checked by script against the seat's text (whitespace normalised); a quote that is not
  there sends the packet back to the same grader. A packet a grader fails to write goes back to it
  in a fresh session.
- A seat's grade: G1's where G1 and G2 agree, otherwise G3's.
- An arm's verdict on a case: majority of its three runs. Benchmark standard: caught when at least 2
  of the 3 seats are HIT or PARTIAL (all 50). Strict: caught when at least 2 are HIT (the 42 only).
  The council's verdict (C) is its best seat, as graded in run 4.
- False findings (for S4): a separate pass over the same packets by grader GH, Claude Opus 5.5,
  isolated the same way, with `weak_primary/GRADER_H.md`: per seat, the number of findings and which
  are FALSE (the diff shows the claim wrong). 10 packets drawn with the seed above
  (`random.Random(seed).sample(sorted ids, 10)`) are graded again by Claude Sonnet 5.5, the same way,
  for agreement.
- Graders run only on the author's go-ahead (they use the Claude subscription's session limits).

## Analysis

Exact McNemar and Wilson intervals as in grading_run3/stats.py; "only B / only A" reported with
every p-value.

Primary (H1):
- B against A, benchmark standard, 50 cases.
- B against A, strict, 42 cases.

Secondary:
- S1. A or C (caught by the arm-A majority or by the council) against A, both standards: what the
  council could have added at best. By construction only the union can gain, so this is reported as
  headroom, the count of cases C caught and A missed, with its p-value for reference only.
- S2. B against C, both standards: does the primary with notes beat the council read alone.
- S3. Uptake and loss: of the cases C caught and A missed, the share B caught; of the cases A
  caught, the share B missed; Wilson intervals.
- S4. Harm: false findings per review in B against A (GH's counts). Per case, the mean over its
  three runs of each arm; the B-minus-A difference tested with a two-sided paired sign-flip
  permutation test (100,000 draws, seeded as above), with a bootstrap 95% interval for the mean
  difference (10,000 resamples of cases); also findings per review and the false share per arm, and
  the cases where B has more false findings than A, fewer, and as many. GH's agreement with the
  Sonnet re-grade (seats with equal counts, mean absolute difference) is reported beside it.
- S5. Sensitivity: B against A for each run separately (run 1 with run 1, and so on), and the mean
  fraction of the three runs catching each case, B minus A, with a bootstrap interval over cases.
- S6. Per repository (calcom, discourse, grafana, keycloak, sentry), descriptive.
- S7. Blinding: the number of answers per arm that mention reviewers, notes, a consensus or a
  council (case-insensitive: `reviewer \d`, `other reviewer`, `the notes`, `consensus`, `council`);
  answers are graded as returned, never edited.
- S8. Graders: Cohen's kappa G1 against G2 on seat grades and on arm verdicts; the lower bound that
  credits a case only where both G1's and G2's majorities credit it, for A and B.

## Power

The exact two-sided McNemar depends only on the discordant cases, b (only B caught) and c (only A
caught). The smallest one-sided splits that reach p < 0.05, the same at n = 50 and n = 42:
6-0, 7-0, 8-0, 8-1, 9-1, 10-1, 10-2, 11-2, 12-2, 12-3, 13-3, 13-4, 14-4, 15-4, 15-5
(for 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20 discordant cases). Five discordant cases
or fewer can never reach it.

Power to find B > A at alpha 0.05, if each case is discordant with probability d and a discordant
case favours B with probability theta:

| d (discordant) | n = 50: theta 0.7 | 0.8 | 0.9 | n = 42: theta 0.7 | 0.8 | 0.9 |
| :--- | ---: | ---: | ---: | ---: | ---: | ---: |
| 0.10 | 0.04 | 0.10 | 0.21 | 0.03 | 0.06 | 0.13 |
| 0.20 | 0.15 | 0.36 | 0.68 | 0.12 | 0.28 | 0.56 |
| 0.30 | 0.26 | 0.58 | 0.89 | 0.20 | 0.48 | 0.81 |
| 0.40 | 0.35 | 0.74 | 0.97 | 0.30 | 0.65 | 0.93 |

So only a large and nearly one-way effect is detectable: even odds (power 0.5 or more) need about
15 discordant cases with four in five towards B, or about 10 with nine in ten. In run 4, 11 of the
council's 37 catches came from a single seat, and single free seats caught 41-69% of the cases they
sat in. A guess, not a measurement: if M alone catches 20-30 cases, mostly ones the council also
catches, C leaves it roughly 8-17 cases to gain. Majority-of-3 lowers the discordance that sampling
noise alone would produce.

## What can be concluded

- B > A with p < 0.05 on both endpoints: "on the Greptile 50, the council's output (as recorded on
  2026-10-08) raised M's catch". Two endpoints must both pass, so no correction is applied.
- B > A with p < 0.05 on one endpoint only: "suggestive, not shown".
- B < A with p < 0.05 on either: "the notes lowered M's catch" on that standard.
- Otherwise "no difference shown", never "no effect"; the counts, b and c, and S5 are reported.
- The harm result is reported whatever H1 shows; a gain in catch with more false findings is
  reported as both.
- A suggestive result is followed by a new corpus with its own pre-registration, not by more runs
  of these 50 or a changed rule.

## Option C2: re-recording the council

Only if the choice file says C2. A probe first (one council on a 10-line snippet; at least 3
vendors seat, otherwise stop and retry another day), then the 50 cases recorded on the commit then
checked out (recorded in each fixture's `_eval`), with run 4's invocation, config and keeping rules,
into `runs/wp/`; then those councils graded in council packets exactly as run 4 (G1/G2/G3, one
packet per case, seats as recorded); then the arm-B prompts rebuilt from `runs/wp/` and the new
manifest hashed into the choice file; only then the 300 requests. About 600 free requests (one day
on the topped-up account) and 50 more packets to grade. H1 then measures that day's pool.

## ProTake (separate family, optional)

Runs only on the author's separate go-ahead, after H1 is graded. Labelled "ProTake" in every report;
its results never enter H1's.

- C_P: ProTake (`~/protake`, commit recorded at recording; 9d2c8b0 today) on each `review.diff`,
  `protake --format json --quiet --no-cache --no-telemetry --timeout 600`, its default four
  frontier models (GLM 5.2, Kimi K3, GPT-5.6 Luna Pro, Qwen3.8 Max). Every council kept; recorded
  again only when it produced no fixture. Graded in council packets as run 4.
- A_O: Claude Opus 5.5 with the arm-A prompt; B_O: Opus 5.5 with the arm-B format built from C_P.
  Three runs each, `claude -p --model claude-opus-5-5 --effort high` with the M2 flags, or the API
  with adaptive thinking at effort high. Graded in six-seat packets as H1, majority of 3.
- HP1: C_P against A_O, benchmark standard (50) and strict (42), exact McNemar, the decision rule
  above. Exploratory: B_O against A_O; C_P against C (the free council of run 4); A_O against C.
- A council's verdict is its best seat while a model's is its majority of 3: a council is read whole,
  so this is how each is used, and it favours the council; reports say so.

## Limitations to disclose

- One draw of council output from a free pool that has since changed (C1); one primary model; one
  benchmark of 50 public PRs from five repositories, one labelled bug each, 8 labels wrong.
- The benchmark measures recall of the labelled bug. Other true findings are invisible to it; false
  findings are judged from the diff alone, so a claim needing unseen code counts as not false.
- The diffs are public; M may have seen them in training. That affects both arms alike.
- The graders are Claude models; with M2 they grade their own family. Graders may recognise that
  some seats resemble each other. B's prompts are longer than A's, which is part of the treatment.
- The consensus list in B names the council's models; with M3, M reads its own earlier review.

## Cost

Planning figures (`build_prompts.py plan`, characters / 3.5 per token, prices checked 2026-10-22):
300 requests (at most 330 with retries), about 3.6M input tokens over the three runs.

| Model | Route | Output tokens (est.) | Money |
| :--- | :--- | ---: | ---: |
| M1 Gemini 3.8 Flash, $0.75 / $3.75 per M (introductory) | OpenRouter credit or AI Studio | 1.8M | about $9 (range $5-17) |
| M2 Claude Haiku 4.5, $1 / $5 per M | `claude -p`: subscription, $0; API | 1.5M | $0, or about $11 by API |
| M3 nemotron-3-super:free | OpenRouter free tier | 1.8M | $0; ~330 of the 1000 free requests a day |

Grading: 50 packets of six seats for G1 and G2 (about 300 seat grades each, near run 4's 360), G3 on
the disputes, GH on 50 packets plus 10 re-graded: Claude subscription session limits, no money.
ProTake, if run: one recording of 50 frontier councils, about $12 of OpenRouter credit (range $7-21
for 4k-16k output tokens per review) at listed prices (GLM 5.2 $0.50/$3.15, Kimi K3 $2.55/$12.75, GPT-5.6 Luna Pro $0.20/$1.20, Qwen3.8 Max $2/$6
per M), and 300 Opus 5.5 reviews: subscription limits on `claude -p`, or about $60 by API
($4 / $20 per M).
