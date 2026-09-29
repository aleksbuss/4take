"""
Parse the review template every model is instructed to emit, and group findings that
independent models raised about the same defect.

The council's premise is that agreement between models which cannot see each other is
evidence. Until now that premise was invisible in the output: four reviews were printed
side by side and the reader did the cross-referencing by hand. Consensus grouping makes the
premise legible — and the eval harness scores against the same parser rather than growing a
second one that drifts away from it.
"""

from __future__ import annotations

import itertools
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from enum import Enum

SEVERITY_ORDER = {"CRITICAL": 3, "WARNING": 2, "SUGGESTION": 1}


class DefectCategory(str, Enum):
    """
    The closed vocabulary each finding is asked to be tagged with.

    The prompt's list and the parser's pattern are both generated from this enum, so the
    prompt cannot ask for a category the parser does not read. UNKNOWN means the model did not
    declare one, and it is never guessed: keyword inference was tried, agreed with the
    category the model itself wrote on 6 of 13 recorded findings — "block" matched "lock",
    "traceback" matched "race" — and a wrong category is worse than none, because the reader and
    the agents downstream take it at its word.
    """

    INJECTION = "INJECTION"               # SQLi, command injection, path traversal, XSS
    CONCURRENCY = "CONCURRENCY"           # Race conditions, TOCTOU, deadlocks, atomicity
    RESOURCE_LEAK = "RESOURCE_LEAK"       # Unclosed handles, connections, memory, subprocess
    BOUNDS = "BOUNDS"                     # Off-by-one, out of bounds, index error, slice/range
    ERROR_HANDLING = "ERROR_HANDLING"     # Bare except, swallowed exceptions, unhandled None
    AUTH_CRYPTO = "AUTH_CRYPTO"           # Timing attacks, secrets, token/JWT, crypto, auth
    LOGIC = "LOGIC"                       # Inverted condition, broken calculation, division by zero
    STRUCTURE = "STRUCTURE"               # Leaky boundaries, high coupling, duplicated drift
    SIMPLICITY = "SIMPLICITY"             # Indirection with one implementation, hand-rolled stdlib
    UNKNOWN = "UNKNOWN"


# The vocabulary as the prompt lists it; UNKNOWN is the parser's word for "not declared".
CATEGORY_NAMES = tuple(c.value for c in DefectCategory if c is not DefectCategory.UNKNOWN)

# Models put the review axis where the severity belongs — `[SECURITY]` — and now and then echo
# the template's own placeholder. Dropping those findings dropped real defects: 15 recorded
# findings, among them the only report of one labelled defect.
_AXIS_TO_SEVERITY = {
    "SECURITY": "CRITICAL",
    "CORRECTNESS": "WARNING",
    "STRUCTURE": "SUGGESTION",
    "SIMPLICITY": "SUGGESTION",
    # `[SEVERITY:LOGIC]`: no severity was given at all, so the middle one rather than a guess
    # that would rank it first.
    "SEVERITY": "WARNING",
}

# A severity tag anywhere on a line opens a finding. Matching the tag rather than the bullet
# is deliberate: models reliably reproduce `[CRITICAL]` but freely rewrite the bullet around
# it as `-`, `1.`, `###`, or `* **[CRITICAL]**`. A category may share the bracket, and there
# the separator and the spelling vary too — `[WARNING - RESOURCE LEAK]`, `[CRITICAL:AUTH/CRYPTO]`
# — which a strict `:[A-Z_]+` failed to match, losing the whole finding rather than its category.
_SEVERITY_RE = re.compile(
    r"\[\s*(CRITICAL|WARNING|SUGGESTION|" + "|".join(_AXIS_TO_SEVERITY) + r")"
    r"(?:\s*[:/|,\-\u2013\u2014]\s*([A-Za-z][A-Za-z_ /\-]{0,30}?))?\s*\]",
    re.IGNORECASE,
)
# The category in a bracket of its own: `[CRITICAL] [INJECTION]`, which the template asks for.
_CATEGORY_TAG_RE = re.compile(r"\[\s*(" + "|".join(CATEGORY_NAMES) + r")\s*\]", re.IGNORECASE)
# ... and as most recorded models actually wrote it: the bare word after the tag,
# `[CRITICAL] LOGIC <title>`. Upper case only, so a title that opens with the ordinary word —
# "Logic error in the retry loop" — keeps it.
_BARE_CATEGORY_RE = re.compile(
    r"^[\s*`]*(" + "|".join(CATEGORY_NAMES) + r")\b[*`]*\s*[:\-\u2013\u2014]?\s*"
)


