"""
Findings parsing and consensus grouping (plan item S6).

The parser reads what models actually emit, not what the prompt asks for, so the cases here
are transcribed from recorded council runs: models reproduce the `[SEVERITY]` tag reliably
and rewrite everything around it.
"""

import itertools
import os
import pathlib
import subprocess
import sys

import pytest

from fourtake.client import ModelResponse
from fourtake.findings import (
    DefectCategory,
    Finding,
    build_consensus,
    cluster_findings,
    is_review,
    parse_findings,
)
from fourtake.prompts import build_system_prompt


def _f(model, severity, title, symbol, issue=""):
    return Finding(
        severity=severity, title=title, symbol=symbol, issue=issue, fix="", model=model, raw=""
    )


# --------------------------------------------------------------------------------------
# Parsing: the template as written, and the four ways models rewrite it
# --------------------------------------------------------------------------------------

def test_parses_the_template_the_prompt_asks_for():
    content = (
        "* [CRITICAL] SQL injection via term (at search_users)\n"
        "  - Issue: term is concatenated into the query, so a quote ends the literal.\n"
        "  - Fix: cur.execute(sql, (term,))\n"
    )
    (finding,) = parse_findings(content, "model-a")
    assert finding.severity == "CRITICAL"
    assert finding.symbol == "search_users"
    assert finding.title == "SQL injection via term"
    assert "concatenated" in finding.issue
    assert finding.fix.startswith("cur.execute")
    assert finding.model == "model-a"


def test_review_axes_in_severity_position_are_mapped():
    content = (
        "* [SECURITY:AUTH_CRYPTO] Timing-safe signature comparison missing (at verify_sig)\n"
        "  - Issue: != leaks timing information.\n"
        "  - Fix: Use hmac.compare_digest.\n"
        "* [CORRECTNESS:ERROR_HANDLING] No timeout on HTTP request (at fetch)\n"
        "  - Issue: requests.get hangs forever without timeout.\n"
        "  - Fix: Add timeout=10.\n"
        "* [SIMPLICITY] Store wrapper with a single implementation (at Store)\n"
        "  - Issue: every call forwards to dict unchanged.\n"
    )
    f1, f2, f3 = parse_findings(content, "model-a")
    assert f1.severity == "CRITICAL"
    assert f1.category == DefectCategory.AUTH_CRYPTO
    assert f1.symbol == "verify_sig"
    assert f2.severity == "WARNING"
    assert f2.category == DefectCategory.ERROR_HANDLING
    assert f2.symbol == "fetch"
    # An axis that is also a category names both.
    assert (f3.severity, f3.category) == ("SUGGESTION", DefectCategory.SIMPLICITY)



def test_parses_the_bullet_and_emphasis_variants_models_actually_emit():
    variants = [
        "- **[WARNING]** File handle leak (in load_rows)\n  - Issue: fh is not closed on raise\n",
        "### [WARNING] File handle leak (at `load_rows`)\n**Issue:** fh is not closed on raise\n",
        "1. [ warning ] File handle leak (load_rows)\n   Issue - fh is not closed on raise\n",
    ]
    for content in variants:
        (finding,) = parse_findings(content, "m")
        assert finding.severity == "WARNING", content
        assert finding.symbol == "load_rows", content
        assert "not closed" in finding.issue, content


def test_a_clean_review_and_an_empty_one_both_yield_no_findings():
    assert parse_findings("NO_ISSUES_FOUND: Correct, safe, well-structured.", "m") == []
    assert parse_findings("", "m") == []
    assert parse_findings(None, "m") == []
    assert parse_findings("The code looks broadly reasonable to me.", "m") == []


def test_line_numbers_are_not_treated_as_symbols():
    """Models are told not to invent line numbers; two models' numbers never agree anyway."""
    (finding,) = parse_findings("* [WARNING] Off by one (at line 42)\n  - Issue: x\n", "m")
    assert finding.symbol is None


@pytest.mark.parametrize("symbol", ["index_of", "attempt_count", "interface", "nearest",
                                    "internal_state", "at_capacity"])
def test_a_symbol_beginning_with_a_locator_keyword_keeps_its_first_letters(symbol):
    """
    The locator's optional "at"/"in"/"near" prefix used to be allowed to match without any
    following space, so it ate the first two letters of every symbol that happened to start
    with those letters: `index_of` parsed as `dex_of`, `attempt_count` as `tempt_count`.
    """
    (finding,) = parse_findings(f"* [WARNING] Something is wrong ({symbol})\n", "m")
    assert finding.symbol == symbol


def test_a_symbol_is_recovered_from_backticks_when_the_locator_is_missing():
    (finding,) = parse_findings(
        "* [CRITICAL] Unchecked None\n  - Issue: `build_endpoint` dereferences host\n", "m"
    )
    assert finding.symbol == "build_endpoint"


