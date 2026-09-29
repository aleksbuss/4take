"""
Choose reference context for a diff: the code the change breaks or leans on, found by name.

Hand-picked context is what lets the council see between files: on the cross-file corpus the
caller a change breaks was named in 12 of 12 reviews with the right files, and in 0-1 without
them. But an agent in the middle of a task rarely stops to name those files, and a person at the
command line never does. This module names them the way a reviewer would go looking:

- who still uses what the change removed: a deleted or renamed definition, an event name or a key
  that no longer appears in the new lines;
- who uses what the change edited or added, and the rest of the class it edited;
- where the things the new lines call are defined, and the module they read settings from.

It never sends the repository. It picks a few excerpts, ranked, and hands them back as ordinary
`path:START-END` specs, so `context.assemble` applies every refusal and the budget unchanged.
Selection is lexical, names and indentation rather than a parser, so it works the same on every
language 4take reviews, and it is wrong the way grep is wrong: a common name matches everything.
Such names are dropped, not followed.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from .context import MAX_SOURCE_FILE_BYTES, is_refused

logger = logging.getLogger("4take")

# How much one review may pull in before the token budget is even consulted. The budget bounds
# tokens; these bound attention, which a small reviewer runs out of long before its window.
MAX_PIECES = 8
MAX_PIECES_PER_FILE = 3
MAX_FILES_PER_NAME = 4
MAX_HITS_PER_FILE = 3
# A line longer than this is data, a recorded review or a minified bundle, not code that calls.
MAX_LINE_CHARS = 400
# A name found in more files than this is a word, not a symbol (`get`, `data`, `handle`), and
# following it fills the context with code that has nothing to do with the change.
MAX_FILES_FOR_A_NAME = 25
# More definitions than this and the name is ambiguous; only the one nearest the change is kept.
MAX_DEFINITIONS = 3
MAX_NAMES = 60
# A file this short is sent whole: its imports and constants are half of what explains it.
SMALL_FILE_LINES = 80
MAX_BLOCK_LINES = 60
WINDOW_LINES = 15
# The head of a long class, which is where its fields, its lock and its constructor are.
CLASS_HEAD_LINES = 40
# Fragments shorter than this, left after cutting the diff's hunks out of a changed file, are
# a closing brace or a blank line, not context.
MIN_FRAGMENT_LINES = 2
GIT_TIMEOUT_SECONDS = 10
MAX_WALK_FILES = 20_000
MAX_SCAN_BYTES = 1024 * 1024
_BINARY_PROBE_BYTES = 8192

_HUNK = re.compile(r"^@@ -\d+(?:,(?P<old>\d+))? \+(?P<start>\d+)(?:,(?P<new>\d+))? @@ ?(?P<func>.*)$")
_IDENT = re.compile(r"[A-Za-z_$][\w$]*")
_STRING = re.compile(r"""(?P<q>["'`])(?P<body>(?:\\.|(?!(?P=q))[^\\\n])*)(?P=q)""")
_COMMENT_LINE = re.compile(r"""^\s*(?:#(?!\s*(?:include|define)\b)|//|/\*|\*(?:\s|/|$)|\"\"\"|'''|--\s)""")
_TRAILING_COMMENT = re.compile(r"(?:^|\s)(?:#|//).*$")
_CALL = re.compile(r"(?<![\w$])([A-Za-z_$][\w$]*)\s*\(")
_MODULE_ATTR = re.compile(r"(?<![\w$.])([A-Za-z_$][\w$]*)\.([A-Za-z_$][\w$]*)")
_ASSIGN_TARGET = re.compile(r"^\s*(?:(?:const|let|var|val|final|auto)\s+)?([A-Za-z_$][\w$]*)\s*(?::[^=]*)?=(?!=)")
_RECEIVER = re.compile(r"[A-Za-z_$][\w$]*$")
# Imports, by what they bind. Only the bound names count: `from app.users import find_user` makes
# `find_user` a name the file can use, not `app` or `users`.
_PY_FROM = re.compile(r"^\s*from\s+\S+\s+import\s+\(?\s*(?P<names>[^)#]+)")
_PY_IMPORT = re.compile(r"^\s*import\s+(?P<names>[\w.]+(?:\s+as\s+\w+)?(?:\s*,\s*[\w.]+(?:\s+as\s+\w+)?)*)\s*$")
_IMPORTS = (
    _PY_FROM,
    _PY_IMPORT,
    re.compile(r"^\s*import\s+(?:type\s+)?(?P<names>.+?)\s+from\s"),  # ES modules
    re.compile(r"^\s*(?:const|let|var)\s+(?P<names>.+?)\s*=\s*require\("),  # CommonJS
    re.compile(r"^\s*import\s+(?:static\s+)?[\w.]*\.(?P<names>\w+)\s*;"),  # Java, Kotlin
    re.compile(r"^\s*use\s+(?P<names>[\w:{}, ]+);"),  # Rust
)
_IMPORT_LINE = re.compile(
    r"^\s*(?:from\s+\S+\s+import\b|import\b|export\s+\{[^}]*\}\s+from\b"
    r"|(?:const|let|var)\s+[^=]+=\s*require\(|#\s*include\b|use\s+[\w:\\]+|require(?:_relative)?[\s(])"
)

# The first group of the first pattern that matches is the name a line defines. Ordered from the
# keyword forms most languages share to the signature shapes only C-family languages have.
_DEFINITIONS = tuple(re.compile(p) for p in (
    r"^\s*(?:export\s+)?(?:default\s+)?(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?"
    r"(?:def|class|function\*?|fn|struct|interface|enum|trait|type|module|object|record)\s+(?:self\.)?([A-Za-z_$][\w$]*)",
    r"^\s*func\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)",
    r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s+)?"
    r"(?:function\b|\([^)]*\)\s*=>|[A-Za-z_$][\w$]*\s*=>)",
    # Java, C#, Kotlin and C members: modifiers, a return type, the name, a parameter list.
    r"^\s*(?:@\w+(?:\([^)]*\))?\s+)*(?:(?:public|private|protected|internal|static|final|abstract"
    r"|override|virtual|synchronized|suspend|open)\s+)+(?:[\w<>\[\],.?]+\s+)?([A-Za-z_]\w*)\s*\(",
    # A method in a JS/TS class body or object literal: `name(args) {`.
    r"^\s*(?:(?:async|static|get|set|public|private|protected)\s+)*"
    r"(?!(?:if|for|while|switch|catch|return|function|with|elif)\b)([A-Za-z_$][\w$]*)\s*\([^)]*\)"
    r"\s*(?::\s*[\w<>\[\],.| ]+)?\s*\{\s*$",
    # A C-family signature without modifiers: `User findUser(String id) {`.
    r"^\s*(?!(?:return|new|throw|else|await|yield|case|if|for|while|switch|catch|import|package)\b)"
    r"[\w<>\[\],.?*&]+\s+\*?([A-Za-z_]\w*)\s*\([^;]*\)\s*(?:throws\s+[\w.,\s]+)?\{\s*$",
))
_CLASS_LIKE = re.compile(r"^\s*(?:[\w@]+\s+)*(?:class|struct|impl|interface|object|trait|enum|record)\b")
# What else defines a name: a module-level constant or assignment in code, a key in config, a
# field in a C-family class body.
_TOP_LEVEL_ASSIGN = re.compile(
    r"^(?:export\s+)?(?:(?:const|let|var|val|final|static|pub)\s+)*(?P<name>[A-Za-z_$][\w$]*)\s*(?::[^=]*)?=(?!=)"
)
_CONFIG_KEY = re.compile(r"""^\s*["']?(?P<name>[\w.$-]+)["']?\s*[:=]""")
_FIELD = re.compile(
    r"^\s*(?:(?:public|private|protected|static|final|readonly|const)\s+)+(?:[\w<>\[\],.?]+\s+)?"
    r"(?P<name>[A-Za-z_$][\w$]*)\s*[=;]"
)
# A block ends at a line back at its own indentation; these two shapes are part of it anyway.
_CLOSER = re.compile(r"^\s*(?:[}\])]|end\b)")
_CONTINUES = re.compile(r"^\s*[)\]][^{}]*[:{]\s*$")  # the close of a multi-line signature

