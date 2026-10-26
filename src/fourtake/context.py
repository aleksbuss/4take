"""
Reference context: related code the council reads but does not review.

A diff on its own hides every defect that lives between files — a changed return value that a
caller still dereferences, a new route without the check each of its neighbours makes. The whole
repository is the wrong answer too: it multiplies every request by the number of reviewers and
buries the change in code nobody asked about. The caller — an agent acting as chairman, or a
person at the command line — names the few files that matter, and this module turns them into one
bounded block, refusing what must never leave the machine.

Both front ends go through `assemble`, so the rules cannot drift between the CLI and the MCP
server the way the pre-flight checks once did.
"""

from __future__ import annotations

import dataclasses
import hashlib
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .safety import redact_secrets_with_report
from .tokenizer import estimate_tokens

logger = logging.getLogger("4take")

# A whole file larger than this is rarely the context a reviewer needs; a line range of it is.
MAX_CONTEXT_FILE_BYTES = 256 * 1024
# Even for a line range, a file this large is not source code: refuse it rather than read a log
# or a data dump into memory to cut forty lines out of it.
MAX_SOURCE_FILE_BYTES = 4 * 1024 * 1024
_BINARY_PROBE_BYTES = 8192

INLINE_LABEL = "(inline)"

# Files that are credentials, or keep them in plain text (Terraform state). Redaction still runs
# over everything that is sent; these are the files where nothing would be left to review once it
# had, and where one pattern it misses is a live secret handed to a third party.
_DENIED_FILE = re.compile(
    r"^(?:\.env(?:[._-].*|rc)?|\.netrc|\.npmrc|\.pypirc|\.pgpass|\.git-credentials|\.dockercfg"
    r"|id_(?:rsa|dsa|ecdsa|ed25519)(?:\..*)?"
    r"|.*\.(?:env|pem|key|p12|pfx|kdbx|tfstate|tfstate\.backup))$",
    re.IGNORECASE,
)
# Compared case-folded: on a case-insensitive filesystem `.GIT/config` is the same file.
_DENIED_DIRS = frozenset({".git", ".ssh", ".aws", ".gnupg", ".docker", ".kube"})

_RANGE_SPEC = re.compile(r"^(?P<path>.+?):(?P<start>\d+)-(?P<end>\d+)$")

# The file a unified diff leaves behind: the `+++` line that follows a `---` header. Anchored on
# the pair so an added line that happens to start with "++ " is not taken for a header.
_DIFF_TARGET = re.compile(r"^--- [^\n]*\n\+\+\+ (?:b/)?(?P<path>[^\t\n]+)", re.MULTILINE)

# Appended to the header of a context file the reviewed diff changes. The context rules in the
# prompt quote it, so the two must change together.
AFTER_CHANGE_NOTE = "after the change under review"


class ContextError(ValueError):
    """A context file that cannot, or must not, be sent. The message names the file and why."""


@dataclass(frozen=True)
class ContextPiece:
    """One file, or one line range of a file, as the models will see it."""

    path: str
    content: str
    start: int | None = None
    end: int | None = None
    # A file the reviewed diff changes, read as it stands after the change. Unmarked, the changed
    # code met twice — in the diff and here — was reported as a method defined twice.
    changed: bool = False
    # Why auto context chose this piece; empty for one the caller named. Reported, never sent.
    why: str = dataclasses.field(default="", compare=False)

    @property
    def label(self) -> str:
        return self.path if self.start is None else f"{self.path} (lines {self.start}-{self.end})"

    def render(self) -> str:
        note = f" — {AFTER_CHANGE_NOTE}" if self.changed else ""
        return f"--- {self.label}{note} ---\n{self.content.rstrip()}\n"


@dataclass(frozen=True)
class ReviewContext:
    """The reference block a council is sent, and what was left out of it to fit the budget."""

    pieces: tuple[ContextPiece, ...]
    dropped: tuple[str, ...] = ()
    tokens: int = 0

    def render(self) -> str:
        return "\n".join(piece.render() for piece in self.pieces)

    def block(self) -> str | None:
        """What is sent, or None when every piece was dropped: then nothing about the request changes."""
        return self.render() if self.pieces else None

    @property
    def digest(self) -> str:
        """Identity of what is sent. The same code reviewed against other context is another review."""
        hasher = hashlib.sha256()
        for piece in self.pieces:
            # The rendered header, so a file marked as changed is not served the review of the
            # same file unmarked.
            hasher.update(piece.render().encode("utf-8"))
            hasher.update(b"\x00")
        return hasher.hexdigest()

    def summary(self) -> dict:
        summary = {
            "files": [piece.label for piece in self.pieces],
            "tokens": self.tokens,
            "dropped": list(self.dropped),
        }
        chosen = {piece.label: piece.why for piece in self.pieces if piece.why}
        if chosen:
            summary["auto"] = chosen
        return summary


