"""Documentation must not drift from the code it describes."""

import pathlib
import re
import unicodedata
import urllib.parse

import pytest

from fourtake import __version__

ROOT = pathlib.Path(__file__).resolve().parent.parent
_JSON_VERSION = re.compile(r'"version":\s*"([^"]+)"')


@pytest.mark.parametrize("doc", ["README.md", "AGENTS.md", "SKILL.md", "llms.txt"])
def test_json_examples_show_the_current_version(doc):
    # The README and AGENTS.md examples kept saying 0.1.0 for a release after the CLI began
    # printing 0.2.0, so an agent parsing the documented schema saw a version nothing emits.
    path = ROOT / doc
    if not path.exists():
        pytest.skip(f"{doc} is not in this checkout")
    for found in _JSON_VERSION.findall(path.read_text(encoding="utf-8")):
        assert found == __version__, f'{doc} shows "version": "{found}", the package is {__version__}'


def test_changelog_names_the_current_version():
    # A release whose notes still sit under [Unreleased] cannot be told apart from the next one.
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert re.search(rf"^## \[{re.escape(__version__)}\]", text, re.MULTILINE), (
        f"CHANGELOG.md has no [{__version__}] section"
    )


def test_changelog_has_no_repeated_headings_within_a_release():
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    for release in re.split(r"^## ", text, flags=re.MULTILINE)[1:]:
        headings = re.findall(r"^### (.+)$", release, re.MULTILINE)
        repeated = {h for h in headings if headings.count(h) > 1}
        assert not repeated, f"{release.splitlines()[0]!r} repeats {sorted(repeated)}"


def _github_anchor(heading: str) -> str:
    # GitHub keeps letters, marks, digits and connector punctuation. An emoji drops out, but the
    # variation selector after ⚙️ or 🏗️ is a mark and stays, so the anchor starts with an
    # invisible U+FE0F before the hyphen. Two nav links that left it out went nowhere.
    text = re.sub(r"`|\*\*|\[|\]\([^)]*\)", "", heading).lower()
    kept = (
        c for c in text if c in " -" or unicodedata.category(c)[0] in "LMN" or unicodedata.category(c) == "Pc"
    )
    return "".join(kept).replace(" ", "-")


def test_readme_links_point_at_headings():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    anchors, seen = set(), {}
    in_code = False
    for line in text.splitlines():
        if line.startswith("```"):
            in_code = not in_code
        elif not in_code and (m := re.match(r"#{1,6} (.+?)\s*$", line)):
            base = _github_anchor(m.group(1))
            n = seen.get(base, 0)
            seen[base] = n + 1
            anchors.add(base if n == 0 else f"{base}-{n}")
    broken = [f for f in re.findall(r"\]\(#([^)]+)\)", text) if urllib.parse.unquote(f) not in anchors]
    assert not broken, f"README.md links to anchors no heading produces: {broken}"