def test_the_symbol_is_shown_as_the_model_spelled_it():
    """`currentuser` is not something a reader can grep for; `currentUser` is."""
    (finding,) = parse_findings("* [CRITICAL] Unverified token (at currentUser)\n", "m")
    assert finding.symbol == "currentUser"


def test_symbols_group_across_spelling_differences():
    findings = [
        _f("a", "CRITICAL", "Unverified JWT", "currentUser", "the signature is never checked"),
        _f("b", "CRITICAL", "Signature not verified", "currentuser", "the jwt signature is never checked"),
    ]
    (cluster,) = cluster_findings(findings)
    assert cluster.agreement == 2
    assert cluster.symbol in {"currentUser", "currentuser"}


def test_qualified_names_reduce_to_the_specific_symbol():
    (a,) = parse_findings("* [CRITICAL] Overflow (at RingBuffer.push)\n", "m")
    (b,) = parse_findings("* [CRITICAL] Overflow (in `push`)\n", "m")
    assert a.symbol == b.symbol == "push"


@pytest.mark.parametrize("locator", ["GroupAdapter.java", "FilterView.tsx", "paginator.py",
                                     "GroupAdapter.java, getSubGroupsCount"])
def test_a_file_name_is_not_a_symbol(locator):
    """
    `(GroupAdapter.java)` parsed as the symbol `java`, which every finding a model filed against
    a Java file then shared, pulling unrelated defects towards one another; `.py` was too short
    to be read as a name, so `(paginator.py)` became the module. The function the finding names
    in backticks is the better symbol.
    """
    (finding,) = parse_findings(
        f"* [WARNING] Null return from `getSubGroupsCount()` ({locator})\n", "m"
    )
    assert finding.symbol == "getSubGroupsCount"


def test_a_title_starting_with_a_number_keeps_it():
    """The bullet stripper had a blanket digit class, so "42 bytes ..." lost the 42."""
    (finding,) = parse_findings("* [WARNING] 42 bytes over the frame limit (at pack)\n", "m")
    assert finding.title == "42 bytes over the frame limit"


@pytest.mark.parametrize("bullet", ["* ", "- ", "1. ", "2) ", "### ", "> "])
def test_the_bullet_itself_is_always_stripped(bullet):
    (finding,) = parse_findings(f"{bullet}[WARNING] Leaks a handle (at load_rows)\n", "m")
    assert finding.title == "Leaks a handle"


def test_findings_are_split_at_each_severity_tag():
    content = (
        "* [CRITICAL] First (at f)\n  - Issue: a\n"
        "* [WARNING] Second (at g)\n  - Issue: b\n"
        "* [SUGGESTION] Third (at h)\n  - Issue: c\n"
    )
    assert [f.severity for f in parse_findings(content, "m")] == ["CRITICAL", "WARNING", "SUGGESTION"]


# --------------------------------------------------------------------------------------
# Clustering: the point of running four models at once
# --------------------------------------------------------------------------------------

def test_the_same_defect_clusters_even_when_models_rate_it_differently():
    """
    Keying on (symbol, severity) — the obvious first cut — would split exactly the strongest
    signal the council can produce: two independent models finding one bug.
    """
    findings = [
        _f("a", "CRITICAL", "SQL injection", "search_users", "term is concatenated into the query"),
        _f("b", "WARNING", "Unparameterised query", "search_users", "user input concatenated into SQL query"),
    ]
    (cluster,) = cluster_findings(findings)
    assert cluster.agreement == 2
    assert cluster.severity == "CRITICAL", "the cluster takes the worst severity raised"
    assert cluster.disputed
    assert cluster.severities == {"CRITICAL": 1, "WARNING": 1}


def test_severity_counts_are_reviewers_not_findings():
    """
    A verbose model listing one defect three times as CRITICAL must not read as three
    reviewers calling it critical.
    """
    findings = [
        _f("a", "CRITICAL", "SQL injection", "f", "term concatenated into the query"),
        _f("a", "CRITICAL", "Injection again", "f", "term concatenated into the query string"),
        _f("b", "WARNING", "Unparameterised", "f", "user input concatenated into query"),
    ]
    (cluster,) = cluster_findings(findings)
    assert cluster.agreement == 2
    assert cluster.severities == {"CRITICAL": 1, "WARNING": 1}


def test_two_unrelated_defects_in_one_function_stay_apart():
    """
    The guarantee is on vocabulary, not on the symbol: sharing a function name must not be
    enough to merge. Two defects described in the *same* words — "division by zero on an
    empty list" and "index error on an empty list" — do merge; that limitation is measured
    by the eval harness rather than asserted here, because it is a semantic problem and the
    lexical grouping is explicitly a first iteration.
    """
    findings = [
        _f("a", "CRITICAL", "SQL injection", "handle_request", "term is concatenated into the query"),
        _f("b", "WARNING", "File handle leak", "handle_request", "the log file is never closed"),
    ]
    clusters = cluster_findings(findings)
    assert len(clusters) == 2
    assert all(c.agreement == 1 for c in clusters)


