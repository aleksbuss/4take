"""
Record real council runs over the corpus so scoring can stay offline.

Recording is the only step that touches the network, and it is deliberately not part of the
test suite: CI scores the checked-in fixtures instead, which keeps it hermetic, free and
deterministic. Re-record only when the prompt or the model pool changes.

    .venv/bin/python evals/record.py            # record what is missing
    .venv/bin/python evals/record.py --force    # re-record everything
    .venv/bin/python evals/record.py --snippet auth_jwt_decode --force  # re-record one snippet
    .venv/bin/python evals/record.py --force --out evals/runs/2          # a repeat run, for the spread
    .venv/bin/python evals/record.py --corpus ctx                         # cross-file cases, with and without context
    .venv/bin/python evals/record.py --corpus ctx --out evals/runs_ctx/2 --stop-after 2  # stop if the provider fails
    .venv/bin/python evals/record.py --corpus ctx --mode auto --stop-after 2  # context chosen by --auto-context

A stopped batch is resumed by running the same command again: what was recorded is skipped.
"""

import argparse
import atexit
import json
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass

ROOT = pathlib.Path(__file__).resolve().parent
CORPUS = ROOT / "corpus"
RECORDINGS = ROOT / "recordings"
BACKUP_DIR = ROOT / "recordings_backup"
FOURTAKE = ROOT.parent / ".venv" / "bin" / "4take"

# Cross-file cases: a directory each, holding `review.*` (piped in) and `context/` (sent as
# reference context). Recorded twice, with and without the context, into sibling directories.
CORPUS_CTX = ROOT / "corpus_ctx"
RECORDINGS_CTX = ROOT / "recordings_ctx"
CTX_MODES = ("without", "with")
# The context chosen by `--auto-context` instead of by hand. Recorded on its own, not paired.
AUTO_MODE = "auto"

SNIPPET_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")

# A batch stopped because the provider is failing, as opposed to finishing with failures.
EXIT_STOPPED = 3
# The free tier's daily cap: once hit, nothing else will be answered until it resets.
_QUOTA_EXHAUSTED = re.compile(r"per-day", re.IGNORECASE)
# Errors that come from the provider refusing or failing, not from a model being slow. A 5xx is
# reported as "Provider error 503 after N retries", so it needs no pattern of its own.
_PROVIDER_ERROR = re.compile(r"\b429\b|Provider error|Upstream provider error|Rate limited", re.IGNORECASE)


@dataclass
class Outcome:
    """What recording one council did, and whether it says the provider has stopped serving."""

    line: str
    written: bool = False
    # Failed outright, or recorded short of a full council because the provider was failing.
    impaired: bool = False
    quota_exhausted: bool = False


def _judge(payload: dict, stderr: str) -> tuple[bool, bool]:
    """(provider errors in this council, daily quota exhausted) from its JSON errors and stderr."""
    texts = [str(e.get("error") or "") for e in payload.get("errors", [])] + [stderr]
    provider = any(_PROVIDER_ERROR.search(t) for t in texts)
    return provider, any(_QUOTA_EXHAUSTED.search(t) for t in texts)


def backup_recordings(backup_dir: pathlib.Path = BACKUP_DIR) -> int:
    """
    Snapshot the recordings as they stand, before a run can overwrite them.

    A fixture already in the backup is refreshed when it has changed. Copying only missing ones
    kept the first version forever, so after a second re-record the backup was not the recording
    just replaced but the oldest one.
    """
    if not RECORDINGS.exists():
        return 0
    backup_dir.mkdir(exist_ok=True)
    count = 0
    for item in RECORDINGS.glob("*.json"):
        dest = backup_dir / item.name
        if not dest.exists() or dest.read_bytes() != item.read_bytes():
            shutil.copy2(item, dest)
            count += 1
    return count


def ctx_invocation(
    case: pathlib.Path, mode: str, shared_root: pathlib.Path | None = None
) -> tuple[pathlib.Path, list[str], pathlib.Path]:
    """
    The input, the extra arguments and the working directory for one cross-file case.

    It runs from inside `context/`, which is then the project root, so the files are named to the
    models by their paths in the case (`app/mailer.py`) rather than by where the corpus keeps them.
    In auto mode it runs from `shared_root`, where auto context has every case's files to choose
    from, as it would in a real repository.
    """
    (review,) = case.glob("review.*")
    if mode == "without":
        return review, [], case
    if mode == AUTO_MODE:
        if shared_root is None:
            raise ValueError("auto mode runs from the shared repository of every case")
        return review, ["--auto-context"], shared_root
    context_dir = case / "context"
    args: list[str] = []
    for f in sorted(p for p in context_dir.rglob("*") if p.is_file()):
        args += ["--context-file", f.relative_to(context_dir).as_posix()]
    return review, args, context_dir