_TEST_PATH = re.compile(
    r"(?:^|/)(?:tests?|spec|specs|__tests__|testing)/|(?:^|/)test_[^/]*$|_test\.\w+$"
    r"|\.(?:test|spec)\.\w+$|Tests?\.(?:java|cs|kt)$"
)
# Languages whose files see the rest of their package without importing it.
_PACKAGE_SCOPED = frozenset({".go", ".java", ".kt", ".scala", ".cs", ".swift"})
_GO_IMPORT = re.compile(r'^\s*(?:import\s+)?(?:[\w.]+\s+)?"[\w./-]+"\s*$')
_CONFIG_SUFFIXES = frozenset({".yaml", ".yml", ".toml", ".json", ".ini", ".cfg", ".conf", ".properties"})
# Text that mentions names without being code: documentation, lock files, build output, saved
# diffs and recorded model output.
_SKIPPED_SUFFIXES = frozenset({
    ".md", ".rst", ".txt", ".adoc", ".lock", ".map", ".svg", ".csv", ".tsv", ".log", ".snap",
    ".sum", ".min.js", ".min.css", ".diff", ".patch", ".rej", ".orig", ".jsonl", ".ipynb",
})
_SKIPPED_NAMES = frozenset({
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "cargo.lock", "go.sum",
    "composer.lock", "gemfile.lock", "changelog", "license",
})
_SKIPPED_DIRS = frozenset({
    "node_modules", "vendor", "third_party", "dist", ".venv", "venv", "__pycache__",
    "site-packages", ".tox", ".next", "coverage",
})

# Keywords and builtins across the languages 4take sees, compared case-folded. They name nothing
# a reviewer could look up.
_STOPWORD_TEXT = """
and as assert async await break case catch class const continue def default del delete do elif
else enum except export extends false final finally for from func function global go if impl
import in instanceof interface is lambda let match mod module mut new nil none not null of or
package pass private protected pub public raise return self static struct super switch this throw
throws true try type typeof undefined use var void where while with yield print len str int
float bool dict list set tuple range object string number boolean any never unknown console log
require exports args kwargs cls isinstance super promise error exception array map math json date
"""
_STOPWORDS = frozenset(_STOPWORD_TEXT.split())


@dataclass(frozen=True)
class Pick:
    """One excerpt chosen for a review, as a spec `context.read_context` accepts, and why."""

    path: str
    start: int | None
    end: int | None
    why: str

    @property
    def spec(self) -> str:
        return self.path if self.start is None else f"{self.path}:{self.start}-{self.end}"


