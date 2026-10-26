from fourtake.safety import redact_secrets


def test_redact_secrets_basic():
    input_text = "password = 'super_secret'"
    expected = "password = '***REDACTED***'"
    assert redact_secrets(input_text) == expected


def test_redact_secrets_json_style():
    input_text = '{"api_key": "sk-1234567890abcdef12345678"}'
    expected = '{"api_key": "***REDACTED***"}'
    assert redact_secrets(input_text) == expected


def test_redact_secrets_multiple():
    input_text = """
    const token = "ghp_123456789012345678901234567890123456"
    let AUTH_TOKEN="abc"
    """
    assert "***REDACTED***" in redact_secrets(input_text)


def test_redact_secrets_standalone_patterns():
    # OpenAI / OpenRouter key
    assert redact_secrets("sk-or-v1-abcdef12345678901234567890") == "***REDACTED***"

    # AWS Key
    assert redact_secrets("AWS_KEY=AKIAIOSFODNN7EXAMPLE") == "AWS_KEY=***REDACTED***"

    # Bearer JWT
    jwt = "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.doNotLeakThisSignature123"
    assert redact_secrets(jwt) == "Bearer ***REDACTED JWT***"

    # Connection String with password
    db_url = "DATABASE_URL=postgres://appuser:SecretPassword123@db.example.com:5432/production"
    assert (
        redact_secrets(db_url)
        == "DATABASE_URL=postgres://appuser:***REDACTED***@db.example.com:5432/production"
    )

    # Private Key
    rsa_key = "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA0...\n-----END RSA PRIVATE KEY-----"
    assert redact_secrets(rsa_key) == "***REDACTED PRIVATE KEY***"


def test_redact_secrets_no_match():
    input_text = "const max_retries = 10; let foo = 'bar';"
    assert redact_secrets(input_text) == input_text


def test_redact_secrets_empty():
    assert redact_secrets("") == ""
    assert redact_secrets(None) is None


# --------------------------------------------------------------------------------------
# S4.1 — forms that previously leaked, and code that must not be mangled
# --------------------------------------------------------------------------------------

import pytest

from fourtake.safety import redact_secrets_with_report


@pytest.mark.parametrize(
    "text,label",
    [
        ("DB_PASSWORD=hunter2", ".env / docker-compose"),
        ("password: hunter2", "YAML / kubernetes"),
        ("  api_key: abcd1234efgh", "indented YAML"),
        ("AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY", "AWS secret"),
        ("xoxb-123456789012-1234567890123-abcdefghijklmnopqrstuvwx", "Slack bot token"),
        ("AIzaSyD-1234567890abcdefghijklmnopqrstu", "Google API key"),
        ("sk_live_51H8xKzabcdefghijklmnop", "Stripe live key"),
        ("client_secret = GOCSPX-abcdef123456", "OAuth client secret"),
        ("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijklmnop", "bare JWT"),
        ("Authorization: Bearer abcdef123456", "HTTP auth header"),
        ("access_token=ya29.abcdefghijk", "OAuth access token"),
        ("TOKEN=abcdef123456", "the bare word, uppercase"),
        # A bot token inside the Bot API URL has no key name in front of it, so it went out.
        (
            "fetch(`https://api.telegram.org/bot110201543:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw/sendMessage`)",
            "Telegram bot token in a URL",
        ),
        ("await bot.start('7123456789:AAE-5nMqZ3rXyL0pT2vWkQ8sHjF4dGcB1aN')", "Telegram bot token, 35 chars"),
        ("new Groq({ apiKey: env, fallback: 'gsk_Q2w3E4r5T6y7U8i9O0p1A2s3D4f5G6h7J8k9' })", "Groq key"),
    ],
)
def test_previously_leaked_forms_are_redacted(text, label):
    """The old rule required a quote right after '=' or ':', so all of these went out intact."""
    assert "REDACTED" in redact_secrets(text), label


