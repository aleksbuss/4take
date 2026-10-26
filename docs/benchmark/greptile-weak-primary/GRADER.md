# Grading instructions

You are grading code reviews of real pull requests against a known bug label. Each packet
(`packets/<packet>.md`, e.g. `packets/W017.md`) gives the label and several reviews of the same
change, each written independently, as `<seat id="A">` … `</seat>`. The diff the reviewers saw is the
`packets/<case>.diff` the packet names; consult it when you need the code to decide whether a finding
is about the labelled defect. Use only the packets you were given and the diffs they name. Do not
open or search any other file. Grade each seat on its own; seats may resemble each other.

## Rubric

Grade each seat on its own. A seat's grade is the best grade among its findings.

- HIT: a finding names the labelled defect: the same location (file or function) and the same
  mechanism, so that a developer reading only that finding would find this bug.
- PARTIAL: the right location and a related concern, but the mechanism is wrong, vague or generic
  (for example "consider try/catch" on an unhandled rejection; "check for null" without naming
  which value is null and why).
- MISS: anything else, including a different bug on the same line.

When the packet says the label was shown to be wrong, grade the seats against the label as written
or any of the readings it lists, with the same rubric.

Grade every seat, not only the first that catches the defect. Judge what the finding says, not
whether the reviewer is right about anything else, and not how confident or long it is. A seat that
says it found no defects, or is empty, is a MISS.

## Output

For each packet, write `out/<packet>.json` (the `out` directory you were given) with the Write tool,
exactly this shape:

```json
{"packet": "W017",
 "seats": {
   "A": {"grade": "HIT", "quote": "…", "reason": "…"},
   "B": {"grade": "MISS"}
 }}
```

- Every seat in the packet appears, graded `HIT`, `PARTIAL` or `MISS`.
- For a `HIT` or `PARTIAL`: `quote` is copied verbatim from that seat's text, at most 40 words, the
  words that name the defect. Keep its characters exactly, backticks and punctuation included; you
  may start and end it at any word, but do not paraphrase, join separate passages, or fix typos. It
  is checked by a script against the seat's text. `reason` is one sentence on why it is a HIT or
  PARTIAL rather than the grade below it.
- For a `MISS`, `quote` and `reason` are left out.

When you are done, reply with one line per packet: the packet id and its seat grades, e.g.
`W017 A=HIT B=MISS C=PARTIAL D=MISS E=MISS F=HIT`.
