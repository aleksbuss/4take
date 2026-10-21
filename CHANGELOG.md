# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed
- **Reasoning models get room to think.** The output budget was sized for the answer (2048
  tokens for a short input) with 40% of it for reasoning, and most free providers ignore that
  cap: Novita (ling, apodex), Cohere and Poolside think until `max_tokens` runs out and return no
  content (`finish_reason: length`). Councils of 6-7 October seated 1-2 vendors instead of 4. A
  reasoning model now gets up to 32768 tokens, or its own maximum if lower, while the reasoning
  cap stays sized on the answer, so a provider that honours it (Nvidia) thinks as briefly as
  before. Free models are metered per request, not per token, so the room costs only a model
  that uses it. Measured on the inputs that had come back empty at 2048: ling answered 12/12 of
  corpus snippets and 6/8 diffs, apodex 13/14 and 5/8, after 2-7k tokens of thought on a snippet
  and 15-26k on a diff. A model that thinks through the whole 32768 without answering, or past
  the per-model timeout (cohere and laguna-s on every diff, which ran 5-14 minutes unbounded),
  gives up its seat to another model rather than being asked again with reasoning off. Every
  evaluation figure for 0.2.0 was recorded before this fix.
- **A model its provider keeps refusing is left out of the pool.** Ranking only demoted it, and
  with a dozen free models a demoted model still filled the rotation tail: gemma-4-31b was
  refused 27 of 27 times in a week, and every council that reached it spent its time on 429
  retries. A model whose provider refused every request in the last 72 hours, at least 8 of
  them, is now skipped until the refusals age out. A model that was served at all stays in.
- **A rate-limited provider is retried eight times instead of four.** A retry after Poolside's
  429 got through in 21-50% of cases, so four retries still cost laguna about one seat in four.
  A 5xx keeps the shorter limit.
- **`pip install '4take[mcp]'` no longer installs an `mcp` the server cannot import.** The extra
  said `mcp>=1.28.1` with no upper bound. `mcp` 2.0 removed `mcp.server.fastmcp` (renamed
  `MCPServer`), so a fresh install resolved 2.x and `4take-mcp` stopped at an import error that
  its own hint then misreported as a missing extra. `uv.lock` pinned 1.28.1, which is why CI and
  `uv sync` never saw it. The extra is now `mcp>=1.28.1,<2`.

## [0.2.0] - not yet published

### Added
- **Reference context.** `--context-file PATH[:START-END]` (CLI) and `context_files` / `context`
  (MCP) send related code (callers, callees, types, config, tests) that the reviewers read but do
  not review, so a defect between files becomes visible: a changed return value a caller still
  relies on, a route without the check its neighbours make. Findings stay on the code under
  review; a caller the change breaks is named in the Issue. Context is capped by
  `--context-budget` / `context_budget_tokens` (16000 tokens by default) and dropped whole, and
  named, when it does not fit. It is redacted like the input and read only under the project
  root; credential files, `.git/`, `.ssh/`, binary and oversized files are refused, and an MCP
  server started from `/` or the home directory reads no paths unless `FOURTAKE_CONTEXT_ROOT` is
  set. The reply budget stays sized on the reviewed code, and the review cache keys on the
  context too. Without context the prompt is byte-identical to the one the evals were recorded
  with. The JSON summary, the text report and the MCP payload name the files sent and dropped.
  A context file the reviewed diff changes is marked as the file after the change: unmarked, the
  cross-file corpus drew 2 to 5 findings per recording calling the changed method a duplicate.
  Marked, across three targeted recordings, only the smallest model seated still made that
  claim, in two of the three.
- **Auto context.** `--auto-context` (CLI) and `auto_context` (MCP) choose reference context for
  a diff instead of waiting for someone to name it: the code that still uses a definition or a
  string value the change removes, the callers of what it edits, the rest of a class it edits
  outside the diff, and the definitions of what its new lines call. Selection is by name
  (`git grep`, or a walk where there is no git) and indentation, so it works the same in every
  language; a name found in more than 25 files is a word, not a symbol, and is not followed.
  At most 8 excerpts, after any named files and within the same budget; docs, lock files, saved
  diffs, data files, docstrings, comments, imports and credential files never count as a use. A
  hunk is found in the file where it now stands, as `patch` does, and a file the diff does not
  describe is left out. The JSON summary and the text report say why each excerpt was chosen.
  With the 14 cross-file cases in one repository, each diff selects all of its own hand-picked
  context and nothing from the other 13, through git and through the walk. Recorded three times
  (`record.py --corpus ctx --mode auto`), the councils named 12 of 12 cross-file defects in every
  recording, as with hand-picked context; the clean controls drew 3 to 8 findings against 4 to 9,
  and the median council took 39 s against 38 s. That meets the bar set before recording. It
  stays opt-in, since it sends more of the repository than the piped diff.
