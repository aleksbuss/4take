"""
Score recorded council runs against the labelled defects in the corpus.

This is the measurement behind the project's central claim: that several independent models
find more real defects together than the best one finds alone. It reads only the checked-in
recordings, so it is hermetic, free and repeatable — the network step lives in record.py.

    .venv/bin/python evals/score.py             # human-readable report
    .venv/bin/python evals/score.py --json      # machine-readable summary
    .venv/bin/python evals/score.py --ctx       # cross-file corpus, without and with context
    .venv/bin/python evals/score.py --ctx evals/recordings_ctx evals/runs_ctx/2 evals/runs_ctx/3  # its spread
"""

from __future__ import annotations

import argparse
import json
import math
import pathlib
import re
import statistics
import sys
from collections import Counter, defaultdict

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

from fourtake.findings import build_consensus, parse_findings

ROOT = pathlib.Path(__file__).resolve().parent
LABELS = json.loads((ROOT / "labels.json").read_text(encoding="utf-8"))
RECORDINGS = ROOT / "recordings"

# Share of the corpus's labelled defects a model must have been seated for before its recall
# is allowed to stand as the single-model baseline.
MIN_EXPOSURE_FOR_BASELINE = 0.5


class _Seat:
    """One model's review of one snippet, as recorded."""

    def __init__(self, payload: dict):
        self.model = payload.get("served_model") or payload["model"]
        self.content = payload.get("content")
        self.truncated = bool(payload.get("truncated"))
        self.duplicate_of = payload.get("duplicate_of")
        self.served_model = self.model


def _plain(text: str) -> str:
    # Markdown code spans are formatting, not wording: "calls `session.commit()`" says what
    # "calls session.commit()" says, and a keyword must not miss it over a backtick.
    return text.lower().replace("`", "")


def _matches(finding, defect: dict, include_fix: bool = True) -> bool:
    """
    Whether one finding reports one labelled defect.

    Both halves are required. The symbol alone would credit a model for saying anything at
    all about the right function; a keyword alone would credit it for using the word "race"
    anywhere in a long review. `include_fix=False` reads only what the finding claims is wrong,
    not what it suggests doing about it.
    """
    haystack = _plain(f"{finding.title} {finding.issue} {finding.fix if include_fix else ''}")
    symbol = _plain(defect["symbol"])
    located = _plain(finding.symbol or "") == symbol or symbol in haystack
    return located and any(_plain(k) in haystack for k in defect["keywords"])


def _clusters_containing_defect(clusters, defect: dict) -> list:
    """Return all consensus clusters holding at least one finding matching the labelled defect."""
    return [c for c in clusters if any(_matches(f, defect) for f in c.findings)]


def _same_model_pairs(clusters) -> tuple[int, int]:
    """
    Pairs of findings one model wrote separately, and how many of those share an entry.

    This is the over-merging the labelled measures cannot see: an entry that swallows a whole
    snippet "groups" every defect in it. A model that writes two findings mostly means two
    defects, so this needs no labels. Some pairs are one defect restated under a second axis,
    so it is not expected to reach zero.
    """
    def pairs(counts: Counter) -> int:
        return sum(math.comb(n, 2) for n in counts.values())

    total = pairs(Counter(f.model for c in clusters for f in c.findings))
    merged = sum(pairs(Counter(f.model for f in c.findings)) for c in clusters)
    return total, merged


# A council seats four reviewers, so four is "all of them"; the rare fifth seat joins that row.
AGREEMENT_BUCKETS = ("1", "2", "3", "4+")


def _agreement_bucket(agreement: int) -> str:
    return "4+" if agreement >= 4 else str(agreement)


def _load(recordings: pathlib.Path | None = None, labels: dict | None = None) -> dict[str, dict]:
    labels = LABELS if labels is None else labels
    runs = {}
    for path in sorted((recordings or RECORDINGS).glob("*.json")):
        name = path.stem
        if name not in labels:
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        runs[name] = payload
    return runs