def _category(raw: str | None) -> DefectCategory:
    """`resource leak`, `Resource-Leak` and `AUTH/CRYPTO` are spellings of a listed name."""
    name = re.sub(r"[\s/\-]+", "_", (raw or "").strip()).upper()
    return DefectCategory(name) if name in CATEGORY_NAMES else DefectCategory.UNKNOWN


def _read_tag(match: re.Match[str], rest: str) -> tuple[str, DefectCategory, str]:
    """Severity and category from a finding's opening tag, and the rest of its line without them."""
    word = match.group(1).upper()
    severity = _AXIS_TO_SEVERITY.get(word, word)
    category = _category(match.group(2))
    if category is DefectCategory.UNKNOWN:
        separate = _CATEGORY_TAG_RE.search(rest)
        bare = _BARE_CATEGORY_RE.match(rest)
        if separate:
            category = _category(separate.group(1))
            rest = rest[: separate.start()] + rest[separate.end():]
        elif bare:
            category = DefectCategory(bare.group(1))
            rest = rest[bare.end():]
        elif word in CATEGORY_NAMES:
            # `[STRUCTURE]` written where the severity goes still names the category.
            category = DefectCategory(word)
    return severity, category, rest


# `- Issue:` / `**Fix:**` / `Fix -` all occur in practice.
_FIELD_RE = re.compile(r"^\s*[-*>\s]*\**\s*(Issue|Fix)\**\s*[:\-]\s*(.*)$", re.IGNORECASE)

# The template's placeholders, copied instead of filled in. In a title they are removed rather
# than read: a recorded model wrote `<Concise Title>` over twelve real issues, and another opened
# eight real titles with `<Title>`. What is left decides — an empty title falls back to the issue.
_TITLE_PLACEHOLDER_RE = re.compile(r"(?:\[CATEGORY\]|<\s*(?:Concise\s+)?Title\s*>)\s*", re.IGNORECASE)
# A field placeholder is copied loosely — `<What is wrong, …>`, `<how to fix>`, `<...>`, `<>` — so
# it is recognised by shape: the whole field one bracketed run of plain words. Only the whole
# field: `<>` is also the diamond in a fix that writes `new HashSet<>()`.
_FIELD_PLACEHOLDER_RE = re.compile(r"<[\w\s.,\-]*>")
# A question the issue answers itself with a retraction is the model still deciding. On both
# recorded cases it went on to "so it might be correct" and to "let's look at the code". The
# word alone is no sign: "a process (which is actually a thread)" opened a real finding.
_SELF_ANSWERED_RE = re.compile(r"\?\s*Actually,?\s+no\b", re.IGNORECASE)

# Trailing locator: "(at parse_row)", "(in RingBuffer.push)", "(at line 42)", "(parse_row)".
# One level of nesting is allowed because models write the signature — "(def summarise(s))" —
# and a `[^)]` body stopped at the inner bracket, leaving the whole locator inside the title.
# The `\s+` after the keyword is load-bearing: with `\s*` the optional group matched the
# first two letters of any symbol beginning with "at", "in" or "near", so `(index_of)`
# parsed as `dex_of` and `(attempt_count)` as `tempt_count`.
_LOCATOR_RE = re.compile(
    r"\(\s*(?:(?:at|in|near)\s+)?([^()]{0,80}(?:\([^()]{0,40}\))?[^()]{0,40})\)\s*$",
    re.IGNORECASE,
)