def shared_repository(corpus: pathlib.Path = CORPUS_CTX) -> pathlib.Path:
    """
    Every case's context in one temporary git repository, for auto context to search.

    The other cases' files are the distractors: a selection that pulled one of them in would send
    code unrelated to the change, as it would in a real repository. Removed when the run ends.
    """
    root = pathlib.Path(tempfile.mkdtemp(prefix="4take-ctx-auto-"))
    atexit.register(shutil.rmtree, root, True)
    for case in sorted(p for p in corpus.iterdir() if p.is_dir()):
        shutil.copytree(case / "context", root, dirs_exist_ok=True)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    return root


def record_one(
    path: pathlib.Path,
    out: pathlib.Path,
    timeout: int,
    extra_args: list[str] | None = None,
    cwd: pathlib.Path | None = None,
) -> Outcome:
    started = time.monotonic()
    with path.open("rb") as snippet:
        proc = subprocess.run(
            [
                str(FOURTAKE),
                "--format", "json",
                "--accept-risk",
                "--quiet",
                "--no-cache",       # a fixture must record a real run, not a replay
                # Recording the corpus is a maintainer's batch job, not the user's usage.
                # Its ~110 calls would dominate the 2000-event history and reshape which
                # models get seated for everything else they do.
                "--no-telemetry",
                "--timeout", str(timeout),
                *(extra_args or []),
            ],
            stdin=snippet,
            capture_output=True,
            timeout=timeout + 60,
            cwd=cwd,
        )
    elapsed = time.monotonic() - started
    stdout = proc.stdout.decode("utf-8", "replace")
    stderr = proc.stderr.decode("utf-8", "replace")
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        quota = bool(_QUOTA_EXHAUSTED.search(stderr))
        return Outcome(f"{path.name}: FAILED - stdout was not JSON ({stdout[:120]!r})",
                       impaired=True, quota_exhausted=quota)
    provider_failing, quota = _judge(payload, stderr)

    if proc.returncode not in (0, 2):
        return Outcome(f"{path.name}: FAILED - council returned exit code {proc.returncode} ({stderr[:120]!r})",
                       impaired=True, quota_exhausted=quota)

    responses = payload.get("responses", [])
    if not responses:
        return Outcome(f"{path.name}: FAILED - zero model responses received",
                       impaired=True, quota_exhausted=quota)

    payload["_eval"] = {"snippet": path.name, "exit_code": proc.returncode, "wall_s": round(elapsed, 1)}
    out.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n"
    )
    seats = payload.get("summary", {}).get("distinct_models", 0)
    # Exit 2 is a council short of its seats. On its own that is ordinary for free models; while
    # the provider is refusing requests it is the provider failing, and not a fair draw.
    return Outcome(f"{path.name}: {seats} seats, {elapsed:.0f}s, exit {proc.returncode}", written=True,
                   impaired=proc.returncode == 2 and provider_failing, quota_exhausted=quota)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--force", action="store_true", help="re-record snippets that already have fixtures")
    ap.add_argument("--snippet", action="append", dest="snippets",
                    help="record specific snippet(s) by name, e.g. --snippet auth_jwt_decode")
    ap.add_argument("--timeout", type=int, default=300)
    # One recording per snippet is one draw from stochastic free models; repeat runs written
    # beside the checked-in set are what turn a number into a range.
    ap.add_argument("--out", type=pathlib.Path, default=None,
                    help="directory to write fixtures into (default: evals/recordings, or "
                         "evals/recordings_ctx for --corpus ctx)")
    ap.add_argument("--corpus", choices=("main", "ctx"), default="main",
                    help="ctx records the cross-file cases in evals/corpus_ctx")
    ap.add_argument("--mode", choices=(*CTX_MODES, AUTO_MODE, "both"), default="both",
                    help="for --corpus ctx: record with the context, without it, or both (default); "
                         "auto records the context --auto-context chooses")
    ap.add_argument("--delay", type=float, default=2.0, help="delay in seconds between runs (default: 2.0s)")
    ap.add_argument("--stop-after", type=int, default=0, metavar="N",
                    help="stop once N councils in a row fail at the provider (default 0: never). A spent "
                         "daily free-model quota stops the batch at once either way")
    ap.add_argument("--backup", action="store_true", default=True, help="backup existing recordings first")
    ap.add_argument("--no-backup", dest="backup", action="store_false", help="do not backup existing recordings")
    args = ap.parse_args()

    if args.snippets:
        # Validate names against path traversal
        for name in args.snippets:
            if not SNIPPET_NAME_RE.match(name):
                print(f"Error: Invalid snippet name {name!r}. Must match {SNIPPET_NAME_RE.pattern}", file=sys.stderr)
                return 1

    jobs = _ctx_jobs(args) if args.corpus == "ctx" else _main_jobs(args)
    if not jobs:
        print(f"Error: No matching snippets found for {args.snippets}", file=sys.stderr)
        return 1

    failures = 0
    streak: list[pathlib.Path] = []  # the current run of councils the provider failed
    written: list[pathlib.Path] = []  # fixtures this invocation wrote
    for i, (label, path, out, extra_args, cwd) in enumerate(jobs, 1):
        if out.exists() and not args.force:
            print(f"[{i}/{len(jobs)}] {label}: cached", flush=True)
            continue
        out.parent.mkdir(parents=True, exist_ok=True)
        try:
            outcome = record_one(path, out, args.timeout, extra_args, cwd)
        except subprocess.TimeoutExpired:
            outcome = Outcome(f"{path.name}: TIMED OUT", impaired=True)
        failures += not outcome.written
        print(f"[{i}/{len(jobs)}] {label}: {outcome.line.split(': ', 1)[-1]}", flush=True)
        if outcome.written:
            written.append(out)
        streak = [*streak, out] if outcome.impaired else []
        if outcome.quota_exhausted or (args.stop_after and len(streak) >= args.stop_after):
            pairs = args.corpus == "ctx" and args.mode == "both"
            _stop(streak, written, outcome.quota_exhausted, pairs)
            return EXIT_STOPPED
        if i < len(jobs) and args.delay > 0:
            time.sleep(args.delay)
    # A batch in which snippets failed must not look like a clean one to whatever ran it.
    return 1 if failures else 0


