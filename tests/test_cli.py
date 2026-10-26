import io
from unittest.mock import MagicMock, patch

import pytest

from fourtake import cli
from fourtake.cli import main, read_stdin
from fourtake.engine import CouncilResult


def test_read_stdin_isatty():
    with patch("sys.stdin.isatty", return_value=True):
        with pytest.raises(SystemExit) as exc_info:
            read_stdin()
        assert exc_info.value.code == 1


def test_read_stdin_success():
    mock_stdin = MagicMock()
    mock_stdin.isatty.return_value = False
    mock_stdin.buffer.read.return_value = b"def foo(): pass"
    with patch("sys.stdin", mock_stdin):
        data = read_stdin()
        assert data == "def foo(): pass"


def test_main_version():
    with patch("sys.argv", ["4take", "--version"]):
        with patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0
            assert "4take" in mock_stdout.getvalue()


def test_main_empty_stdin():
    with patch("sys.argv", ["4take"]), patch("fourtake.cli.read_stdin", return_value="   "):
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 1


def test_main_success():
    with patch("sys.argv", ["4take", "--quiet"]):
        with patch("fourtake.cli.read_stdin", return_value="print('hello')"):
            with patch("fourtake.cli.run_council") as mock_run:
                mock_run.return_value = CouncilResult(
                    responses=[], errors=[], warning=None, error=None, total_latency_ms=10
                )
                with patch("fourtake.cli.output_result", return_value=0) as mock_output:
                    with pytest.raises(SystemExit) as exc_info:
                        main()
                    assert exc_info.value.code == 0
                    mock_output.assert_called_once()


def test_main_keyboard_interrupt():
    with patch("sys.argv", ["4take"]), patch("fourtake.cli.read_stdin", side_effect=KeyboardInterrupt):
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 130


def test_main_clear_cache():
    with (
        patch("sys.stdin.isatty", return_value=False),
        patch("sys.stdin.read", return_value=""),
        patch("sys.argv", ["4take", "--clear-cache"]),
        patch("fourtake.cache.clear_cache", return_value=5),
    ):
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 0


def test_main_generic_exception():
    with patch("sys.argv", ["4take"]):
        with patch("fourtake.cli.read_stdin", side_effect=Exception("Test Error")):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 1


def test_init_config_creates_zero_cost_template(tmp_path):
    import json

    from fourtake.cli import init_config

    with patch("pathlib.Path.home", return_value=tmp_path):
        with pytest.raises(SystemExit) as exc_info:
            init_config()
        assert exc_info.value.code == 0

        cfg_file = tmp_path / ".config" / "4take" / "config.json"
        assert cfg_file.exists()
        with open(cfg_file) as f:
            cfg = json.load(f)
        assert cfg["default_models"] == ["auto"]
        assert "zero" in cfg["//notice"].lower()
        # Verify no paid models leaked into template
        for m in cfg.get("default_models", []):
            assert "gpt-4" not in m
            assert "sonnet" not in m


def test_init_config_already_exists(tmp_path):
    from fourtake.cli import init_config

    with patch("pathlib.Path.home", return_value=tmp_path):
        cfg_file = tmp_path / ".config" / "4take" / "config.json"
        cfg_file.parent.mkdir(parents=True, exist_ok=True)
        cfg_file.write_text("{}")
        with pytest.raises(SystemExit) as exc:
            init_config()
        assert exc.value.code == 1


def test_main_init_config_flag(tmp_path):
    with patch("pathlib.Path.home", return_value=tmp_path), patch("sys.argv", ["4take", "--init-config"]):
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 0


def test_main_auto_discovery_fallback():
    with (
        patch("sys.argv", ["4take", "--quiet"]),
        patch("fourtake.cli.read_stdin", return_value="print('test')"),
        patch("fourtake.discovery.get_best_free_models", return_value=[]),
        patch("fourtake.cli.run_council", return_value=CouncilResult([], [], None, None, 0)),
        patch("fourtake.cli.output_result", return_value=0),
    ):
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 0