def score(recordings: pathlib.Path | None = None, labels: dict | None = None) -> dict:
    labels = LABELS if labels is None else labels
    runs = _load(recordings, labels)
    if not runs:
        raise SystemExit(f"no recordings in {recordings or RECORDINGS} — run evals/record.py first")
    # A partial corpus produces a real-looking report over whatever happened to be recorded,
    # so say what is missing rather than quietly scoring a subset.
    unrecorded = sorted(set(labels) - set(runs))

    total_defects = 0
    council_found = 0
    per_model_hits: dict[str, int] = defaultdict(int)
    per_model_findings: dict[str, int] = defaultdict(int)
    per_model_matched: dict[str, int] = defaultdict(int)
    per_model_seen_defects: dict[str, int] = defaultdict(int)
    agreement: dict[tuple[str, str], int] = defaultdict(int)
    co_found = 0          # labelled defects that more than one model reported
    co_clustered = 0      # ... and that consensus grouping put into a single cluster (strict)
    co_quorum = 0         # ... and that reached quorum in at least one cluster
    own_pairs = 0         # pairs of findings one model wrote separately
    own_pairs_merged = 0  # ... that grouping put into one entry anyway
    # Per agreement bucket: entries on labelled snippets, and how many hold a finding that
    # matches one of the snippet's labelled defects.
    calibration = {bucket: [0, 0] for bucket in AGREEMENT_BUCKETS}
    clean_findings: dict[str, int] = defaultdict(int)
    clean_snippets = 0
    truncations = 0
    snippet_rows = []

    for name, payload in sorted(runs.items()):
        defects = labels[name]
        seats = [_Seat(r) for r in payload.get("responses", []) if not r.get("duplicate_of")]
        truncations += sum(1 for s in seats if s.truncated)
        # Accumulated, not assigned: two seats can carry the same serving model in an old
        # recording made before duplicate seats were rotated out, and a dict comprehension
        # would silently drop one model's findings and quietly understate its recall.
        findings_by_model: dict[str, list] = defaultdict(list)
        for s in seats:
            findings_by_model[s.model].extend(parse_findings(s.content, s.model))
        for model, fs in findings_by_model.items():
            per_model_findings[model] += len(fs)
        # Clustering depends only on the snippet's findings, so it is computed once per
        # snippet rather than once per defect inside the loop below.
        clusters = build_consensus(seats)
        pairs, merged = _same_model_pairs(clusters)
        own_pairs += pairs
        own_pairs_merged += merged

        if not defects:
            clean_snippets += 1
            for model, fs in findings_by_model.items():
                clean_findings[model] += len(fs)
            snippet_rows.append((name, 0, 0, len(seats), sum(len(f) for f in findings_by_model.values())))
            continue

        total_defects += len(defects)
        found_here = 0
        for c in clusters:
            row = calibration[_agreement_bucket(c.agreement)]
            row[0] += 1
            row[1] += any(_matches(f, d) for f in c.findings for d in defects)
        for defect in defects:
            finders = [m for m, fs in findings_by_model.items() if any(_matches(f, defect) for f in fs)]
            if finders:
                council_found += 1
                found_here += 1
            for m in finders:
                per_model_hits[m] += 1
            for a in finders:
                for b in finders:
                    if a < b:
                        agreement[(a, b)] += 1
            if len(finders) > 1:
                # Does the shipped grouping actually put these together? Agreement the reader
                # never sees is agreement the tool failed to deliver.
                co_found += 1
                holding = _clusters_containing_defect(clusters, defect)
                if len(holding) == 1 and set(holding[0].models) >= set(finders):
                    co_clustered += 1
                if any(set(c.models) >= set(finders) for c in holding):
                    co_quorum += 1
        for model in findings_by_model:
            per_model_seen_defects[model] += len(defects)
            per_model_matched[model] += sum(
                1 for f in findings_by_model[model] if any(_matches(f, d) for d in defects)
            )
        snippet_rows.append((name, len(defects), found_here, len(seats),
                             sum(len(f) for f in findings_by_model.values())))

    per_model = {}
    for model in sorted(set(per_model_findings) | set(per_model_hits)):
        seen = per_model_seen_defects.get(model, 0)
        raised = per_model_findings.get(model, 0)
        per_model[model] = {
            "defects_seen": seen,
            "defects_found": per_model_hits.get(model, 0),
            "recall": per_model_hits.get(model, 0) / seen if seen else 0.0,
            "findings_raised": raised,
            "findings_matching_a_label": per_model_matched.get(model, 0),
            # Named for what it measures. An unmatched finding is not proven wrong: the
            # snippets contain defects nobody labelled, so this is a floor on precision.
            "label_precision": per_model_matched.get(model, 0) / raised if raised else 0.0,
            "findings_on_clean_snippets": clean_findings.get(model, 0),
        }

    # A model that happened to be seated on three easy snippets can score 100% and make the
    # council look worthless. The baseline only counts models that reviewed a real share of
    # the corpus; the pool rotates, so no model sees all of it.
    eligible = {
        name: m for name, m in per_model.items()
        if m["defects_seen"] >= MIN_EXPOSURE_FOR_BASELINE * total_defects
    }
    best_single = max((m["recall"] for m in eligible.values()), default=0.0)
    best_single_model = max(eligible, key=lambda k: eligible[k]["recall"], default=None)
    return {
        "snippets": len(runs),
        "snippets_without_a_recording": unrecorded,
        "labelled_defects": total_defects,
        "council_recall": council_found / total_defects if total_defects else 0.0,
        "best_single_model_recall": best_single,
        "best_single_model": best_single_model,
        "models_eligible_for_baseline": len(eligible),
        "lift": (council_found / total_defects - best_single) if total_defects else 0.0,
        "clean_snippets": clean_snippets,
        # Not "false positives" any more. The controls are free of labelled *defects*, and
        # the reviewers are now asked for structure and simplicity too, so a finding here can
        # be legitimate. It still measures what it was built to measure — a model rewarded for
        # reporting everything shows up here first — but the number has to be read, not just
        # counted, and it is expected to be non-zero.
        "findings_on_clean_controls": sum(clean_findings.values()),
        "truncated_reviews": truncations,
        "agreed_defects": co_found,
        "agreed_defects_in_one_cluster": co_clustered,
        "agreed_defects_reaching_quorum": co_quorum,
        "fragmented_with_quorum": co_quorum - co_clustered,
        "clustering_accuracy": (co_clustered / co_found) if co_found else 0.0,
        "quorum_clustering_accuracy": (co_quorum / co_found) if co_found else 0.0,
        "same_model_pairs": own_pairs,
        "same_model_pairs_merged": own_pairs_merged,
        "same_model_merge_rate": (own_pairs_merged / own_pairs) if own_pairs else 0.0,
        # What an `agreement` value is worth. An entry that matches no label is not proven
        # wrong: the snippets hold defects nobody labelled, so each share is a floor.
        "entries_matching_a_label": {
            bucket: {"entries": entries, "matching": matching}
            for bucket, (entries, matching) in calibration.items()
        },
        "per_model": per_model,
        "agreement": {f"{a} + {b}": n for (a, b), n in sorted(agreement.items(), key=lambda kv: -kv[1])},
        "per_snippet": [
            {"snippet": n, "defects": d, "found": f, "seats": s, "findings": r}
            for n, d, f, s, r in snippet_rows
        ],
    }