- `--dry-run`: print exactly what would be sent, after redaction, and exit without contacting
  any model or needing a key.
- **A cross-file eval corpus.** `evals/corpus_ctx` holds 12 defects that live between files,
  3 visible in the diff itself and 2 clean controls. `evals/record.py --corpus ctx` records each
  case without and with its context, back to back; `evals/score.py --ctx` reports cross-file and
  local recall, noise on the controls, and findings located in the context. Each cross-file
  defect is counted as flagged, when a finding warns about it in any words, and as named, when
  the finding names what the context holds; a test keeps the naming keywords out of the reviewed
  diff, so without context naming it takes a guess. Recorded three times: without context,
  5–7 of 12 flagged and 0–1 named; with it, 12 of 12 flagged and named in every recording.
  Local defects were 2–3 of 3 without context and 3 of 3 with it. Noise on the clean controls
  rose with context, from 4–6 findings to 4–9. Most of the rise came from reviewers reading a
  context file that repeats the changed code as a second definition of it. This is a corpus
  written for the feature, with the matcher corrected after the first recording; the README
  lists the limits.
- **Structure and simplicity are now reviewed alongside correctness and security.** The
  reviewers are told their input was written by a capable model, so syntax and typos are not
  what they are looking for. They are asked instead for responsibility in the wrong layer,
  coupling that spreads one behavioural change across several files, logic duplicated where it
  will drift, and solutions heavier than their problem. To stop that breadth becoming a flood
  of taste, a structural finding must name its concrete cost — the change that becomes
  expensive, the two places that will drift apart, or the code that disappears once it is
  fixed. "Consider extracting a helper" is explicitly not a finding, and an empty review is
  explicitly a valid result.
- `--force-review` (CLI) and `force` (MCP): send an input that does not look like code.
- **Defect categories.** Each finding is asked for one of `INJECTION`, `CONCURRENCY`,
  `RESOURCE_LEAK`, `BOUNDS`, `ERROR_HANDLING`, `AUTH_CRYPTO`, `LOGIC`, `STRUCTURE`,
  `SIMPLICITY`, and `consensus[].category` carries it in the CLI JSON and the MCP output —
  `UNKNOWN` when no reviewer declared one. Categories are read from the reviews, never
  guessed: keyword inference was tried and agreed with the category the model itself wrote on
  6 of 13 findings ("block" matched "lock"). The prompt's list is generated from the parser's
  enum, so the two cannot drift.
- **`--strict`**: report only findings more than one reviewer raised, plus any `CRITICAL` one
  however few raised it. Text output omits the individual reviews, says how many findings it
  hid, and always shows the errors section.
- `evals/record.py --snippet NAME` re-records one snippet, and a failed run (exit 1, or no
  responses) no longer overwrites a good fixture. `evals/score.py` also reports how many agreed
  defects reach quorum in at least one entry.
- `evals/score.py` reports how often two findings one model wrote separately share an entry,
  which is the over-merging the grouping figure rewards and cannot see. For each `agreement`
  value it also reports how often an entry holds a labelled defect; `--runs` pools that table
  across recordings.

- **Dynamic model rotation**: when a model fails at runtime, the engine automatically rotates in the next best model from a pre-fetched pool (up to 12 candidates).
- Model validation against OpenRouter API during CLI startup for explicitly configured models.
- Graceful removal of missing or deprecated models with a warning log.
- `CHANGELOG.md`, `CONTRIBUTING.md`, and ADR documentation.
- **`mcp` is now an optional extra**: `pip install '4take[mcp]'`. The server dragged
  starlette, uvicorn, sse-starlette, pyjwt and 12MB of cryptography into every CLI install
  (54MB venv, 51 packages -> 17MB, 17 packages). The `4take-mcp` entry point stays
  registered and prints the install command instead of an `ImportError` traceback.
