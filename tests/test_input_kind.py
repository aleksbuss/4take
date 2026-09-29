"""
The classifier that stands between a design question and four wasted requests.

Its two errors do not cost the same, so they are not tested the same: every file in the eval
corpus must be admitted, and prose must be refused. The corpus sweep is the load-bearing
test — it is the only one that grows when someone adds a language.
"""

import pathlib

import pytest

from fourtake.input_kind import looks_like_code

CORPUS = pathlib.Path(__file__).resolve().parent.parent / "evals" / "corpus"


@pytest.mark.parametrize("path", sorted(CORPUS.iterdir()), ids=lambda p: p.name)
def test_no_corpus_snippet_is_ever_refused(path):
    """Refusing real code blocks a review the user asked for. This may not regress."""
    assert looks_like_code(path.read_text())


PROSE = {
    "russian_architecture_question": """
Как я понимаю, на данный момент при проверке он ищет синтаксические ошибки. Но модели уже
практически не делают синтаксических ошибок. Проверять нужно структуру, логику и чистоту
решений. Я так понимаю, что нам нужно переписывать системные инструкции.
Если у тебя есть какие-то вопросы, задай их.
""",
    "english_design_doc": """
We want to move notification delivery out of the request path. Today the handler blocks on
the provider call, which means a slow provider becomes a slow API for everyone.

The handler writes an intent row and returns immediately. A worker picks the row up and
performs delivery, retrying with backoff. Delivery state lives on the row, so an operator
can answer whether a user got the email without reading logs.
""",
    "options_writeup_with_a_colon_heading": """
Open questions: we considered three options for the cache layer and picked the second.

The first was process memory. It is the fastest and it is free, but it does not survive a
restart and every replica holds its own copy.

The second was Redis. It costs money and it is one more thing that can be down, but the
state is shared and eviction is somebody else's problem.
""",
    # An address in angle brackets opens a line the way a tag does; it is not markup.
    "notes_with_an_address_in_brackets": """
Contact the team lead for access to the staging cluster.
<john@example.com> handles the rollout this week.
We ship on Friday after the review.
""",
}


@pytest.mark.parametrize("name", sorted(PROSE), ids=list(sorted(PROSE)))
def test_prose_is_refused(name):
    assert not looks_like_code(PROSE[name])


ACCEPTED = {
    # A shebang, a hunk header or a language-tagged fence is enough on its own.
    "shell_script": "#!/bin/sh\nset -eu\npg_dump \"$DATABASE_URL\" > dump.sql\n",
    "diff_hunk_alone": "@@ -12,6 +12,9 @@ export default {\n   async fetch(request, env) {\n",
    # A diff whose content is prose — this project's own system-prompt edits score 0.06 on
    # density and pass on the header alone. Reviewing a prompt change is a real use.
    "diff_of_a_prose_file": (
        "diff --git a/src/fourtake/prompts.py b/src/fourtake/prompts.py\n"
        "--- a/src/fourtake/prompts.py\n"
        "+++ b/src/fourtake/prompts.py\n"
        "@@ -8,23 +26,27 @@ Input Handling:\n"
        "-- [SUGGESTION]: Significant architectural defect or severe performance pitfall.\n"
        "+- [SUGGESTION]: A structure or simplicity finding whose cost you can name.\n"
    ),
    "prose_around_a_fenced_block": (
        "When a Worker serves HTML from a template literal the closing tag has to be\n"
        "escaped, or the page renders white.\n\n"
        "```js\nconst html = `<script>x<\\/script>`;\n```\n\n"
        "This has bitten us in production twice.\n"
    ),
    # Config and query languages carry none of those markers and must pass on density alone.
    "yaml_workflow": "name: deploy\non:\n  push:\n    branches: [main]\njobs:\n  build:\n    runs-on: ubuntu-latest\n",
    "sql_query": "SELECT u.id, count(o.id) AS orders\nFROM users u\nLEFT JOIN orders o ON o.user_id = u.id\nGROUP BY u.id\n",
    "toml_wrangler": 'name = "mystic"\nmain = "src/index.js"\ncompatibility_date = "2025-01-01"\n',
    # Markup carries no keyword, bracket or semicolon at a line's end; its tags are the signal.
    "html_page": (
        "<!doctype html>\n<html lang=\"en\">\n<head>\n  <title>Login</title>\n</head>\n<body>\n"
        "  <form action=\"/login\" method=\"post\">\n"
        "    <input name=\"password\" type=\"password\" required>\n"
        "    <button type=\"submit\">Go</button>\n  </form>\n</body>\n</html>\n"
    ),
    "xml_config": (
        "<project>\n  <modelVersion>4.0.0</modelVersion>\n  <groupId>org.example</groupId>\n"
        "  <artifactId>app</artifactId>\n</project>\n"
    ),
    # Cyrillic is not evidence of prose; this project's code is full of Russian comments.
    "js_with_russian_comments": (
        "// Персона выбирается пользователем, затем начинается чат.\n"
        "export async function handlePersona(update, env) {\n"
        "  const persona = await env.SESSIONS.get(update.message.chat.id);\n"
        "  return persona ? reply(persona) : sendKeyboard(update);\n"
        "}\n"
    ),
}


@pytest.mark.parametrize("name", sorted(ACCEPTED), ids=list(sorted(ACCEPTED)))
def test_non_source_languages_are_still_accepted(name):
    assert looks_like_code(ACCEPTED[name])


def test_too_short_to_judge_is_admitted():
    """A one-line function and a one-line question are indistinguishable; admit both."""
    assert looks_like_code("What should I do here?")
    assert looks_like_code("return a / b")
