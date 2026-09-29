"""
Tell source code from prose before a council is dispatched.

A design question piped into 4take used to come back as four copies of the NO_ISSUES_FOUND
sentinel: the system prompt frames its input as code to audit, so a model that obeys the
instruction has nothing to report about a paragraph of Russian. The compliant models looked
broken and the one model that ignored the instruction looked smart. Refusing up front is
the honest answer — 4take reviews code.

The bar is deliberately lopsided. Rejecting real code blocks a review the user wanted;
accepting prose only reproduces the old behaviour, which `--force-review` exists to reach on
purpose. So every ambiguous input is code.
"""

from __future__ import annotations

import re

# Markers no prose carries by accident. One is enough to accept the whole input.
_STRONG_MARKERS = re.compile(
    r"""^\s*(?: diff\ --git\           # a unified diff, whatever its content
              | @@\ .*\ @@             # ... or one of its hunk headers alone
              | (?:---|\+\+\+)\ [ab]/  # ... or its file headers
              | \#!                    # shebang
              | \#\s*include
              | <\?php
              | \x60\x60\x60\w         # a fenced block that names a language
              )""",
    re.MULTILINE | re.VERBOSE,
)

_KEYWORDS = (
    r"def|class|function|import|from|return|elif|else|for|while|try|except|catch|finally"
    r"|const|let|var|public|private|protected|static|async|await|package|namespace|struct"
    r"|enum|impl|fn|func|using|export|module|switch|case|throw|raise|with|yield|lambda"
    r"|interface"
)

# Any one of these makes a line a code line. Bare indentation is deliberately absent: a
# markdown bullet is indented too, and counting it classified plain documents as code.
_CODE_LINE = re.compile(
    rf"""(?: [{{}}\[\]();]\s*$                  # closes on a bracket or a semicolon
        | ^\s*(?:{_KEYWORDS})\b                # opens with a language keyword
        | (?:=>|->|::|===|!==|&&|\|\||\+=|-=|\*=|/=|!=|==|<=|>=)
        | ^\s*[@#]\w                           # decorator, preprocessor, or a shell comment
        | ^\s*(?://|/\*|\*\s)                  # C-family comment
        | ^\s*(?:-\s+)?[\w.\-\[\]"']+\s*[:=]\s*(?:\S|$)  # assignment, or a JSON/YAML/TOML key
        | (?i:^\s*(?:select|insert|update|delete|create|alter|drop|from|where|join
                    |left\ join|inner\ join|group\ by|order\ by|limit|having|union
                    |values|set)\b)                # a SQL clause opening a line
        | ^\s*\w+\([^)]*\)\s*[:{{;]?\s*$       # a bare call or signature
        | ^\s*<(?:/?[A-Za-z][\w.:-]*(?=[\s/>]|$)|!(?i:doctype)|!--)  # a markup tag, not <a@b.c>
        )""",
    re.VERBOSE,
)

# The gap this sits in is wide, which is the only reason a ratio is trustworthy here. Every
# file in evals/corpus scores 0.71 or above; the sparsest real input tried — a five-line
# shell script — scores 0.40; prose scores 0.00, in Russian and in English alike. A markdown
# document whose only code is a fenced block scores 0.14 and is admitted by the fence, not
# by this number. Markup has no keyword, bracket or semicolon at a line's end, so an HTML page
# scored 0.00 and was refused as prose until tag lines were counted.
_CODE_LINE_RATIO = 0.15


def looks_like_code(text: str) -> bool:
    """True unless the input is confidently prose rather than source, a diff, or config."""
    if _STRONG_MARKERS.search(text):
        return True
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) < 3:
        # Too little to measure. A one-line function and a one-line question look alike.
        return True
    return sum(bool(_CODE_LINE.search(line)) for line in lines) / len(lines) >= _CODE_LINE_RATIO