def test_a_single_shared_word_is_not_agreement():
    """Containment alone would let a two-word finding join anything that repeats one word."""
    findings = [
        _f("a", "WARNING", "Leak", "f", "handle"),
        _f("b", "WARNING", "Race condition on the shared counter", "g",
           "two threads increment without the lock and one update is lost"),
    ]
    assert len(cluster_findings(findings)) == 2


def test_clusters_are_ranked_by_how_many_reviewers_agreed():
    findings = [
        _f("a", "CRITICAL", "Rare thing", "x", "only one model saw this"),
        _f("b", "WARNING", "File handle leak", "y", "the file handle is not closed when the check raises"),
        _f("c", "WARNING", "Leaked file handle", "y", "file handle stays open if the function raises"),
        _f("d", "WARNING", "Handle not closed", "y", "the open file handle leaks on the raise path"),
    ]
    clusters = cluster_findings(findings)
    assert [c.agreement for c in clusters] == [3, 1]
    assert clusters[0].symbol == "y"


def _as_sets(clusters, findings):
    """Clusters as a set of frozensets of positions in `findings`: equality with no order in it."""
    position = {id(f): i for i, f in enumerate(findings)}
    return {frozenset(position[id(f)] for f in c.findings) for c in clusters}


def test_the_order_findings_arrive_in_never_changes_the_clusters():
    """
    First-fit put each finding into the first group it resembled and never joined two groups.
    Here the first and last reviewer share only the symbol, and the middle one shares a sentence
    with each, so first-fit returned one entry, two, or a different two, depending on which
    model answered first. Single linkage joins all three whatever the order.
    """
    findings = [
        _f("a:free", "WARNING", "Handle leaked when parsing raises", "load_rows",
           "the handle open() returns is never closed if parsing raises"),
        _f("b:free", "WARNING", "Handle stays open when parsing raises", "load_rows",
           "open() sits outside a with block, so the descriptor leaks on a malformed row"),
        _f("c:free", "SUGGESTION", "Use a with block", "load_rows",
           "a with block releases the descriptor on every path, a malformed row included"),
        _f("d:free", "CRITICAL", "SQL injection via the search term", "search_users",
           "term is concatenated into the query string"),
    ]
    expected = {frozenset({0, 1, 2}), frozenset({3})}
    for order in itertools.permutations(findings):
        assert _as_sets(cluster_findings(list(order)), findings) == expected


def _rendered(clusters):
    """Everything an entry tells the reader, in the order the reader sees it."""
    return [(c.severity, c.category, c.symbol, c.title, c.models, list(c.severities.items()))
            for c in clusters]


def test_the_order_findings_arrive_in_never_changes_what_an_entry_says():
    """
    Membership alone is not the whole answer. Every tie was settled by the order the findings
    arrived in: the title among equally severe findings, the category two reviewers split on,
    and the order of equally ranked entries. So the same council, answering in another order,
    read differently.
    """
    leak_a = _f("a:free", "WARNING", "Handle leaked when parsing raises", "load_rows",
                "the handle open() returns is never closed if parsing raises")
    leak_a.category = DefectCategory.RESOURCE_LEAK
    leak_b = _f("b:free", "WARNING", "Handle stays open when parsing raises", "read_rows",
                "the handle open() returns stays open if parsing raises")
    leak_b.category = DefectCategory.ERROR_HANDLING
    sqli = _f("c:free", "CRITICAL", "SQL injection via the search term", "search_users",
              "term is concatenated into the query string")
    shell = _f("d:free", "CRITICAL", "Command injection through the branch name", "checkout",
               "branch is interpolated into a shell command")
    findings = [leak_a, leak_b, sqli, shell]
    expected = _rendered(cluster_findings(findings))
    for order in itertools.permutations(findings):
        assert _rendered(cluster_findings(list(order))) == expected


# Three reviewers name one leak after three different functions, so the symbol is a three-way
# tie. Run in a fresh interpreter because the hash seed is fixed at start-up.
_HASH_SEED_SCRIPT = """
import json
from fourtake.findings import Finding, cluster_findings

def finding(model, title, symbol, issue):
    return Finding(severity="WARNING", title=title, symbol=symbol, issue=issue, fix="", model=model, raw="")

findings = [
    finding("a:free", "Handle leaked when parsing raises", "load_rows",
            "the handle open() returns is never closed if parsing raises"),
    finding("b:free", "Handle stays open when parsing raises", "read_rows",
            "the handle open() returns stays open if parsing raises"),
    finding("c:free", "Handle not closed when parsing raises", "parse_rows",
            "open() returns a handle that parsing never closes if it raises"),
]
print(json.dumps([[c.symbol, c.title, c.models] for c in cluster_findings(findings)]))
"""


