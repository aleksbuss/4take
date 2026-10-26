"""
The three places a council's result is read agree, on councils that really ran.

Every other output test builds its CouncilResult by hand. These start from two councils recorded
live on OpenRouter's free models (tests/fixtures/councils): the text report, the JSON output and
the MCP payload are all produced from the same result, and must show the same entries, lose no
finding any model made, count agreement in distinct reviewers, flag a dispute exactly when the
reviewers rated a defect differently, and keep an answer held as a duplicate seat out of every
count. Offline: nothing is sent anywhere.
"""

import json
import pathlib

import pytest

from fourtake.client import ModelResponse
from fourtake.config import FourtakeConfig
from fourtake.engine import CouncilResult, distinct_seats
from fourtake.findings import build_consensus, parse_findings
from fourtake.mcp_server import serialise_result
from fourtake.output import format_markdown, output_result

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "councils"
RECORDED = ["accounts", "find_user", "self_review"]
SEVERITY_RANK = {"CRITICAL": 3, "WARNING": 2, "SUGGESTION": 1}


def _load(name: str) -> tuple[CouncilResult, str]:
    data = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    responses = [
        ModelResponse(
            model=r["model"],
            content=r["content"],
            error=None,
            latency_ms=r["latency_ms"],
            served_model=r["served_model"],
            served_provider=r["served_provider"],
            finish_reason=r["finish_reason"],
            truncated=r["truncated"],
            duplicate_of=r["duplicate_of"],
        )
        for r in data["responses"]
    ]
    errors = [
        ModelResponse(model=e["model"], content=None, error=e["error"], latency_ms=e["latency_ms"])
        for e in data["errors"]
    ]
    result = CouncilResult(
        responses=responses,
        errors=errors,
        warning=data["warning"],
        error=data["error"],
        total_latency_ms=data["total_latency_ms"],
    )
    return result, data["code"]


def _config() -> FourtakeConfig:
    return FourtakeConfig(
        providers={},
        default_models=["auto"],
        rpm_limit=10,
        min_responses=4,
        timeout=600,
        provider_concurrency={},
        model_token_limits={},
    )


@pytest.fixture(params=RECORDED)
def council(request):
    result, code = _load(request.param)
    return request.param, result, code


def _seated(result):
    return [r for r in result.responses if not r.duplicate_of]


def _held(result):
    return [r for r in result.responses if r.duplicate_of]


def _reviewer(r) -> str:
    return r.served_model or r.model


def _json_output(result, code, tmp_path):
    path = tmp_path / "council.json"
    output_result(result, str(path), _config(), code, output_format="json")
    return json.loads(path.read_text(encoding="utf-8"))


def _entry(c):
    """What every output must say about one consensus entry."""
    return (c.agreement, c.severity, c.symbol, c.title, c.disputed, tuple(sorted(c.models)))


def _entry_from_payload(c):
    return (c["agreement"], c["severity"], c["symbol"], c["title"], c["disputed"], tuple(sorted(c["models"])))


# --------------------------------------------------------------------------------------
# The recordings must contain what the checks below are about
# --------------------------------------------------------------------------------------


def test_the_recordings_cover_a_dispute_and_a_held_duplicate_and_failed_models():
    accounts, _ = _load("accounts")
    find_user, _ = _load("find_user")
    assert any(c.disputed for c in build_consensus(accounts.responses)), "no dispute to check"
    assert _held(find_user), "no duplicate seat to check"
    assert accounts.errors and find_user.errors, "no failed model to check"
    assert any(len(c.models) == 4 for c in build_consensus(accounts.responses)), "no unanimous entry"
    assert any(len(c.models) == 1 for c in build_consensus(accounts.responses)), "no lone finding"
    self_review, _ = _load("self_review")
    assert any(len(c.findings) > c.agreement for c in build_consensus(self_review.responses)), (
        "no entry where one reviewer filed the same defect twice"
    )


# --------------------------------------------------------------------------------------
# What each model said reaches the reader
# --------------------------------------------------------------------------------------


def test_every_models_whole_review_is_in_the_text_report_verbatim(council):
    _, result, code = council
    text = format_markdown(result, _config(), code)
    assert result.responses
    for r in result.responses:
        assert r.content.strip() in text, f"{r.model}'s review is cut or altered in the text report"


def test_every_model_has_its_own_section_and_every_failure_is_listed(council):
    _, result, code = council
    text = format_markdown(result, _config(), code)
    for r in result.responses:
        assert f"## Model: `{r.model}`" in text
    for e in result.errors:
        assert f"- **`{e.model}`**: {e.error}" in text


def test_a_held_duplicate_says_so_in_its_section(council):
    _, result, code = council
    text = format_markdown(result, _config(), code)
    for r in _held(result):
        assert f"**Duplicate seat** — `{r.duplicate_of}` already holds this seat" in text


def test_the_json_output_holds_every_review_identically(council, tmp_path):
    _, result, code = council
    payload = _json_output(result, code, tmp_path)
    by_model = {r["model"]: r for r in payload["responses"]}
    assert set(by_model) == {r.model for r in result.responses}
    for r in result.responses:
        got = by_model[r.model]
        assert got["content"] == r.content
        assert got["served_model"] == r.served_model
        assert got["duplicate_of"] == r.duplicate_of
    assert [e["model"] for e in payload["errors"]] == [e.model for e in result.errors]