@dataclass
class FileChange:
    """What a diff does to one file, numbered as the file stands after the change."""

    path: str | None  # None when the diff deletes the file
    old_path: str | None = None  # None when the diff creates it
    hunks: list[tuple[int, int]] = field(default_factory=list)
    headers: list[str] = field(default_factory=list)  # the function git names on each hunk
    starts: list[int] = field(default_factory=list)  # where each hunk begins in `body`
    # Every hunk line in order, as (tag, line after the change, text). A removed line carries the
    # number of the line that now stands where it was.
    body: list[tuple[str, int, str]] = field(default_factory=list)
    # The file as the diff leaves it, when that is what is on disk; None otherwise.
    disk: list[str] | None = None

    def lines(self, tag: str) -> list[tuple[int, str]]:
        return [(number, text) for kind, number, text in self.body if kind == tag]

    def touches(self, line: int) -> bool:
        return any(start <= line <= end for start, end in self.hunks)


# --------------------------------------------------------------------------------------
# Reading the diff
# --------------------------------------------------------------------------------------


def parse_diff(text: str) -> list[FileChange]:
    """Every file a unified diff changes. Empty for anything that is not a diff."""
    lines = text.split("\n")
    changes: list[FileChange] = []
    i = 0
    while i < len(lines):
        if lines[i].startswith("--- ") and i + 1 < len(lines) and lines[i + 1].startswith("+++ "):
            changes.append(FileChange(_diff_path(lines[i + 1][4:], "b/"), _diff_path(lines[i][4:], "a/")))
            i += 2
            continue
        match = _HUNK.match(lines[i])
        if match and changes:
            i = _read_hunk(lines, i + 1, match, changes[-1])
            continue
        i += 1
    return [change for change in changes if change.body]


def _diff_path(raw: str, prefix: str) -> str | None:
    path = raw.split("\t", 1)[0].strip().strip('"')
    if path == "/dev/null":
        return None
    return path[len(prefix):] if path.startswith(prefix) else path


def _read_hunk(lines: list[str], i: int, match: re.Match, change: FileChange) -> int:
    # The counts in the header, not the look of a line, say where a hunk ends: a removed line that
    # begins "-- " and an added one that begins "++ " would otherwise read as the next file.
    old_left = int(match["old"]) if match["old"] is not None else 1
    new_left = int(match["new"]) if match["new"] is not None else 1
    number = int(match["start"])
    change.hunks.append((number, max(number, number + new_left - 1)))
    change.headers.append(match["func"].strip())
    change.starts.append(len(change.body))
    while i < len(lines) and (old_left > 0 or new_left > 0):
        line = lines[i]
        tag, text = line[:1], line[1:]
        if tag == "+":
            change.body.append(("+", number, text))
            number, new_left = number + 1, new_left - 1
        elif tag == "-":
            change.body.append(("-", number, text))
            old_left -= 1
        elif tag == " " or line == "":  # some tools strip the space off a blank context line
            change.body.append((" ", number, text))
            number, old_left, new_left = number + 1, old_left - 1, new_left - 1
        elif tag != "\\":  # "\ No newline at end of file" belongs to the hunk; anything else ends it
            break
        i += 1
    return i


# --------------------------------------------------------------------------------------
# Reading lines of code
# --------------------------------------------------------------------------------------


def defined_name(text: str) -> str | None:
    """The name a line defines, or None when it defines nothing."""
    for pattern in _DEFINITIONS:
        match = pattern.match(text)
        if match:
            return match[1]
    return None


def _defines(text: str, name: str, config: bool) -> bool:
    # A definition names its subject before any call, assignment or keyword that uses a value: the
    # cheap test that spares the patterns below the call sites, which outnumber definitions a
    # hundred to one for a helper used everywhere.
    prefix = text[:text.find(name)]
    if "(" in prefix or "=" in prefix or prefix.split()[:1] in (["return"], ["await"], ["yield"], ["throw"], ["raise"]):
        return False
    if defined_name(text) == name:
        return True
    for pattern in (_CONFIG_KEY if config else _TOP_LEVEL_ASSIGN, _FIELD):
        match = pattern.match(text)
        if match and match["name"] == name:
            return True
    return False


def _quiet_lines(lines: list[str]) -> tuple[frozenset[int], frozenset[int]]:
    """
    1-based numbers of the lines that mention names without using them: those inside a docstring or
    a block comment, and those of an import, including every line of a parenthesised one.
    """
    prose: set[int] = set()
    imports: set[int] = set()
    until: str | None = None  # what closes the docstring or comment we are in
    import_until: str | None = None  # what closes the import we are in
    for number, text in enumerate(lines, 1):
        if until is not None:
            prose.add(number)
            if until in text:
                until = None
        elif import_until is not None:
            imports.add(number)
            if import_until in text:
                import_until = None
        elif _IMPORT_LINE.match(text) or _GO_IMPORT.match(text):
            imports.add(number)
            tail = text.rstrip()
            import_until = ")" if tail.endswith("(") else "}" if tail.endswith("{") else None
        else:
            for opener, closer in (('"""', '"""'), ("'''", "'''"), ("/*", "*/")):
                at = text.find(opener)
                if at >= 0 and text.find(closer, at + len(opener)) < 0:
                    until = closer
                    prose.add(number)
                    break
    return frozenset(prose), frozenset(imports)


def _indent(text: str) -> int:
    return len(text) - len(text.lstrip(" \t"))


def _code_of(text: str) -> str:
    """The line with its strings emptied and its trailing comment cut."""
    return _TRAILING_COMMENT.sub("", _STRING.sub('""', text))


def _is_symbol(name: str) -> bool:
    # Dunders are called through their class or an operator, never by their own name.
    return len(name) >= 3 and name.lower() not in _STOPWORDS and not (name.startswith("__") and name.endswith("__"))


