# Ruling on disputed seats

Two graders graded AI code reviews of real pull requests against a known bug label and disagreed on
some seats. For each packet you are given, `disputes/<packet>.md` names the disputed seats and shows
both graders' grades, quotes and reasons. The packet itself is `packets/<packet>.md`, with the
reviews as `<seat id="A">` … `</seat>`; the diff the reviewers saw is the `packets/<case>.diff` it
names. Read the packet and decide each disputed seat yourself, using the rubric below. The two
graders are not authorities: either may be right, or neither. Use only the files named here.

## Rubric

A seat's grade is the best grade among its findings.

- HIT: a finding names the labelled defect: the same location (file or function) and the same
  mechanism, so that a developer reading only that finding would find this bug.
- PARTIAL: the right location and a related concern, but the mechanism is wrong, vague or generic
  (for example "consider try/catch" on an unhandled rejection; "check for null" without naming
  which value is null and why).
- MISS: anything else, including a different bug on the same line.

When the packet says the label was shown to be wrong, grade against the label as written or any of
the readings it lists, with the same rubric. Judge what the finding says, not whether the reviewer
is right about anything else, and not how confident or long it is.

## Output

For each packet, write `out/<packet>.json` with the disputed seats only, using the Write tool:

```json
{"packet": "P017",
 "seats": {
   "B": {"grade": "PARTIAL", "reason": "…"}
 }}
```

`grade` is `HIT`, `PARTIAL` or `MISS`; `reason` is one sentence. When you are done, reply with one
line per packet, e.g. `P017 B=PARTIAL`.