def _stop(streak: list[pathlib.Path], written: list[pathlib.Path], quota_exhausted: bool,
          pairs: bool) -> None:
    """
    Leave a stopped batch resumable and fair: remove the fixtures the failing provider degraded,
    and, when both modes are being recorded, any case recorded in one of them only, so the next
    run records each case whole, both modes from one pool.
    """
    doomed = {p for p in streak if p.exists()}
    for p in written if pairs else []:
        if p.parent.name in CTX_MODES:
            other = next(m for m in CTX_MODES if m != p.parent.name)
            sibling = p.parent.parent / other / p.name
            if not sibling.exists() or sibling in doomed:
                doomed.add(p)
    for p in sorted(doomed):
        p.unlink(missing_ok=True)
    why = ("the daily free-model quota is spent" if quota_exhausted
           else f"{len(streak)} councils in a row failed at the provider")
    print(f"STOPPED: {why}. Nothing more was sent, and {len(doomed)} incomplete fixture(s) were removed. "
          f"Run the same command again once the quota resets: what was recorded is skipped.", flush=True)


# (label, input piped in, fixture written, extra CLI arguments, working directory)
Job = tuple[str, pathlib.Path, pathlib.Path, list[str], pathlib.Path | None]


def _main_jobs(args) -> list[Job]:
    out_dir = args.out or RECORDINGS
    out_dir.mkdir(parents=True, exist_ok=True)
    if args.backup and out_dir == RECORDINGS:
        n_backed = backup_recordings()
        if n_backed > 0:
            print(f"Backed up {n_backed} existing fixture(s) to {BACKUP_DIR.name}/", flush=True)
    snippets = sorted(p for p in CORPUS.iterdir() if p.is_file())
    if args.snippets:
        wanted = set(args.snippets)
        snippets = [p for p in snippets if p.stem in wanted or p.name in wanted]
    return [(p.name, p, out_dir / f"{p.stem}.json", [], None) for p in snippets]


def _ctx_jobs(args) -> list[Job]:
    """
    Each case twice, without its context and then with it, back to back: a free model's
    availability drifts over an hour, and recording one mode entirely before the other would
    compare two different pools rather than two inputs.
    """
    out_dir = args.out or RECORDINGS_CTX
    modes = CTX_MODES if args.mode == "both" else (args.mode,)
    cases = sorted(p for p in CORPUS_CTX.iterdir() if p.is_dir())
    if args.snippets:
        cases = [c for c in cases if c.name in set(args.snippets)]
    shared = shared_repository() if AUTO_MODE in modes and cases else None
    jobs: list[Job] = []
    for case in cases:
        for mode in modes:
            review, extra_args, cwd = ctx_invocation(case, mode, shared)
            jobs.append((f"{case.name} [{mode}]", review, out_dir / mode / f"{case.name}.json", extra_args, cwd))
    return jobs


if __name__ == "__main__":
    sys.exit(main())
