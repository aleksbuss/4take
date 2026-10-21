"""
Reference context: what may be read, what is refused, and how it is fitted to a budget.

In the MCP server the paths come from an agent, and the contents go to third-party model
providers, so the refusals here are the line between "review this change" and "send my keys".
"""

from pathlib import Path

import pytest

from fourtake.context import (
    AFTER_CHANGE_NOTE,
    INLINE_LABEL,
    ContextError,
    ContextPiece,
    ReviewContext,
    assemble,
    changed_paths,
    default_root,
    fit_to_budget,
    parse_spec,
    read_context,
    redact,
)


def _write(root: Path, rel: str, text: str = "def f():\n    return 1\n") -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# --------------------------------------------------------------------------------------
# Specs
# --------------------------------------------------------------------------------------


def test_a_plain_path_has_no_range():
    assert parse_spec("src/app.py") == ("src/app.py", None, None)


def test_a_range_is_parsed_from_the_end_of_the_spec():
    assert parse_spec("src/app.py:10-40") == ("src/app.py", 10, 40)
    # A drive letter is not a range.
    assert parse_spec(r"C:\repo\app.py:3-4") == (r"C:\repo\app.py", 3, 4)


@pytest.mark.parametrize("spec", ["a.py:0-3", "a.py:5-4"])
def test_an_impossible_range_is_refused(spec):
    with pytest.raises(ContextError):
        parse_spec(spec)


# --------------------------------------------------------------------------------------
# Reading under the root
# --------------------------------------------------------------------------------------


def test_files_are_shown_relative_to_the_root(tmp_path):
    _write(tmp_path, "src/users.py", "def get_user(uid):\n    return None\n")
    (piece,) = read_context(["src/users.py"], tmp_path)
    assert piece.path == "src/users.py"
    assert "get_user" in piece.content
    assert piece.label == "src/users.py"


def test_an_absolute_path_inside_the_root_is_accepted(tmp_path):
    path = _write(tmp_path, "pkg/mod.py")
    (piece,) = read_context([str(path)], tmp_path)
    assert piece.path == "pkg/mod.py"


