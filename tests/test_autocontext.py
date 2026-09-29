"""
Auto context: which excerpts a diff pulls in, and what it must never pull in.

The load-bearing test is the corpus sweep. Every cross-file case in evals/corpus_ctx was given its
context by hand; put all of them in one repository and the selection for each diff must find its
own case's files and none of the others'. It grows when someone adds a case, and it runs twice,
through git and through the walk a directory without git gets.

Selection is lexical, so most of the rest pins the ways grep goes wrong: namesakes, words that
are everywhere, names mentioned in docstrings, data and imports rather than used.
"""

import logging
import shutil
import subprocess
from pathlib import Path

import pytest

from fourtake import autocontext
from fourtake.autocontext import defined_name, parse_diff, select
from fourtake.context import AFTER_CHANGE_NOTE, assemble

CORPUS = Path(__file__).resolve().parent.parent / "evals" / "corpus_ctx"
CASES = sorted(p for p in CORPUS.iterdir() if p.is_dir())


def _repo(root: Path, files: dict[str, str], git: bool = True) -> Path:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    if git:
        subprocess.run(["git", "init", "-q", str(root)], check=True)
    return root


def _paths(picks) -> list[str]:
    return [p.path for p in picks]


# --------------------------------------------------------------------------------------
# The corpus sweep
# --------------------------------------------------------------------------------------


@pytest.fixture(scope="module", params=["git", "walk"])
def corpus_repo(request, tmp_path_factory):
    root = tmp_path_factory.mktemp(f"corpus_{request.param}")
    for case in CASES:
        shutil.copytree(case / "context", root, dirs_exist_ok=True)
    if request.param == "git":
        subprocess.run(["git", "init", "-q", str(root)], check=True)
    return root


@pytest.mark.parametrize("case", CASES, ids=lambda p: p.name)
def test_each_case_selects_its_own_context_and_no_other_cases(case, corpus_repo):
    own = {p.relative_to(case / "context").as_posix() for p in (case / "context").rglob("*") if p.is_file()}
    diff = next(case.glob("review.*")).read_text()
    chosen = set(_paths(select(diff, corpus_repo)))
    assert own <= chosen, f"missed {sorted(own - chosen)}"
    assert chosen <= own, f"pulled in another case's files: {sorted(chosen - own)}"


# --------------------------------------------------------------------------------------
# Reading the diff
# --------------------------------------------------------------------------------------


def test_hunks_end_where_their_counts_say():
    """A removed line starting '-- ' and an added one starting '++ ' are lines, not file headers."""
    diff = (
        "--- a/q.sql\n+++ b/q.sql\n@@ -1,2 +1,2 @@\n"
        "--- old comment\n+++ new comment\n SELECT 1;\n"
    )
    (change,) = parse_diff(diff)
    assert change.path == "q.sql"
    assert [tag for tag, _, _ in change.body] == ["-", "+", " "]
    assert change.hunks == [(1, 2)]


def test_a_deleted_file_has_no_path_after_the_change():
    diff = "--- a/gone.py\n+++ /dev/null\n@@ -1,2 +0,0 @@\n-def gone():\n-    return 1\n"
    (change,) = parse_diff(diff)
    assert change.path is None and change.old_path == "gone.py"


def test_anything_but_a_diff_parses_to_nothing():
    assert parse_diff("def f():\n    return 1\n") == []


@pytest.mark.parametrize("line, name", [
    ("def find_user(uid):", "find_user"),
    ("    async def refresh(self):", "refresh"),
    ("class RateCounter:", "RateCounter"),
    ("export async function createUser(input) {", "createUser"),
    ("const transfer = async (from, to, amount) => {", "transfer"),
    ("  handle(event) {", "handle"),
    ("func (s *Server) ServeHTTP(w http.ResponseWriter, r *http.Request) {", "ServeHTTP"),
    ("func main() {", "main"),
    ("    public User findUser(String id) throws NotFound {", "findUser"),
    ("    User findUser(String id) {", "findUser"),
    ("pub fn parse(input: &str) -> Result<Ast> {", "parse"),
    ("  def self.call(env)", "call"),
])
def test_definitions_are_recognised_across_languages(line, name):
    assert defined_name(line) == name


@pytest.mark.parametrize("line", [
    "    return find_user(uid)",
    "    if (user) {",
    "  } else if (ready) {",
    "    await transfer(a, b, 10);",
    "user = find_user(uid)",
])
def test_calls_and_control_flow_are_not_definitions(line):
    assert defined_name(line) is None


# --------------------------------------------------------------------------------------
# The changed file itself
# --------------------------------------------------------------------------------------

_COUNTER = """import threading


class Counter:
    def __init__(self):
        self._lock = threading.Lock()
        self._counts = {}

    def hit(self, key):
        with self._lock:
            self._counts[key] = self._counts.get(key, 0) + 1

    def reset(self, key):
        self._counts.pop(key, None)
"""