def test_main_configured_models_invalid_exit():
    with (
        patch("sys.argv", ["4take", "--models=openrouter/paid-model", "--quiet"]),
        patch("fourtake.cli.read_stdin", return_value="print('test')"),
        patch("fourtake.discovery.validate_configured_models", return_value=[]),
    ):
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 1


def test_main_token_limit_exceeded_all():
    with (
        patch("sys.argv", ["4take", "--quiet"]),
        patch("fourtake.cli.read_stdin", return_value="print('large code')"),
        patch("fourtake.cli.estimate_tokens", return_value=999999),
        patch("fourtake.discovery.get_best_free_models", return_value=["openrouter/m1:free"]),
    ):
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 1


def test_main_token_limit_pruning_partial():
    with (
        patch("sys.argv", ["4take", "--quiet"]),
        patch("fourtake.cli.read_stdin", return_value="print('medium code')"),
        patch("fourtake.cli.estimate_tokens", return_value=20000),
        patch(
            "fourtake.discovery.get_best_free_models",
            return_value=["openrouter/m1:free", "openrouter/m2:free"],
        ),
        patch("fourtake.cli.load_config") as mock_load,
        patch("fourtake.cli.run_council", return_value=CouncilResult([], [], None, None, 0)) as mock_run,
        patch("fourtake.cli.output_result", return_value=0),
    ):
        from fourtake.config import FourtakeConfig, ProviderConfig

        mock_load.return_value = FourtakeConfig(
            # A key, because the run now stops before dispatch without one, and this test is
            # about pruning models by context rather than about credentials.
            providers={"openrouter": ProviderConfig(url="https://x", api_key="k")},
            default_models=["auto"],
            rpm_limit=10,
            min_responses=1,
            timeout=10,
            provider_concurrency={},
            model_token_limits={"openrouter/m1:free": 10000, "openrouter/m2:free": 50000},
        )
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 0
        # openrouter/m1:free should be pruned because 20000 > 10000
        assert mock_run.call_args.kwargs["model_pool"] == ["openrouter/m2:free"]


def test_read_stdin_truncation():
    mock_stdin = MagicMock()
    mock_stdin.isatty.return_value = False
    mock_stdin.buffer.read.return_value = b"x" * (600 * 1024)
    with patch("sys.stdin", mock_stdin):
        data = read_stdin()
        assert len(data) == 512 * 1024


def test_main_configured_models_success():
    with (
        patch("sys.argv", ["4take", "--models=openrouter/m1:free", "--quiet"]),
        patch("fourtake.cli.read_stdin", return_value="print('test')"),
        patch("fourtake.discovery.validate_configured_models", return_value=["openrouter/m1:free"]),
        patch("fourtake.cli.run_council", return_value=CouncilResult([], [], None, None, 0)) as mock_run,
        patch("fourtake.cli.output_result", return_value=0),
    ):
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 0
        assert mock_run.call_args.kwargs["model_pool"] == ["openrouter/m1:free"]


def test_cli_dunder_main():
    import runpy

    with patch("sys.argv", ["4take", "--version"]), pytest.raises(SystemExit) as exc:
        runpy.run_module("fourtake.cli", run_name="__main__")
    assert exc.value.code == 0


# --------------------------------------------------------------------------------------
# S4.1 / S4.4 — user-visible paths added in the safety sprint
# --------------------------------------------------------------------------------------


def test_cli_reports_how_many_secrets_it_redacted(caplog):
    """A silent redaction that eats real code gives the user no way to notice."""
    from fourtake.engine import CouncilResult

    empty = CouncilResult(responses=[], errors=[], warning=None, error=None, total_latency_ms=1)
    argv = ["4take", "--accept-risk"]  # --quiet would suppress INFO, as it should
    with (
        patch("sys.argv", argv),
        patch("fourtake.cli.read_stdin", return_value="DB_PASSWORD=hunter2\nghp_" + "a" * 36),
        patch("fourtake.cli.run_council", return_value=empty),
        patch("fourtake.cli.output_result", return_value=0),
        patch("fourtake.discovery.get_best_free_models", return_value=["openrouter/m:free"]),
    ):
        with caplog.at_level("INFO"):
            with pytest.raises(SystemExit):
                main()

    assert "Redacted 2 secret(s)" in caplog.text


