# Security policy

## Reporting a vulnerability

Please report security issues privately through GitHub Security Advisories for
this repository. Do not open a public issue containing credentials, personal
financial data, or exploit details.

Include the affected component, reproduction steps, potential impact, and any
suggested mitigation. You should receive an acknowledgement within seven days.

## Supported version

Security fixes target the latest commit on the default branch. This project is
a local-first research tool and does not provide hosted account isolation.

## Operational guidance

- Bind the API to loopback unless remote access is explicitly required.
- Set `ADMIN_API_TOKEN` before exposing mutation endpoints beyond loopback.
- Keep `backend/.env`, SQLite databases, portfolio files, and generated reports
  outside version control.
- Use read-only credentials for optional data integrations whenever possible.
- Review tunnel output before sharing a temporary public URL.
- Treat all generated investment analysis as untrusted research input, not an
  executable trading instruction.

If a credential is accidentally committed, revoke it immediately. Removing it
from the latest commit is not sufficient because it remains in Git history.