def _calibration_lines(tables: list[dict]) -> list[str]:
    """Each agreement bucket's share of entries matching a labelled defect, pooled over `tables`."""
    lines = []
    for bucket in AGREEMENT_BUCKETS:
        entries = sum(t[bucket]["entries"] for t in tables)
        matching = sum(t[bucket]["matching"] for t in tables)
        share = f"{matching / entries:6.1%}" if entries else "     -"
        lines.append(f"  agreement {bucket:<3} {share}  ({matching} of {entries} entries)")
    return lines


def _report(data: dict) -> str:
    lines = [
        "# 4take eval — council vs. best single model",
        "",
        f"Corpus: {data['snippets']} snippets, {data['labelled_defects']} labelled defects, "
        f"{data['clean_snippets']} clean controls.",
        "",
    ]
    if data["snippets_without_a_recording"]:
        lines += [
            f"NOT SCORED — no recording for: "
            f"{', '.join(data['snippets_without_a_recording'])}",
            "",
        ]
    lines += [
        f"  council recall            {data['council_recall']:6.1%}",
        f"  best single-model recall  {data['best_single_model_recall']:6.1%}"
        f"   ({data['best_single_model'] or 'no model saw enough of the corpus'})",
        f"  lift from the council     {data['lift']:+6.1%}",
        f"  findings on clean code    {data['findings_on_clean_controls']}"
        f"   (read them: structure findings here can be real)",
        f"  truncated reviews         {data['truncated_reviews']}",
        f"  quorum consensus reached  {data['quorum_clustering_accuracy']:6.1%} "
        f"({data['agreed_defects_reaching_quorum']}/{data['agreed_defects']})",
        f"  agreed defects grouped    {data['clustering_accuracy']:6.1%} "
        f"({data['agreed_defects_in_one_cluster']}/{data['agreed_defects']}) "
        f"[{data['fragmented_with_quorum']} multi-cluster splits]",
        f"  same-model pairs merged   {data['same_model_merge_rate']:6.1%} "
        f"({data['same_model_pairs_merged']}/{data['same_model_pairs']} share an entry)",
        "",
        "## Per model",
        "",
        f"{'model':52} {'seen':>5} {'recall':>7} {'found':>6} {'raised':>7} "
        f"{'label prec':>11} {'on clean':>9}",
    ]
    for model, m in sorted(data["per_model"].items(), key=lambda kv: -kv[1]["recall"]):
        lines.append(
            f"{model:52} {m['defects_seen']:5} {m['recall']:6.1%} {m['defects_found']:6} "
            f"{m['findings_raised']:7} {m['label_precision']:10.1%} "
            f"{m['findings_on_clean_snippets']:9}"
        )
    lines += [
        "",
        f"Only models seated for at least {MIN_EXPOSURE_FOR_BASELINE:.0%} of the labelled "
        f"defects ({data['models_eligible_for_baseline']} of {len(data['per_model'])}) count "
        f"towards the single-model baseline.",
    ]
    lines += ["", "## Entries that match a labelled defect, by agreement", ""]
    lines += _calibration_lines([data["entries_matching_a_label"]])
    lines += ["", "Labelled snippets only. An entry that matches no label is not proven wrong: the",
              "snippets hold defects nobody labelled, so each share is a floor."]
    lines += ["", "## Pairs that agreed on a labelled defect", ""]
    for pair, n in list(data["agreement"].items())[:12]:
        lines.append(f"  {n:3}  {pair}")
    return "\n".join(lines)