def _literals(text: str) -> dict[str, None]:
    """String values distinctive enough to look up: event names, cache keys, routes, dict keys."""
    # Ordered, like every collection of names here: the order decides which names a capped file
    # keeps, and a selection that changed with the hash seed would change the review's cache key.
    found: dict[str, None] = {}
    for match in _STRING.finditer(text):
        body = match["body"]
        if _IDENT.fullmatch(body):
            if _is_symbol(body):
                found[body] = None
        elif 6 <= len(body) <= 100 and not any(c.isspace() for c in body) and any(c.isalpha() for c in body):
            found[body] = None
    return found


def _enclosing_def(lines: list[str], k: int, indent: int) -> int | None:
    """Index of the innermost definition above line index `k` indented less than `indent`."""
    for j in range(min(k, len(lines) - 1), -1, -1):
        text = lines[j]
        if not text.strip():
            continue
        level = _indent(text)
        if level >= indent:
            continue
        if defined_name(text):
            return j
        if level == 0:
            return None
        indent = level
    return None


def _outermost_class(lines: list[str], k: int, indent: int) -> int | None:
    found = None
    while True:
        j = _enclosing_def(lines, k, indent)
        if j is None:
            return found
        if _CLASS_LIKE.match(lines[j]):
            found = j
        k, indent = j - 1, _indent(lines[j])
        if indent == 0:
            return found


def _block_at(lines: list[str], i: int) -> tuple[int, int]:
    """Line indices of the statement or definition at `i`: its decorators, body and closing line."""
    start = i
    while start > 0 and lines[start - 1].lstrip().startswith("@"):
        start -= 1
    base, end = _indent(lines[i]), i
    for j in range(i + 1, len(lines)):
        text = lines[j]
        if not text.strip():
            continue
        level = _indent(text)
        if level > base or (level == base and _CONTINUES.match(text)):
            end = j
            continue
        if level == base and _CLOSER.match(text):
            end = j
        break
    return start, end


# --------------------------------------------------------------------------------------
# What the change names
# --------------------------------------------------------------------------------------


@dataclass
class _Names:
    removed: list[str]  # definitions the change deletes, or renames away
    literals: list[str]  # string values the change removes
    edited: list[str]  # definitions that existed and changed: their users can break
    new: list[str]  # definitions the change adds: a namesake elsewhere may be the one to reuse
    named: list[str]  # what the new lines call, construct or import, to be found where defined
    methods: list[str]  # what the new lines call on self or this: defined in the changed file
    modules: dict[str, set[str]]  # a receiver the new lines read attributes of -> those attributes
    imported: set[str]  # every name the changed files import
    origins: dict[str, set[str]]  # a removed or edited name -> the changed files it lives in

    def words(self) -> list[str]:
        literal_words = [lit for lit in self.literals if _IDENT.fullmatch(lit)]
        every = self.removed + literal_words + self.edited + self.new + self.named + self.methods
        return list(dict.fromkeys([*every, *(a for attrs in self.modules.values() for a in attrs)]))

    def substrings(self) -> list[str]:
        return [lit for lit in self.literals if not _IDENT.fullmatch(lit)]


@dataclass
class _Added:
    """What the added lines of a diff name, gathered line by line."""

    defs: dict[str, None] = field(default_factory=dict)
    named: dict[str, None] = field(default_factory=dict)
    methods: dict[str, None] = field(default_factory=dict)
    local: set[str] = field(default_factory=set)
    modules: dict[str, set[str]] = field(default_factory=dict)
    text: list[str] = field(default_factory=list)


def _collect(changes: list[FileChange]) -> _Names:
    added = _Added()
    removed_defs: dict[str, None] = {}
    removed_literals: dict[str, None] = {}
    enclosing: dict[str, None] = {}
    imported: set[str] = set()
    origins: dict[str, set[str]] = {}
    for change in changes:
        in_file = _imported_names(change)
        imported |= in_file
        where = {p for p in (change.path, change.old_path) if p}
        for _, text in change.lines("+"):
            _read_added_line(text, in_file, added)
            name = defined_name(text)
            if name:
                origins.setdefault(name, set()).update(where)
        for _, text in change.lines("-"):
            name = defined_name(text)
            if name:
                removed_defs[name] = None
                origins.setdefault(name, set()).update(where)
            elif not _COMMENT_LINE.match(text):
                removed_literals.update(_literals(text))
        for name in _enclosing_names(change):
            enclosing[name] = None
            origins.setdefault(name, set()).update(where)
    removed = [n for n in removed_defs if n not in added.defs and _is_symbol(n)]
    new = [n for n in added.defs if n not in removed_defs and _is_symbol(n)]
    edited = [n for n in {**added.defs, **enclosing} if n not in removed and n not in new and _is_symbol(n)]
    skip = set(removed) | set(new) | set(edited) | added.local
    added_blob = "\n".join(added.text)
    return _Names(
        removed=removed,
        literals=[lit for lit in removed_literals if lit not in added_blob],
        edited=edited,
        new=new,
        named=[n for n in added.named if n not in skip and _is_symbol(n)][:MAX_NAMES],
        methods=[n for n in added.methods if n not in skip and _is_symbol(n)][:MAX_NAMES],
        modules={m: a for m, a in added.modules.items() if m not in added.local and _is_symbol(m)},
        imported=imported,
        origins=origins,
    )


