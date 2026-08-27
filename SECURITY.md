# Security Policy

## Supported Versions

C.O.D.A is currently under active development. Security fixes are generally
applied to the latest release series rather than being backported to older
versions.

| Version | Supported          |
| ------- | ------------------ |
| 1.4.x   | :white_check_mark: |
| < 1.4   | :x:                |

Users are encouraged to update to the latest available release before
reporting an issue.

## Reporting a Vulnerability

If you discover a security vulnerability in C.O.D.A, please report it
privately rather than opening a public GitHub issue.

Where possible, use GitHub's **Private Vulnerability Reporting** feature for
this repository. Please include enough information to reproduce and understand
the issue, including:

- A description of the vulnerability.
- The affected version or commit.
- Steps to reproduce the issue.
- The potential impact.
- Any suggested fix or mitigation, if known.

Please avoid publicly disclosing the vulnerability until it has been reviewed
and, where appropriate, a fix has been released.

I will aim to acknowledge vulnerability reports within **7 days**. Once the
issue has been investigated, I will provide an update on whether the report
has been accepted, requires more information, or is not considered a security
issue.

Accepted vulnerabilities will be prioritised based on their severity and
potential impact. Where appropriate, a fix will be included in the next
patch release or released sooner for high-severity issues.

## Scope

Security reports are particularly welcome for issues involving:

- Exposure of API keys, tokens, credentials, or other secrets.
- Privacy-aware LLM routing or sanitisation failures.
- Sensitive information being sent to a cloud provider when it should remain
  local.
- Command execution or unintended code execution.
- Unsafe handling of external input or provider responses.
- Dependency vulnerabilities that directly affect C.O.D.A.
- Dashboard or network-facing functionality that allows unauthorised access.

General bugs, crashes, feature requests, and non-security issues should be
reported through the normal GitHub issue tracker.