# --------------------------------------------------------------------------------------
# The cross-file corpus: does reference context let the council see between files?
# --------------------------------------------------------------------------------------

CORPUS_CTX = ROOT / "corpus_ctx"
RECORDINGS_CTX = ROOT / "recordings_ctx"
# `_about` documents the labelling rule inside the file; it is not a case.
LABELS_CTX = {
    name: defects
    for name, defects in json.loads((ROOT / "labels_ctx.json").read_text(encoding="utf-8")).items()
    if not name.startswith("_")
}
CTX_MODES = ("without", "with")
# Context chosen by --auto-context. Scored beside the other two wherever it was recorded.
AUTO_MODE = "auto"

_DEFINITION_RE = re.compile(r"\b(?:def|class|function|const|let|var)\s+([A-Za-z_$][A-Za-z0-9_$]*)")


def _context_only_names(case: str, corpus: pathlib.Path = CORPUS_CTX) -> set[str]:
    """Names defined in a case's context files that the code under review never mentions."""
    case_dir = corpus / case
    review = next(case_dir.glob("review.*")).read_text(encoding="utf-8")
    names: set[str] = set()
    for path in (case_dir / "context").rglob("*"):
        if path.is_file():
            names.update(_DEFINITION_RE.findall(path.read_text(encoding="utf-8")))
    return {name.lower() for name in names if name not in review}


def score_context(recordings: pathlib.Path, labels: dict | None = None,
                  corpus: pathlib.Path = CORPUS_CTX) -> dict:
    """
    One mode of the cross-file corpus: council recall by kind of defect, noise on the clean
    controls, and findings the reviewers located in the context.

    A finding located in the context breaks the prompt's rule, but it is not always noise: one
    that names the caller a change breaks, and matches a label, is the right finding filed under
    the wrong symbol. Only the ones that match no label are counted as noise.

    Findings are matched on what they claim is wrong, not on their suggested fix. Without the
    context, the fix is where a reviewer guesses at code it has not seen — "wrap it in
    self._lock", "keep the old key format as a fallback" — and the first recording credited
    exactly those guesses.
    """
    labels = LABELS_CTX if labels is None else labels
    runs = _load(recordings, labels)
    seconds = sorted(p["summary"]["total_latency_ms"] / 1000 for p in runs.values()
                     if isinstance(p.get("summary"), dict) and "total_latency_ms" in p["summary"])
    found: dict[str, int] = defaultdict(int)
    total: dict[str, int] = defaultdict(int)
    findings = on_clean = in_context = context_noise = flagged = 0

    def reports(finding, defect: dict) -> bool:
        return _matches(finding, defect, include_fix=False)

    for name, payload in sorted(runs.items()):
        seats = [_Seat(r) for r in payload.get("responses", []) if not r.get("duplicate_of")]
        raised = [f for s in seats for f in parse_findings(s.content, s.model)]
        defects = labels[name]
        findings += len(raised)
        context_only = _context_only_names(name, corpus)
        for finding in raised:
            if finding.symbol and finding.symbol.lower() in context_only:
                in_context += 1
                context_noise += not any(reports(finding, d) for d in defects)
        if not defects:
            on_clean += len(raised)
            continue
        for defect in defects:
            kind = defect.get("kind", "local")
            total[kind] += 1
            found[kind] += any(reports(f, defect) for f in raised)
            if kind == "cross_file":
                # Warned about in any words, the affected code named or not: what a review of the
                # diff alone can reach by inference.
                loose = {**defect, "keywords": [*defect["keywords"], *defect.get("suspect", [])]}
                flagged += any(reports(f, loose) for f in raised)
    return {
        "cases": len(runs),
        "cases_without_a_recording": sorted(set(labels) - set(runs)),
        "found": dict(found),
        "cross_file_flagged": flagged,
        "defects": dict(total),
        "recall": {kind: found[kind] / total[kind] for kind in total},
        "findings": findings,
        "findings_on_clean_controls": on_clean,
        "findings_located_in_context": in_context,
        "context_noise": context_noise,
        # What a reader waits: context makes every request longer.
        "median_council_s": statistics.median(seconds) if seconds else None,
    }


