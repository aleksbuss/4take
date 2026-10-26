---
name: 4take-code-review
description: Get multiple independent AI opinions on your code using the 4take CLI or FastMCP server.
---
# 4take Code Review Skill

## When to use
- After writing a new feature or complex algorithm
- Before committing critical security, database, or async logic
- When you want several independent AI reviews to catch what one model misses
- When code works and you want to know whether its structure will survive the next change

Reviews four axes: correctness, security, structure and simplicity. Takes code, a diff or
config — a design question is refused unless you pass `--force-review`.

## How to use
Runs only on OpenRouter's free models (`:free` tier); a paid model is rejected before any request goes out. Free-tier limits apply: roughly 4 to 8 reviews a day on an OpenRouter account that has never been topped up, roughly 80 to 160 once it has, so do not call it in a loop.

```bash
cat path/to/file.py | 4take --format json --accept-risk --quiet

# With a diff, send the code it touches as reference context: read, never reviewed.
git diff -W | 4take --format json --accept-risk --quiet --context-file src/caller.py
```

A diff alone hides defects between files. Pass each changed function's direct callers, the
functions it calls and the types or config it relies on with `--context-file PATH[:START-END]`,
most relevant first: a few files, not the repository (16000-token budget by default).

## How to interpret the output
The command outputs a JSON object to stdout. Read the `consensus` array first — findings
already grouped across models:
- `consensus[].agreement`: how many independent reviewers raised it
- `consensus[].severity`, `consensus[].category`, `consensus[].symbol`, `consensus[].title`
- `category` is what the reviewers declared, `UNKNOWN` when none did; it is never guessed

`responses[].content` holds each model's full review when you need a finding's wording.

A large diff can take up to 10 minutes and often seats fewer than four reviewers (exit code `2`):
few free models reason through one to an answer. A partial council is still a usable review; read
`agreement` against `summary.distinct_models`.

## Synthesizer Protocol
1. **Read `consensus`, then the responses behind it**: Parse every critique you act on.
2. **Weigh agreement as measured**: on the eval corpus, an entry all four reviewers raised held
   a labelled defect 96% of the time — fix it once you have confirmed it in the code. Two or
   three reviewers: 60% — verify before fixing. One reviewer: 41% — judge it on its merits.
3. **Verify security findings at any agreement**: fix a confirmed vulnerability even if only 1
   model spotted it.
4. **Judge structure findings on their cost**, not on agreement: reviewers rarely word one
   design opinion alike, so a real structural flaw usually arrives with `agreement: 1`.
5. **Dismiss noise**: Disregard hallucinated syntax or minor stylistic differences.
6. **Apply fixes**: Implement refined code and summarize findings to the user.