def _read_added_line(text: str, imported: set[str], added: _Added) -> None:
    added.text.append(text)
    name = defined_name(text)
    if name:
        added.defs[name] = None
        return
    if _COMMENT_LINE.match(text):
        return
    code = _code_of(text)
    target = _ASSIGN_TARGET.match(code)
    if target:
        added.local.add(target[1])
    # Only what is imported, or spelled like a type or a constant, is looked up by name alone: a
    # bare lower-case word on a new line is almost always a local, and its "definition" elsewhere
    # is a namesake.
    for ident in _IDENT.findall(code):
        if ident in imported or ident[:1].isupper():
            added.named[ident] = None
    for match in _CALL.finditer(code):
        before = code[:match.start()].rstrip()
        if not before.endswith("."):
            added.named[match[1]] = None
        elif (receiver := _RECEIVER.search(before[:-1])) and receiver[0] in ("self", "this", "cls"):
            added.methods[match[1]] = None
        # `anything.name(` on another receiver is a method of an object whose class is unknown:
        # a function of that name elsewhere is a namesake. A module receiver is `modules`' job.
    for match in _MODULE_ATTR.finditer(code):
        added.modules.setdefault(match[1], set()).add(match[2])


def _imported_names(change: FileChange) -> set[str]:
    """The names a changed file's imports bind, read from the file or, failing that, its hunks."""
    text = change.disk if change.disk is not None else [t for _, _, t in change.body]
    names: set[str] = set()
    for line in text:
        names |= _bound_names(line)
    return names


def _bound_names(line: str) -> set[str]:
    for pattern in _IMPORTS:
        match = pattern.match(line)
        if match:
            bound = match["names"]
            if pattern is _PY_IMPORT:
                # `import a.b.c` binds `a`; `import a.b as c` binds `c`.
                return {item.split(" as ")[-1].strip().split(".")[0] for item in bound.split(",")}
            return {n for n in _IDENT.findall(bound) if n not in ("as", "type", "crate", "super", "self")}
    return set()


def _enclosing_names(change: FileChange) -> dict[str, None]:
    """The function each changed line sits in: its callers are what the change can break."""
    names: dict[str, None] = {}
    for i, (tag, number, text) in enumerate(change.body):
        if tag == " " or not text.strip() or defined_name(text):
            continue
        if change.disk is not None:
            j = _enclosing_def(change.disk, number - 2, _indent(text))
            name = defined_name(change.disk[j]) if j is not None else None
        else:
            name = _enclosing_in_body(change.body[:i], _indent(text)) or _header_name(change, number)
        if name:
            names[name] = None
    return names


def _enclosing_in_body(entries: list[tuple[str, int, str]], indent: int) -> str | None:
    for _, _, text in reversed(entries):
        if not text.strip() or _indent(text) >= indent:
            continue
        name = defined_name(text)
        if name:
            return name
        indent = _indent(text)
    return None


def _header_name(change: FileChange, line: int) -> str | None:
    for (start, end), header in zip(change.hunks, change.headers, strict=True):
        if start <= line <= end + 1 and header:
            return defined_name(header)
    return None


# --------------------------------------------------------------------------------------
# The repository
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Hit:
    path: str
    line: int  # 1-based
    text: str


def _searchable(path: str) -> bool:
    lowered = path.lower()
    parts = lowered.split("/")
    if _SKIPPED_DIRS.intersection(parts[:-1]) or parts[-1] in _SKIPPED_NAMES:
        return False
    if any(lowered.endswith(suffix) for suffix in _SKIPPED_SUFFIXES):
        return False
    return not is_refused(path)


class _Repo:
    """The files under the root, searched with git when it is a repository and by walking when not."""

    def __init__(self, root: Path):
        self.root = root
        listed = _git(root, ["ls-files", "-z", "--cached", "--others", "--exclude-standard"])
        self.git = listed is not None
        names = listed.split("\0") if listed is not None else _walk(root)
        self.files = [name for name in names if name and _searchable(name)]
        self.file_set = frozenset(self.files)
        self._lines: dict[str, list[str] | None] = {}

    def lines(self, path: str) -> list[str] | None:
        if path not in self._lines:
            self._lines[path] = _read_lines(self.root / path, MAX_SOURCE_FILE_BYTES)
        return self._lines[path]

    def state_after(self, change: FileChange) -> list[str] | None:
        """The changed file as it stands, if that is what the diff leaves; None if it is not.

        A diff between two other revisions leaves a different file from the one checked out, and
        its line numbers then point at unrelated code.
        """
        if change.path is None or change.path not in self.file_set:
            return None
        lines = self.lines(change.path)
        return lines if lines is not None and _align(change, lines) else None

    def search(self, words: list[str], substrings: list[str]) -> list[_Hit]:
        hits = None
        if self.git:
            by_word = _git_grep(self.root, words, whole_words=True)
            by_text = _git_grep(self.root, substrings, whole_words=False)
            if by_word is not None and by_text is not None:
                hits = by_word + by_text
        if hits is None:
            hits = self._scan(words, substrings)
        return [hit for hit in hits if hit.path in self.file_set]

    def _scan(self, words: list[str], substrings: list[str]) -> list[_Hit]:
        if not words and not substrings:
            return []
        alternatives = "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True))
        word = re.compile(rf"(?<![\w])(?:{alternatives})(?![\w])") if words else None
        hits = []
        for path in self.files:
            lines = _read_lines(self.root / path, MAX_SCAN_BYTES)
            for number, text in enumerate(lines or [], 1):
                if (word and word.search(text)) or any(s in text for s in substrings):
                    hits.append(_Hit(path, number, text))
        return hits