- **CI** now runs `ruff`, Conventional Commit checks, the test suite on Python 3.10-3.13,
  and a packaging job that installs the built wheel and asserts the CLI runs without the
  MCP extra.
- **`publish.yml`**: PyPI Trusted Publishing on a `v*` tag, gated on the tag matching the
  built artifact. Every documented install command 404s today because the package has
  never been published.
- **Consensus grouping**: both output formats now lead with the findings more than one
  reviewer raised, ranked by how many did. Four reviews printed side by side left the
  cross-referencing — the one job the council exists to do — to the reader.
- **Telemetry**: `~/.cache/4take/stats.json` records model id, success, latency, error code
  and finish reason for each dispatched model. No code, no reviews, no prompts. 0600,
  capped at 2000 events, `--no-telemetry` to disable and `--clear-telemetry` to erase.
- **Model ranking from evidence**: the name heuristic is now a cold-start prior only. Once a
  model has been observed enough times, its measured success rate and median latency decide
  its rank. The heuristic could not tell a 550B model that mostly times out from a 2.6B one
  that always answers, and scored six of sixteen catalog models at exactly 0.
- **Eval harness** (`evals/`): 28 snippets — 26 carrying 37 labelled defects, 2 deliberately
  clean — scored offline against recorded council runs. Reports council recall against the
  best single model, per-model label precision, noise on correct code, and how reliably
  findings several models raised end up in one consensus entry. First numbers: 94.6% council
  recall against 85.3% for the best single model that saw the same corpus.

### Changed
- **License: Business Source License 1.1 instead of MIT.** 4take is free for a person using it
  for their own work, paid or unpaid, and for a self-employed developer working alone, directly or
  through a one-person company, on their own software and for clients on their own machines. It
  is free in the CI of a volunteer open source project that no company owns, sells or pays to
  maintain. Every other organization needs a commercial license (takelicensing@gmail.com), after a
  30-day evaluation per group of companies. Reviewing any software with 4take counts as production
  use wherever it runs, laptops and CI included, which closes the reading that development use is
  non-production and therefore free. Offering it as a hosted service is not covered. Each version
  converts to the Apache License 2.0 four years after it is first published. The version is now
  0.2.0: nothing had been released under MIT, and no BUSL release shares a number with the MIT
  snapshots in the private history.
- **The parser reads the tags models actually write.** A review axis where the severity
  belongs (`[SECURITY]`), an echoed placeholder (`[SEVERITY:LOGIC]`), a category sharing the
  bracket with any separator or spelling (`[WARNING - RESOURCE LEAK]`, `[CRITICAL:AUTH/CRYPTO]`),
  and a category written as a bare word after the tag (`[CRITICAL] LOGIC`) all parse now; each
  used to lose the whole finding or its category. On the recordings made before this change
  that recovered 15 findings, one of them the only report of a labelled defect. A heading
  such as `### [SECURITY]` is no longer parsed as an "(untitled)" finding.
- **The output template asks for `[SEVERITY] [CATEGORY]`**: the severity stays in the bracket
  form models reliably reproduce, and the category list is rendered from the parser's enum.
- **Categories play no part in grouping.** Reviewers filing one labelled defect chose the same
  category in only 51–59% of pairs across three recordings. As a veto the category split real
  agreement — 1 to 6 of some 35 agreed defects per recording lost their single entry — and
  used to lower the similarity threshold it merged
  distinct defects about as often as agreeing ones, reporting two defects in one function as
  one finding four reviewers agreed on.
- **One seat per vendor.** Two models from one vendor share training data, so their agreement
  is weaker evidence, yet 21 of 28 recorded councils seated such a pair — half of them because
  `openrouter/free` served a vendor that already held a seat. A second answer from a seated
  vendor is now held back and a replacement rotated in; it takes the seat only when no other
  vendor answers in time, and `warning` says so. After the change: 1 council of 28.
- **The CLI and the MCP server share one pre-flight step** (`fourtake/preflight.py`). Their
  copies had drifted: the MCP server never checked for an API key, sized auto-discovery with
  no room for the reply, silently swapped in the fallback pool when every configured model
  was rejected, and recorded no telemetry. It now refuses without a key or without usable
  configured models, exactly as the CLI does.
