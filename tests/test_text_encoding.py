"""Text files are read and written as UTF-8 on every platform, not as whatever the OS defaults to.

`open()`, `Path.read_text()` and `Path.write_text()` without `encoding=` use the locale's encoding:
UTF-8 on Linux and macOS, cp1252 on Windows until Python 3.15. `evals/score.py` read the recordings
that way, so on Windows `python evals/score.py` (the command the README gives for reproducing its
numbers) stopped on the first recording holding a non-ASCII character, and 11 tests with it. Linux
and macOS CI cannot see that, so the rule is checked in the source.
"""

import ast
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
CHECKED = [*sorted((ROOT / "src" / "fourtake").glob("*.py")), *sorted((ROOT / "evals").glob("*.py"))]

# What a mode argument looks like; a file name, a URL or a prompt never matches.
_MODE = re.compile(r"[rwxabt+]{1,3}")


def _mode_of(call: ast.Call) -> str | None:
    """The mode a file-opening call asks for, or None when it leaves the default (text, read)."""
    for keyword in call.keywords:
        if keyword.arg == "mode" and isinstance(keyword.value, ast.Constant):
            return str(keyword.value.value)
    for argument in call.args[:2]:
        is_mode = (
            isinstance(argument, ast.Constant)
            and isinstance(argument.value, str)
            and _MODE.fullmatch(argument.value)
        )
        if is_mode:
            return argument.value
    return None


def _opens_text_without_encoding(call: ast.Call) -> bool:
    if any(keyword.arg == "encoding" for keyword in call.keywords):
        return False
    function = call.func
    if isinstance(function, ast.Attribute):
        if function.attr in ("read_text", "write_text"):
            return True
        # `os.open` takes flags and returns a descriptor: no text, no encoding.
        is_os_open = (
            function.attr == "open" and isinstance(function.value, ast.Name) and function.value.id == "os"
        )
        if is_os_open or function.attr not in ("open", "fdopen"):
            return False
    elif not (isinstance(function, ast.Name) and function.id == "open"):
        return False
    return "b" not in (_mode_of(call) or "")


def violations(source: str, name: str = "<source>") -> list[str]:
    return [
        f"{name}:{node.lineno}"
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call) and _opens_text_without_encoding(node)
    ]


@pytest.mark.parametrize(
    "source",
    [
        'Path("m.json").read_text()',
        'Path("m.json").write_text("{}")',
        'open("m.json")',
        'open("m.json", "w")',
        "path.open()",
        'os.fdopen(fd, "w")',
        'open("m.json", mode="r")',
    ],
)
def test_the_check_catches_text_io_that_leaves_the_encoding_to_the_platform(source):
    assert violations(source) == ["<source>:1"]


@pytest.mark.parametrize(
    "source",
    [
        'Path("m.json").read_text(encoding="utf-8")',
        'Path("m.json").write_text("{}", encoding="utf-8", newline="\\n")',
        'open("m.json", encoding="utf-8")',
        'open("m.bin", "rb")',
        'path.open("rb")',
        'os.fdopen(fd, "wb")',
        "os.open(path, os.O_RDWR | os.O_CREAT, 0o600)",
        "target.read_bytes()",
    ],
)
def test_the_check_lets_explicit_encodings_and_binary_files_through(source):
    assert violations(source) == []


@pytest.mark.parametrize("path", CHECKED, ids=lambda p: str(p.relative_to(ROOT)))
def test_text_files_name_their_encoding(path):
    assert violations(path.read_text(encoding="utf-8"), path.name) == []