def test_the_hash_seed_never_changes_the_consensus():
    """
    The symbol was chosen with `max(set(names), key=names.count)`, so a tie went to whichever
    name the set yielded first. The hash seed decides that order, so two runs over one council
    could name one entry after two different functions.
    """
    import fourtake

    source = str(pathlib.Path(fourtake.__file__).resolve().parent.parent)
    outputs = set()
    for seed in range(8):
        env = {**os.environ, "PYTHONHASHSEED": str(seed), "PYTHONPATH": source}
        run = subprocess.run([sys.executable, "-c", _HASH_SEED_SCRIPT], env=env,
                             capture_output=True, text=True, check=True)
        outputs.add(run.stdout)
    assert len(outputs) == 1, outputs


def test_one_model_repeating_itself_is_still_one_opinion():
    """Otherwise a verbose model manufactures consensus with itself."""
    findings = [
        _f("a", "WARNING", "Leak", "load_rows", "handle leaked on the raise path"),
        _f("a", "WARNING", "Leak again", "load_rows", "handle leaked on the raise path"),
    ]
    (cluster,) = cluster_findings(findings)
    assert cluster.agreement == 1
    assert len(cluster.findings) == 2


# --------------------------------------------------------------------------------------
# Seats, not responses
# --------------------------------------------------------------------------------------

def _response(model, content, served=None, duplicate_of=None):
    return ModelResponse(
        model=model, content=content, error=None, latency_ms=1,
        served_model=served or model, duplicate_of=duplicate_of,
    )


def test_consensus_is_keyed_on_the_model_that_actually_answered():
    body = "* [CRITICAL] Injection (at f)\n  - Issue: input concatenated into the query\n"
    responses = [
        _response("openrouter/free", body, served="vendor/x:free"),
        _response("vendor/x:free", body, served="vendor/x:free", duplicate_of="vendor/x:free"),
    ]
    (cluster,) = build_consensus(responses)
    assert cluster.agreement == 1, "a router seating one model twice is one voice"
    assert cluster.models == ["vendor/x:free"]


def test_two_distinct_seats_agreeing_count_twice():
    body = "* [CRITICAL] Injection (at f)\n  - Issue: input concatenated into the query\n"
    responses = [_response("a:free", body), _response("b:free", body)]
    (cluster,) = build_consensus(responses)
    assert cluster.agreement == 2
    assert cluster.models == ["a:free", "b:free"]


# --------------------------------------------------------------------------------------
# Categories: read as the models write them, never guessed, and never a reason to group
# --------------------------------------------------------------------------------------

@pytest.mark.parametrize("tag, severity, category", [
    ("[CRITICAL] [INJECTION]", "CRITICAL", DefectCategory.INJECTION),
    ("**[CRITICAL]** **[INJECTION]**", "CRITICAL", DefectCategory.INJECTION),
    ("[CRITICAL: INJECTION]", "CRITICAL", DefectCategory.INJECTION),
    ("[CRITICAL/INJECTION]", "CRITICAL", DefectCategory.INJECTION),
    ("[CRITICAL - INJECTION]", "CRITICAL", DefectCategory.INJECTION),
    ("[CRITICAL | INJECTION]", "CRITICAL", DefectCategory.INJECTION),
    ("[CRITICAL, INJECTION]", "CRITICAL", DefectCategory.INJECTION),
    ("[CRITICAL \N{EM DASH} INJECTION]", "CRITICAL", DefectCategory.INJECTION),
    ("[WARNING:RESOURCE LEAK]", "WARNING", DefectCategory.RESOURCE_LEAK),
    ("[WARNING:resource-leak]", "WARNING", DefectCategory.RESOURCE_LEAK),
    ("[CRITICAL:AUTH/CRYPTO]", "CRITICAL", DefectCategory.AUTH_CRYPTO),
    # Copied from the template's placeholder in a recorded review. The finding it opened was
    # one of two that agreed; losing it erased the agreement.
    ("[SEVERITY:LOGIC]", "WARNING", DefectCategory.LOGIC),
])
def test_a_finding_survives_the_ways_models_write_its_tag(tag, severity, category):
    """A strict `:[A-Z_]+` after the severity lost the whole finding, not just its category."""
    (finding,) = parse_findings(f"* {tag} SQL injection via term (at search_users)\n"
                                "  - Issue: term is concatenated\n", "m")
    assert (finding.severity, finding.category) == (severity, category)
    assert finding.title == "SQL injection via term"
    assert finding.symbol == "search_users"