_ADD_RESET = (
    "--- a/counter.py\n+++ b/counter.py\n@@ -12,1 +12,3 @@ class Counter:\n"
    " \n+    def reset(self, key):\n+        self._counts.pop(key, None)\n"
)


def test_a_changed_class_is_sent_without_the_lines_the_diff_already_shows(tmp_path):
    root = _repo(tmp_path, {"counter.py": _COUNTER})
    (pick,) = select(_ADD_RESET, root)
    # Lines 4-11: the class up to the hunk, which holds the lock reset() does not take.
    assert (pick.path, pick.start, pick.end) == ("counter.py", 4, 11)
    assert "rest of class Counter" in pick.why


def test_a_hunk_moved_by_an_edit_above_it_is_found_where_it_now_stands(tmp_path):
    """`git diff --cached` numbers hunks without the unstaged lines above them."""
    root = _repo(tmp_path, {"counter.py": "# an unstaged comment\n# and another\n" + _COUNTER})
    (pick,) = select(_ADD_RESET, root)
    assert (pick.start, pick.end) == (6, 13)


def test_a_file_that_is_not_what_the_diff_leaves_is_not_used(tmp_path):
    """A diff of other revisions: its line numbers point at unrelated code in the checked-out file."""
    root = _repo(tmp_path, {"counter.py": _COUNTER})
    diff = (
        "--- a/counter.py\n+++ b/counter.py\n@@ -12,1 +12,3 @@ class Counter:\n"
        " \n+    def clear(self):\n+        self._counts = {}\n"
    )
    assert select(diff, root) == []


# --------------------------------------------------------------------------------------
# Refusals and noise
# --------------------------------------------------------------------------------------

_FIND_USER_DIFF = (
    "--- a/app/users.py\n+++ b/app/users.py\n@@ -1,4 +1,4 @@\n def find_user(uid):\n"
    "     row = db.get(uid)\n     if row is None:\n-        raise LookupError(uid)\n+        return None\n"
)


def test_credentials_and_repository_internals_are_never_chosen(tmp_path):
    root = _repo(tmp_path, {
        "app/api.py": "from app.users import find_user\n\ndef show(uid):\n    return find_user(uid).email\n",
        "app/.env": "find_user=sk-live-secret\n",
        "app/keys/find_user.pem": "find_user\n",
    })
    assert _paths(select(_FIND_USER_DIFF, root)) == ["app/api.py"]


def test_a_mention_in_data_a_docstring_or_an_import_is_not_a_use(tmp_path):
    root = _repo(tmp_path, {
        "app/recording.json": '{"content": "find_user now returns None"}\n',
        "app/saved.diff": "+    return find_user(uid)\n",
        "app/doc.py": 'from app.users import find_user\n\n"""\nfind_user is documented here.\n"""\n',
        "app/reexport.py": "from app.users import (\n    find_user,\n)\n",
    })
    assert select(_FIND_USER_DIFF, root) == []


def test_a_namesake_in_a_module_that_cannot_see_the_change_is_left_out(tmp_path):
    """Editing users.main says nothing about the `main()` another module defines and calls."""
    root = _repo(tmp_path, {
        "app/users.py": "def main():\n    return 2\n",
        "tools/other.py": "def main():\n    return 1\n\nmain()\n",
        "tools/runner.py": "from app.users import main\n\nmain()\n",
    })
    diff = "--- a/app/users.py\n+++ b/app/users.py\n@@ -1,2 +1,2 @@\n def main():\n-    return 1\n+    return 2\n"
    assert _paths(select(diff, root)) == ["tools/runner.py"]


def test_a_method_called_on_an_object_does_not_pull_in_a_function_of_that_name(tmp_path):
    root = _repo(tmp_path, {
        "app/search.py": "def search(query):\n    return []\n",
        "app/handler.py": "import re\n\ndef handle(text):\n    return re.search(r'x', text)\n",
    })
    diff = (
        "--- a/app/handler.py\n+++ b/app/handler.py\n@@ -3,2 +3,2 @@\n def handle(text):\n"
        "-    return 1\n+    return re.search(r'x', text)\n"
    )
    assert select(diff, root) == []


def test_a_name_used_everywhere_is_a_word_and_is_not_followed(tmp_path, monkeypatch):
    monkeypatch.setattr(autocontext, "MAX_FILES_FOR_A_NAME", 3)
    files = {f"app/m{i}.py": "from app.users import find_user\n\nx = find_user(1)\n" for i in range(4)}
    root = _repo(tmp_path, files)
    assert select(_FIND_USER_DIFF, root) == []