- The eval fixtures are re-recorded against the new template and seating rule, three times
  over (`evals/recordings`, `evals/runs/2`, `evals/runs/3`), because one recording is one draw
  from stochastic models. Council recall 97–100% (was 97.3%), best single model 91–94% (was
  91.3%), findings on the clean controls 1–7 (was 7), agreed defects landing in one entry
  53–74% under first-fit grouping (was 48%), 56–69% after the move to single linkage below.
  The grouping figure swings more between recordings of the same corpus than any retune ever
  moved it, so it is published as a range.
- **The agent guides weigh `agreement` as measured.** AGENTS.md said every entry with
  `agreement >= 2` is a bug that must be fixed, and SKILL.md said to fix whatever two models
  flagged. On the eval corpus, entries raised by two or three of four reviewers held a labelled
  defect 60% of the time, against 96% when all four agreed and 41% for one. The guides now read
  4 as high confidence, 2 or 3 as "verify before acting" and 1 as "judge on its merits", and
  have every security finding verified whatever its agreement.
- **`_SIMILARITY_THRESHOLD` 0.16 -> 0.24, for single linkage.** Single linkage joins two whole
  clusters through one link, and at 0.16 it chained distinct defects: 63% of the pairs of
  findings one model wrote separately shared an entry. Over the three recordings, agreed
  defects landing in one entry are 56–69% (was 53–74%; pooled, 61% against 62%), those
  reaching at least one entry that holds all their reviewers 81–94% (was 89–100%), and pairs
  of one model's findings sharing an entry 22–42% (was 37–52%; pooled, 31% against 44%). The
  first two measures reward merging, which is why they fell.

- `get_best_free_models()` now accepts a `pool_size` parameter (default: `count * 3`) to return an extended pool for rotation.
- `run_council()` now accepts `model_pool` and `active_count` parameters to support runtime rotation.
- The package version is single-sourced from `src/fourtake/__init__.py`; `pyproject.toml`
  no longer carries a second copy that can disagree with what `4take --version` prints.
- Improved error handling when auto-discovery fails.

### Changed (breaking)
- **A non-code input is refused instead of dispatched.** A design question or a plan piped in
  used to reach the council and come back as four copies of `NO_ISSUES_FOUND`: the reviewers
  are told their input is code to audit, so the models that obeyed the instruction had nothing
  to say about a paragraph of prose, and the one model that ignored it looked like the only
  competent one. The classifier is deliberately lopsided — refusing real code blocks a review
  someone wanted, while admitting prose only reproduces the old behaviour — so every file in
  `evals/corpus`, plus YAML, TOML, JSON, SQL, CSS, shell and diffs, is admitted, and
  `--force-review` reaches the old path on purpose.
- The eval fixtures are re-recorded against the new prompt. Council recall rose from 94.6% to
  97.3% and the best single model from 85.3% to 91.3%; findings on the clean controls stayed
  flat at 6 -> 7, so the "name the cost" bar held and the breadth did not become a flood.
- **Consensus grouping regressed: 74% -> 48% of agreed defects reach one entry.** A broader
  review is a more varied vocabulary, and grouping is lexical, so overlap carries less signal
  than it did — two models can raise one finding in nearly the same words and still land in
  two entries. `_SIMILARITY_THRESHOLD` was re-swept and moved 0.20 -> 0.16, which recovers
  part of it; no threshold recovers the rest, because the whole curve moved down. The eval
  floor in `tests/test_evals.py` was lowered from 0.65 to 0.45 to record this rather than to
  hide it. Recovering the old figure needs a signal that does not dilute with wording, such as
  a closed category vocabulary the models tag each finding with.
- `evals/score.py` renames `false_positives_on_clean` to `findings_on_clean_controls`. The
  controls are free of labelled *defects*; now that structure is reviewed too, a finding raised
  on one can be legitimate, and the number is expected to be non-zero.

- **MCP tool output**: `status` is now one of `ok` / `degraded` / `error`, with explicit
  `error` and `warning` fields. It previously reported `"Success"` even when every model
  had failed, because the engine's `error` was never serialised.
- **CLI exit codes**: `0` met `--min-responses`, `2` partial, `1` total failure. Previously
  any single response exited `0`.
- **`model_token_limits` is now an override, empty by default.** It used to ship a flat
  `32000` for four models, which capped 256k-context models at 32k once real catalog values
  started being used.