@pytest.mark.parametrize("line, category, title, symbol", [
    # How most recorded models wrote the template's `[CATEGORY]`: the word, without brackets,
    # often with the `<Concise Title>` placeholder's brackets copied too.
    ("* [CRITICAL] LOGIC <Incorrect result filtering> (function fan_out)",
     DefectCategory.LOGIC, "Incorrect result filtering", "fan_out"),
    ("* [CRITICAL] RESOURCE_LEAK - Unreleased connection on failure (run_migration, lines 7-11)",
     DefectCategory.RESOURCE_LEAK, "Unreleased connection on failure", "run_migration"),
    ("* [WARNING] **ERROR_HANDLING** Incorrect exception handling in fan_out (fan_out)",
     DefectCategory.ERROR_HANDLING, "Incorrect exception handling in fan_out", "fan_out"),
    # Only the upper-case name is a category; an ordinary first word stays in the title.
    ("* [WARNING] Logic error in the retry loop (at retry)",
     DefectCategory.UNKNOWN, "Logic error in the retry loop", "retry"),
])
def test_a_category_written_as_a_bare_word_is_read(line, category, title, symbol):
    (finding,) = parse_findings(f"{line}\n  - Issue: x\n", "m")
    assert (finding.category, finding.title, finding.symbol) == (category, title, symbol)


def test_a_category_is_read_never_guessed():
    """Keyword guessing filed this under CONCURRENCY, because `block` contains `lock`."""
    (finding,) = parse_findings("* [WARNING] Exception swallowed in except block (at load)\n"
                                "  - Issue: the traceback is lost\n", "m")
    assert finding.category is DefectCategory.UNKNOWN


def test_an_axis_heading_over_findings_is_not_a_finding():
    """Each `### [SECURITY]` section heading used to become an "(untitled)" CRITICAL."""
    content = (
        "### [SECURITY]\n"
        "* [CRITICAL] [INJECTION] SQL injection via term (at search_users)\n"
        "  - Issue: term is concatenated\n\n"
        "## [STRUCTURE] Findings\n"
        "* [SUGGESTION] [STRUCTURE] Query building duplicated (at search_users)\n"
        "  - Issue: both handlers build SQL by hand and will drift\n"
    )
    titles = [f.title for f in parse_findings(content, "m")]
    assert titles == ["SQL injection via term", "Query building duplicated"]


def test_a_finding_written_on_one_line_is_still_a_finding():
    """Four recorded findings carry no Issue or Fix line; the heading rule must not eat them."""
    (finding,) = parse_findings(
        "* [WARNING] No thread-safety guards: concurrent put and get corrupt the ordering\n", "m"
    )
    assert finding.title.startswith("No thread-safety guards")


def test_an_unlabelled_issue_under_the_title_is_read():
    """
    A recorded model wrote many issues as the first bullet under the title, with no `Issue:`
    label. The parser kept the title and the fix and dropped the explanation, so the finding
    reached grouping as its title alone: 54 of that model's 125 findings on one benchmark.
    """
    content = (
        "* [WARNING] ERROR_HANDLING \N{EM DASH} Null return from `getSubGroupsCount()` (GroupAdapter.java)\n"
        "  - When the cached model is gone the method returns null, and a caller that unboxes\n"
        "    the Long throws.\n"
        "  - Fix: return 0L instead.\n"
    )
    (finding,) = parse_findings(content, "m")
    assert finding.issue == ("When the cached model is gone the method returns null, and a caller "
                             "that unboxes the Long throws.")
    assert finding.fix == "return 0L instead."


def test_an_unlabelled_paragraph_after_a_blank_line_is_the_issue():
    """Another recorded model's form: title, blank line, the explanation, blank line, `Fix:`."""
    content = (
        "[CRITICAL] [CORRECTNESS] Negative offset slicing crashes\n"
        "\n"
        "A negative `cursor.offset` reaches `queryset[start:stop]`, and querysets reject\n"
        "negative indexing.\n"
        "\n"
        "Fix: clamp the offset at zero.\n"
        "\n"
        "Overall the change is otherwise sound.\n"
    )
    (finding,) = parse_findings(content, "m")
    assert finding.issue == ("A negative `cursor.offset` reaches `queryset[start:stop]`, and "
                             "querysets reject negative indexing.")
    assert finding.fix == "clamp the offset at zero."


def test_a_labelled_issue_is_preferred_to_a_paragraph_above_it():
    content = (
        "* [WARNING] Unbounded retry (at fetch)\n"
        "  Seen in the new client.\n"
        "  - Issue: the loop never gives up when the host is down.\n"
    )
    (finding,) = parse_findings(content, "m")
    assert finding.issue == "the loop never gives up when the host is down."


def test_reasoning_that_mentions_a_tag_does_not_borrow_the_next_paragraph():
    """
    Chain of thought leaked into one model's answer names the tags mid-sentence. The paragraph
    after such a line is more reasoning, and reading it as an issue would hand grouping a long
    run of words shared with every real finding — nor is the line itself a finding.
    """
    content = (
        "Severity: [CRITICAL] or [WARNING]? We need to decide.\n"
        "\n"
        "The override conversion could shift every slot, which the caller then trusts.\n"
    )
    assert parse_findings(content, "m") == []