def _align(change: FileChange, lines: list[str]) -> bool:
    """
    Find each hunk in the file and renumber it to where it stands, as `patch` does.

    A hunk is found by its lines after the change, context and additions together. Its header is
    tried first; an unrelated edit above it, which `git diff --cached` leaves out, moves it. False
    when a hunk is not in the file at all: then the file is not what the diff leaves.
    """
    bounds = [*change.starts, len(change.body)]
    for h in range(len(change.starts)):
        entries = change.body[bounds[h]:bounds[h + 1]]
        after = [text.rstrip() for tag, _, text in entries if tag != "-"]
        while after and not after[-1]:
            after.pop()  # blank context past the end of the file: a trailing newline, not a line
        if not after:
            continue  # a pure deletion leaves nothing to look for
        stated = change.hunks[h][0]
        found = _find_run(lines, after, stated)
        if found is None:
            return False
        shift = found - stated
        if shift:
            change.body[bounds[h]:bounds[h + 1]] = [(tag, n + shift, t) for tag, n, t in entries]
            change.hunks[h] = (change.hunks[h][0] + shift, change.hunks[h][1] + shift)
    return True


def _find_run(lines: list[str], run: list[str], stated: int) -> int | None:
    """The 1-based line nearest `stated` where `run` begins, or None."""
    def at(start: int) -> bool:
        return start >= 1 and start - 1 + len(run) <= len(lines) and all(
            lines[start - 1 + i].rstrip() == text for i, text in enumerate(run)
        )
    if at(stated):
        return stated
    starts = [n for n in range(1, len(lines) - len(run) + 2) if lines[n - 1].rstrip() == run[0] and at(n)]
    return min(starts, key=lambda n: abs(n - stated)) if starts else None


def _git(root: Path, args: list[str]) -> str | None:
    try:
        run = subprocess.run(
            ["git", "-C", str(root), "-c", "core.quotepath=off", *args],
            capture_output=True, timeout=GIT_TIMEOUT_SECONDS, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    # git grep exits 1 when nothing matched, which is an answer, not a failure.
    if run.returncode != 0 and not (run.returncode == 1 and args[0] == "grep"):
        return None
    return run.stdout.decode("utf-8", "replace")


def _git_grep(root: Path, patterns: list[str], whole_words: bool) -> list[_Hit] | None:
    if not patterns:
        return []
    args = ["grep", "-n", "-z", "-I", "--untracked", "--no-color", "-F"]
    if whole_words:
        args.append("-w")
    for pattern in patterns:
        args += ["-e", pattern]
    out = _git(root, args)
    if out is None:
        return None
    hits = []
    for record in out.split("\n"):
        parts = record.split("\0", 2)
        if len(parts) == 3 and parts[1].isdigit():
            hits.append(_Hit(parts[0], int(parts[1]), parts[2]))
    return hits


def _walk(root: Path) -> list[str]:
    found: list[str] = []
    for directory, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d.lower() not in _SKIPPED_DIRS)
        for name in sorted(files):
            found.append((Path(directory) / name).relative_to(root).as_posix())
            if len(found) >= MAX_WALK_FILES:
                return found
    return found


def _read_lines(path: Path, limit: int) -> list[str] | None:
    try:
        if path.stat().st_size > limit:
            return None
        raw = path.read_bytes()
    except OSError:
        return None
    if b"\x00" in raw[:_BINARY_PROBE_BYTES]:
        return None
    lines = raw.decode("utf-8", "replace").split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return [line.rstrip("\r") for line in lines]


# --------------------------------------------------------------------------------------
# Choosing
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Found:
    path: str
    line: int  # 1-based
    why: str
    kind: str  # "use": excerpt the code around it; "definition" and "class": excerpt from it down


def select(reviewed: str, root: Path, skip_paths: Iterable[str] = ()) -> list[Pick]:
    """
    Excerpts of the code a diff breaks or relies on, most relevant first.

    `reviewed` is the diff as piped, unredacted: names are read from it, nothing of it is sent.
    `skip_paths` are files the caller already sends whole. Never raises: whatever cannot be read
    or searched is left out, and a review with less context is still a review.
    """
    changes = parse_diff(reviewed)
    if not changes:
        logger.warning("Auto context reads a unified diff, such as `git diff` prints; nothing was selected.")
        return []
    try:
        repo = _Repo(root)
        for change in changes:
            change.disk = repo.state_after(change)
        names = _collect(changes)
        hits = _usable_hits(changes, repo.search(names.words(), names.substrings()))
        found = _rank(_Scope(changes, repo, names.origins), names, _Index(hits, names.words()))
        return _pieces(found, repo, changes, frozenset(skip_paths))
    except Exception as e:
        # Best effort by design: a selection bug must cost the review its context, not the review.
        logger.warning(f"Auto context failed and was skipped: {e}")
        return []


class _Scope:
    """Where the change is: which files sit near it, and which of them depend on what it changed."""

    def __init__(self, changes: list[FileChange], repo: _Repo, origins: dict[str, set[str]]):
        self.repo = repo
        self.changes = changes
        self.origins = origins
        paths = [p for c in changes for p in (c.path, c.old_path) if p]
        self.changed = frozenset(c.path for c in changes if c.path)
        self.anchors = [PurePosixPath(p).parent for p in paths]
        self._quiet_cache: dict[str, tuple[frozenset[int], frozenset[int]]] = {}

    def closeness(self, path: str) -> int:
        parts = PurePosixPath(path).parent.parts
        best = 0
        for anchor in self.anchors:
            shared = 0
            for mine, theirs in zip(parts, anchor.parts, strict=False):
                if mine != theirs:
                    break
                shared += 1
            best = max(best, shared)
        return best

    def sees(self, path: str, origins: set[str]) -> bool:
        """
        The file can reach a name defined in `origins`: it is one of them, imports one by its module
        name, or shares a package with one in a language whose files do not import each other. A
        `main()` in another module calls another `main`.
        """
        if path in origins:
            return True
        here = PurePosixPath(path)
        for origin in origins:
            there = PurePosixPath(origin)
            if here.parent == there.parent and here.suffix in _PACKAGE_SCOPED and here.suffix == there.suffix:
                return True
            stem = _module_name(origin)
            if any(stem in _IDENT.findall(line) for line in self._import_lines(path)):
                return True
        return False

    def _import_lines(self, path: str) -> list[str]:
        lines = self.repo.lines(path) or []
        return [lines[n - 1] for n in sorted(self._quiet(path)[1])]

    def in_prose(self, hit: _Hit) -> bool:
        """The hit is inside a docstring or a block comment, where a name is mentioned, not used."""
        return hit.line in self._quiet(hit.path)[0]

    def in_import(self, hit: _Hit) -> bool:
        return hit.line in self._quiet(hit.path)[1] or bool(_IMPORT_LINE.match(hit.text))

    def _quiet(self, path: str) -> tuple[frozenset[int], frozenset[int]]:
        if path not in self._quiet_cache:
            self._quiet_cache[path] = _quiet_lines(self.repo.lines(path) or [])
        return self._quiet_cache[path]