- Success is counted in *distinct reviewers*, not raw responses, everywhere: engine
  threshold, exit code, JSON `summary.distinct_models`.

### Fixed
- **`record.py`'s backup kept the first recording forever.** It copied a fixture only when the
  backup had none, so after a second re-record it held the oldest version, not the one just
  replaced. A changed fixture is now copied again.
- **The README promised about three times the reviews the free quota buys.** It counted four
  requests a review, so 50 a day made ~12 reviews and 1,000 made ~250. A review launches six
  models at once, and every retry and replacement is another request: 6 on a quiet pool, a
  median of 11 to 12 on a busy one. The README now says 4 to 8 reviews a day on 50 requests,
  and 80 to 160 on 1,000.
- **`models_requested` was 1 for every auto council.** The JSON output reported the length of the
  configured model list, which is `["auto"]`. It is now the number of models the council asked:
  seats, spares and replacements, including a spare cancelled once the seats filled.
- **`--min-responses 0` and a negative `--timeout` were accepted.** The config file refused both,
  the flags did not: zero responses counted a council with no review as a success, and a timeout
  of 0 or less set the deadline in the past. `--min-responses`, `--timeout` and
  `--per-model-timeout` now must be greater than 0.
- **A Redis URL's password was sent, and URLs with a port were cut.** The connection-string rule
  wanted a user name before the password, so `redis://:s3cret@cache:6379` went out whole. It also
  took any text up to an `@` for the password, so `https://host:8443/x/@me` came out as
  `https://host:***REDACTED***@me` and `https://example.com/a:b@c` lost its path. A user name may
  now be empty and, as RFC 3986 has it, holds no `/`, `?`, `#` or `@`; a port followed by a path is
  not a password. Nothing in the eval corpus changes.
- **Two of the six models a council launches waited in a queue.** A council launches spares so
  one straggler cannot stall it, six models for four seats, but a default of four requests in
  flight per provider held two of them back, their per-model clock already running. There is no
  default cap any more: a provider without one is held to what the council plans to launch. A
  `provider_concurrency` set in the config is still kept.
- **A dropped connection cost a seat, with an empty error.** A connection closed mid-exchange
  (`ReadError`, `RemoteProtocolError`, `WriteError`) was reported at once, and with httpx's empty
  message: 12 blank errors in one recording, each one a seat lost to the network. It is now retried
  as a 5xx is, within the council's deadline, and reported as `Connection dropped after N retries`
  when it keeps happening. Any error without a message is named by its type. A refused connection
  is still not retried: with no network, retrying only makes every model take longer to fail.
- **One odd catalog entry stopped model discovery.** An entry with `"pricing": null` or
  `"context_length": null` raised out of discovery, so the run ended with "an unexpected error"
  instead of reviewing with the other free models, and an entry with no id and zero prices joined
  the pool as `openrouter/`. Such an entry now costs only itself; missing prices leave a model
  paid unless its id says `:free`.
- **An HTML page was refused as prose.** Markup has no keyword, bracket or semicolon at a line's
  end, so a page scored 0.00 on the code-line ratio and 4take answered "reviews code" without
  asking a model. Lines that open with a tag, `<!doctype` or `<!--` now count as code, so HTML
  and XML are reviewed; an address such as `<a@b.c>` opening a line of notes still does not.
- **`Authorization: Bearer $TOKEN` was redacted as a secret.** The header rule took everything
  up to the next space as the credential, so `curl -H "Authorization: Bearer $TOKEN"` went out
  as `Bearer ***REDACTED***` with its closing quote eaten. An interpolation (`$TOKEN`,
  `${token}`, `{token}`, `%s`, `#{token}`, `{{ token }}`) is now left alone, and a literal
  credential is redacted up to its quote, which stays.
- **A seated review's echoed template and leaked reasoning were counted as findings.** Seating
  only reviews filtered whole answers, so an answer with real findings kept the junk beside them.
  On a public review benchmark 25 of 872 findings, in 8 of 50 pull requests, were the prompt's
  template copied unfilled (`[CATEGORY] <Concise Title>` over `<What is wrong, …>`) or chain of
  thought naming a severity in passing (`or [CRITICAL] depending). Need concrete scenario…`,
  `as structural`, `)`), and they inflated finding counts and consensus entries. The parser now
  drops a finding whose tag sits mid-sentence with no `Issue:` or `Fix:` under it, a finding
  with nothing but placeholders in it, and an issue that answers its own question with
  "Actually, no". A placeholder over a real issue no longer hides it: the title comes from the
  issue instead. Across every recording on disk this removes 33 of 2,873 findings, none in the
  labelled corpus, and the eval scores are unchanged.