# Transcribed from one benchmark run, where 25 of 872 findings were the prompt's template echoed
# back unfilled or chain of thought that named a severity in passing.
_REAL_FINDING = (
    "* [WARNING] RESOURCE_LEAK <Thread resource leak when restarting flusher process for shard> "
    "(at SpanFlusher._create_process_for_shards)\n"
    "    - Issue: When produce_to_pipe is not None and we restart a process (which is actually a\n"
    "      thread) for a shard, the new thread replaces the old one without stopping it.\n"
    "    - Fix: Signal the old thread to exit and join it before starting the new one.\n"
)


@pytest.mark.parametrize("junk", [
    # The template echoed verbatim, then the model talking to itself about it.
    "* [SEVERITY] [CATEGORY] <Concise Title> (at symbol/line)\n"
    "    - Issue: <What is wrong, and the concrete scenario, input, or future change that makes it "
    "cost something>\n"
    "    - Fix: <1-3 line code correction>\n\n"
    "So we need to include the issue and fix lines indented.\n",
    "* [SEVERITY] [CATEGORY] <Concise Title> (at symbol/line)\n  - Issue: <>\n  - Fix: <>\n",
    "* [SEVERITY] [CATEGORY] <Concise Title> (at symbol/line)\n  - Issue: <...>\n  - Fix: <how to fix>\n",
    # ... with the reasoning where the issue would be.
    "* [SEVERITY] [CATEGORY] <Concise Title> (at symbol/line)\n\n"
    "But we need to reference symbol/line. Since we have diff, we can reference function name.\n",
    # The template quoted inside a sentence.
    "We need to ensure each finding follows the template: * [SEVERITY] [CATEGORY] <Concise Title> "
    "(at symbol/line) - Issue: ... - Fix: ...\n",
    # A severity weighed mid-sentence: the rest of the sentence became the title.
    "Also we need to decide if there are any [CRITICAL] findings. The last one about bypass could "
    "be considered a security issue, but is it confirmed exploitable?\n",
    "That's a security concern (maybe [WARNING] or [CRITICAL] depending). Need concrete scenario.\n",
    "This is a security issue (maybe [WARNING]).\n",
    "- For OAuthLoginView.dispatch mixing concerns: [SUGGESTION] as structural.\n",
    # A draft list the model rewrote in the template further down.
    "Issue 1: [WARNING] LOGIC Incorrect calendarId fallback in GoogleCalendarService updateEvent\n",
    # An issue that argues with itself and never concludes.
    "* [WARNING] LOGIC <Incorrect metric tag for segment size> (at SpanFlusher.main.produce)\n"
    "    - Issue: We record the segment size with a shard tag, and each flushed_segment corresponds\n"
    "      to one shard? Actually, no: the flushed_segments is a dictionary keyed by shard? Let's\n"
    "      look at the code that produces flushed_segments.\n",
])
def test_an_echoed_template_or_leaked_reasoning_is_not_a_finding(junk):
    """A seated answer's real findings must come through without the junk around them."""
    findings = parse_findings(f"{junk}\n{_REAL_FINDING}", "m")
    assert [f.title for f in findings] == [
        "Thread resource leak when restarting flusher process for shard"
    ]
    assert "actually a thread" in findings[0].issue


def test_the_prompts_own_template_echoed_unfilled_is_not_a_finding():
    """Holds the placeholders the parser recognises to the ones the prompt actually uses."""
    lines = build_system_prompt().splitlines()
    start = next(i for i, line in enumerate(lines) if "[SEVERITY] [CATEGORY]" in line)
    template = "\n".join(lines[start:start + 3])
    assert "- Fix:" in template
    assert parse_findings(template, "m") == []
    assert not is_review(template)


def test_a_placeholder_title_over_a_real_issue_keeps_the_finding():
    """One model wrote `<Concise Title>` above twelve real issues; another opened titles `<Title>`."""
    content = (
        "* [CORRECTNESS] LOGIC <Concise Title> (at CreateOrUpdateDevice)\n"
        "        - Issue: The device limit check and the insert are not atomic, so two concurrent\n"
        "          requests both see the count below the limit and both insert.\n"
        "        - Fix: Check the count and upsert in one transaction.\n"
        "* [WARNING] <Title> Unhandled nil TopicUser causing crash in unsubscribe action\n"
        "  - Issue: When no TopicUser record exists, tu is nil and tu.notification_level raises.\n"
    )
    first, second = parse_findings(content, "m")
    assert first.title.startswith("The device limit check and the insert are not atomic")
    assert first.symbol == "CreateOrUpdateDevice"
    assert second.title == "Unhandled nil TopicUser causing crash in unsubscribe action"