def test_tests_come_after_the_code_and_the_selection_is_capped(tmp_path, monkeypatch):
    monkeypatch.setattr(autocontext, "MAX_PIECES", 2)
    root = _repo(tmp_path, {
        "tests/test_users.py": "from app.users import find_user\n\ndef test_it():\n    find_user(1)\n",
        "app/api.py": "from app.users import find_user\n\ndef show(uid):\n    return find_user(uid)\n",
        "app/mailer.py": "from app.users import find_user\n\ndef mail(uid):\n    find_user(uid)\n",
    })
    assert _paths(select(_FIND_USER_DIFF, root)) == ["app/api.py", "app/mailer.py"]


def test_input_that_is_not_a_diff_selects_nothing_and_says_so(tmp_path, caplog):
    with caplog.at_level(logging.WARNING, logger="4take"):
        assert select("def f():\n    return 1\n", tmp_path) == []
    assert "unified diff" in caplog.text


# --------------------------------------------------------------------------------------
# Through assemble
# --------------------------------------------------------------------------------------


def test_auto_pieces_say_why_and_come_after_named_ones(tmp_path):
    root = _repo(tmp_path, {
        "app/api.py": "from app.users import find_user\n\ndef show(uid):\n    return find_user(uid).email\n",
        "app/models.py": "class User:\n    pass\n",
    })
    context, _ = assemble(["app/models.py"], 16000, root=root, reviewed=_FIND_USER_DIFF, auto=True)
    assert [p.label for p in context.pieces] == ["app/models.py", "app/api.py"]
    assert context.summary()["auto"] == {"app/api.py": "uses find_user"}
    # The reason is for the reader; the reviewers see the same header a named file gets.
    assert "uses find_user" not in context.block()


def test_a_file_named_whole_is_not_sent_again(tmp_path):
    root = _repo(tmp_path, {"app/api.py": "from app.users import find_user\n\nx = find_user(1)\n"})
    context, _ = assemble(["app/api.py"], 16000, root=root, reviewed=_FIND_USER_DIFF, auto=True)
    assert [p.label for p in context.pieces] == ["app/api.py"]
    assert "auto" not in context.summary()


def test_an_excerpt_of_the_changed_file_is_marked_as_after_the_change(tmp_path):
    root = _repo(tmp_path, {"counter.py": _COUNTER})
    context, _ = assemble(None, 16000, root=root, reviewed=_ADD_RESET, auto=True)
    (piece,) = context.pieces
    assert piece.changed and AFTER_CHANGE_NOTE in context.block()


def test_a_root_too_broad_to_search_skips_auto_context_instead_of_refusing(monkeypatch, caplog):
    monkeypatch.chdir(Path.home())
    monkeypatch.delenv("FOURTAKE_CONTEXT_ROOT", raising=False)
    with caplog.at_level(logging.WARNING, logger="4take"):
        context, _ = assemble(None, 16000, reviewed=_FIND_USER_DIFF, auto=True)
    assert context is None
    assert "Auto context skipped" in caplog.text


def test_without_auto_nothing_is_searched(tmp_path, monkeypatch):
    monkeypatch.setattr(autocontext, "select", lambda *a, **k: pytest.fail("searched without auto"))
    assert assemble(None, 16000, root=tmp_path, reviewed=_FIND_USER_DIFF) == (None, {})


def test_the_selection_does_not_depend_on_the_hash_seed(tmp_path):
    """
    The order of names decides which a capped file keeps, and the selection is part of the review's
    cache key. Collected in sets, the same diff chose different excerpts from run to run.
    """
    import os
    import sys

    edited = [f"step_{i}" for i in range(6)]
    module = "".join(f"def {name}(x):\n    return x + {i}\n\n\n" for i, name in enumerate(edited))
    filler = "".join(f"def pad_{i}():\n    return {i}\n\n\n" for i in range(30))
    callers = "from app.steps import " + ", ".join(edited) + "\n\n\n" + filler + "".join(
        f"def call_{name}():\n    return {name}(1)\n\n\n" + filler for name in edited
    )
    root = _repo(tmp_path, {"app/steps.py": module.replace("x + 0", "x - 0"), "app/flow.py": callers})
    body = "".join(f" def {name}(x):\n-    return x + {i}\n+    return x - {i}\n \n \n" for i, name in enumerate(edited))
    diff = f"--- a/app/steps.py\n+++ b/app/steps.py\n@@ -1,{len(edited) * 4} +1,{len(edited) * 4} @@\n{body}"
    module_after = "".join(f"def {name}(x):\n    return x - {i}\n\n\n" for i, name in enumerate(edited))
    (root / "app/steps.py").write_text(module_after)
    script = (
        "import sys; from pathlib import Path; from fourtake.autocontext import select; "
        "print([p.spec for p in select(sys.stdin.read(), Path(sys.argv[1]))])"
    )
    runs = {
        subprocess.run(
            [sys.executable, "-c", script, str(root)], input=diff, capture_output=True, text=True,
            env={**os.environ, "PYTHONHASHSEED": str(seed)}, check=True,
        ).stdout
        for seed in range(4)
    }
    assert len(runs) == 1, runs
    assert "app/flow.py" in runs.pop()