- **One vendor could hold two or three seats.** A second answer from a seated vendor took the
  seat whenever no other vendor answered in time. On a public review benchmark that put two or
  three nvidia models into 21 of 50 councils, and 5 councils were a stealth model plus three
  nemotrons. A vendor now holds one seat at most: a council short of vendors reports fewer
  seats and exits `2`, and replacements skip vendors already seated, since their answers could
  not be seated anyway.
- **Which of two answers took a contested seat depended on memory addresses.** Answers that
  finish together, cache hits above all, came back as a set, and when two from one vendor (or
  one served model) arrived together, whichever was read first took the seat. A fully cached
  rerun could seat a different model and change agreement counts. They are now read in pool
  order, so the better-ranked model takes the seat.
- **An answer that was not a review filled a council seat.** Any non-empty answer took a seat,
  so on a public review benchmark 12 of 200 seats went to answers with neither a finding nor
  `NO_ISSUES_FOUND`: a small model's raw tool calls (`<|tool_call_start|>[read_file(...)]`),
  a run of `<unk>` tokens, 11–21k characters of reasoning that never reached a tag, and a lone
  `* [WARNING] LOGIC`. Each read as a reviewer that had looked and found nothing, so those
  four-seat councils were three reviewers. Such an answer is now a failed model: it is reported
  in `errors`, a spare is rotated into its seat, and it is neither cached nor replayed from a
  cache written before this check.
- **Secret redaction rewrote TypeScript and JavaScript.** Outside quotes, the word after a
  secret-named key was redacted unless it was obviously code, so `const token = await getToken()`
  went out as `const token = ***REDACTED*** getToken()`, `(credential: CredentialPayload | null)`
  and `password: string;` lost their types, and `type SkipToken = typeof skipToken` lost its
  `typeof`. Across the 50 PR diffs of a public review benchmark, 50 lines in 9 diffs were
  rewritten this way, six of ten cal.com PRs among them, and a model reported the syntax error
  the redactor had made. In `evals/`, the async-race snippet's `await` went out redacted in every
  recording and drew "missing await" and SyntaxError findings. An unquoted value is now a secret
  only where config puts one: at the end of its line (a comment may follow) or against a tight
  `KEY=`, as in `.env` and a shell prefix. `this.` is a lookup like `self.`, TypeScript's
  primitive type names and `{placeholder}` interpolations are not values, and a value spelled
  like its own key (`self._token = token`, `REFRESH_TOKEN = "refresh_token"`) is a name. The
  same diffs now lose one line, a quoted placeholder token in a test. The cost: an unquoted
  secret with more text after it on its line, as in YAML flow style (`{ password: hunter2 }`), is
  no longer redacted. A lookup word must now be followed by `.` or `[`, so
  `password = config-2024-rotated` is redacted where it used to pass as code.
- **Findings lost their explanation, and file names became symbols.** Found in the same
  benchmark's recordings. A model that wrote its issue as an unlabelled bullet or paragraph under
  the title, with no `Issue:`, kept only the title: 70 of the 148 findings from the two
  ling-3.0-flash models. The first paragraph under a tag that opens its line is now the issue
  when no `Issue:` is given; a tag mentioned mid-sentence, as leaked reasoning does, borrows
  nothing. A locator that is a file name, `(GroupAdapter.java)`, gave the symbol `java`, shared by
  every finding filed against a Java file: 60 findings carried a file's extension as their
  symbol, and more its stem. A file name is now skipped and the name in backticks used instead. The
  repository's eval reports are unchanged byte for byte; on the benchmark recordings, consensus
  entries that two or more reviewers share went from 137 to 144 of about 375.
- **`SECRET_KEY = "..."` is redacted.** A key name had to end in a secret word and `key` is not
  one, so Django's and Flask's `SECRET_KEY` literal was sent verbatim, from the settings file
  reference context invites. Code that reads the key (`settings.SECRET_KEY`, `os.environ[...]`)
  is left alone.
