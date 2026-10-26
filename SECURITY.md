# Security Policy

## Supported versions

Only the latest release receives security fixes. Today that is 0.2.x.

## Reporting a vulnerability

Please do not open a public issue for a vulnerability.

- Use GitHub's **Report a vulnerability** button on the Security tab, or
- write to **takelicensing@gmail.com** with the subject `4take security`.

Include the output of `4take --version`, the steps to reproduce, and what an attacker gains. A
minimal input that triggers the problem helps most.

4take is maintained by one person. I aim to acknowledge a report within 7 days and to say within
30 days whether it is accepted and when a fix can be expected. I ask that you keep the details
private until a fixed release is out, or for 90 days, whichever comes first. The fix is released
before the advisory is published, and the reporter is credited unless they prefer not to be.

## What counts as a vulnerability

- **Secrets that leave the machine.** A credential that the default settings should redact but
  that reaches a provider in clear text (a key format, connection string or token the patterns in
  `src/fourtake/safety.py` miss). Redaction is pattern-based and cannot be complete, so a missed
  pattern is a bug to report, not a guarantee that was broken.
- **Reading files it should not.** A path that makes `--context-file`, `--auto-context` or the MCP
  `context_files` argument read outside the project root, or read `.git/`, `.ssh/` or another
  credential file the tool is meant to refuse.
- **Local files readable by other users.** The review cache and telemetry under `~/.cache/4take/`
  are meant to be private to the account that wrote them.
- **Output that attacks the reader.** Model output that injects terminal control sequences or
  breaks the JSON contract an agent parses.

## What does not

- Sending your code to a model provider. That is what the tool does; the README's Privacy section
  explains that free endpoints are run by third parties that may retain or train on prompts under
  their own policies, and `--dry-run` shows exactly what would be sent.
- The files under `evals/corpus` and `evals/corpus_ctx`. They are deliberately defective code (SQL
  injection, shell injection, path traversal) that the eval scores reviewers against. Nothing runs
  them.
- A reviewer's finding being wrong, or missing a bug.
- A vulnerability in a dependency. Report it upstream; tell me if 4take pins an affected version.