@pytest.mark.parametrize(
    "text,label",
    [
        ('api_key = os.getenv("OPENAI_API_KEY")', "reads from the environment"),
        ("token = None", "None assignment"),
        ("self.auth_token = self.config.token", "attribute access"),
        ("password: ${DB_PASSWORD}", "shell interpolation"),
        ("api_key: your_key_here", "placeholder"),
        ('secret = "***"', "already masked"),
        ("const max_retries = 10;", "ordinary code"),
        ("password = process.env.DB_PASS", "node env lookup"),
        ("api_key: <YOUR_KEY>", "angle-bracket placeholder"),
        # Found by running 4take on its own source: two models reported a CRITICAL syntax
        # error in code that was fine, because the redactor had corrupted it before sending.
        ("tokens = tokens_of[position]", "identifier merely containing 'token'"),
        ("MAX_TOKENS = 4096", "constant merely containing 'token'"),
        ("authors = load_authors()", "identifier merely containing 'auth'"),
        ("self.token_count = 12", "attribute merely containing 'token'"),
        (
            "if len(common) < _MIN_SHARED_TOKENS:\n        return 0.0",
            "a key must not bind to a value on the next line",
        ),
        ('secret_key:\n    "value on the next line"', "the quoted rule must not cross a newline either"),
        (
            'const started = "2026-09-14T12:30:45Z"; const ratio = "100000:1";',
            "times and ratios are not bot tokens",
        ),
        ("fetch(`http://localhost:8080/bot/status`)", "a URL with a port is not a bot token"),
    ],
)
def test_code_is_not_mangled_by_broader_patterns(text, label):
    """
    A false positive silently degrades the review: the models see redacted code and
    critique something the user never wrote.
    """
    assert redact_secrets(text) == text, label


@pytest.mark.parametrize(
    "text,label",
    [
        # Every form below was redacted in a public benchmark's TypeScript diffs before they
        # went out: six of ten cal.com PRs reached the council with holes in them, and a model
        # reported the syntax error the redactor had made in a Grafana one.
        ("const myGoogleAuth = await this.auth.getToken();", "await"),
        ("type SkipToken = typeof skipToken;", "typeof"),
        ("const refreshAccessToken = async (refreshToken: string) => {", "async"),
        (
            "export const getCalendar = (credential: CredentialPayload | null): Calendar | null => {",
            "a parameter's type",
        ),
        ("  password: string;", "an interface member"),
        ("  password: string", "the same member without a semicolon"),
        ("    credential: CredentialWithAppName,", "a type or a value in an object literal"),
        ("    Authorization: authHeader,", "a header built from a variable"),
        ("  meetingPassword: event ? event.password : null,", "an expression"),
        ("      calendarCredential = foundCalendarCredential;", "a statement"),
        ("  const token = res?.data;", "optional chaining"),
        ("  const token = this.token", "`this`, as `self` already is"),
        ("            self._token = token", "a variable handed on under its own name"),
        ('    refreshTokenResponse.data.refresh_token = "refresh_token";', "a string naming its own key"),
        ('    public static final String USER_PASSWORD_AUTH = "USER_PASSWORD_AUTH";', "a Java constant"),
        ('    body=f"access_token={token}",', "an f-string interpolation"),
    ],
)
def test_typescript_and_javascript_are_not_mangled(text, label):
    """
    Outside quotes, a secret is a config value: `.env`, YAML, a shell prefix. There it ends its
    line, or sits right against a tight `KEY=`; in code the same key names a type, a variable or
    the first word of an expression, and redacting that word rewrites the code under review.
    """
    assert redact_secrets(text) == text, label


@pytest.mark.parametrize(
    "text,secret",
    [
        ("password: hunter2  # rotated monthly", "hunter2"),
        ("api_key: abcd1234efgh // staging", "abcd1234efgh"),
        ("password: hunter2\r\nuser: bob", "hunter2"),
        ("DB_PASSWORD=hunter2 docker compose up", "hunter2"),
        ("export API_TOKEN=abc123def; ./deploy.sh", "abc123def"),
        ("curl https://api.example.com/v1?access_token=abc123def&page=2", "abc123def"),
        # A lookup is `this.x` or `self[...]`; a passphrase merely starting with the word is not.
        ("password: this-is-my-passphrase", "this-is-my-passphrase"),
        ("password = config-2024-rotated", "config-2024-rotated"),
    ],
)
def test_a_config_value_is_still_a_secret_after_the_code_fix(text, secret):
    """The rule above must not let a comment, a CRLF, or the command a variable prefixes leak it."""
    assert secret not in redact_secrets(text)


@pytest.mark.parametrize(
    "text,secret",
    [
        ("Authorization: Bearer abcdef123456ghijkl", "abcdef123456ghijkl"),
        ("authorization: bearer sk-abc123def456xyz0", "sk-abc123def456xyz0"),
        ("Proxy-Authorization: Basic dXNlcjpwYXNzd29yZA==", "dXNlcjpwYXNzd29yZA=="),
    ],
)
def test_an_auth_header_loses_the_credential_not_the_scheme(text, secret):
    """
    This shipped as a leak: the assignment rule treated the scheme word as the value, so
    `Authorization: Bearer <token>` went out as `Authorization: ***REDACTED*** <token>` —
    the credential intact. Asserting only that something was redacted hid it.
    """
    out = redact_secrets(text)
    assert secret not in out
    assert "REDACTED" in out


