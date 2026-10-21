"""Documentation must not drift from the code it describes."""

import pathlib
import re

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