def parse_spec(spec: str) -> tuple[str, int | None, int | None]:
    """`path`, or `path:START-END` with 1-based inclusive line numbers."""
    text = spec.strip()
    match = _RANGE_SPEC.match(text)
    if not match:
        return text, None, None
    start, end = int(match["start"]), int(match["end"])
    if start < 1 or end < start:
        raise ContextError(f"{spec}: a line range is START-END with 1 <= START <= END")
    return match["path"], start, end


def default_root(environ: dict[str, str] | None = None) -> Path:
    """
    The directory context files must live under: FOURTAKE_CONTEXT_ROOT, else the working directory.

    A working directory of `/` or of the home directory is refused as a root. A desktop app often
    starts MCP servers from `/`, and there "inside the root" would admit every file on the machine —
    including whatever a prompt injection in the reviewed code asks the agent to attach.
    """
    env = os.environ if environ is None else environ
    configured = env.get("FOURTAKE_CONTEXT_ROOT")
    root = (Path(configured).expanduser() if configured else Path.cwd()).resolve()
    if not configured and root in (Path(root.anchor), Path.home().resolve()):
        raise ContextError(
            f"context files are read relative to the working directory, and {root} is too broad to "
            f"serve as a project root. Run from the project directory, or set FOURTAKE_CONTEXT_ROOT."
        )
    return root


def read_context(specs: list[str], root: Path) -> list[ContextPiece]:
    """Read each spec under `root`, refusing anything outside it, credential-shaped, binary or oversized."""
    root = root.resolve()
    pieces: list[ContextPiece] = []
    seen: set[tuple[str, int | None, int | None]] = set()
    for spec in specs:
        raw_path, start, end = parse_spec(spec)
        target = _resolve_inside(raw_path, root, spec)
        shown = target.relative_to(root).as_posix()
        if (shown, start, end) in seen:
            continue
        seen.add((shown, start, end))
        text = _read_text(target, spec, whole=start is None)
        if start is not None and end is not None:
            text, end = _slice_lines(text, start, end, spec)
        pieces.append(ContextPiece(shown, text, start, end))
    return pieces


def is_refused(relative_path: str) -> bool:
    """True for a path under the root that is never sent: credentials, keys, repository internals."""
    parts = PurePosixPath(relative_path).parts
    if not parts:
        return False
    return bool({part.lower() for part in parts} & _DENIED_DIRS) or bool(_DENIED_FILE.match(parts[-1]))


def _resolve_inside(raw_path: str, root: Path, spec: str) -> Path:
    candidate = Path(raw_path).expanduser()
    # resolve() follows every symlink first, so a link inside the root that points out of it is
    # caught by the same containment check as a `../` path.
    target = (candidate if candidate.is_absolute() else root / candidate).resolve()
    if not target.is_relative_to(root):
        raise ContextError(f"{spec}: outside the project root {root}")
    if is_refused(target.relative_to(root).as_posix()):
        raise ContextError(f"{spec}: refused — credentials and repository internals are never sent")
    if not target.is_file():
        raise ContextError(f"{spec}: not a file")
    return target


def _read_text(target: Path, spec: str, whole: bool) -> str:
    size = target.stat().st_size
    limit = MAX_CONTEXT_FILE_BYTES if whole else MAX_SOURCE_FILE_BYTES
    if size > limit:
        hint = "; send a line range instead, path:START-END" if whole else ""
        raise ContextError(f"{spec}: {size // 1024} KB is over the {limit // 1024} KB limit{hint}")
    raw = target.read_bytes()
    if b"\x00" in raw[:_BINARY_PROBE_BYTES]:
        raise ContextError(f"{spec}: binary file")
    # Lenient for the reason read_stdin is: one stray byte should not cost the whole review.
    return raw.decode("utf-8", "replace")


def _slice_lines(text: str, start: int, end: int, spec: str) -> tuple[str, int]:
    # Numbered the way editors, `grep -n` and diffs number them, by "\n" alone. splitlines() also
    # breaks on form feeds and Unicode separators, and shifted every range below one.
    lines = text.split("\n")
    if lines[-1] == "":
        lines.pop()  # the newline that ends the last line does not begin another
    if start > len(lines):
        raise ContextError(f"{spec}: the file has only {len(lines)} lines")
    end = min(end, len(lines))
    excerpt = "\n".join(lines[start - 1 : end])
    if len(excerpt.encode("utf-8")) > MAX_CONTEXT_FILE_BYTES:
        raise ContextError(f"{spec}: the range is over the {MAX_CONTEXT_FILE_BYTES // 1024} KB limit")
    return excerpt, end