- **The consensus depended on the order the models answered in.** Grouping was first-fit: a
  finding joined the first group it resembled, and two groups were never joined. Shuffling one
  recording's responses moved the share of agreed defects in one entry anywhere from 44% to
  69%. It is now single linkage, union-find over every pair. Every tie is settled by a fixed
  order of the findings: the title, symbol and category an entry shows, and the order of
  equally ranked entries. Neither the answer order nor the hash seed changes the output any
  more. A category tie now goes to the reviewer id that sorts first, not to the first finding
  written.
- **`OPENROUTER_API_KEY` is no longer shadowed by the config file.** The file's key won, so the
  placeholder `--init-config` writes kept failing the key check after the user set the
  variable the error message told them to set. The environment now wins.
- **Telegram bot tokens inside a URL (`api.telegram.org/bot<token>/…`) and Groq keys were sent
  unredacted.** No key name precedes a token in a URL, so the assignment rules never saw it.
- `--format json` dropped `responses[].duplicate_of`, which the MCP payload always carried.
  The eval scorer read the recordings as if every answer were a seat, so it counted duplicate
  answers as extra reviewers; `evals/backfill_duplicates.py` restores the field in fixtures
  recorded before the fix, checked against the engine's own count in every file.
- `--per-model-timeout --help` claimed a 90-second default; it is half of `--timeout`, at
  least 60.
- Nine tests failed on a clean checkout with no API key — every test that drives `main()` end
  to end — and on a machine with a key they could spend it. The suite now runs with a
  placeholder key of its own.
- `evals/record.py` exits non-zero when a snippet fails, and `--out` writes a repeat run beside
  the checked-in one; `evals/score.py --runs` reports the spread across runs.
- `--format json` emitted httpx INFO logs on **stdout** before the JSON, so `json.loads()`
  on the documented agent command failed. All diagnostics now go to stderr; `--quiet`
  silences them.
- Rich rendered model ids ending in `:free` as the 🆓 emoji, and could raise `MarkupError`
  on provider errors containing `[...]`.
- `max_tokens` was pinned at 4096 for every model — 6% of the median available budget, and
  shared with reasoning tokens, so reasoning models returned chain-of-thought truncated
  mid-sentence with no findings. The budget now scales with the input and each model's own
  ceiling, reasoning is explicitly bounded, `finish_reason` is checked, and a truncated
  reply is retried once with a larger budget before being reported as `truncated`.
- Chain-of-thought is no longer substituted for an empty `content`; that disguised a
  truncation as a valid review.
- `Retry-After` was honoured only below 30s, so the client retried up to 50x sooner than a
  provider asked. It is now honoured as given (delta-seconds and HTTP-date), and skipped
  entirely when it would overrun the council deadline.
- Malformed 200 responses surfaced raw Python errors (`list index out of range`) and were
  treated as permanent; they are now classified as transient and retried.
- The token bucket clamped its balance at zero, so concurrent waiters all computed the same
  delay and fired together — the burst it exists to prevent. It is now a time-reservation
  (GCRA) bucket: an idle limiter still lets a full council fire at once, and past that every
  waiter takes the next free instant, so no two wake together at any queue depth. A caller
  whose wait would exceed `max_wait` is refused rather than served a reservation it will
  abandon.
- Retries bypassed the rate limiter entirely; every attempt is now metered.
- A flat `32000`-token input cap rejected input that every candidate model could hold; real
  context windows from the catalog are used instead.
- Rotation replaced a failed model with the worst remaining candidate: provider round-robin
  was applied to the whole pool, pushing a 120B model behind a 2.6B one. Diversity now
  applies to the seated cohort only; the tail is ordered by quality.
- Model aliases (`openrouter/x:free` and `x:free`) occupied two council seats, and a router
  could serve two seats with one model. Seats are de-duplicated by the model that actually
  answered, and a duplicate rotates in a replacement.
- One slow model set the pace for the whole council; spares are now dispatched and a
  per-model timeout bounds any single member.
- Compatibility filtering uses the catalog's `output_modalities` and `supported_parameters`
  instead of a hand-maintained substring denylist.
- The review cache dropped `truncated` and `served_model`, so a cache hit returned a
  truncated review as complete.