_LINE_ONLY_RE = re.compile(r"^(?:line|lines?|l\.)\s*[\d,\-\s]+$", re.IGNORECASE)
# A locator is only usable if it names something the models can agree on. A dotted
# identifier chain qualifies; "the list comprehension" and "the call to jwt.decode" do not,
# and accepting them invented symbols like `comprehension` and `call` that split genuine
# agreement across three fake functions.
_IDENT_CHAIN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*(?:\(\))?$")
_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")
# A file name says where a finding is, not what it is about. Read as a dotted name,
# `(GroupAdapter.java)` gave the symbol `java`, shared by every finding a model filed against a
# Java file, and `(paginator.py)` gave the module, since `py` is too short to be a name.
_FILE_NAME_RE = re.compile(
    r"\.(?:py|pyi|js|jsx|mjs|cjs|ts|tsx|mts|cts|java|kt|kts|scala|groovy|gradle|go|rs|rb|erb|"
    r"php|cs|c|h|cc|cpp|cxx|hpp|swift|dart|lua|sh|bash|sql|html?|css|scss|vue|svelte|json|"
    r"ya?ml|toml|ini|cfg|xml|properties|md|proto|tf)$",
    re.IGNORECASE,
)

# Words that survive the phrase-stripping above but name a language construct rather than a
# symbol: "(at the list comprehension)" reduces to `list`, which is not a function anyone can
# agree on. A real identifier of the same spelling is vanishingly rare next to this prose.
_NOT_A_SYMBOL = frozenset({
    "list", "dict", "set", "tuple", "loop", "call", "block", "line", "code", "body",
    "comprehension", "handler", "expression", "statement", "variable", "parameter",
    "argument", "constructor", "closure", "callback", "branch", "condition", "clause",
})
_TOKEN_RE = re.compile(r"[a-z0-9_]{3,}")

# Words that appear in nearly every review and therefore carry no grouping signal. Terms a
# reviewer uses to name a defect class — race, leak, none, null, inject — are deliberately
# absent: those are exactly the words that should pull two findings together.
# A block of prose is the readable form for a word list; the equivalent list literal is a
# 90-element column nobody proofreads.
_STOPWORDS = frozenset("""
about above after again against all also always and any are because been before being
both but can cannot code could day does doing done during each either else even ever every
example few for from function had has have here how however into its just like line lines
make many may might more most much must never new not now off once one only other our out
over own same should since some such than that the their them then there these they thing
this those through thus too under until use used uses using very via was way what when
where which while who why will with within without would you your
issue fix problem code snippet review finding severity title
""".split())  # noqa: SIM905


@dataclass
class Finding:
    """One defect as a single model reported it."""

    severity: str
    title: str
    symbol: str | None
    issue: str
    fix: str
    model: str
    raw: str
    category: DefectCategory = DefectCategory.UNKNOWN

    @property
    def text(self) -> str:
        return f"{self.title} {self.issue}"


@dataclass
class Consensus:
    """One defect as the council reported it, across every model that raised it."""

    severity: str            # the highest severity any model assigned
    symbol: str | None
    title: str
    models: list[str] = field(default_factory=list)
    # How many distinct reviewers assigned each severity. Counted in reviewers rather than
    # findings so it is comparable with `agreement`; a reviewer who raised the defect twice
    # at two severities is counted under both, so the values can sum above `agreement`.
    severities: dict[str, int] = field(default_factory=dict)
    findings: list[Finding] = field(default_factory=list)
    category: DefectCategory = DefectCategory.UNKNOWN

    @property
    def agreement(self) -> int:
        """How many distinct reviewers raised it. This is the number that means something."""
        return len(self.models)

    @property
    def disputed(self) -> bool:
        """Models agreed the defect exists but not on how bad it is."""
        return len(self.severities) > 1


def _normalise_symbol(raw: str | None) -> str | None:
    """
    Reduce a locator to a comparable identifier.

    `RingBuffer.push`, `` `push` `` and `push()` all name one function; a bare `line 42` names
    nothing comparable across models, because the models do not agree on line numbers and the
    prompt tells them not to invent any.
    """
    if not raw:
        return None
    text = raw.strip()
    # A phrase that quotes the symbol is usable; the prose around it is not.
    backticked = re.search(r"`([^`]{1,60})`", text)
    if backticked:
        text = backticked.group(1)
    text = text.strip("`'\"").strip()
    if not text or _LINE_ONLY_RE.match(text):
        return None
    # Drop a trailing ", line 12" so "parse_row, line 12" still groups with "parse_row", and a
    # file name, so "GroupAdapter.java, getSubGroupsCount" names the method.
    parts = [part.strip() for part in re.split(r"[,;]", text)]
    text = next((part for part in parts if not _FILE_NAME_RE.search(part)), "")
    text = re.sub(r"^(?:the|a|an)\s+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+(?:function|method|call|loop|block|comprehension|handler)$", "",
                  text, flags=re.IGNORECASE)
    # "def summarise(samples)" and "checkoutBranch(repoPath, branch)" are how models write a
    # signature instead of a name; both mean the symbol in front of the parentheses.
    text = re.sub(r"^(?:def|async def|function|class|fn)\s+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*\(.*$", "", text).strip()
    if not _IDENT_CHAIN_RE.match(text):
        return None
    idents = _IDENT_RE.findall(text)
    if not idents:
        return None
    # The last dotted component is the most specific name: `RingBuffer.push` -> `push`.
    # Case is preserved for display — a reader given `currentuser` cannot grep for it —
    # while every comparison below folds case.
    symbol = idents[-1]
    return None if symbol.lower() in _NOT_A_SYMBOL else symbol