def test_cli_show_redactions_names_the_rules(caplog):
    from fourtake.engine import CouncilResult

    empty = CouncilResult(responses=[], errors=[], warning=None, error=None, total_latency_ms=1)
    argv = ["4take", "--accept-risk", "--show-redactions"]
    with (
        patch("sys.argv", argv),
        patch("fourtake.cli.read_stdin", return_value="DB_PASSWORD=hunter2"),
        patch("fourtake.cli.run_council", return_value=empty),
        patch("fourtake.cli.output_result", return_value=0),
        patch("fourtake.discovery.get_best_free_models", return_value=["openrouter/m:free"]),
    ):
        with caplog.at_level("INFO"):
            with pytest.raises(SystemExit):
                main()

    assert "bare-assignment x1" in caplog.text


def test_cli_config_error_names_the_file_and_exits_1(caplog):
    """A bad config used to surface as a TypeError from deep inside the rate limiter."""
    from fourtake.config import ConfigError

    with (
        patch("sys.argv", ["4take"]),
        patch("fourtake.cli.read_stdin", return_value="print(1)"),
        patch(
            "fourtake.cli.load_config",
            side_effect=ConfigError("/tmp/config.json: 'rpm_limit' must be greater than 0, got 0"),
        ),
    ):
        with caplog.at_level("ERROR"):
            with pytest.raises(SystemExit) as exc:
                main()

    assert exc.value.code == 1
    assert "rpm_limit" in caplog.text
    assert "config.json" in caplog.text


# --------------------------------------------------------------------------------------
# A file with one bad byte is still a file worth reviewing
# --------------------------------------------------------------------------------------


def test_undecodable_input_is_reviewed_rather_than_fatal():
    """
    `sys.stdin.read()` decodes with surrogateescape, so a stray byte survived the read and
    killed the run much later with "utf-8 codec can't encode ... surrogates not allowed" and
    no review at all. Piping a latin-1 source file did this.
    """
    mock_stdin = MagicMock()
    mock_stdin.isatty.return_value = False
    mock_stdin.buffer.read.return_value = b'def f():\n    return b"\xff\xfe\x80"\n'

    with patch("sys.stdin", mock_stdin):
        data = read_stdin()

    assert "def f():" in data
    data.encode("utf-8")  # must not raise anywhere downstream
    assert "�" in data, "the undecodable bytes are replaced, not dropped silently"


def test_the_size_cap_is_measured_in_bytes():
    """The warning says 512 KB; a multi-byte character must not let more through."""
    mock_stdin = MagicMock()
    mock_stdin.isatty.return_value = False
    mock_stdin.buffer.read.return_value = "я".encode() * (400 * 1024)

    with patch("sys.stdin", mock_stdin):
        data = read_stdin()

    assert len(data.encode("utf-8")) <= 512 * 1024


# --------------------------------------------------------------------------------------
# The config file is where the key lives, and where the first run goes wrong
# --------------------------------------------------------------------------------------


def test_init_config_does_not_leave_the_key_world_readable(monkeypatch, tmp_path):
    """
    `--init-config` writes the file the docs tell users to paste their API key into. The
    process umask left it 0644 inside a 0755 directory, next to a cache that had been
    deliberately tightened to 0600.
    """
    from pathlib import Path

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    with pytest.raises(SystemExit):
        cli.init_config()

    config = tmp_path / ".config" / "4take" / "config.json"
    assert oct(config.stat().st_mode & 0o777) == "0o600"
    assert oct(config.parent.stat().st_mode & 0o777) == "0o700"


