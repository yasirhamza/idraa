# Security Policy

## Reporting a vulnerability

Please report suspected vulnerabilities privately to **yasirhamza@gmail.com**.
Do not open a public issue for security reports.

You can expect an acknowledgement within 72 hours. Please include reproduction
steps and the commit/version you tested against.

## Scope

- The application code in this repository (`src/idraa/`, `fair_cam/`).
- The deployed UAT instance is access-gated and not a bug-bounty target;
  please test against a local checkout instead.

## Supply-chain posture (summary)

- Dependencies are pinned with hashes in `uv.lock`; Docker builds use
  `uv sync --frozen`.
- Vendored front-end assets are integrity-pinned (`static/vendor/integrity.json`).
- Secret scanning (gitleaks) runs at commit and push; a tracked-path denylist
  (`scripts/lint_tracked_paths.py`) guards against committing local state or
  sensitive files.