def _tokens(text: str) -> set[str]:
    return {t for t in _TOKEN_RE.findall(text.lower()) if t not in _STOPWORDS}


def _finding_tokens(finding: Finding) -> set[str]:
    """Words of the finding, plus its symbol as a token of its own so a shared name counts."""
    tokens = _tokens(finding.text)
    if finding.symbol:
        tokens.add(f"sym:{finding.symbol.lower()}")
    return tokens


# Bullets, headings and an ordered-list marker. Digits are only stripped as part of "1." or
# "2)": a blanket digit class ate the first word of a title like "42 bytes over the limit".
_BULLET_RE = re.compile(r"^[\s>#*\-]*(?:\d+[.)]\s*)?[\s>#*\-]*")


def _strip_markdown(text: str) -> str:
    return _BULLET_RE.sub("", text).replace("**", "").replace("`", "").strip(" :.-")


def parse_findings(content: str | None, model: str) -> list[Finding]:
    """
    Extract findings from one model's review.

    Returns an empty list for `NO_ISSUES_FOUND`, for an empty response, and for prose that
    never produced a severity tag — all three mean "this model contributed no finding", and
    the caller must not be able to tell them apart by accident.

    There is deliberately no JSON path. The prompt never asks for JSON, no recorded review
    contains any, and one that tried the first code fence before this parser replaced a whole
    review with a single bogus finding whenever a Fix quoted a JSON body — an RFC 7807 error
    object has a `title` — silently dropping every real finding around it.
    """
    if not content:
        return []

    lines = content.splitlines()
    starts = [i for i, line in enumerate(lines) if _SEVERITY_RE.search(line)]
    findings: list[Finding] = []

    for n, start in enumerate(starts):
        end = starts[n + 1] if n + 1 < len(starts) else len(lines)
        block = lines[start:end]
        head = block[0]
        match = _SEVERITY_RE.search(head)
        if match is None:  # unreachable: `starts` was built from this same regex
            continue
        severity, category, title_raw = _read_tag(match, head[match.end():].strip())

        symbol_match = _LOCATOR_RE.search(title_raw)
        symbol = None
        if symbol_match:
            symbol = _normalise_symbol(symbol_match.group(1))
            title_raw = title_raw[: symbol_match.start()]
        written = _strip_markdown(title_raw)
        # A filled-in title often keeps the `<Concise Title>` placeholder's brackets.
        title = re.sub(r"^<(.+)>$", r"\1", _TITLE_PLACEHOLDER_RE.sub("", written).strip())
        # Nothing but placeholders is the template line quoted, mostly inside reasoning about it.
        echoed = bool(written) and not title

        # A tag with nothing to report is a heading over findings, not one: `### [SECURITY]`
        # parsed as a finding invented an "(untitled)" CRITICAL for every section a model made.
        # A title alone still counts — four recorded findings were written on a single line.
        heading = head.lstrip().startswith("#") and match.group(1).upper() in _AXIS_TO_SEVERITY

        issue_parts: list[str] = []
        fix_parts: list[str] = []
        # Some models leave the issue unlabelled: the first paragraph under the title, as a
        # bullet above `- Fix:` or after a blank line. Dropping it cost two recorded models the
        # explanation of 70 of their 148 findings. It is read only under a tag that opens its line —
        # chain of thought leaked into an answer names tags mid-sentence, and the paragraph after
        # one of those is more of it — and never under a section heading or an echoed template.
        unlabelled: list[str] = []
        opens_line = _BULLET_RE.fullmatch(head[: match.start()]) is not None
        current: list[str] | None = unlabelled if opens_line and not heading and not echoed else None
        for line in block[1:]:
            field_match = _FIELD_RE.match(line)
            if field_match:
                current = issue_parts if field_match.group(1).lower() == "issue" else fix_parts
                current.append(field_match.group(2).strip())
            elif current is not None and line.strip():
                text = line.strip()
                current.append(_BULLET_RE.sub("", text) if current is unlabelled else text)
            elif not line.strip() and (current is not unlabelled or unlabelled):
                # A blank line ends a field, except the gap between a title and its paragraph.
                current = None

        issue = " ".join(issue_parts or unlabelled).strip()
        fix = " ".join(fix_parts).strip()
        issue = "" if _FIELD_PLACEHOLDER_RE.fullmatch(issue) else issue
        fix = "" if _FIELD_PLACEHOLDER_RE.fullmatch(fix) else fix
        if symbol is None:
            # Models that omit the locator almost always name the symbol in backticks instead.
            backticked = re.search(r"`([^`]{1,60})`", f"{title_raw} {issue}")
            symbol = _normalise_symbol(backticked.group(1)) if backticked else None

        # A tag named mid-sentence with no field under it is reasoning that weighs a severity,
        # and the rest of its sentence is no title: "or [CRITICAL] depending). Need concrete
        # scenario…", "maybe [CRITICAL] if it leads to crash", ")". Across every recording on
        # disk, 23 findings had this shape and not one of them was a finding.
        if not issue and not fix and (heading or not title or not opens_line):
            continue
        if _SELF_ANSWERED_RE.search(issue):
            continue

        findings.append(
            Finding(
                severity=severity,
                category=category,
                title=title or (issue[:80] if issue else "(untitled)"),
                symbol=symbol,
                issue=issue,
                fix=fix,
                model=model,
                raw="\n".join(block).strip(),
            )
        )
    return findings