def test_a_pre_existing_loose_config_is_tightened(monkeypatch, tmp_path):
    """`mkdir(mode=...)` applies only on creation, so an old directory keeps its old mode."""
    from pathlib import Path

    (tmp_path / ".config" / "4take").mkdir(parents=True)
    (tmp_path / ".config" / "4take").chmod(0o755)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

    with pytest.raises(SystemExit):
        cli.init_config()

    assert oct((tmp_path / ".config" / "4take").stat().st_mode & 0o777) == "0o700"


@pytest.mark.parametrize("key", [None, "", "   ", cli.CONFIG_KEY_PLACEHOLDER])
def test_a_missing_key_fails_before_a_single_request(key):
    """
    Measured before this check existed: 52 seconds and twelve requests against the shared
    rate limit, to report "Invalid API key" — which is true of the empty string and useless
    to someone who never set one.
    """
    from fourtake.config import FourtakeConfig, ProviderConfig

    config = FourtakeConfig(
        providers={"openrouter": ProviderConfig(url="https://x", api_key=key)},
        default_models=["auto"],
        rpm_limit=10,
        min_responses=4,
        timeout=30,
        provider_concurrency={},
    )
    with pytest.raises(SystemExit) as exit_info:
        cli.require_api_key(config)
    assert exit_info.value.code == 1


def test_a_real_key_passes_the_pre_flight_check():
    from fourtake.config import FourtakeConfig, ProviderConfig

    config = FourtakeConfig(
        providers={"openrouter": ProviderConfig(url="https://x", api_key="sk-or-v1-real")},
        default_models=["auto"],
        rpm_limit=10,
        min_responses=4,
        timeout=30,
        provider_concurrency={},
    )
    cli.require_api_key(config)  # must not raise


# --------------------------------------------------------------------------------------
# A non-code input is refused before any request goes out
# --------------------------------------------------------------------------------------

_PROSE = (
    "We want to move notification delivery out of the request path. Today the handler\n"
    "blocks on the provider call, so a slow provider becomes a slow API for everyone.\n"
    "Should the worker be a separate deployment or a thread in the API process?\n"
)


def test_main_refuses_prose_without_dispatching():
    """
    Four requests spent to produce four copies of NO_ISSUES_FOUND is a worse answer than
    saying the input is not code, so the council must not be reached at all.
    """
    with (
        patch("sys.argv", ["4take", "--quiet"]),
        patch("fourtake.cli.require_api_key"),
        patch("fourtake.cli.read_stdin", return_value=_PROSE),
        patch("fourtake.cli.run_council") as mock_run,
    ):
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 1
        mock_run.assert_not_called()


def test_force_review_sends_prose_anyway():
    with (
        patch("sys.argv", ["4take", "--quiet", "--force-review"]),
        patch("fourtake.cli.require_api_key"),
        patch("fourtake.cli.read_stdin", return_value=_PROSE),
        patch("fourtake.cli.run_council") as mock_run,
        patch("fourtake.cli.output_result", return_value=0),
    ):
        mock_run.return_value = CouncilResult(
            responses=[], errors=[], warning=None, error=None, total_latency_ms=10
        )
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 0
        mock_run.assert_called_once()


def test_code_is_not_refused():
    code = (
        "def fan_out(urls):\n"
        "    results = gather(*[fetch(u) for u in urls], return_exceptions=True)\n"
        "    return [r for r in results if r]\n"
    )
    with (
        patch("sys.argv", ["4take", "--quiet"]),
        patch("fourtake.cli.require_api_key"),
        patch("fourtake.cli.read_stdin", return_value=code),
        patch("fourtake.cli.run_council") as mock_run,
        patch("fourtake.cli.output_result", return_value=0),
    ):
        mock_run.return_value = CouncilResult(
            responses=[], errors=[], warning=None, error=None, total_latency_ms=10
        )
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 0
        mock_run.assert_called_once()