def _usable_hits(changes: list[FileChange], hits: list[_Hit]) -> list[_Hit]:
    by_path = {c.path: c for c in changes if c.path}
    kept = []
    for hit in hits:
        change = by_path.get(hit.path)
        # The changed file only as the diff leaves it, and never inside the diff itself: that code
        # is already under review.
        if change is not None and (change.disk is None or change.touches(hit.line)):
            continue
        # A line this long is data — a recorded review, a minified bundle — not code that calls.
        if len(hit.text) <= MAX_LINE_CHARS and not _COMMENT_LINE.match(hit.text):
            kept.append(hit)
    return kept


def _rank(scope: _Scope, names: _Names, index: _Index) -> list[_Found]:
    """What still uses the removed names first; then callers and callees in turn; tests last."""
    removed = _uses(scope, index, names.removed, "uses {}, which the change removes", imports=True, related=True)
    removed += _uses(scope, index, names.literals, "uses {}, which the change removes", imports=True, quoted=True)
    callers = _class_rests(scope)
    callers += _uses(scope, index, names.edited, "uses {}", related=True)
    callers += _uses(scope, index, names.new, "uses {}", duplicates=True)
    callees = _definitions(scope, index, names.named)
    callees += _definitions(scope, index, names.methods, within=scope.changed)
    callees += _modules(scope, names)
    ordered = removed + _interleave(callers, callees)
    # Tests are the likeliest place for a name to recur and the least likely to be what broke.
    return [f for f in ordered if not _TEST_PATH.search(f.path)] + [f for f in ordered if _TEST_PATH.search(f.path)]


class _Index:
    """The hits, by the identifier or string value each mentions. Built once, read per name."""

    def __init__(self, hits: list[_Hit], words: list[str]):
        self._hits = hits
        self._by_word: dict[str, list[_Hit]] = {}
        wanted = frozenset(words)
        for hit in hits:
            for word in wanted.intersection(_IDENT.findall(hit.text)):
                self._by_word.setdefault(word, []).append(hit)

    def of(self, name: str) -> list[_Hit]:
        if _IDENT.fullmatch(name):
            return self._by_word.get(name, [])
        return [hit for hit in self._hits if name in hit.text]


def _uses(
    scope: _Scope, index: _Index, names: list[str], why: str, imports: bool = False,
    related: bool = False, quoted: bool = False, duplicates: bool = False,
) -> list[_Found]:
    """
    Where the names are used, files that use more of them first.

    `imports` counts an import line: for a removed name that line is the break. `related` keeps
    only files that can reach the changed file the name lives in. `duplicates` reports a file that
    defines the same name as a second definition, which is the finding for a name the change adds.
    A name is used by code: config and data files, docstrings and comments only mention it, except
    for a string value, which is exactly what config holds.
    """
    per_path: dict[str, dict[str, list[_Hit]]] = {}
    for name in names:
        matching = [h for h in index.of(name) if quoted or not _is_config(h.path)]
        if len(_group(matching)) > MAX_FILES_FOR_A_NAME:
            continue  # a word, not a symbol; counted before the checks that read whole files
        matching = [h for h in matching if (imports or not scope.in_import(h)) and not scope.in_prose(h)]
        by_path = _group(matching)
        kept = [p for p in by_path if not related or scope.sees(p, scope.origins.get(name, set()))]
        for path in _nearest(scope, kept)[:MAX_FILES_PER_NAME]:
            per_path.setdefault(path, {})[name] = by_path[path]
    order = sorted(per_path, key=lambda p: (-len(per_path[p]), -scope.closeness(p), p))
    found: list[_Found] = []
    for path in order:
        for name, path_hits in list(per_path[path].items())[:MAX_HITS_PER_FILE]:
            label = why.format(f'"{name}"' if quoted else name)
            for hit in path_hits[:MAX_HITS_PER_FILE]:
                if duplicates and defined_name(hit.text) == name:
                    found.append(_Found(path, hit.line, f"also defines {name}", "definition"))
                elif defined_name(hit.text) != name:
                    found.append(_Found(path, hit.line, label, "use"))
    return found


def _definitions(
    scope: _Scope, index: _Index, names: list[str], within: frozenset[str] | None = None
) -> list[_Found]:
    found: list[_Found] = []
    for name in names:
        defining = [
            h for h in index.of(name)
            if (within is None or h.path in within) and _defines(h.text, name, _is_config(h.path))
            and not scope.in_prose(h)
        ]
        by_path = _group(defining)
        ranked = _nearest(scope, list(by_path))
        for path in ranked[:1] if len(ranked) > MAX_DEFINITIONS else ranked:
            found.append(_Found(path, by_path[path][0].line, f"defines {name}", "definition"))
    return found