def is_review(content: str | None) -> bool:
    """
    Whether an answer is a review at all: a finding, or the clean verdict.

    `parse_findings` deliberately cannot tell "found nothing" from "said nothing", but a council
    seat must. On a public benchmark 12 of 200 seats went to answers that were neither: a small
    model's raw tool calls (`<|tool_call_start|>[read_file(...)]`), a run of `<unk>` tokens,
    18k characters of reasoning that never reached a tag, and a lone `* [WARNING] LOGIC`. Each
    counted as a reviewer that had looked and found nothing.
    """
    if not content:
        return False
    return "NO_ISSUES_FOUND" in content or bool(parse_findings(content, ""))


# Calibrated by sweeping evals/ over the three recordings of the corpus, pooled, against two
# measurements that pull in opposite directions:
#
#   grouped  — models that independently found one labelled defect landed in one cluster
#   split    — two findings ONE model wrote separately stayed in separate clusters. That
#              model mostly meant two defects, so this needs no labels and catches the
#              over-merging the labelled measure rewards: a cluster that swallows a whole
#              snippet "groups" every defect in it.
#
# Jaccard punishes a verbose finding for being verbose: two models describing one bug in 20
# and 120 words share most of the short one's vocabulary and almost none of the union. The
# overlap coefficient asks the question that actually matters, whether the smaller finding is
# contained in the larger.
#
# Re-swept when first-fit gave way to single linkage (see `_linked_groups`). Single linkage
# chains: one link at the threshold joins two whole clusters, so the value that suited
# first-fit merged distinct defects. Judged by hand, about two in three of the links scoring
# 0.20-0.24 that joined two clusters joined two different defects — a ZeroDivisionError to a
# wrong median, a race to a failed connect — against about half of those scoring 0.24-0.28.
#
#                        grouped   split     F
#     first-fit   0.16     62%      56%     0.59   <- previous
#     single      0.16     79%      37%     0.51
#     single      0.20     71%      53%     0.61
#     single      0.22     65%      61%     0.63
#     single      0.24     61%      69%     0.65   <- chosen
#     single      0.26     49%      75%     0.59
#     single      0.28     43%      81%     0.56
#
# F is flat across 0.23-0.24, and 0.24 is taken for the half of the trade F understates: an
# over-merged cluster hides one defect's title inside another's entry. Chosen on any two of
# the recordings, the best threshold was 0.23 or 0.24, and it beat first-fit on the third.
#
# Grouping is lexical, and a four-axis review is a more varied vocabulary than a defect
# report, so overlap carries less signal than it did when the reviewers reported defects
# alone. Three recordings of one corpus spread wider than neighbouring rows of this table,
# so a sweep over a single recording decides little.
#
# A closed category vocabulary was tried as the signal that does not dilute with wording, and
# it carries little. Across three recordings, models filing one labelled defect chose the same
# category in only 51-59% of pairs, so as a veto it cost 1 to 6 of some 35 agreed defects
# their single entry per recording. As a discount on this threshold (0.10 for a shared
# category, waived outright for a shared symbol) it was worse: on the earlier recordings it
# merged 11 pairs of findings one model had reported as distinct against 14 pairs that were
# one defect, across the 0.06-0.16 band, and two defects in one function, each raised by two
# models, came out as a single entry claiming four. The category is reported on every entry
# and ignored here.
_SIMILARITY_THRESHOLD = 0.24