def compare_context(base: pathlib.Path = RECORDINGS_CTX, labels: dict | None = None,
                    corpus: pathlib.Path = CORPUS_CTX) -> dict:
    """Both modes of the cross-file corpus side by side, and auto context where it was recorded."""
    modes = [*CTX_MODES, *([AUTO_MODE] if (base / AUTO_MODE).is_dir() else [])]
    data = {mode: score_context(base / mode, labels, corpus) for mode in modes}
    if not any(data[mode]["cases"] for mode in modes):
        raise SystemExit(f"no recordings in {base} — run evals/record.py --corpus ctx first")
    return data


def _context_report(data: dict) -> str:
    def fraction(mode: str, kind: str) -> str:
        found, total = data[mode]["found"].get(kind, 0), data[mode]["defects"].get(kind, 0)
        return f"{found}/{total}" if total else "-"

    def flagged(mode: str) -> str:
        total = data[mode]["defects"].get("cross_file", 0)
        return f"{data[mode]['cross_file_flagged']}/{total}" if total else "-"

    rows = [
        ("cross-file defects flagged", flagged),
        ("... naming the affected code", lambda m: fraction(m, "cross_file")),
        ("local defects found", lambda m: fraction(m, "local")),
        ("findings on the clean controls", lambda m: str(data[m]["findings_on_clean_controls"])),
        ("findings located in the context", lambda m: str(data[m]["findings_located_in_context"])),
        ("... that match no label (noise)", lambda m: str(data[m]["context_noise"])),
        ("findings in total", lambda m: str(data[m]["findings"])),
        ("median council, seconds", lambda m: _seconds(data[m]["median_council_s"])),
    ]
    modes = list(data)
    width = max(len(label) for label, _ in rows) + 2
    lines = [
        "# 4take eval — the cross-file corpus, without and with reference context",
        "",
        " " * width + "".join(f"{mode:>10}" for mode in modes),
    ]
    lines += [f"{label:<{width}}" + "".join(f"{cell(m):>10}" for m in modes) for label, cell in rows]
    missing = {m: data[m]["cases_without_a_recording"] for m in modes if data[m]["cases_without_a_recording"]}
    for mode, names in missing.items():
        lines += ["", f"NOT SCORED ({mode}) — no recording for: {', '.join(names)}"]
    lines += [
        "",
        "A cross-file defect is flagged by a finding that warns about it in any words (\"callers may",
        "break\"), and named only by one that names what the context holds — the caller a change",
        "breaks, the helper's real behaviour — which the diff alone cannot supply. Without context,",
        "naming it takes a lucky guess: the gap between the two rows is what context adds beyond",
        "inference, and the flagged row is the fair comparison of how many breaks were caught.",
    ]
    return "\n".join(lines)


def _seconds(value: float | None) -> str:
    return "-" if value is None else f"{value:.0f}"


# The cross-file rows compared across recordings: label, how to read it from one mode, and the
# kind of defect it is a share of (None for a plain count).
_CTX_SPREAD_ROWS = (
    ("cross-file defects flagged", lambda data, mode: data[mode]["cross_file_flagged"], "cross_file"),
    ("... naming the affected code", lambda data, mode: data[mode]["found"].get("cross_file", 0), "cross_file"),
    ("local defects found", lambda data, mode: data[mode]["found"].get("local", 0), "local"),
    ("findings on the clean controls", lambda data, mode: data[mode]["findings_on_clean_controls"], None),
    ("findings located in the context", lambda data, mode: data[mode]["findings_located_in_context"], None),
    ("median council, seconds", lambda data, mode: round(data[mode]["median_council_s"] or 0), None),
)


