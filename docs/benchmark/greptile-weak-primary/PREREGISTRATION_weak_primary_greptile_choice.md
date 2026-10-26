# Choice file for PREREGISTRATION_weak_primary_greptile.md

Written 2026-10-22, on the author's instruction ("Primary model M1 Gemini 3.8 Flash, council C1
(reuse runs/4), no ProTake for now"), before any request of the experiment was sent. Its sha256, with
those of `weak_primary/send.py` and `weak_primary/schedule.json`, is appended to
`PREREGISTRATION_weak_primary_greptile.sha256`. The pre-registration (sha256 5fdb1a44…395d) is
unchanged; this file only makes the choices it leaves open and pins the sender.

## The choices

1. **Primary model: M1, Gemini 3.8 Flash.** H1 is B against A for this model only.
   - Route: OpenRouter chat completions, model id `google/gemini-3.8-flash` (the catalogue alias;
     each response's served model, e.g. the `google/gemini-3.8-flash-20260902` snapshot, and its
     provider are logged, and each sitting's preflight records the catalogue's canonical slug and
     price).
   - Request, as the pre-registration fixes it: `messages = [system, user]` with the system prompt
     and the prompt file byte for byte, `reasoning: {"effort": "high"}` (OpenRouter's mapping to
     Gemini's high thinking level, the setting of the 2026-10-07 Flash sessions), `max_tokens` 32768,
     no temperature, no streaming. Added: `usage: {"include": true}`, which only asks OpenRouter to
     report the cost and does not reach the model.
2. **Council output: C1.** `runs/4` as recorded on 2026-10-08. The arm-B prompts stay as built
   (`weak_primary/manifest.json`, sha256 786ef874…d509), and every report says the notes are the
   council of 2026-10-08, from a pool that has since lost apodex and ling-sante.
3. **ProTake: not run now.** HP keeps its place in the pre-registration and needs its own go-ahead.

## The sender, `weak_primary/send.py`

- Dry by default: `python send.py` checks that the pre-registration, this file, send.py, the
  prompts and the schedule match their recorded hashes, prints the progress and the next request
  with the prompt elided, and opens no connection. Only `python send.py --go` sends, and only on the
  author's go-ahead. It refuses to run if any pinned file has changed.
- Order: `schedule.json` (seed 5fdb1a44e4a2fd6d), at most 4 requests in flight. A rerun skips every
  run that has an answer or has used its 3 attempts; a run that produced an answer is never sent
  again.
- Failures, as in the pre-registration's "Requests":
  - HTTP 429 and 5xx (also when reported inside a 200 body) wait 15 s, doubling to at most 300 s,
    and are not attempts. When a single request meets a ninth one, the sitting stops as "provider
    unavailable" and resumes on a rerun.
  - A transport error, another HTTP error, an error inside the choice, or an answer without text
    (including one cut off before any text) is a failed attempt. 3 failed attempts leave the run
    without an answer, logged as `exhausted`.
  - An answer with text that was cut off at the token limit is kept and logged `truncated`.
  - 401, 402 and 403 stop the sitting.
- Timeout: the pre-registration's "timeout (600 s)" is implemented as a 600-second socket read
  timeout per request, which is what the standard library offers. It is not a cap on a request's
  total time.
- Added safeguards, which change no request:
  - Each `--go` sitting first makes three read-only calls: `/key` (stop if the account never bought
    credits), `/credits` (stop if less than $5 is left) and `/models` (stop if the model is missing,
    or costs more than $1.50 / $7.50 per million tokens, the reported price from 2027-01-01).
  - No request is launched once the credit left would fall below $2.50. A zero or negative balance
    makes OpenRouter refuse even the free models that 4take itself uses, so the run must never
    drain the account.
- Logs:
  - `answers/<arm>/<case>.r<run>.txt` holds the final answer text, written only on success.
  - `answers/raw/…json` holds the full response, including OpenRouter's `reasoning` text when it
    returns one.
  - `attempts.jsonl` gets every call, with wall-clock and monotonic seconds so a sleep of the
    machine shows up.
  - `preflight.jsonl` gets each sitting's checks.
  - The key is read as 4take reads it (OPENROUTER_API_KEY, FOURTAKE_API_KEY, then
    ~/.config/4take/config.json) and never written anywhere.
- Before it was hashed, send.py was exercised only against a fake server on 127.0.0.1 in the
  session's scratchpad. The test covered a 429, an empty cut-off answer, three 400s, a 502 inside a
  200, a 402, the credit floor, and resuming from the log. Nothing was sent to OpenRouter.

## Budget

`build_prompts.py plan`: about $9 at $0.75 / $3.75 per million tokens (range $5-17), 300 requests.
With the $2.50 floor the account needs roughly $12-20 of credit to finish in one sitting. Otherwise a
sitting stops at the floor and resumes, in the same order, after a top-up. That is a pause, not a
departure.