# A single shared word is not agreement: containment alone would let a two-word finding join
# any long one that happens to repeat it.
_MIN_SHARED_TOKENS = 2


def _weights(findings: list[Finding]) -> dict[str, float]:
    """
    How much each word counts towards "these are the same defect".

    Plain Jaccard treats `empty` and `division` as equally informative, and every review of
    the same function shares its filler vocabulary. Weighting a word by how rare it is among
    the findings under comparison is what stops two unrelated defects in one function from
    merging on the words they were bound to share.
    """
    document_frequency: dict[str, int] = {}
    for f in findings:
        # The same token set the comparison uses, symbol included: weighting only the prose
        # would leave the symbol on the default weight, which is an accident, not a design.
        for token in _finding_tokens(f):
            document_frequency[token] = document_frequency.get(token, 0) + 1
    total = max(1, len(findings))
    return {t: math.log(1 + total / df) for t, df in document_frequency.items()}


def _similarity(a: set[str], b: set[str], weights: dict[str, float]) -> float:
    """Weighted overlap coefficient: how much of the smaller finding the larger one covers."""
    if not a or not b:
        return 0.0
    common = a & b
    # Exempting a shared symbol from this bound was tried and measured: across ~250 recorded
    # findings it did not change a single clustering decision, because one shared token out
    # of a whole finding never reaches the threshold anyway. Dropped rather than kept as an
    # unfalsifiable special case.
    if len(common) < _MIN_SHARED_TOKENS:
        return 0.0
    # `fsum`, not `sum`: a set iterates in an order the hash seed picks, and float addition is
    # not associative, so a plain sum can differ in its last bit between two runs. A score that
    # lands on the threshold would then fall on either side of it.
    shared = math.fsum(weights.get(t, 1.0) for t in common)
    smaller = min(math.fsum(weights.get(t, 1.0) for t in a), math.fsum(weights.get(t, 1.0) for t in b))
    return shared / smaller if smaller else 0.0


