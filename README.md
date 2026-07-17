# Idraa

Quantitative cyber-risk analysis platform — FAIR methodology, control-aware modeling (FAIR-CAM), Monte Carlo simulation, financial impact analysis, and executive reporting.

**Status: MVP shipped** and in production UAT at a live Fly.io deployment. Post-MVP epics delivered since: native FAIR engine (pyfair removed), native distribution authoring (PERT-calibrated, lognormal for catastrophic tails), a curated 102-entry scenario library (OT/ICS first-class), per-control Shapley attribution, enterprise PDF reports, first-party server-rendered SVG charts. See [ROADMAP.md](ROADMAP.md).

## What it does

- **Scenarios** — FAIR-grounded risk scenarios (Threat / Asset / Method / Effect), authored via a guided SME-elicitation wizard, cloned from the curated library, or imported from CSV/JSON.
- **Library** — 102 curated scenario archetypes with primary-cited FAIR distributions (IRIS 2025 sector anchors), three-tier provenance, per-org override layer with versioning + audit.
- **Controls** — FAIR-CAM control modeling with sub-function assignments, framework crosswalks (NIST CSF, CIS v8), and a curated control library.
- **Analysis** — native Monte Carlo engine (single-scenario and portfolio AGGREGATE), full sample persistence, VaR/ES tail ladder, loss exceedance curves, per-control Shapley attribution.
- **Reporting** — executive web dashboards + tiered PDF reports with snapshot provenance; CSV/JSON exports (audited).
- **Platform** — session auth + RBAC (analyst / reviewer / admin), first-class audit log, mobile-responsive UI.

This is the v3 ground-up rebuild, succeeding two prior attempts:
- **v1 RiskFlux** (Python + Streamlit, validated FAIR engine but UI collapsed)
- **v2 RiskFlow** (Node + Remix + MongoDB, never finished)

The validated `fair_cam` Python library (first-party, at `./fair_cam/`) survives both attempts and is the calculation core of v3.

Stack: FastAPI + Jinja2 + HTMX/Alpine (no JS build step), SQLAlchemy 2 + Alembic, SQLite (Postgres-compatible). (Architecture/design documents cited in code docstrings as `docs/superpowers/...` or `docs/plans/...` live in the project's private development archive, not this tree.)

## Development

Prerequisites: Python 3.11+, [uv](https://docs.astral.sh/uv/), Docker Desktop.

```bash
# First-time setup — BOTH pre-commit stages are required
uv sync --extra dev
uv run playwright install chromium
uv run python -m ipykernel install --sys-prefix --name=python3
uv run pre-commit install                       # per-commit lints
uv run pre-commit install --hook-type pre-push  # branch gates + local verification gate

# Local verification gate (GHA is billing-disabled — pre-push IS the CI):
# ruff check + ruff format --check + mypy + fast pytest. Runs automatically
# on `git push`; run manually with:
uv run python scripts/run_local_gate.py

# Individual tasks
uv run python -m idraa.tasks lint
uv run python -m idraa.tasks typecheck
uv run python -m idraa.tasks test
uv run python -m idraa.tasks e2e

# Run the dev server
docker compose up -d --build
curl http://localhost:8000/healthz
docker compose down -v

# Database migrations
uv run alembic upgrade head
```

Deployment: Fly.io via the Keychain-token wrapper — `./scripts/fly deploy --remote-only -c fly.toml` (never bare `flyctl`).

Notes on cross-platform setup, operational envelope (VM size, MC iteration caps, memory patterns), and collaboration conventions live in [`CLAUDE.md`](CLAUDE.md).

## License

**Source-visible, all rights reserved.** No license is currently granted: you may
read this code, but you may not use, copy, modify, or redistribute it. A license
will be chosen at product launch.

## Trademarks

FAIR™ and FAIR-CAM™ (Factor Analysis of Information Risk / FAIR Controls
Analytics Model) are trademarks of the FAIR Institute; Open FAIR™ is a trademark
of The Open Group. Idraa implements these published methodologies and is **not
affiliated with, sponsored by, or endorsed by** the FAIR Institute or The Open
Group. References to the standards are nominative — they describe what the
software implements.
