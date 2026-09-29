"""
Secret redaction applied to every payload before it leaves the machine.

This is best-effort defence in depth, not a guarantee: regexes cannot recognise every
secret, and a value that looks like ordinary code is deliberately left alone rather than
mangling the snippet under review. Treat it as a safety net, not a licence to pipe
production configuration into a third-party model.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass

REDACTED = "***REDACTED***"

# Key names that make the assigned value a secret.
# `secret_key` is spelled out because the key has to END with a secret word, and `key` alone is
# not one: Django's and Flask's SECRET_KEY went out verbatim, in the settings file an agent is
# told to send as reference context.
_SECRET_WORD = (
    r"(?:password|passwd|pwd|api[_-]?key|apikey|secret|secret[_-]?key|token|auth|authorization|"
    r"credential|private[_-]?key|access[_-]?key|access[_-]?token|client[_-]?secret)"
)

# The secret word has to be a whole component of the key, not a substring of one. Matching it
# anywhere turned every ordinary identifier that contains it into a secret: `tokens` matched
# `token`, so `tokens = tokens_of[position]` was rewritten to `tokens = ***REDACTED***]` and
# the models were asked to review code with a syntax error nobody wrote.
_SECRET_KEY = rf"(?:^|(?<=[^a-z0-9]))[a-z0-9]*(?:[_.\-][a-z0-9]+)*?{_SECRET_WORD}(?=$|[^a-z0-9])"

# Not a secret in any position: a placeholder, an interpolation, an already-masked value.
_NOT_A_LITERAL = re.compile(
    r"""^(?:
        (?:None|null|nil|true|false|True|False|undefined)$
      | (?:bearer|basic|digest)$                             # an auth scheme, not the token
      | \$                                                   # $VAR / ${VAR} interpolation
      | <                                                    # <placeholder>
      | \{\{                                                 # {{ template }}
      | (?:x{3,}|\*{3,}|\.{3,})$                             # already masked
      | (?:your|my|the|some|a)[_-]                           # your_api_key_here
    )""",
    re.IGNORECASE | re.VERBOSE,
)

# Additionally rejected for an UNQUOTED value only. Between quotes, `"super_secret"` is a
# literal by construction; without them the same characters are almost always an expression,
# and redacting `api_key = provider.api_key` corrupts the snippet without protecting
# anything — the council then reviews code the user never wrote. Every entry here is a real
# false positive, found by running 4take over its own source or over a benchmark's TypeScript.
_LOOKS_LIKE_CODE = re.compile(
    r"""^(?:
        (?:os|process|config|settings|self|cls|this|env|Deno)(?:[.\[]|$)   # a lookup, not a value
      | [-+]?[0-9][0-9_.]*(?:[eE][-+]?[0-9]+)?$              # a number is never a secret
      | _                                                    # _private_name
      | [A-Za-z0-9.]*_[A-Za-z0-9_.]*$                        # snake_case / attribute chain
      | [^\[\]]*[\[\]]                                      # indexing
      | \{                                                   # f"token={token}", a placeholder
      | (?:string|number|boolean|bigint|symbol|object|unknown|never)$   # a TypeScript type
    )""",
    re.IGNORECASE | re.VERBOSE,
)


def _looks_like_a_literal_secret(value: str, quoted: bool = True) -> bool:
    if len(value) < 4:
        return False
    if "(" in value or ")" in value:
        return False
    if _NOT_A_LITERAL.search(value):
        return False
    return quoted or not _LOOKS_LIKE_CODE.search(value)


@dataclass(frozen=True)
class _Rule:
    """One detection rule. `replacement` is passed straight to re.sub."""

    name: str
    pattern: re.Pattern
    replacement: str | Callable[[re.Match], str]


# Outside quotes a secret is a config value — `.env`, YAML, INI, a shell prefix — and there it
# ends its line, or sits against a tight `KEY=` that a command may follow. In code the same key
# is followed by a type, a variable or an expression: `token = await fetch()`,
# `(credential: CredentialPayload | null)`, `password: string;`. Redacting their first word
# handed the council code with a hole in it: six of ten cal.com PRs in a public benchmark, and
# the async-race snippet in evals/, whose `await` went out as ***REDACTED*** in every recording
# and drew "missing await" and SyntaxError findings.
_TIGHT_EQUALS = re.compile(r"[^ \t]=$")
_ENDS_ITS_LINE = re.compile(r"[ \t\r]*(?:(?:#|//).*)?$", re.MULTILINE)


def _is_config_value(match: re.Match) -> bool:
    """A value against a tight `KEY=`, or one that ends its line."""
    return bool(_TIGHT_EQUALS.search(match.group(1))
                or _ENDS_ITS_LINE.match(match.string, match.end()))


# The rules match only the key's last word — `token` in `self._token` — so the rest of the name
# is read back from the text in front of it.
_KEY_HEAD = re.compile(r"[\w.$-]*$")


def _names_its_own_key(match: re.Match, value: str) -> bool:
    """`self._token = token`, `REFRESH_TOKEN = "refresh_token"`: a name handed on, not a secret."""
    head = _KEY_HEAD.search(match.string, max(0, match.start() - 200), match.start())
    key = (head.group(0) if head else "") + match.group(1).strip("'\" \t:=")
    key, value = key.lower(), value.lower()
    return key == value or key.endswith((f".{value}", f"_{value}", f"-{value}"))


def _redact_assignment_value(match: re.Match) -> str:
    """Redact the value of a `key = value` pair, leaving the key and syntax intact."""
    prefix, value = match.group(1), match.group(2)
    if (not _is_config_value(match) or _names_its_own_key(match, value)
            or not _looks_like_a_literal_secret(value, quoted=False)):
        return match.group(0)
    return f"{prefix}{REDACTED}"


def _redact_quoted_value(match: re.Match) -> str:
    """Same, for a quoted value; the closing quote is preserved."""
    prefix, value, suffix = match.group(1), match.group(2), match.group(3)
    if _names_its_own_key(match, value) or not _looks_like_a_literal_secret(value):
        return match.group(0)
    return f"{prefix}{REDACTED}{suffix}"


def _redact_header_credential(match: re.Match) -> str:
    """The credential after the auth scheme, unless the program fills it in at run time."""
    prefix, value = match.group(1), match.group(2)
    # `$TOKEN`, `${token}`, `{token}`, `%s`, `#{token}`, `{{ token }}`: an interpolation names
    # where the secret comes from, not the secret. `curl -H "Authorization: Bearer $TOKEN"` went
    # out as `Bearer ***REDACTED***` with its closing quote eaten, a shell line nobody wrote.
    if _NOT_A_LITERAL.search(value) or value.startswith(("{", "%", "#{")):
        return match.group(0)
    return f"{prefix}{REDACTED}"


_RULES: list[_Rule] = [
    # `Authorization: Bearer <credential>` — first, because the assignment rules below see
    # the scheme word as the value and would redact "Bearer" while sending the credential
    # straight out. That is the failure this module exists to prevent, and it shipped.
    # The credential stops at a quote, so the string that holds the header stays closed.
    _Rule(
        "auth-header",
        re.compile(
            r"(?i)((?:proxy-)?authorization[ \t]*[:=][ \t]*"
            r"(?:bearer|basic|digest|token|apikey|api-key)[ \t]+)([^\s'\"`]+)"
        ),
        _redact_header_credential,
    ),
    # Quoted assignment: api_key = "..." / {"token": "..."}
    _Rule(
        "assignment",
        re.compile(
            rf"""(?i)(['"]?{_SECRET_KEY}['"]?[ \t]*[:=][ \t]*['"])(.*?)(['"])"""
        ),
        _redact_quoted_value,
    ),
    # Unquoted assignment: DB_PASSWORD=hunter2, `password: hunter2` in YAML, .env lines.
    # These are the commonest real-world forms and were previously missed entirely, because
    # the rule above requires a quote right after the separator.
    _Rule(
        "bare-assignment",
        re.compile(
            rf"""(?i)({_SECRET_KEY}[ \t]*[:=][ \t]*)([^\s'"`,;#\]}}]+)"""
        ),
        _redact_assignment_value,
    ),
    # OpenAI / Anthropic / OpenRouter
    _Rule("openai-style", re.compile(r"\b(sk-[a-zA-Z0-9_-]{20,})\b"), REDACTED),
    # Stripe
    _Rule("stripe", re.compile(r"\b((?:sk|rk|pk)_(?:live|test)_[0-9A-Za-z]{16,})\b"), REDACTED),
    # GitHub
    _Rule("github-pat", re.compile(r"\b(gh[pousr]_[A-Za-z0-9_]{36,})\b"), REDACTED),
    _Rule("github-fine-grained", re.compile(r"\b(github_pat_[A-Za-z0-9_]{22,})\b"), REDACTED),
    # GitLab
    _Rule("gitlab-pat", re.compile(r"\b(glpat-[A-Za-z0-9_-]{20,})\b"), REDACTED),
    # Slack
    _Rule("slack", re.compile(r"\b(xox[baprse]-[A-Za-z0-9-]{10,})\b"), REDACTED),
    # Google / Firebase
    _Rule("google-api-key", re.compile(r"\b(AIza[0-9A-Za-z_-]{35})\b"), REDACTED),
    # Telegram bot token, `<bot id>:<secret>`. It usually sits inside a URL —
    # `api.telegram.org/bot<token>/sendMessage` — where no key name precedes it, so the
    # assignment rules above never saw it. The secret's length varies by a character or two
    # between issued tokens, hence the range.
    _Rule(
        "telegram-bot-token",
        re.compile(r"(?<![0-9])([0-9]{5,16}:A[A-Za-z0-9_-]{32,36})(?![A-Za-z0-9_-])"),
        REDACTED,
    ),
    # Groq
    _Rule("groq", re.compile(r"\b(gsk_[A-Za-z0-9]{20,})\b"), REDACTED),
    # AWS access key id
    _Rule("aws-access-key-id", re.compile(r"\b(AKIA[0-9A-Z]{16})\b"), REDACTED),
    # Bearer JWT
    _Rule(
        "bearer-jwt",
        re.compile(r"\b(Bearer\s+eyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+)\b"),
        "Bearer ***REDACTED JWT***",
    ),
    # Bare JWT
    _Rule(
        "jwt",
        re.compile(r"\beyJ[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}\b"),
        "***REDACTED JWT***",
    ),
    # PEM private keys
    _Rule(
        "private-key",
        re.compile(
            r"-----BEGIN[ A-Z0-9_-]*PRIVATE KEY-----[\s\S]*?-----END[ A-Z0-9_-]*PRIVATE KEY-----"
        ),
        "***REDACTED PRIVATE KEY***",
    ),
    # Credentials embedded in a connection string. The user name may be empty (`redis://:pw@host`,
    # which used to go out whole) and, as RFC 3986 has it, holds no `/?#@`, so a colon further on in
    # the path is not a password. Nor is a port followed by a path: `https://host:8443/x/@me` came
    # out as `https://host:***REDACTED***@me`.
    _Rule(
        "connection-string",
        re.compile(r"([a-zA-Z][a-zA-Z0-9+.-]*://[^:/?#@\s]*):(?!\d+(?:[/?#]|$))([^@\s]+)(@[^\s/]+)"),
        r"\1:" + REDACTED + r"\3",
    ),
]


def redact_secrets_with_report(text: str) -> tuple[str, dict[str, int]]:
    """
    Redact secrets and report how many matches each rule made.

    The report exists so the CLI can say what it removed: broad patterns can misfire, and a
    silent redaction that eats real code degrades the review with no way to notice.
    """
    if not text:
        return text, {}

    report: dict[str, int] = {}
    result = text
    for rule in _RULES:
        # `subn` counts matches, not changes. The assignment rules deliberately return the
        # text untouched when the value turns out to be code, so the count included every
        # rejection: `--show-redactions` reported four secrets removed from input that had
        # two, which defeats the flag's whole purpose of spotting over-redaction.
        applied = 0

        def _apply(match: re.Match, _rule: _Rule = rule) -> str:
            nonlocal applied
            replacement = _rule.replacement
            new = replacement(match) if callable(replacement) else match.expand(replacement)
            if new != match.group(0):
                applied += 1
            return new

        result = rule.pattern.sub(_apply, result)
        if applied:
            report[rule.name] = report.get(rule.name, 0) + applied
    return result, report


def redact_secrets(text: str) -> str:
    """
    Redact common secrets from text before sending it to an external model.

    Covers quoted and unquoted key/value assignments, provider-specific key formats
    (OpenAI, Stripe, GitHub, GitLab, Slack, Google, AWS), JWTs, PEM private keys and
    credentials inside connection strings.
    """
    redacted, _ = redact_secrets_with_report(text)
    return redacted