def test_only_a_whole_field_is_a_placeholder():
    """`<>` alone is an unfilled fix; inside one it is the diamond of `new HashSet<>()`."""
    unfilled, diamond = parse_findings(
        "* [WARNING] [CONCURRENCY] Unsynchronised iteration over cached ids (at getLoginIds)\n"
        "  - Issue: another thread mutates the set mid-iteration.\n"
        "  - Fix: <>\n"
        "* [WARNING] [CONCURRENCY] Iteration races with invalidation (at getLoginIds)\n"
        "  - Issue: the set is cleared while callers iterate it.\n"
        "  - Fix: Set<String> ids = new HashSet<>(cached);\n",
        "m",
    )
    assert (unfilled.issue, unfilled.fix) == ("another thread mutates the set mid-iteration.", "")
    assert diamond.fix == "Set<String> ids = new HashSet<>(cached);"


def test_an_axis_heading_does_not_become_a_finding_through_its_introduction():
    content = (
        "## [SECURITY] Findings\n"
        "Both handlers below take user input.\n"
        "\n"
        "* [CRITICAL] [INJECTION] SQL injection via term (at search_users)\n"
        "  - Issue: term is concatenated\n"
    )
    assert [f.title for f in parse_findings(content, "m")] == ["SQL injection via term"]


def test_parses_severity_with_explicit_category():
    content = (
        "* [CRITICAL:INJECTION] SQLi in search (at search_users)\n"
        "  - Issue: term is concatenated into SQL\n"
        "  - Fix: cur.execute(sql, (term,))\n"
    )
    (finding,) = parse_findings(content, "m")
    assert finding.severity == "CRITICAL"
    assert finding.category.value == "INJECTION"
    assert finding.symbol == "search_users"


def test_a_json_body_quoted_in_a_fix_does_not_replace_the_review():
    """
    A parser stage that read the first code fence as JSON turned this review into one finding
    titled "Internal Server Error" — an RFC 7807 body has a `title` — and the SQL injection
    above it vanished without an error.
    """
    content = (
        "* [CRITICAL:INJECTION] SQL injection via term (at search_users)\n"
        "  - Issue: term is concatenated into the query\n"
        "  - Fix: cur.execute(sql, (term,))\n\n"
        "* [WARNING:ERROR_HANDLING] Errors leak stack traces (at handle_error)\n"
        "  - Issue: the raw exception text is returned to the client\n"
        "  - Fix: return a problem-details body:\n"
        "```json\n"
        '{"type": "about:blank", "title": "Internal Server Error", "status": 500}\n'
        "```\n"
    )
    findings = parse_findings(content, "m")
    assert [f.title for f in findings] == ["SQL injection via term", "Errors leak stack traces"]
    assert findings[0].severity == "CRITICAL"


def test_two_defects_sharing_only_a_function_name_stay_apart():
    """An injection and a resource leak in one function share nothing but its name."""
    f1 = _f("m1", "CRITICAL", "SQL Injection", "handle_request", "concatenated query")
    f1.category = DefectCategory.INJECTION
    f2 = _f("m2", "WARNING", "File handle leak", "handle_request", "file descriptor leaked")
    f2.category = DefectCategory.RESOURCE_LEAK

    clusters = cluster_findings([f1, f2])
    assert len(clusters) == 2
    assert all(c.agreement == 1 for c in clusters)


def test_one_defect_in_divergent_wording_still_groups():
    """
    Two models describing one injection in different words land in one entry. Transcribed from
    evals/runs/2: the titles share one word, and the two locators name different identifiers.

    Wording further apart than this, such as two findings sharing nothing but `query` and the
    function name, scores about 0.17 and reads as two entries. Below 0.24, links on the
    recordings joined two different defects more often than one; see `_SIMILARITY_THRESHOLD`.
    """
    responses = [
        _response("laguna:free", "* [CRITICAL] INJECTION search_users SQL injection vulnerability (at line 4-5)\n"
                  "  - Issue: The `term` parameter is directly concatenated into the SQL query string without "
                  "any sanitization or parameterization. An attacker can supply a malicious term like "
                  "`' OR 1=1 --` to bypass the LIKE clause and extract all user records, or use "
                  "`'; DROP TABLE users; --` to destroy data. The `limit` parameter is also vulnerable to "
                  "injection via `str(limit)`.\n"),
        _response("north:free", "* [CRITICAL] INJECTION Unsafe SQL query construction (search_users)\n"
                  "  - Issue: The `term` argument is directly concatenated into the SQL string without "
                  "sanitization, allowing an attacker to inject arbitrary SQL. For example, supplying "
                  "`term = \"' OR '1'='1\"` yields a query that returns all rows, bypassing the intended "
                  "search.\n"),
    ]
    (cluster,) = build_consensus(responses)
    assert cluster.agreement == 2
    assert cluster.category == DefectCategory.INJECTION