def test_a_path_outside_the_root_is_refused(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    _write(tmp_path, "outside.py")
    with pytest.raises(ContextError, match="outside the project root"):
        read_context(["../outside.py"], root)


def test_a_symlink_that_leads_out_of_the_root_is_refused(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    secret = _write(tmp_path, "elsewhere/notes.py")
    (root / "link.py").symlink_to(secret)
    with pytest.raises(ContextError, match="outside the project root"):
        read_context(["link.py"], root)


@pytest.mark.parametrize(
    "rel",
    [
        ".env",
        ".env.local",
        ".env-local",
        ".envrc",
        "deploy/prod.env",
        "config/id_rsa",
        "certs/server.pem",
        "tls.key",
        ".git/config",
        ".GIT/config",
        ".ssh/known_hosts",
        ".npmrc",
        ".pgpass",
        ".git-credentials",
        "infra/terraform.tfstate",
        "infra/terraform.tfstate.backup",
        ".kube/config",
        ".docker/config.json",
    ],
)
def test_credential_files_and_repository_internals_are_never_read(tmp_path, rel):
    _write(tmp_path, rel, "SECRET=1\n")
    with pytest.raises(ContextError, match="never sent"):
        read_context([rel], tmp_path)


@pytest.mark.parametrize("rel", ["environment.py", "src/env.py", "envelope.js", "keys.py"])
def test_source_files_named_like_credentials_are_still_read(tmp_path, rel):
    _write(tmp_path, rel, "x = 1\n")
    (piece,) = read_context([rel], tmp_path)
    assert piece.content == "x = 1\n"


def test_a_directory_or_a_missing_file_is_named_in_the_error(tmp_path):
    (tmp_path / "pkg").mkdir()
    with pytest.raises(ContextError, match="pkg: not a file"):
        read_context(["pkg"], tmp_path)
    with pytest.raises(ContextError, match=r"nope\.py: not a file"):
        read_context(["nope.py"], tmp_path)


def test_a_binary_file_is_refused(tmp_path):
    (tmp_path / "blob.py").write_bytes(b"abc\x00def")
    with pytest.raises(ContextError, match="binary"):
        read_context(["blob.py"], tmp_path)


def test_an_oversized_whole_file_points_at_a_line_range(tmp_path, monkeypatch):
    monkeypatch.setattr("fourtake.context.MAX_CONTEXT_FILE_BYTES", 64)
    _write(tmp_path, "big.py", "x = 1\n" * 50)
    with pytest.raises(ContextError, match="line range"):
        read_context(["big.py"], tmp_path)
    # The same file as a short range fits.
    (piece,) = read_context(["big.py:1-3"], tmp_path)
    assert piece.content == "x = 1\nx = 1\nx = 1"


def test_a_range_is_cut_to_its_lines_and_clamped_to_the_file(tmp_path):
    _write(tmp_path, "m.py", "a\nb\nc\nd\n")
    (piece,) = read_context(["m.py:2-99"], tmp_path)
    assert (piece.content, piece.start, piece.end) == ("b\nc\nd", 2, 4)
    assert piece.label == "m.py (lines 2-4)"
    with pytest.raises(ContextError, match="only 4 lines"):
        read_context(["m.py:9-12"], tmp_path)


def test_lines_are_numbered_as_an_editor_numbers_them(tmp_path):
    # A form feed ends a line for str.splitlines(), not for an editor, `grep -n` or a diff, so
    # the agent's line numbers pointed one line past what was sent.
    (tmp_path / "old.c").write_bytes(b"int a; \x0c/* page two */\nint b;\nint c;\n")
    (piece,) = read_context(["old.c:2-2"], tmp_path)
    assert piece.content == "int b;"
    with pytest.raises(ContextError, match="only 3 lines"):
        read_context(["old.c:4-4"], tmp_path)
    # Windows line endings number the same lines.
    (tmp_path / "win.py").write_bytes(b"a = 1\r\nb = 2\r\n")
    (piece,) = read_context(["win.py:2-2"], tmp_path)
    assert piece.content.rstrip() == "b = 2"


def test_the_same_file_named_twice_is_sent_once(tmp_path):
    path = _write(tmp_path, "m.py")
    pieces = read_context(["m.py", str(path), "./m.py"], tmp_path)
    assert len(pieces) == 1


def test_a_stray_byte_does_not_cost_the_file(tmp_path):
    (tmp_path / "latin.py").write_bytes(b"name = 'caf\xe9'\n")
    (piece,) = read_context(["latin.py"], tmp_path)
    assert piece.content.startswith("name = 'caf")


# --------------------------------------------------------------------------------------
# The root itself
# --------------------------------------------------------------------------------------


def test_the_environment_names_the_root(tmp_path):
    assert default_root({"FOURTAKE_CONTEXT_ROOT": str(tmp_path)}) == tmp_path.resolve()


def test_the_working_directory_is_the_root_when_it_is_a_project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert default_root({}) == tmp_path.resolve()


def test_the_home_directory_is_too_broad_to_be_a_root(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ContextError, match="FOURTAKE_CONTEXT_ROOT"):
        default_root({})


def test_the_filesystem_root_is_too_broad_to_be_a_root(monkeypatch):
    monkeypatch.chdir(Path("/"))
    with pytest.raises(ContextError, match="too broad"):
        default_root({})


# --------------------------------------------------------------------------------------
# Redaction and budget
# --------------------------------------------------------------------------------------


def test_context_is_redacted_like_the_reviewed_code():
    pieces = [ContextPiece("settings.py", 'API_KEY = "sk-abcdefghijklmnopqrstuvwxyz123456"\n')]
    cleaned, report = redact(pieces)
    assert "sk-abcdefghij" not in cleaned[0].content
    assert sum(report.values()) >= 1
    assert cleaned[0].path == "settings.py"


def test_pieces_are_kept_in_order_while_they_fit():
    small = ContextPiece("a.py", "x = 1\n")
    large = ContextPiece("b.py", "y = 2\n" * 400)
    later = ContextPiece("c.py", "z = 3\n")
    fitted = fit_to_budget([small, large, later], budget_tokens=100)
    assert [p.path for p in fitted.pieces] == ["a.py", "c.py"]
    # Dropped whole, never cut: a truncated file reads as a complete one.
    assert fitted.dropped == ("b.py",)
    assert 0 < fitted.tokens <= 100


def test_a_budget_nothing_fits_drops_everything_and_says_so():
    fitted = fit_to_budget([ContextPiece("a.py", "x = 1\n" * 50)], budget_tokens=5)
    assert fitted.pieces == ()
    assert fitted.dropped == ("a.py",)
    assert fitted.tokens == 0


def test_the_digest_changes_with_what_is_sent():
    one = ReviewContext((ContextPiece("a.py", "x = 1"),))
    two = ReviewContext((ContextPiece("a.py", "x = 2"),))
    renamed = ReviewContext((ContextPiece("b.py", "x = 1"),))
    assert len({one.digest, two.digest, renamed.digest}) == 3
    assert one.digest == ReviewContext((ContextPiece("a.py", "x = 1"),)).digest


def test_the_rendered_block_names_every_piece():
    block = ReviewContext((ContextPiece("a.py", "x = 1\n"), ContextPiece("b.py", "y", 3, 3))).render()
    assert "--- a.py ---\nx = 1\n" in block
    assert "--- b.py (lines 3-3) ---\ny\n" in block


# --------------------------------------------------------------------------------------
# assemble: the one path both front ends use
# --------------------------------------------------------------------------------------


def test_no_context_given_means_none_at_all(tmp_path):
    assert assemble([], budget_tokens=1000, root=tmp_path) == (None, {})
    assert assemble(None, budget_tokens=1000, inline="   ", root=tmp_path) == (None, {})


def test_inline_context_needs_no_root(monkeypatch):
    """An MCP server started from `/` cannot read paths, but must still accept pasted context."""
    monkeypatch.chdir(Path("/"))
    review, _ = assemble(None, budget_tokens=1000, inline="def helper():\n    pass\n")
    assert review is not None
    assert [p.label for p in review.pieces] == [INLINE_LABEL]


def test_paths_and_inline_are_read_redacted_and_fitted_together(tmp_path):
    _write(tmp_path, "svc.py", 'TOKEN = "ghp_abcdefghijklmnopqrstuvwxyz0123456789"\n')
    review, report = assemble(["svc.py"], budget_tokens=1000, inline="def g(): pass", root=tmp_path)
    assert review is not None
    assert [p.label for p in review.pieces] == ["svc.py", INLINE_LABEL]
    assert "ghp_abc" not in review.render()
    assert report


_DIFF = """\
diff --git a/cache/lru.py b/cache/lru.py
--- a/cache/lru.py
+++ b/cache/lru.py
@@ -24,3 +24,5 @@ class LRUCache:
+    def _evict_oldest(self):
+++ this added line starts with two pluses and is not a header
diff --git a/new.py b/new.py
--- /dev/null
+++ b/new.py
@@ -0,0 +1 @@
+x = 1
diff --git a/gone.py b/gone.py
--- a/gone.py
+++ /dev/null
"""


def test_the_files_a_diff_changes_are_read_from_its_headers():
    assert changed_paths(_DIFF) == {"cache/lru.py", "new.py"}
    assert changed_paths("def f():\n    return 1\n") == frozenset()


def test_a_context_file_the_diff_changes_is_marked_as_after_the_change(tmp_path):
    """
    Measured on the cross-file corpus: sent the file under review as context, unmarked, reviewers
    reported the changed method as defined twice, 2 to 5 times per recording.
    """
    _write(tmp_path, "cache/lru.py", "class LRUCache:\n    def _evict_oldest(self):\n        pass\n")
    _write(tmp_path, "cache/store.py")
    review, _ = assemble(["cache/lru.py", "cache/store.py"], 1000, root=tmp_path, reviewed=_DIFF)
    assert [p.changed for p in review.pieces] == [True, False]
    rendered = review.render()
    assert f"--- cache/lru.py — {AFTER_CHANGE_NOTE} ---" in rendered
    assert "--- cache/store.py ---" in rendered
    # Nothing is marked for code that is not a diff, and the mark is part of the cache identity.
    plain, _ = assemble(["cache/lru.py", "cache/store.py"], 1000, root=tmp_path, reviewed="x = 1\n")
    assert not any(p.changed for p in plain.pieces)
    assert plain.digest != review.digest
    # The summary lists files as they were named, mark or not.
    assert review.summary()["files"] == ["cache/lru.py", "cache/store.py"]


def test_redaction_can_be_turned_off_like_it_can_for_the_code(tmp_path):
    _write(tmp_path, "svc.py", 'TOKEN = "ghp_abcdefghijklmnopqrstuvwxyz0123456789"\n')
    review, report = assemble(["svc.py"], budget_tokens=1000, root=tmp_path, strip_secrets=False)
    assert review is not None and "ghp_abc" in review.render()
    assert report == {}