def _context_spread_report(directories: list[pathlib.Path], results: list[dict]) -> str:
    """The cross-file corpus over several recordings, each mode a row: one draw is not a number."""
    width = max(len(label) for label, _, _ in _CTX_SPREAD_ROWS) + 2
    lines = [
        f"# 4take eval — the cross-file corpus recorded {len(results)} times",
        "",
        " " * width + f"{'':<9}" + "".join(f"{d.name:>16}" for d in directories) + f"{'range':>16}",
    ]
    # A mode is compared across recordings only where every one of them recorded it.
    modes = [m for m in (*CTX_MODES, AUTO_MODE) if all(m in data for data in results)]
    for label, value, kind in _CTX_SPREAD_ROWS:
        for mode in modes:
            values = [value(data, mode) for data in results]
            total = results[0][mode]["defects"].get(kind, 0) if kind else 0
            shown = [f"{v}/{total}" if total else str(v) for v in values]
            low, high = shown[values.index(min(values))], shown[values.index(max(values))]
            lines.append(f"{label:<{width}}{mode:<9}" + "".join(f"{s:>16}" for s in shown) + f"{low + ' .. ' + high:>16}")
    return "\n".join(lines)


# What the spread report compares across runs, with how each value is printed.
_SPREAD_ROWS = (
    ("council recall", "council_recall", "{:.1%}"),
    ("best single-model recall", "best_single_model_recall", "{:.1%}"),
    ("lift from the council", "lift", "{:+.1%}"),
    ("findings on clean code", "findings_on_clean_controls", "{}"),
    ("agreed defects grouped", "clustering_accuracy", "{:.1%}"),
    ("quorum consensus reached", "quorum_clustering_accuracy", "{:.1%}"),
    ("same-model pairs merged", "same_model_merge_rate", "{:.1%}"),
)


def _spread_report(directories: list[pathlib.Path], results: list[dict]) -> str:
    """
    One recording per snippet is one draw from stochastic free models: re-recording the same
    corpus with the same prompt moved best single-model recall by several points and the
    grouping measure by far more. A number published without its range overstates what it knows.
    """
    width = max(len(label) for label, _, _ in _SPREAD_ROWS) + 2
    lines = [
        f"# 4take eval — the same corpus recorded {len(results)} times",
        "",
        " " * width + "".join(f"{d.name:>12}" for d in directories) + f"{'range':>18}",
    ]
    for label, key, fmt in _SPREAD_ROWS:
        values = [r[key] for r in results]
        cells = "".join(f"{fmt.format(v):>12}" for v in values)
        low, high = fmt.format(min(values)), fmt.format(max(values))
        lines.append(f"{label:<{width}}{cells}{low + ' .. ' + high:>18}")
    # Pooled rather than per recording: one recording puts a dozen or so entries in the middle
    # buckets, too few for a share to mean anything on its own.
    lines += ["", f"Entries that match a labelled defect, by agreement, over all {len(results)} recordings:"]
    lines += _calibration_lines([r["entries_matching_a_label"] for r in results])
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true", help="emit the summary as JSON")
    ap.add_argument("--runs", nargs="+", type=pathlib.Path, metavar="DIR",
                    help="score several recordings of the corpus and report the spread")
    ap.add_argument("--ctx", nargs="*", type=pathlib.Path, metavar="DIR",
                    help="score the cross-file corpus without and with context (default DIR: "
                         "evals/recordings_ctx, holding without/ and with/); several DIRs report the spread")
    args = ap.parse_args()
    if args.ctx is not None:
        directories = args.ctx or [RECORDINGS_CTX]
        results = [compare_context(directory) for directory in directories]
        if args.json:
            print(json.dumps(results[0] if len(results) == 1 else results, indent=2))
        elif len(results) == 1:
            print(_context_report(results[0]))
        else:
            print(_context_spread_report(directories, results))
        return 0
    if args.runs:
        results = [score(directory) for directory in args.runs]
        print(json.dumps(results, indent=2) if args.json else _spread_report(args.runs, results))
        return 0
    data = score()
    print(json.dumps(data, indent=2) if args.json else _report(data))
    return 0


if __name__ == "__main__":
    sys.exit(main())
