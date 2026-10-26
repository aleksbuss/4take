# Amendment 1 to PREREGISTRATION_weak_primary_greptile.md: the Antigravity CLI route

Written 2026-10-22, before any request of the experiment, on the author's instruction to use their
Google subscription instead of OpenRouter. Its sha256, with those of `weak_primary/send_agy.py`,
`weak_primary/agy/plain.md` and the `agy` binary, is appended to
`PREREGISTRATION_weak_primary_greptile.sha256`.

**What does not change:**
- the pre-registration (sha256 5fdb1a44…395d);
- the choices in the choice file (d7dcb940…8e07): primary model M1, Gemini 3.8 Flash, council C1
  (`runs/4` as recorded), no ProTake now;
- the prompts (manifest 786ef874…d509), the schedule (493b2086…6c79), three runs per arm, the
  grading and the analysis.

**What changes:** the route.
- This amendment replaces the choice file's OpenRouter route and its sender, `send.py`. send.py
  stays pinned because `send_agy.py` reuses its bookkeeping, but it is not run.
- Gemini CLI could not be used: its account check refused the client. See
  `weak_primary/ROUTE_NOTES.md`.

## The route

- Client: the Antigravity CLI, `~/.local/bin/agy`, version 1.4.5 (binary sha256 b89677b7…7554),
  signed in to the author's Google account. `AGY_CLI_DISABLE_AUTO_UPDATE=1` keeps it from updating
  during the run, and `send_agy.py` refuses to run if the binary's hash has changed.
- Model: `gemini-3.8-flash-high`, shown by `agy models` as "Gemini 3.8 Flash (High)". This is the
  setting of the author's 2026-10-07 Flash sessions; the reasoning level is part of the model
  variant.
- Agent: `weak_primary/agy/plain.md`, a Markdown custom agent named `plain`.
  - `excludeDefaultComponents: true`: none of Antigravity's default prompt sections or default
    tools.
  - `inheritCustomizations: false` and `inheritMcp: false`: none of the author's skills, rules,
    plugins, subagents or MCP servers.
  - Empty `tools`, `skills`, `rules`, `plugins` and `agents`; `subagent: false`.
  - Its body is the pre-registered system prompt, "You review code changes. Answer in plain text."
- Each request is one process, run from `/private/tmp/agywp_<6 hex>`, which holds only
  `.agents/agents/plain.md`. It starts a new conversation each time:
  `agy -p=<prompt> --model gemini-3.8-flash-high --agent plain --output-format stream-json
  --disable-slash-commands --print-timeout 600s --log-file agy_logs/<run>.log`, with stdin closed.
- The prompt is the prompt file byte for byte, passed as the value of `-p=`. The largest is 273,774
  bytes, under macOS's 1 MiB argument limit; send_agy.py refuses any prompt over 900,000 bytes.
  agy has no max-tokens or temperature option, so those are Antigravity's own settings for this
  model, the same in both arms.
- At most 2 requests in flight; the process is killed after 700 s.

## What the model receives (the probe)

One probe was run on 2026-10-22 at 13:29 UTC with exactly this agent, model and directory. Its
prompt was a three-line connectivity check, not from the benchmark, with a fragment testing
verbatim delivery. An earlier invocation had stopped locally on argument parsing (`-p` takes the
prompt as its value) and sent nothing. Records are in `weak_primary/probes/agy_20261022/`,
conversation 163de600-44c3-4958-84e3-e95d38882c66.
- The stream's `init` names model `gemini-3.8-flash-high` and agent `plain`. The result is
  `SUCCESS`: 1,072 input tokens, 752 output tokens, 709 of them thinking.
  - 1,072 input tokens is the system prompt plus the user turn. There is no room in that for skill
    lists, rules, plugin context or summaries of the 17 earlier conversations.
- The stored user turn wraps the prompt as `<USER_REQUEST>\n…\n</USER_REQUEST>`. Two blocks follow:
  `<ADDITIONAL_METADATA>` with the local time, and `<USER_SETTINGS_CHANGE>` noting the model
  selection. The prompt itself arrives verbatim: `@@`, `@Override`, `/usage`, `$HOME`, backticks,
  quotes and non-ASCII were all unchanged. The wrapper is the same in both arms.
- The `init` event lists the CLI's whole tool registry. Asked which tools it could call, the model
  named three: `list_resources`, `read_resource`, `manage_task`. None of them reads files or the
  web. With no MCP servers inherited, nothing is listed and nothing is readable.
  - Nothing relies on the model's report: an attempt whose stream shows any step other than
    `user_input` and `agent_response` is a failed attempt.

## Rules for each request

These replace the OpenRouter-specific rules of the choice file.

An answer counts only when all of these hold:
- the exit code is 0;
- `init` names `gemini-3.8-flash-high` and `plain`;
- the result status is `SUCCESS`;
- the stream has no step other than `user_input` and `agent_response`;
- the response has text.

Otherwise:
- **Quota or rate limit in stderr** (429, RESOURCE_EXHAUSTED, quota, rate limit, exhausted, too many
  requests): wait 5 minutes, doubling to at most 60, and do not count an attempt. A seventh such
  error on one request stops the sitting until the quota resets.
- **Sign-in or authentication error** (401, 403, UNAUTHENTICATED, PERMISSION_DENIED, sign in, log
  in, authentication, OAuth): stop the sitting.
- **Anything else**: a failed attempt. After 3 failed attempts the run has no answer, as in the
  pre-registration. A run that produced an answer is never requested again.

Logs:
- `answers/<arm>/<case>.r<run>.txt`: the response text.
- `answers/raw/…json`: the full event stream.
- `attempts.jsonl`: every attempt, with conversation id, model, agent, step types, usage, and
  wall-clock and monotonic seconds.
- `agy_logs/`: the CLI's own log for every attempt.

No route is mixed with another. If agy becomes unusable after the first answer, the run pauses.
Finishing it on another route is a departure: the answers already given by agy are kept and
reported, but they are not analysed together with the other route's answers.

## Side effects, disclosed

- Every request leaves a conversation in `~/.gemini/antigravity-cli/` (`conversations/`, `brain/`,
  `annotations/`). The run adds 300 or more conversations to the author's Antigravity history.
  Their ids are logged, so they can be deleted after grading if the author says so.
- The bare agent is given no past-conversation context (shown by the probe), so these
  conversations do not reach later requests of this experiment.
- The cost is the author's subscription quota, not money.

## Verified offline before hashing

`send_agy.py` was exercised against a fake `agy` in the session's scratchpad, never against the
real one. The test covered:
- a quota error, which waits;
- a tool step, a wrong model, an error status and an empty answer, which are failed attempts;
- three failed attempts, after which the run has no answer;
- a sign-in error, which stops the sitting;
- the environment: auto-update off and the API key scrubbed;
- the working directory holding only the agent file;
- the exact `-p=` value and the flags.