# --------------------------------------------------------------------------------------
# Reference context from the command line
# --------------------------------------------------------------------------------------

_CHANGED = "def rename(uid):\n    user = find_user(uid)\n    return user.email\n"


def _run_main(argv, stdin=_CHANGED):
    with (
        patch("sys.argv", argv),
        patch("fourtake.cli.read_stdin", return_value=stdin),
        patch("fourtake.discovery.get_best_free_models", return_value=["openrouter/m:free"]),
        patch("fourtake.cli.run_council") as mock_run,
        patch("fourtake.cli.output_result", return_value=0),
    ):
        mock_run.return_value = CouncilResult([], [], None, None, 1)
        with pytest.raises(SystemExit) as exc:
            main()
    return exc.value.code, mock_run


def test_context_files_reach_the_council(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "users.py").write_text("def find_user(uid):\n    return None\n")
    code, mock_run = _run_main(["4take", "--quiet", "--context-file", "users.py"])
    assert code == 0
    context = mock_run.call_args.kwargs["context"]
    assert [p.label for p in context.pieces] == ["users.py"]
    assert "find_user" in context.block()


def test_marking_reads_the_diff_as_piped_and_the_council_gets_it_redacted(tmp_path, monkeypatch):
    """
    Marking reads only diff headers and sends nothing, so it takes the input as piped: a redaction
    that rewrote a header could otherwise unmark the file. Raised by the council reviewing this.
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / "users.py").write_text("def find_user(uid):\n    return None\n")
    diff = (
        "--- a/users.py\n+++ b/users.py\n@@ -1,2 +1,3 @@\n def find_user(uid):\n"
        '+    API_KEY = "sk-abcdefghijklmnopqrstuvwx"\n     return None\n'
    )
    with patch("fourtake.cli.assemble", wraps=cli.assemble) as spy:
        code, mock_run = _run_main(["4take", "--quiet", "--context-file", "users.py"], stdin=diff)
    assert code == 0
    assert spy.call_args.kwargs["reviewed"] == diff
    assert "sk-abc" not in mock_run.call_args.args[1]
    (piece,) = mock_run.call_args.kwargs["context"].pieces
    assert piece.changed


def test_without_the_flag_there_is_no_context(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    code, mock_run = _run_main(["4take", "--quiet"])
    assert code == 0
    assert mock_run.call_args.kwargs["context"] is None


def test_an_unreadable_context_file_stops_the_run_before_dispatch(tmp_path, monkeypatch, caplog):
    monkeypatch.chdir(tmp_path)
    with caplog.at_level("ERROR"):
        code, mock_run = _run_main(["4take", "--quiet", "--context-file", "missing.py"])
    assert code == 1
    mock_run.assert_not_called()
    assert "missing.py" in caplog.text


def test_context_over_the_budget_is_named_and_left_out(tmp_path, monkeypatch, caplog):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "big.py").write_text("x = 1\n" * 2000)
    (tmp_path / "small.py").write_text("def find_user(uid):\n    return None\n")
    argv = [
        "4take",
        "--quiet",
        "--context-budget",
        "60",
        "--context-file",
        "big.py",
        "--context-file",
        "small.py",
    ]
    with caplog.at_level("WARNING"):
        code, mock_run = _run_main(argv)
    assert code == 0
    assert "big.py" in caplog.text
    context = mock_run.call_args.kwargs["context"]
    assert context.dropped == ("big.py",)
    assert [p.label for p in context.pieces] == ["small.py"]


def test_a_context_budget_of_zero_is_refused_by_the_parser():
    with patch("sys.argv", ["4take", "--context-budget", "0"]):
        with pytest.raises(SystemExit) as exc:
            main()
    assert exc.value.code == 2


def test_dry_run_prints_what_would_be_sent_and_contacts_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "users.py").write_text(
        'API_TOKEN = "ghp_' + "a" * 36 + '"\n\ndef find_user(uid):\n    return None\n'
    )
    with (
        patch("sys.argv", ["4take", "--dry-run", "--context-file", "users.py"]),
        patch("fourtake.cli.read_stdin", return_value=_CHANGED),
        patch("fourtake.cli.require_api_key") as mock_key,
        patch("fourtake.cli.choose_models") as mock_choose,
        patch("fourtake.cli.run_council") as mock_run,
    ):
        with pytest.raises(SystemExit) as exc:
            main()
    assert exc.value.code == 0
    # Nothing is sent, so nothing is authenticated or discovered.
    mock_key.assert_not_called()
    mock_choose.assert_not_called()
    mock_run.assert_not_called()
    out = capsys.readouterr().out
    assert "=== system ===" in out and "=== user ===" in out
    assert "--- users.py ---" in out
    assert "NOT under review" in out
    # Redacted exactly as it would be for a real council.
    assert "ghp_aaaa" not in out
    user = out.split("=== user ===", 1)[1]
    assert user.index("<reference_context_") < user.index("<code_to_review_")


def test_dry_run_as_json_reports_the_context(tmp_path, monkeypatch, capsys):
    import json

    monkeypatch.chdir(tmp_path)
    (tmp_path / "users.py").write_text("def find_user(uid):\n    return None\n")
    with (
        patch("sys.argv", ["4take", "--dry-run", "--format", "json", "--context-file", "users.py:1-1"]),
        patch("fourtake.cli.read_stdin", return_value=_CHANGED),
    ):
        with pytest.raises(SystemExit) as exc:
            main()
    assert exc.value.code == 0
    data = json.loads(capsys.readouterr().out)
    assert data["context"]["files"] == ["users.py (lines 1-1)"]
    assert data["review_tokens"] > 0
    assert "reference_context_" in data["system"]


def test_auto_context_chooses_the_callers_of_a_changed_function(tmp_path, monkeypatch, capsys):
    import json

    monkeypatch.chdir(tmp_path)
    (tmp_path / "users.py").write_text("def find_user(uid):\n    return None\n")
    (tmp_path / "api.py").write_text(
        "from users import find_user\n\ndef show(uid):\n    return find_user(uid).email\n"
    )
    diff = (
        "--- a/users.py\n+++ b/users.py\n@@ -1,2 +1,2 @@\n def find_user(uid):\n"
        "-    raise LookupError(uid)\n+    return None\n"
    )
    with (
        patch("sys.argv", ["4take", "--dry-run", "--format", "json", "--auto-context"]),
        patch("fourtake.cli.read_stdin", return_value=diff),
    ):
        with pytest.raises(SystemExit) as exc:
            main()
    assert exc.value.code == 0
    context = json.loads(capsys.readouterr().out)["context"]
    assert context["files"] == ["api.py"]
    assert context["auto"] == {"api.py": "uses find_user"}


def test_without_auto_context_nothing_is_chosen(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "api.py").write_text("from users import find_user\n\nx = find_user(1)\n")
    with patch("fourtake.cli.assemble", wraps=cli.assemble) as spy:
        code, mock_run = _run_main(["4take", "--quiet"])
    assert code == 0
    assert spy.call_args.kwargs["auto"] is False
    assert mock_run.call_args.kwargs["context"] is None


@pytest.mark.parametrize(
    "argv",
    [
        ["--min-responses", "0"],
        ["--min-responses=-1"],
        ["--timeout", "0"],
        ["--timeout=-5"],
        ["--per-model-timeout", "0"],
        ["--per-model-timeout=-1.5"],
    ],
    ids=lambda a: " ".join(a),
)
def test_counts_and_timeouts_must_be_positive(argv):
    """
    `--min-responses 0` counted a council with no review as a success, and a negative timeout set
    the deadline in the past. The config file already refused both; the flags did not.
    """
    with patch("sys.argv", ["4take", *argv]):
        with pytest.raises(SystemExit) as exc:
            main()
    assert exc.value.code == 2