# --------------------------------------------------------------------------------------
# Nothing a model found is lost, and nothing is counted twice
# --------------------------------------------------------------------------------------


def test_every_finding_of_every_seated_model_is_in_exactly_one_entry(council):
    _, result, _ = council
    clusters = build_consensus(result.responses)
    for r in _seated(result):
        reviewer = _reviewer(r)
        parsed = parse_findings(r.content or "", reviewer)
        grouped = [f for c in clusters for f in c.findings if f.model == reviewer]
        assert len(grouped) == len(parsed), f"{reviewer}: {len(parsed)} findings made, {len(grouped)} shown"
        assert sorted(f.raw for f in grouped) == sorted(f.raw for f in parsed)
    total = sum(len(parse_findings(r.content or "", _reviewer(r))) for r in _seated(result))
    assert sum(len(c.findings) for c in clusters) == total


def test_agreement_counts_distinct_reviewers_and_never_exceeds_the_seats(council):
    _, result, _ = council
    seats = distinct_seats(result.responses)
    for c in build_consensus(result.responses):
        assert c.agreement == len(set(c.models))
        assert 1 <= c.agreement <= seats


def test_an_answer_held_as_a_duplicate_counts_for_no_entry(council):
    _, result, _ = council
    held_seat = {r.duplicate_of for r in _held(result)}
    seats = {_reviewer(r) for r in _seated(result)}
    for c in build_consensus(result.responses):
        assert set(c.models) <= seats
    # the seat it shadows is still counted once, through the answer that took it
    for seat in held_seat:
        assert seat in seats
    assert distinct_seats(result.responses) == len(seats)


# --------------------------------------------------------------------------------------
# A dispute is shown exactly when there was one
# --------------------------------------------------------------------------------------


def test_an_entry_is_disputed_exactly_when_the_reviewers_rated_it_differently(council):
    _, result, _ = council
    for c in build_consensus(result.responses):
        assert c.disputed == (len(c.severities) > 1)
        assert sum(c.severities.values()) == len({f.model for f in c.findings})


def test_an_entry_takes_the_worst_severity_any_reviewer_gave(council):
    _, result, _ = council
    for c in build_consensus(result.responses):
        assert c.severity == max(c.severities, key=lambda s: SEVERITY_RANK.get(s, 0))


def test_the_text_report_states_each_dispute_with_its_counts(council):
    _, result, code = council
    text = format_markdown(result, _config(), code)
    disputed = [c for c in build_consensus(result.responses) if c.agreement > 1 and c.disputed]
    for c in disputed:
        spread = ", ".join(f"{n} said {sev.lower()}" for sev, n in sorted(c.severities.items()))
        assert f"Severity disputed: {spread}" in text
    assert text.count("Severity disputed") == len(disputed)


# --------------------------------------------------------------------------------------
# The three outputs say the same thing
# --------------------------------------------------------------------------------------


def test_the_json_consensus_is_the_engines_consensus_in_the_same_order(council, tmp_path):
    _, result, code = council
    payload = _json_output(result, code, tmp_path)
    assert [_entry_from_payload(c) for c in payload["consensus"]] == [
        _entry(c) for c in build_consensus(result.responses)
    ]


def test_the_mcp_consensus_is_the_engines_consensus_in_the_same_order(council):
    _, result, _ = council
    payload = json.loads(serialise_result(result, _config()))
    assert [_entry_from_payload(c) for c in payload["consensus"]] == [
        _entry(c) for c in build_consensus(result.responses)
    ]


def test_report_for_user_has_one_line_per_entry_in_order_with_its_agreement(council):
    _, result, _ = council
    seats = distinct_seats(result.responses)
    clusters = build_consensus(result.responses)
    header, *lines = json.loads(serialise_result(result, _config()))["report_for_user"]
    assert f"{len(clusters)} finding" in header and f"{seats} independent reviewer" in header
    assert len(lines) == len(clusters)
    for c, line in zip(clusters, lines, strict=True):
        assert line.startswith(f"- {c.agreement}/{seats} {c.severity}")


def test_the_text_report_lists_every_multi_reviewer_entry_and_counts_the_lone_ones(council):
    _, result, code = council
    text = format_markdown(result, _config(), code)
    clusters = build_consensus(result.responses)
    shared = [c for c in clusters if c.agreement > 1]
    for c in shared:
        assert f"{c.title}" in text
        assert f"**{c.agreement} reviewers**" in text
    lone = len(clusters) - len(shared)
    if lone:
        assert f"{lone} further finding{'s' if lone != 1 else ''} came from a single reviewer only" in text


def test_the_status_and_counts_in_the_mcp_payload_match_the_result(council):
    _, result, _ = council
    payload = json.loads(serialise_result(result, _config()))
    assert payload["status"] == "ok"
    assert payload["error"] is None
    assert payload["responses_received"] == len(result.responses)
    assert payload["distinct_models"] == distinct_seats(result.responses)
