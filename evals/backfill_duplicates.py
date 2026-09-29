"""
Restore `duplicate_of` in fixtures recorded before the CLI's JSON output carried it.

The engine marks an answer that does not count as an independent seat — the same served model
again, or another model from a vendor that already holds a seat — but `4take --format json`
dropped the field, so the scorer counted those answers as extra reviewers. Which answers they
were is fully determined by the order the engine returns them in, so this replays the engine's
seating rule over that order. Each fixture is checked against the engine's own warning, which
states how many answers it held back; a fixture whose count disagrees is left untouched and
reported, to be re-recorded instead.

    .venv/bin/python evals/backfill_duplicates.py evals/recordings evals/runs/2 evals/runs/3
"""

import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

from fourtake.discovery import get_model_provider

_HELD = re.compile(r"(\d+) response\(s\) came from an already-seated model")


def replay(payload: dict) -> list[str | None]:
    """`duplicate_of` for each response, as the engine's seating rule assigned it."""
    minimum = payload["config"]["min_responses"]
    seated: set[str] = set()
    seated_vendor: dict[str, str] = {}
    marks: list[str | None] = [None] * len(payload["responses"])
    held: list[int] = []
    for i, response in enumerate(payload["responses"]):
        seat = response.get("served_model") or response["model"]
        vendor = get_model_provider(seat)
        if seat in seated:
            marks[i] = seat
        elif vendor in seated_vendor:
            held.append(i)
        else:
            seated.add(seat)
            seated_vendor[vendor] = seat
    for i in held:
        seat = payload["responses"][i].get("served_model") or payload["responses"][i]["model"]
        if len(seated) < minimum and seat not in seated:
            seated.add(seat)
        else:
            marks[i] = seated_vendor[get_model_provider(seat)]
    return marks


def main(directories: list[str]) -> int:
    disagreements = 0
    for directory in directories:
        for path in sorted(pathlib.Path(directory).glob("*.json")):
            payload = json.loads(path.read_text())
            if any("duplicate_of" in r for r in payload["responses"]):
                continue  # recorded after the CLI started emitting the field
            marks = replay(payload)
            stated = _HELD.search(payload.get("warning") or "")
            if sum(m is not None for m in marks) != (int(stated.group(1)) if stated else 0):
                print(f"DISAGREES, left as recorded: {path}")
                disagreements += 1
                continue
            for response, mark in zip(payload["responses"], marks, strict=True):
                response["duplicate_of"] = mark
            payload.setdefault("_eval", {})["duplicate_of_backfilled"] = True
            path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    return 1 if disagreements else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
