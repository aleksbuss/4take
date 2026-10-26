# evals

Fixtures and recordings that measure what the council adds over a single reviewer. Start with
`python evals/score.py` (offline, free) and the "Measuring the council" section of the top-level
README.

**`corpus/` and `corpus_ctx/` contain deliberately defective code.** SQL injection, shell
injection, path traversal, races and resource leaks are the test data: reviewers are scored on
whether they find them. Nothing imports or runs these files, and a scanner that flags them is
reading the fixtures correctly. `labels.json` and `labels_ctx.json` list which defect each file
carries.

| Path | What it holds |
| :--- | :--- |
| `corpus/`, `labels.json` | 28 self-contained snippets, 37 labelled defects, 2 clean controls |
| `corpus_ctx/`, `labels_ctx.json` | 14 cross-file cases (a diff plus the files it touches) |
| `recordings/`, `runs/2`, `runs/3` | the snippet corpus recorded three times |
| `runs/5` | the snippet corpus recorded on 2026-10-08 with the 32768-token reasoning room; the source of the README's 50 s median and "27 of 28 councils seated four" |
| `recordings_ctx/`, `runs_ctx/` | the cross-file corpus recorded without context, with it, and with `--auto-context` |
| `record.py` | re-records against live models; the only step that uses the network |
| `score.py` | scores recordings offline |
| `backfill_duplicates.py` | one-off migration that restored `duplicate_of` in older recordings |