def _modules(scope: _Scope, names: _Names) -> list[_Found]:
    """
    A module the new lines read from, found by its file name: the definitions of what they read.

    `payments.charge(...)` reads `charge` from a file named payments. The receiver is taken for a
    module when the changed file imports it, or when a file of that name defines what is read; a
    local named like a module (`user.save()` beside a user.py) passes neither.
    """
    found: list[_Found] = []
    for module, attributes in names.modules.items():
        candidates = [p for p in scope.repo.files if p not in scope.changed and _module_name(p) == module]
        for path in _nearest(scope, candidates):
            lines = scope.repo.lines(path) or []
            config = _is_config(path)
            defs = [n for n, text in enumerate(lines, 1) if any(_defines(text, a, config) for a in attributes)]
            if not defs and module in names.imported and 0 < len(lines) <= SMALL_FILE_LINES:
                defs = [1]  # imported and short: sent whole, and what it lacks is the finding
            if defs:
                found.extend(_Found(path, n, f"module {module}", "definition") for n in defs[:MAX_HITS_PER_FILE])
                break
    return found


def _module_name(path: str) -> str:
    pure = PurePosixPath(path)
    return pure.parent.name if pure.stem in ("__init__", "index", "mod") else pure.stem


def _class_rests(scope: _Scope) -> list[_Found]:
    """The class a change edits, outside the diff: its fields, its lock, the methods beside it."""
    found: list[_Found] = []
    for change in scope.changes:
        if change.disk is None or change.path is None:
            continue
        seen: set[int] = set()
        for tag, number, text in change.body:
            if tag == " " or not text.strip():
                continue
            j = _outermost_class(change.disk, number - 2, _indent(text))
            if j is not None and j not in seen:
                seen.add(j)
                name = defined_name(change.disk[j]) or "?"
                found.append(_Found(change.path, j + 1, f"the rest of class {name}", "class"))
    return found


def _group(hits: list[_Hit]) -> dict[str, list[_Hit]]:
    by_path: dict[str, list[_Hit]] = {}
    for hit in hits:
        by_path.setdefault(hit.path, []).append(hit)
    return by_path


def _nearest(scope: _Scope, paths: list[str]) -> list[str]:
    """Paths nearest the changed files first, tests last: a sibling module outranks a distant one."""
    return sorted(paths, key=lambda p: (bool(_TEST_PATH.search(p)), -scope.closeness(p), p))


def _is_config(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in _CONFIG_SUFFIXES


def _interleave(first: list[_Found], second: list[_Found]) -> list[_Found]:
    """Alternate files from both lists, so the callers cannot crowd out the callees or the reverse."""
    order: list[_Found] = []
    queues = [_by_file(first), _by_file(second)]
    while any(queues):
        for queue in queues:
            if queue:
                order.extend(queue.pop(0))
    return order


def _by_file(found: list[_Found]) -> list[list[_Found]]:
    groups: dict[str, list[_Found]] = {}
    for item in found:
        groups.setdefault(item.path, []).append(item)
    return list(groups.values())


def _pieces(found: list[_Found], repo: _Repo, changes: list[FileChange], skip: frozenset[str]) -> list[Pick]:
    changed = {c.path: c for c in changes if c.path}
    spans: dict[str, list[tuple[int, int]]] = {}
    whys: dict[str, dict[str, None]] = {}
    for item in found:
        lines = repo.lines(item.path)
        if item.path in skip or not lines or not 0 < item.line <= len(lines):
            continue
        spans.setdefault(item.path, []).append(_span(lines, item))
        whys.setdefault(item.path, {})[item.why] = None
    picks: list[Pick] = []
    for path, ranges in spans.items():
        why = "; ".join(list(whys[path])[:3])
        change = changed.get(path)
        lines = repo.lines(path) or []
        if change is None and len(lines) <= SMALL_FILE_LINES:
            picks.append(Pick(path, None, None, why))
        else:
            merged = _merge(ranges)
            if change is not None:
                merged = [part for span in merged for part in _outside(span, change.hunks)]
            picks.extend(Pick(path, start, end, why) for start, end in merged[:MAX_PIECES_PER_FILE])
        if len(picks) >= MAX_PIECES:
            break
    return picks[:MAX_PIECES]


def _span(lines: list[str], item: _Found) -> tuple[int, int]:
    """1-based inclusive lines of the excerpt for one finding."""
    k = item.line - 1
    if item.kind == "use":
        j = _enclosing_def(lines, k - 1, _indent(lines[k]))
        start, end = _block_at(lines, j if j is not None else k)
        if j is None:
            start = max(0, k - 3)  # a top-level statement: the lines that set it up
        if not start <= k <= end:
            start, end = max(0, k - WINDOW_LINES), min(len(lines) - 1, k + WINDOW_LINES)
        if end - start + 1 > MAX_BLOCK_LINES:
            start, end = max(start, k - WINDOW_LINES), min(end, k + WINDOW_LINES)
    else:
        start, end = _block_at(lines, k)
        end = min(end, start + (CLASS_HEAD_LINES if item.kind == "class" else MAX_BLOCK_LINES) - 1)
    return start + 1, end + 1


def _merge(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1] + 3:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _outside(span: tuple[int, int], hunks: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """The parts of a span the diff does not already show."""
    parts = [span]
    for low, high in hunks:
        cut = []
        for start, end in parts:
            if end < low or start > high:
                cut.append((start, end))
                continue
            if start < low:
                cut.append((start, low - 1))
            if end > high:
                cut.append((high + 1, end))
        parts = cut
    return [(s, e) for s, e in parts if e - s + 1 >= MIN_FRAGMENT_LINES]
