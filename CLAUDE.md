# 4take — Multi-Model Code Review (Claude Code Guide)

## What is this?
4take is a CLI and MCP server that queries 4 independent LLMs in parallel to review code.
You pipe code into it, and it returns multi-model opinions to eliminate single-model hallucinations.

It reviews four axes, not just defects: **correctness**, **security**, **structure**
(responsibility in the wrong layer, coupling that spreads one behavioural change across
several files, logic duplicated where it will drift) and **simplicity** (indirection with one
implementation, a hand-rolled stdlib, control flow that can be halved). The reviewers are told
their input was written by a capable model, so they are not hunting syntax. A structural
finding is only reported when the model can name its concrete cost.

It reviews code. A design question or a plan piped in is refused before any request goes out,
because the reviewers would all answer `NO_ISSUES_FOUND`. Pass `--force-review` to override.

## Usage for Claude Code
Runs only on OpenRouter's free models (`:free` tier); a paid model is rejected before any request goes out, so never pass `--models` with one. Free-tier limits apply: roughly 4 to 8 reviews a day on an OpenRouter account that has never been topped up, roughly 80 to 160 once it has.

```bash
cat src/module.py | 4take --format json --accept-risk --quiet

# Reviewing a diff: send the code it touches as reference context, read but never reviewed
git diff -W | 4take --format json --accept-risk --quiet --context-file src/caller.py --context-file src/models.py:10-60

# ...or let 4take choose it by name; `summary.context.auto` says why each excerpt was picked
git diff -W | 4take --format json --accept-risk --quiet --auto-context
```

A diff alone hides defects between files. With a diff, pass each changed function's direct
callers, the functions it calls, and the types or config it relies on — a few files, most
relevant first, not the repository (16000-token budget). `summary.context` lists what was sent
and what the budget dropped.

## Reading the output
stdout is a JSON object and nothing else (all logs go to stderr). Key fields:
- `responses[].model` — the model that was requested
- `responses[].served_model` — the model that actually answered; key consensus on THIS, since
  a router can seat the same underlying model twice
- `responses[].content` — each model's full review critique
- `responses[].truncated` — true if the review was cut off; treat it as incomplete
- `responses[].duplicate_of` — set when a response shadows a seat already taken (the same model
  again, or a second model from a seated vendor); it does not count towards agreement
- `responses[].latency_ms` — response time in milliseconds
- `warning` — non-null if fewer models responded than expected
- `error` — non-null if all models failed
- `consensus[]` — the findings already grouped across models, ranked by how many independent
  reviewers raised each one. Start here, not with `responses`:
  - `agreement` — how many distinct reviewers raised it. This is the number that matters.
  - `severity` — the worst severity any of them assigned; `disputed` is true when they differed
  - `category` — the defect class the reviewers declared, `UNKNOWN` when none did; never guessed.
    Reviewers pick the same category for one defect only about half the time, so it is a label,
    not evidence, and grouping ignores it
  - `symbol`, `title`, `models`, `severities`

Exit codes: `0` met min-responses, `2` partial (see `warning`), `1` total failure (see `error`).

A large diff can take up to the 600 s timeout and often seats fewer than four reviewers (exit
code `2`): few free models reason through one to an answer. A partial council is still a review.
Read `agreement` against `summary.distinct_models`; the shares in step 2 below were measured on
councils of four.

## Your role as Synthesizer / Chairman
1. Read `consensus` first; it is the cross-referencing already done.
2. Weight a finding by `agreement`, not by how confident one model sounds, and read it as
   measured. On the eval corpus, where every council seated four reviewers, an entry all four
   raised held a labelled defect 96% of the time, one raised by two or three 60%, and one raised
   by a single reviewer 41%. So 4 is high confidence, 2 or 3 means verify before acting, and 1
   means judge it on its merits. The shares are floors: the snippets hold defects nobody
   labelled.
3. Verify a security finding whatever its agreement, and fix a confirmed one even if 1 model
   raised it — grouping is lexical, so an `agreement: 1` entry may still be a real defect the
   others simply worded differently.
   Judge a structure or simplicity finding on the cost it names rather than on `agreement`:
   four reviewers rarely word one design opinion alike, so `agreement: 1` is the normal case
   on those two axes, not a warning sign.
4. Verify each claim against the code before acting on it. Measured on `evals/` over three
   recordings of the corpus, the council finds 97–100% of labelled defects against 91–94% for
   the best single model that saw the same corpus — a real lift of 4 to 6 points, and nowhere
   near proof. Models also raise findings on code with no labelled defect at all, and two of
   them can agree on the same wrong claim.
5. Produce the final improved code for the user.

## Measuring it
`evals/` holds 28 snippets — 26 carrying 37 labelled defects, 2 deliberately clean — and the
recorded council runs over them. `python evals/score.py` reports council recall against the
best single model, per-model label precision, noise on the clean controls, the agreement
matrix, and how often a defect several models found lands in one consensus entry (56–69%
across recordings, against 74% when the reviewers were asked for defects alone: grouping is
lexical, and a broader review is a more varied vocabulary). That figure rewards merging, so it
is reported beside how often two findings one model wrote separately share an entry (22–42%).
Grouping is single linkage and does not depend on the order the models answered in. For each
`agreement`, the report also gives how often an entry holds a labelled defect; `--runs` pools
those shares across recordings, and step 2 above quotes them. Recording is the only step that
touches the network (`python evals/record.py`); the corpus is recorded three times, and
`python evals/score.py --runs evals/recordings evals/runs/2 evals/runs/3` reports the spread.

The labels cover defects only, so nothing here measures the structure or simplicity axes.