@pytest.mark.parametrize(
    "text",
    [
        'curl -H "Authorization: Bearer $TOKEN" "$API/login"',
        'curl -H "Authorization: Bearer ${API_TOKEN}" "$API/login"',
        "headers: { Authorization: `Bearer ${token}` }",
        'headers = {"Authorization": f"Bearer {token}"}',
        'req.Header.Set("Authorization", fmt.Sprintf("Bearer %s", token))',
        'headers["Authorization"] = "Bearer #{token}"',
        "Authorization: Bearer {{ api_token }}",
    ],
)
def test_an_auth_header_filled_in_at_run_time_is_left_alone(text):
    """
    `Bearer $TOKEN` names where the credential comes from; it is not the credential. It went
    out as `Bearer ***REDACTED***` with the closing quote eaten, so the council reviewed a
    shell line with an unterminated string.
    """
    assert redact_secrets(text) == text


def test_an_auth_header_credential_keeps_the_quote_that_closes_it():
    text = 'curl -H "Authorization: Bearer abcdef123456ghijkl" https://api.example.com'
    assert redact_secrets(text) == 'curl -H "Authorization: Bearer ***REDACTED***" https://api.example.com'


def test_a_framework_secret_key_is_redacted_and_code_that_reads_one_is_not():
    # Django and Flask settings files: exactly what an agent sends as reference context.
    assert redact_secrets('SECRET_KEY = "django-insecure-abc123xyz"') == 'SECRET_KEY = "***REDACTED***"'
    assert redact_secrets("JWT_SECRET_KEY: 'abc123def456'") == "JWT_SECRET_KEY: '***REDACTED***'"
    for code in (
        "SECRET_KEY = settings.SECRET_KEY",
        'app.config["SECRET_KEY"] = os.environ["SECRET_KEY"]',
        "SECRET_KEY: str = Field(...)",
        "SECRET_KEY = get_random_secret_key()",
        "secret_keys = load()",
    ):
        assert redact_secrets(code) == code


def test_the_projects_own_source_survives_redaction():
    """
    The tool exists to review code. A redactor that rewrites ordinary source hands every
    model a file with errors the user never wrote, and the council dutifully reports them.
    """
    import pathlib

    source_dir = pathlib.Path(__file__).resolve().parent.parent / "src" / "fourtake"
    mangled = [
        p.name
        for p in sorted(source_dir.glob("*.py"))
        # safety.py is the exception on purpose: its comments quote literal example
        # secrets (`DB_PASSWORD=hunter2`), and redacting those is the rule working.
        if p.name != "safety.py" and redact_secrets(p.read_text()) != p.read_text()
    ]
    assert mangled == []


def test_report_names_the_rules_that_fired():
    text = 'DB_PASSWORD=hunter2\napi_key = "sk-1234567890abcdef12345678"\nghp_' + "a" * 36
    redacted, report = redact_secrets_with_report(text)
    assert "hunter2" not in redacted
    assert report["bare-assignment"] == 1
    assert report["assignment"] == 1
    assert report["github-pat"] == 1


def test_report_is_empty_when_nothing_matched():
    assert redact_secrets_with_report("def f(): return 1") == ("def f(): return 1", {})


def test_the_report_counts_what_was_removed_not_what_matched():
    """
    `--show-redactions` exists so over-redaction is visible. `re.subn` counts matches, and
    the assignment rules deliberately return the text untouched when the value turns out to
    be code — so the report claimed four secrets removed from input that had two.
    """
    text = (
        "DB_PASSWORD=hunter2\n"
        "Authorization: Bearer abcdef123456ghij\n"
        "api_key = provider.api_key\n"  # code: matches the rule, must not be counted
    )
    out, report = redact_secrets_with_report(text)

    changed = sum(1 for a, b in zip(text.splitlines(), out.splitlines(), strict=True) if a != b)
    assert sum(report.values()) == changed == 2
    assert "provider.api_key" in out


@pytest.mark.parametrize(
    "text, expected",
    [
        ("redis://:s3cretpass@cache:6379/0", "redis://:***REDACTED***@cache:6379/0"),
        ("mongodb://user:pa/ss@host/db", "mongodb://user:***REDACTED***@host/db"),
        ("amqp://guest:123456@rabbit:5672", "amqp://guest:***REDACTED***@rabbit:5672"),
    ],
    ids=["no-user", "slash-in-password", "digit-password"],
)
def test_connection_string_passwords_are_redacted(text, expected):
    """A Redis URL has no user name; its password went out as written."""
    assert redact_secrets(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        'url = "https://host:8443/x/@me"',
        "see https://example.com/a:b@c for details",
        "ssh://git@github.com:22/org/repo.git",
    ],
    ids=["port-then-path", "colon-in-path", "user-without-password"],
)
def test_a_url_without_a_password_is_left_whole(text):
    """A port before a path, or a colon in the path, is not a password: it was cut to `***REDACTED***@`."""
    assert redact_secrets(text) == text