def test_one_defect_filed_under_two_categories_still_groups():
    """
    Models filing one labelled defect chose the same category in only about half of the recorded
    pairs, so a category veto split real agreement — as it did for these two, from overflow_backoff.
    """
    responses = [
        _response("nex:free", "* [WARNING] BOUNDS Exponential backoff is unbounded (at `retry_with_backoff`)\n"
                              "  - Issue: With a large `attempts` value, `2 ** i` exceeds the range "
                              "accepted by `time.sleep`; large attempt counts make the retry "
                              "effectively non-terminating.\n"),
        _response("nano:free", "* [WARNING] LOGIC Exponential backoff causes sleep time to grow "
                               "exponentially with attempt count, enabling denial of service via "
                               "crafted large attempt counts (retry_with_backoff)\n"
                               "  - Fix: Cap the backoff delay, e.g. `min(2 ** i, 30)`.\n"),
    ]
    (cluster,) = build_consensus(responses)
    assert cluster.models == ["nano:free", "nex:free"]
    # A tie goes to the reviewer that sorts first, not to whichever answered first.
    assert cluster.category is DefectCategory.LOGIC
    (reversed_cluster,) = build_consensus(responses[::-1])
    assert reversed_cluster.category is DefectCategory.LOGIC


def test_a_shared_symbol_and_category_are_not_agreement_on_their_own():
    """
    Two unrelated defects in one function once merged into an entry claiming two reviewers:
    the waiver for same-symbol findings counted the symbol's own token as shared evidence.
    """
    responses = [
        _response("a:free", "* [WARNING:LOGIC] Negative amount is accepted (at transfer)\n"
                            "  - Issue: amount < 0 moves money from the recipient to the sender\n"),
        _response("b:free", "* [WARNING:LOGIC] Fee is charged twice on retry (at transfer)\n"
                            "  - Issue: the retry path re-applies the fee after it was already deducted\n"),
    ]
    assert [c.agreement for c in build_consensus(responses)] == [1, 1]


def test_two_defects_in_one_function_never_share_an_entry():
    """
    Two models found a tax defect and two others an expiry defect in one function, all tagged
    LOGIC. Reviews of one function share its vocabulary — `order`, `total`, the function name —
    so the unrelated pair scores 0.145, and a same-category threshold of 0.10 merged all four
    into one entry claiming four reviewers agreed, hiding the second defect's title.

    The two tax findings score 0.19 against each other, below the threshold single linkage
    needs, so they read as two single-reviewer entries: the price of not chaining distinct
    defects. See `_SIMILARITY_THRESHOLD`.
    """
    responses = [
        _response("a:free", "* [WARNING:LOGIC] Discount is applied to the tax-inclusive total (at apply_discount)\n"
                  "  - Issue: `apply_discount` multiplies `order.total` by `(1 - code.percent / 100)`, but "
                  "`order.total` already includes tax. The discount therefore also reduces the tax: on a 100.00 "
                  "subtotal with 20% tax and a 10% code the customer pays 108.00 instead of 110.00, and the tax "
                  "line sent to the ledger is understated by 2.00 on every discounted order.\n"
                  "  - Fix: `order.subtotal *= 1 - code.percent / 100`, then recompute `order.tax` from it.\n"),
        _response("c:free", "* [CRITICAL:LOGIC] Tax is discounted together with the goods (at apply_discount)\n"
                  "  - Issue: The percentage is taken off `order.total`, which is computed after tax is added. "
                  "Every discount code silently lowers the tax collected, so the reported tax for an order of "
                  "250.00 with a 15% code is 15% too low and the books no longer reconcile.\n"
                  "  - Fix: Apply the percentage to `order.subtotal` and derive tax afterwards.\n"),
        _response("b:free", "* [WARNING:LOGIC] Expired codes are still accepted for hours (at apply_discount)\n"
                  "  - Issue: `code.expires_at` is a naive UTC timestamp but `apply_discount` compares it with "
                  "naive local `datetime.now()`. On a server at UTC+2 a code that expired at 00:30 UTC is still "
                  "applied to an order placed at 01:15 UTC, so an expired promotion keeps reducing "
                  "`order.total` for two hours after it ends.\n"
                  "  - Fix: Compare against `datetime.now(timezone.utc).replace(tzinfo=None)`.\n"),
        _response("d:free", "* [WARNING:LOGIC] Expiry check mixes local time and UTC (at apply_discount)\n"
                  "  - Issue: `expires_at` holds UTC but the comparison uses local `datetime.now()`, so the "
                  "window in which a code is accepted is shifted by the server's offset: codes stay valid after "
                  "their end date east of UTC and are rejected early west of UTC.\n"
                  "  - Fix: Use a UTC clock for the comparison.\n"),
    ]
    clusters = build_consensus(responses)
    tax, expiry = {"a:free", "c:free"}, {"b:free", "d:free"}
    assert all(set(c.models) <= tax or set(c.models) <= expiry for c in clusters)
    assert ["b:free", "d:free"] in [c.models for c in clusters]

