# CLAUDE.md — Idraa

Project instructions for Claude Code working in this repo.

## What this project is

Idraa — a quantitative cyber-risk analysis platform built on the FAIR (Factor
Analysis of Information Risk) methodology. Control-aware risk modeling, native
Monte Carlo simulation, financial impact analysis, and reporting for security
leaders. Single Python codebase: FastAPI + Jinja2 + HTMX/Alpine (no JS build
step), SQLAlchemy 2.x + Alembic, SQLite (Postgres-compatible), reportlab /
xlsxwriter reports, first-party server-rendered SVG charts.

## Canonical repository (READ FIRST)

**All work is committed and tracked on the PUBLIC repo `github.com/yasirhamza/idraa`, local checkout `~/projects/Idraa`.** Before the first commit of any session, run `git remote get-url origin` and confirm it is idraa. If it is `…/riskflow.git`, STOP — you are in the wrong checkout (`~/projects/RiskFlow` is a trap: its dir name says RiskFlow and its CLAUDE.md says Idraa, but its remote is the private archive). A `check-canonical-remote` pre-commit hook enforces this and blocks commits to the riskflow remote unless `IDRAA_ALLOW_PRIVATE_COMMIT=1` is set.

The private `github.com/yasirhamza/riskflow` archive receives **only** the sanctioned sensitive security issues (`#555`, `#487`, `#246`, `#245`, `#6`), and only under embargo. **Prefer GitHub Security Advisories on idraa** (which spin up a temporary private fork of the public repo) for embargoed fixes rather than maintaining a parallel private repo. **Do NOT continuously mirror riskflow with idraa** — that makes the two checkouts indistinguishable (worsening the wrong-remote trap) and defeats the private archive's purpose; sync one-way on demand only when actively working a sanctioned sensitive issue.

This repo was seeded 2026-07-17 from the private development repo
(`yasirhamza/riskflow`, the project's full history archive). Docstrings citing
`docs/superpowers/...` or `docs/plans/...` refer to design documents in that
private archive — they are provenance pointers, not files in this tree.
`docs/reference/` (calibration sources, FAIR-CAM alignment notes) IS in-tree
because code cites it and the Docker build ships part of it.

## fair_cam

`./fair_cam/` is the first-party FAIR computation engine (installed editable via
`uv sync`). It is the ONLY source of truth for FAIR math — never re-derive risk
calculations in the app layer. It is held to the same quality gates as
`src/idraa/`. FAIR™ and FAIR-CAM™ are trademarks of the FAIR Institute; this
project implements the published methodology and is not affiliated with or
endorsed by the FAIR Institute (see README → Trademarks).

## Architectural rules

- Persist FULL Monte Carlo output (sample arrays, VaR, expected shortfall, loss
  exceedance) — never summaries-only.
- Single source of truth for data: the SQLite/Postgres DB owned by this app.
- Audit logging is a first-class table, not an afterthought.
- UUIDs for all entity IDs; `organization_id` on every business table.
- The `riskflow_extension*` keys in `data/seed_framework_crosswalk.json` and the
  DB are FROZEN historical data-contract names — never rename them.
- The prod volume DB filename is `/data/riskflow.db` — deliberately kept through
  the Idraa rename (WAL-safe volume-snapshot restores). See fly.toml.

## Development

- Everything in the project venv: `uv sync --extra dev`. Never install tooling
  system-wide.
- Install BOTH pre-commit stages (first-time setup):
  ```
  uv run pre-commit install
  uv run pre-commit install --hook-type pre-push
  ```
  The pre-push stage IS the CI: `scripts/run_local_gate.py` runs ruff check,
  ruff format --check, mypy, a css-staleness check, and the fast pytest suite
  (~4-7 min). `IDRAA_GATE_SKIP_TESTS=1` skips pytest in emergencies;
  `IDRAA_GATE_SKIP_CSS=1` skips the css check.
- Canonical task runner: `python -m idraa.tasks <command>` (also `uv run idraa`).
- E2e (Playwright) is excluded from the fast gate — run explicitly on chart/JS
  changes: `uv run pytest -m e2e tests/e2e/`.
- Cross-platform: LF endings (.gitattributes), `pathlib.Path` only, no hardcoded
  separators.

## Deploy

Fly.io app `idraa` (https://idraa.fly.dev/), config in `fly.toml`. Deploy via
`./scripts/fly deploy --remote-only -c fly.toml` — the wrapper always deploys
`origin/main` from an isolated checkout and reads the deploy token from the
macOS Keychain (service `riskflow-fly-token`, name kept for credential
compatibility).

## Issue tracker convention

Feature/quality issues live on THIS repo's public tracker (the backlog was
migrated here 2026-07-18 as sanitized recreations; `riskflow#NNN (private
archive)` footers point at the originals). **Security-sensitive issues —
anything enumerating an open gap in the live app — are filed on the private
archive repo's tracker instead**, and migrate here only after they are fixed.
When writing public issue bodies: no bare `#NN` cross-refs to archive numbers
(GitHub autolinks them to unrelated issues here) — use the plain-text
`riskflow#NNN` form.

## Outbound hygiene

`scripts/lint_tracked_paths.py` (pre-commit) fails if any denylisted local-state
or sensitive path becomes tracked (.env*, *.db, agent/tool state dirs, private
archive doc trees). gitleaks runs at commit (staged) and push (full history).
Never commit licensed/copyrighted third-party material — first-party or
verifiably-permissive content only.
