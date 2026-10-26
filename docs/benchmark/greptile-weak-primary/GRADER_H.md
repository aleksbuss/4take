# Counting false findings

You are checking code reviews of real pull requests for findings that are false. Each packet
(`packets/<packet>.md`) holds several reviews of the same change, each written independently, as
`<seat id="A">` … `</seat>`. The diff the reviewers saw is the `packets/<case>.diff` the packet names.
Ignore the bug label in the packet: this task is not about it. Use only the packets you were given
and the diffs they name. Do not open or search any other file.

## What to judge

For each seat, go through its findings in order. A finding is one numbered item, or, where the seat
does not number them, one separately stated defect. Mark each finding:

- FALSE: the diff shows the claim is wrong. The code it points at does not exist in the diff, or does
  not do what the finding says, or the failure it describes cannot happen given the code shown.
- NOT_FALSE: anything else, including a real defect, a doubtful one, and a claim that needs code the
  diff does not show to be checked.

Judge only whether each claim is false, not whether it is important, well written or the labelled
bug. A seat that reports no defects has zero findings.

## Output

For each packet, write `out/<packet>.json` with the Write tool, exactly this shape:

```json
{"packet": "W017",
 "seats": {
   "A": {"findings": 4, "false": [2], "reasons": {"2": "…"}},
   "B": {"findings": 0, "false": [], "reasons": {}}
 }}
```

`findings` is the number of findings in the seat; `false` lists the 1-based positions of the FALSE
ones; `reasons` gives one sentence per FALSE finding saying what in the diff contradicts it. When you
are done, reply with one line per packet, e.g. `W017 A=1/4 B=0/0 C=2/7`.