def _linked_groups(tokens_of: list[set[str]], weights: dict[str, float]) -> list[list[int]]:
    """
    Single linkage: findings joined by a similarity at or above the threshold, directly or
    through other findings, form one group. Returned as positions, each group in ascending
    order and the groups ordered by their first position.

    This used to be first-fit — each finding joined the first group it resembled, and groups
    were never joined — which is single linkage only when the findings happen to arrive in a
    helpful order. If A resembles B and B resembles C but A does not resemble C, first-fit
    returned one group or two depending on the order they arrived in, and that order is the
    order the models answered in. On one recording, twenty shuffles of the response order moved
    the share of agreed defects landing in one entry anywhere from 44% to 69%. Union-find over
    every pair computes the connected components, which no order can change.

    Each finding is compared with every member, not with the group as a whole: comparing
    against the concatenation makes a group harder to join the more it has already absorbed,
    which is backwards.
    """
    parent = list(range(len(tokens_of)))

    def root(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for a, b in itertools.combinations(range(len(tokens_of)), 2):
        root_a, root_b = root(a), root(b)
        if root_a == root_b:
            continue
        if _similarity(tokens_of[a], tokens_of[b], weights) >= _SIMILARITY_THRESHOLD:
            # The smaller position stays the root, so a group is keyed by its first member.
            parent[max(root_a, root_b)] = min(root_a, root_b)

    groups: dict[int, list[int]] = {}
    for position in range(len(tokens_of)):
        groups.setdefault(root(position), []).append(position)
    return list(groups.values())


def _order_key(f: Finding) -> tuple[str, ...]:
    """A total order on findings, drawn from the findings themselves: the reviewer, then the text."""
    return (f.model, f.raw, f.title, f.issue, f.fix, f.symbol or "", f.severity, f.category.value)


def cluster_findings(findings: list[Finding]) -> list[Consensus]:
    """
    Group findings that describe one defect and rank by how many reviewers raised it.

    Grouping is on wording weighted by rarity, with the symbol as one more (heavily weighted)
    word. Keying on `(symbol, severity)` — the obvious first cut — splits the strongest signal
    the council can produce: two models finding one bug and disagreeing only on how bad it is,
    or naming it after two different identifiers. Categories are carried onto each entry for
    the reader and play no part in grouping; see `_SIMILARITY_THRESHOLD` for why.

    The result depends on the findings and never on the order they come in, which is the order
    the models answered in.
    """
    # Every tie below goes to whichever finding comes first: the title among equally severe
    # findings, the symbol and the category the group split on, the order of equally ranked
    # entries. Sorting first makes "first" a property of the findings rather than of which model
    # answered first, and leaves nothing to the hash seed.
    findings = sorted(findings, key=_order_key)
    weights = _weights(findings)
    # Indexed rather than keyed on the object: `Finding` is a mutable dataclass and therefore
    # unhashable, and keying on `id()` is only safe for as long as every object stays alive.
    tokens_of = [_finding_tokens(f) for f in findings]

    # The symbol is a strong signal, not a partition. Bucketing on it looked obviously right
    # and measured badly on the recorded corpus: for one command-injection defect one model
    # wrote `(at archive_directory)` and another `(at src)`, and for one JWT defect one model
    # gave no locator at all — three names for one bug, and three clusters of one voice each.
    groups = _linked_groups(tokens_of, weights)

    clusters: list[Consensus] = []
    for positions in groups:
        group = [findings[i] for i in positions]
        by_severity: dict[str, set[str]] = {}
        for f in group:
            by_severity.setdefault(f.severity, set()).add(f.model)
        severities = {sev: len(models) for sev, models in by_severity.items()}
        worst = max(group, key=lambda f: SEVERITY_ORDER.get(f.severity, 0))
        # Distinct reviewers, not raw findings: one model listing a defect twice is one
        # opinion, and counting it twice would manufacture consensus out of verbosity.
        models = sorted({f.model for f in group})
        # The name most of the group used, spelled the way the models spelled it; a model
        # that omitted the locator does not get to erase the one the others agreed on. Counted
        # in order rather than picked from a set, which left a tie to the hash seed.
        named = [f.symbol for f in group if f.symbol]
        symbol = None
        if named:
            winner = Counter(n.lower() for n in named).most_common(1)[0][0]
            symbol = Counter(n for n in named if n.lower() == winner).most_common(1)[0][0]

        # Reviewers often file one defect under two categories. The entry shows the one most of
        # them chose and, on a tie, the one that comes first in the order above.
        declared = Counter(f.category for f in group if f.category is not DefectCategory.UNKNOWN)
        category = declared.most_common(1)[0][0] if declared else DefectCategory.UNKNOWN

        clusters.append(
            Consensus(
                severity=worst.severity,
                symbol=symbol,
                title=worst.title,
                models=models,
                severities=severities,
                findings=group,
                category=category,
            )
        )

    # A stable sort, so equally ranked entries keep the order of their first findings.
    clusters.sort(
        key=lambda c: (c.agreement, SEVERITY_ORDER.get(c.severity, 0), len(c.findings)),
        reverse=True,
    )
    return clusters


def build_consensus(responses) -> list[Consensus]:
    """
    Cluster findings across a council result.

    A seat is identified by the model that actually answered, so a router seating one model
    twice contributes one voice, exactly as it does for the success threshold.
    """
    findings: list[Finding] = []
    for r in responses:
        if getattr(r, "duplicate_of", None):
            continue
        seat = getattr(r, "served_model", None) or r.model
        findings.extend(parse_findings(r.content, seat))
    return cluster_findings(findings)