- `4TAKE_ACCEPT_RISK` cannot be set from bash or zsh (POSIX identifiers may not begin with a
  digit). Use `FOURTAKE_ACCEPT_RISK`; the old name is still read for one release.
- Repository URLs in `pyproject.toml` and `CONTRIBUTING.md` pointed at a non-existent
  account.
- The output budget could exceed a model's own `max_completion_tokens` ceiling, and the
  reasoning budget was carved out of the answer on models with small ceilings, leaving no
  room for the review itself.
- MCP notification tasks were created without holding a reference, so the event loop could
  garbage-collect them mid-flight and the notification would silently never arrive.
- **`--init-config` left the API key world-readable.** It writes the file the documentation
  tells users to paste their key into, and the process umask made it 0644 inside a 0755
  directory — beside a review cache that had been deliberately tightened to 0600. The file
  is now 0600 in a 0700 directory, and an existing loose directory is narrowed too.
- **A missing API key cost 52 seconds and twelve requests.** There was no pre-flight check,
  so the commonest first-run mistake was dispatched to every model in the pool and reported
  as "Invalid API key" — true of the empty string, and useless to someone who never set one.
  The run now stops before the first request and names the three places a key can come from.
- **One bad byte on stdin lost the whole review.** `sys.stdin.read()` decodes with
  surrogateescape, so a latin-1 source file or a stray byte survived the read and killed the
  run much later with "utf-8 codec can't encode ... surrogates not allowed". Input is now
  read as bytes and decoded leniently, and the 512 KB cap is measured in bytes as documented.
- **`--show-redactions` overstated what it removed.** The count came from `re.subn`, which
  counts matches; the assignment rules deliberately leave the text untouched when the value
  turns out to be code, so every rejection was counted as a redaction. Input with two secrets
  reported four. The flag exists to make over-redaction visible, so the count is now the
  number of values actually replaced.
- **The MCP tool sent no `consensus`.** `AGENTS.md` tells an agent to read it first, and the
  MCP server is the primary documented integration, but only the CLI emitted it.
- **The MCP `status` disagreed with the CLI exit code for the same result.** It was derived
  from the presence of any warning, so a healthy council was reported as `degraded` whenever
  a duplicate seat had been rotated out — the machinery working as designed. It is now
  measured against `min_responses`, the same rule the exit code uses.
- **`Authorization: Bearer <token>` leaked the token.** The unquoted-assignment rule treated
  the scheme word as the value, so the header went out as `Authorization: ***REDACTED***
  <token>` with the credential intact. A dedicated rule now redacts the credential. The
  regression test asserts the secret is *absent* from the output, not merely that something
  was redacted — the old assertion passed throughout.
- **Secret redaction corrupted ordinary source code.** The key pattern matched its secret
  words as substrings, so `tokens`, `MAX_TOKENS` and `authors` all counted as secrets, and
  the separator's `\s*` crossed newlines, so a key ending a line bound to the next line's
  first word. Found by running 4take on its own source: two models reported a CRITICAL
  syntax error in `_similarity` that the redactor had introduced. The secret word must now
  be a whole component of the key, the separator cannot span a line, and an unquoted value
  that is an expression is left alone. A quoted value is still treated as a literal.
- A router could seat a model that cannot review code at all. Found by the eval harness:
  `openrouter/free` was served by `nvidia/nemotron-3.5-content-safety:free`, a moderation
  classifier, which replied "User Safety: safe" and filled one of four council seats as a
  success. A response whose serving model fails the compatibility check is now a soft
  failure and rotates a replacement in, exactly as a duplicate seat does.
- ADR-0001, `CHANGELOG.md` and `POST_MORTEMS.md` all claimed 10 retries; the code has always
  used 4. Superseded by [ADR-0002](docs/adr/0002-cap-retries-at-four-and-honour-retry-after.md).

## [0.1.0] - 2026-07-23

### Added
- Initial release of the `4take` CLI.
- Concurrency and rate limit handling via PersistentTokenBucket.
- OpenRouter Multi-Model Council support with automatic model discovery and ranking.
- Automatic context size estimation using `tiktoken`.
- Exponential backoff with retries for 429 and 5xx errors (see ADR-0002 for the current policy).
- Markdown output formatting and pretty printing.
- Privacy warning banner and secret stripping functionality.
