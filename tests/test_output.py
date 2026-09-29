import io
import json
import sys
from unittest.mock import MagicMock, patch

from fourtake.client import ModelResponse
from fourtake.config import FourtakeConfig
from fourtake.engine import CouncilResult
from fourtake.output import output_result


def test_output_result_json():
    config = FourtakeConfig(
        providers={},
        default_models=["A"],
        rpm_limit=10,
        min_responses=1,
        timeout=10,
        provider_concurrency={},
        model_token_limits={}
    )
    result = CouncilResult(responses=[], errors=[], warning=None, error="fail", total_latency_ms=10)

    with patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
        code = output_result(result, None, config, "test", output_format="json")
        assert code == 1
        output = mock_stdout.getvalue()
        assert '"error": "fail"' in output
        assert json.loads(output)


def test_output_result_markdown():
    config = FourtakeConfig(
        providers={},
        default_models=["A"],
        rpm_limit=10,
        min_responses=1,
        timeout=10,
        provider_concurrency={},
        model_token_limits={}
    )
    result = CouncilResult(
        responses=[
            ModelResponse("modelA", "hello world", None, 100),
            ModelResponse("modelNone", None, "Empty", 100)
        ],
        errors=[ModelResponse("modelB", None, "failed to connect", 50)],
        warning="Some warning",
        error=None,
        total_latency_ms=150
    )

    with patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
        code = output_result(result, None, config, "test", output_format="text")
        assert code == 0
        output = mock_stdout.getvalue()
        assert "# 4take Council Review" in output
        assert "> **Warning:** Some warning" in output
        assert "## Model: `modelA`" in output
        assert "hello world" in output
        assert "[Error from model modelNone]" in output
        assert "## Errors" in output
        assert "- **`modelB`**: failed to connect" in output


def test_output_result_to_file(tmp_path):
    config = FourtakeConfig(
        providers={},
        default_models=["A"],
        rpm_limit=10,
        min_responses=1,
        timeout=10,
        provider_concurrency={},
        model_token_limits={}
    )
    result = CouncilResult(
        responses=[ModelResponse("modelA", "File content test", None, 100)],
        errors=[],
        warning=None,
        error=None,
        total_latency_ms=100
    )
    out_file = tmp_path / "subdir" / "report.md"
    code = output_result(result, str(out_file), config, "code", output_format="text")
    assert code == 0
    assert out_file.exists()
    assert "File content test" in out_file.read_text(encoding="utf-8")


def test_output_result_win32_encoding():
    config = FourtakeConfig(
        providers={},
        default_models=["A"],
        rpm_limit=10,
        min_responses=1,
        timeout=10,
        provider_concurrency={},
        model_token_limits={}
    )
    result = CouncilResult(responses=[], errors=[], warning=None, error="err", total_latency_ms=0)

    mock_stdout = MagicMock()
    mock_stdout.reconfigure.side_effect = Exception("Reconfigure not supported")
    with patch.object(sys, "platform", "win32"), patch.object(sys, "stdout", mock_stdout):
        code = output_result(result, None, config, "test", output_format="json")
        assert code == 1
        mock_stdout.reconfigure.assert_called_with(encoding="utf-8")


def _plain_config():
    return FourtakeConfig(providers={}, default_models=["A"], rpm_limit=10, min_responses=1,
                          timeout=10, provider_concurrency={}, model_token_limits={})


def _with_context():
    from fourtake.context import ContextPiece, fit_to_budget

    fitted = fit_to_budget([ContextPiece("users.py", "def find_user(uid):\n    return None\n"),
                            ContextPiece("big.py", "x = 1\n" * 3000)], budget_tokens=200)
    return CouncilResult(responses=[ModelResponse("modelA", "NO_ISSUES_FOUND: ok", None, 10)], errors=[],
                         warning=None, error=None, total_latency_ms=10, context=fitted)


def test_json_summary_reports_the_context_and_what_was_left_out():
    with patch("sys.stdout", new_callable=io.StringIO) as out:
        output_result(_with_context(), None, _plain_config(), "code", output_format="json")
    context = json.loads(out.getvalue())["summary"]["context"]
    assert context["files"] == ["users.py"]
    assert context["dropped"] == ["big.py"]
    assert context["tokens"] > 0


def test_json_summary_context_is_null_without_context():
    result = CouncilResult(responses=[], errors=[], warning=None, error="fail", total_latency_ms=1)
    with patch("sys.stdout", new_callable=io.StringIO) as out:
        output_result(result, None, _plain_config(), "code", output_format="json")
    assert json.loads(out.getvalue())["summary"]["context"] is None


def test_markdown_names_the_context_and_what_was_left_out():
    from fourtake.output import format_markdown

    text = format_markdown(_with_context(), _plain_config(), "code")
    assert "**Reference context:** `users.py`" in text
    assert "**Not sent, over the context budget:** `big.py`" in text


def test_markdown_says_why_auto_context_chose_a_piece():
    from fourtake.context import ContextPiece, fit_to_budget
    from fourtake.output import format_markdown

    fitted = fit_to_budget([ContextPiece("api.py", "x = find_user(1)\n", why="uses find_user")], 200)
    result = CouncilResult(responses=[ModelResponse("modelA", "NO_ISSUES_FOUND: ok", None, 10)], errors=[],
                           warning=None, error=None, total_latency_ms=10, context=fitted)
    assert "`api.py` (uses find_user)" in format_markdown(result, _plain_config(), "code")


def test_json_reports_the_models_the_council_asked_not_the_configured_list():
    config = FourtakeConfig(providers={}, default_models=["auto"], rpm_limit=10, min_responses=1,
                            timeout=10, provider_concurrency={}, model_token_limits={})
    result = CouncilResult(responses=[ModelResponse("modelA", "NO_ISSUES_FOUND: ok", None, 10)], errors=[],
                           warning=None, error=None, total_latency_ms=10, models_asked=6)
    with patch("sys.stdout", new_callable=io.StringIO) as out:
        output_result(result, None, config, "code", output_format="json")
    assert json.loads(out.getvalue())["config"]["models_requested"] == 6