def redact(pieces: list[ContextPiece]) -> tuple[list[ContextPiece], dict[str, int]]:
    """The same secret redaction the reviewed code gets, over every piece."""
    report: dict[str, int] = {}
    cleaned: list[ContextPiece] = []
    for piece in pieces:
        text, found = redact_secrets_with_report(piece.content)
        for name, count in found.items():
            report[name] = report.get(name, 0) + count
        cleaned.append(dataclasses.replace(piece, content=text))
    return cleaned, report


def fit_to_budget(pieces: list[ContextPiece], budget_tokens: int) -> ReviewContext:
    """
    Keep pieces in the order given — the caller's order of relevance — while they fit.

    A piece is kept or dropped whole, never cut: a truncated file reads as a complete one, and the
    reviewers then reason about code that is not there. A piece that does not fit is skipped, and a
    later, smaller one may still be kept.
    """
    kept: list[ContextPiece] = []
    dropped: list[str] = []
    used = 0
    for piece in pieces:
        cost = estimate_tokens(piece.render())
        if used + cost <= budget_tokens:
            kept.append(piece)
            used += cost
        else:
            dropped.append(piece.label)
    return ReviewContext(tuple(kept), tuple(dropped), used)


def changed_paths(reviewed: str) -> frozenset[str]:
    """The files a unified diff changes, by the paths it gives them; empty for anything else."""
    return frozenset(
        path for path in (m["path"].strip() for m in _DIFF_TARGET.finditer(reviewed)) if path != "/dev/null"
    )


def assemble(
    specs: list[str] | None,
    budget_tokens: int,
    inline: str | None = None,
    root: Path | None = None,
    strip_secrets: bool = True,
    reviewed: str = "",
    auto: bool = False,
) -> tuple[ReviewContext | None, dict[str, int]]:
    """
    Everything either front end does with context: choose, read, mark, redact, fit.

    Returns (None, {}) when no context was given, so a review without it takes exactly the path it
    took before context existed. The root is only resolved when there are paths to read: inline
    context needs no filesystem, and must work from an MCP server started in `/`.

    `reviewed` is the code under review, read only for its diff headers and never sent: pass it
    unredacted. A context file its diff changes is marked, matched by the path the diff gives it;
    `git diff` names files from the top of the repository, which is where both front ends expect
    to be run.

    `auto` adds what `autocontext.select` finds for the diff after everything the caller named,
    so a named file always outranks a guessed one for the budget.
    """
    pieces: list[ContextPiece] = []
    changed = changed_paths(reviewed)
    if specs:
        for piece in read_context(specs, root if root is not None else default_root()):
            pieces.append(dataclasses.replace(piece, changed=True) if piece.path in changed else piece)
    if inline and inline.strip():
        pieces.append(ContextPiece(INLINE_LABEL, inline))
    if auto:
        pieces.extend(_auto_pieces(reviewed, root, pieces, changed))
    if not pieces:
        return None, {}
    report: dict[str, int] = {}
    if strip_secrets:
        pieces, report = redact(pieces)
    return fit_to_budget(pieces, budget_tokens), report


def _auto_pieces(
    reviewed: str, root: Path | None, named: list[ContextPiece], changed: frozenset[str]
) -> list[ContextPiece]:
    """The pieces auto context chooses, read through the same refusals as a named file."""
    from .autocontext import select  # imported here: it imports this module's refusal rules

    try:
        base = (root if root is not None else default_root()).resolve()
    except ContextError as e:
        # A root too broad to search is a reason to send less, not to refuse the review.
        logger.warning(f"Auto context skipped: {e}")
        return []
    whole = {piece.path for piece in named if piece.start is None}
    taken = {(piece.path, piece.start, piece.end) for piece in named}
    pieces: list[ContextPiece] = []
    for pick in select(reviewed, base, skip_paths=whole):
        if (pick.path, pick.start, pick.end) in taken:
            continue
        try:
            [piece] = read_context([pick.spec], base)
        except ContextError:
            continue  # refused or unreadable: auto context leaves it out rather than stop the review
        pieces.append(dataclasses.replace(piece, changed=piece.path in changed, why=pick.why))
    return pieces
