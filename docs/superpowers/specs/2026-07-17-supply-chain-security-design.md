# Supply-chain security posture — GitHub-native gates for the public repo

**Status:** approved direction (owner, 2026-07-17); pending plan-gate
**Owner decisions:** GitHub-native-first (the repo is now public with free Actions
confirmed working); release integrity = **Fly builds kept + SBOM only** (no image
provenance; no deploy token in public-repo Actions); CodeQL **advisory-first**;
the epic issue and security backlog stay in the **private** tracker
(riskflow #555); PRs land publicly here.
**Related:** riskflow #555 (epic, private), AndroDR #252 + its 2026-07-16
close-out notes (the design this adapts), riskflow #557 (licensed-material
removal), idraa PR #1 (first fix on the public repo).

## Why

The original #555 design (2026-07-14) re-homed every gate to the local pre-push
gate because the repo was private with Actions billing-disabled. That premise
died on 2026-07-17: `yasirhamza/idraa` is public and **free Actions work** (the
billing lock was private-only). The GitHub-native layers from AndroDR #252 are
now available at zero cost and this design adopts them, folding in AndroDR's
close-out lessons:

- **CodeQL advisory-first** — AndroDR's `code_scanning` required-check ruleset
  wedged on path-filtered workflows and was rolled back.
- **Graph-on-main + delta review on PRs** — GitHub's automatic pip/uv
  dependency-graph submission is already running on this repo, so
  `dependency-review-action` needs no base-ref pin.
- **Suppressions convention** — `allow-ghsas` with reason + review-date, empty
  until needed.
- **Red-test guards live** — AndroDR red-tested their denylist guard; ours has
  never been tripped on purpose.
- Their sharpest pain (Gradle checksum-metadata regen + a PAT-gated Dependabot
  workflow) is **structurally absent** here: `uv.lock` carries sha256 hashes
  natively and Dependabot's uv ecosystem updates the lock itself.

**Already live** (pre-epic, this week): secret scanning + push protection,
Dependabot alerts, private vulnerability reporting, automatic dependency-graph
submission, gitleaks commit+push hooks, tracked-path denylist guard,
SECURITY.md, README license/trademark notices.

**Discovered state this epic must fix:** `ci.yml` is a fossil — authored
pre-billing-freeze and never reconciled with the local gate as it evolved. On
the first public runs: bare `uv run mypy` checks `tests/` (1,298 errors the
gate deliberately scopes out), standalone `bandit -ll` reports 3 untriaged
mediums (the gate uses ruff-S), and the test matrix runs Python 3.12 while the
project pins 3.11. `lint` was fixed in PR #1; the rest is this epic's work.

## Design — six layers on the public substrate

| # | Layer | Mechanism | Enforcement |
|---|---|---|---|
| 1 | Dependency integrity | `uv.lock` sha256 hashes (done) + `uv lock --check` freshness in the gate | local gate + CI |
| 2 | Dependency vulns (SCA) | `dependency-review-action` (delta, PR gate) + Dependabot alerts (standing tree) + `pip-audit` in the local gate (offline belt-and-suspenders) | PR gate + Security tab + pre-push |
| 3 | CI/build hardening | ci.yml runs the **local gate script verbatim** (single source of truth — kills drift permanently) + SHA-pin every action + least-privilege permissions | Actions |
| 4 | Build/release transparency | Docker base image **digest-pinned**; CycloneDX SBOM from `uv.lock` generated in CI per main push (artifact, sha-keyed); Fly remote builds unchanged — image provenance explicitly out of scope (owner decision) | CI + Dockerfile |
| 5 | First-party SAST | ruff-S in the gate (done) + a **named blocking `sast` CI job** (ruff-S re-run, same config — zero drift; + `zizmor` for GitHub-workflow SAST, locked dev dep) + CodeQL `python` on PRs + weekly cron, **advisory** | gate + ci-success + Security tab |
| 6 | Outbound-leak control | gitleaks hooks + denylist guard (done) — this epic **red-tests** both live and adds `docs/supply-chain.md` | pre-commit/pre-push |

### SCA policy (two complementary layers — NOT the same rule)

- **PR delta gate (`dependency-review-action`): severity-based.**
  `severity ≥ HIGH && introduced by this PR → FAIL`; suppressions in
  `dependency-review-config.yml` `allow-ghsas` (reason + review-date).
- **Local gate (`pip-audit`): fixability-based.** pip-audit's JSON carries
  fixability but NOT severity, so the gate `FAILS on any FIXABLE vulnerability
  not suppressed` and `WARNS on unfixable/suppressed ones` — strictly stronger
  than the PR rule on fixable findings (it also catches standing-tree and
  MEDIUM/LOW fixables), never weaker. Suppressions in
  `scripts/sca_suppressions.txt`; every id REQUIRES a preceding comment stating
  the reason + a review-by date (machine-enforced — a bare id fails the gate).
  Offline hatch: `IDRAA_GATE_SKIP_AUDIT=1`.

## Rollout — three risk-ordered PRs

### PR 1 — CI truth + scanning gates
- Rewrite `ci.yml`: one `gate` job = `uv run --extra dev python
  scripts/run_local_gate.py` (verbatim — CI can never drift from the gate
  again), Python pinned to the project's 3.11; keep separate jobs only for what
  the gate doesn't run: `secrets` (gitleaks full-history), `docker-build`,
  `e2e` (Playwright), `notebook-smoke`. Delete the fossil `lint`/`typecheck`/
  `sast` jobs (their coverage is inside the gate; bandit's 3 medium `-ll`
  findings get triaged in this PR — fixed or suppressed with rationale — before
  the job is deleted).
- SHA-pin **every** action across all workflows (version comments; Dependabot's
  `github-actions` ecosystem keeps pins current).
- Add `codeql.yml` (`language: python`, PRs + weekly cron, **advisory** — no
  required-check ruleset).
- Add `dependency-review.yml` (PR trigger, `fail-on-severity: high`) +
  `dependency-review-config.yml` with the empty `allow-ghsas` block + convention
  comment.
- Add `.github/dependabot.yml`: ecosystems `uv`, `github-actions`, `docker`;
  grouped; monthly; low-noise.
- Add a named blocking `sast` job (owner decision 2026-07-17): `ruff check
  --select S src fair_cam scripts` (same tool+config as the gate — no dual
  suppression syntax, no drift) + `zizmor .github/workflows/` (workflow SAST —
  closes the one tooling gap; zizmor added as a locked dev dependency). Part of
  `ci-success`'s deterministic core.
- **Red-test both outbound guards live** in this PR's branch: stage a fake
  `.env`-shaped tracked file → denylist hook must fail; stage a fake secret →
  gitleaks must block. Record both refusals in the PR body, then remove.

### PR 2 — build integrity
- Digest-pin `python:3.11-slim@sha256:…` in the Dockerfile (both stages, version
  comment; Dependabot `docker` keeps the digest current).
- CI SBOM job: `cyclonedx-py` (dev dependency) generates a CycloneDX JSON from
  `uv.lock` on every main push, uploaded as a sha-keyed artifact. Formal
  attach-to-release lands with the launch release process (out of scope here).
- `uv lock --check` added to the local gate (dev-path lockfile freshness,
  matching Docker's `--frozen`).

### PR 3 — local SCA + posture doc + arming
- `pip-audit` as a dev dependency + `GATE_STEPS` entry with the policy above +
  suppressions file + `IDRAA_GATE_SKIP_AUDIT` hatch.
- `docs/supply-chain.md`: the posture narrative — five layers, the SCA triage
  policy, the outbound-leak surface (denylist + gitleaks + what must never
  leave the machine), the deliberate keeps (Fly-built images unattested, by
  decision), and the "am I affected?" runbook (graph + SBOM lookup).
- **Arm branch protection** on `main`: require `ci-success` (the deterministic
  aggregator), `secrets (gitleaks, full history)`, and `dependency-review`
  once green streak ≥ 3 runs; CodeQL stays advisory (AndroDR lesson). Admin-bypass merges remain possible (agentic flow unchanged); the
  ruleset is the backstop, the local gate remains the authority.

## Out of scope (explicit)
- **Image provenance / SLSA attestation** — owner decision 2026-07-17: Fly
  remote builds stay; no deploy token enters public-repo Actions. Revisit only
  if the deploy architecture changes.
- Third-party scanners (Trivy/Grype/Semgrep) — GitHub-native + gate covers the
  layers; revisit on a demonstrated gap.
- The uat-*.yml ops workflows (excluded from the public seed; operational
  automation stays private).
- Runtime/IP-based monitoring.

## Scope budget

- **target_task_count:** 7 — PR1: (1) ci.yml gate-verbatim rewrite + Python
  pin + fossil-job deletion + bandit triage, (2) SHA-pin sweep + dependabot.yml,
  (3) CodeQL + dependency-review workflows/config, (4) guard red-tests;
  PR2: (5) digest-pin + SBOM job + `uv lock --check`; PR3: (6) pip-audit gate
  step + suppressions, (7) docs/supply-chain.md + branch-protection arming.
- **target_loc_delta:** workflows/config dominated; new Python ≈ the pip-audit
  gate step (`scripts/sca_gate.py` ≤90 physical lines incl. docstring and the
  fail-closed error handling; core logic ≈60). Any task adding >50 lines of non-config logic is out
  of budget.
- **review_budget:** cross-cutting infra → 4-reviewer plan-gate on this design
  + the plan (iterated to 0/0) and a 4-reviewer final PR-gate on the last PR;
  per-task spec review between.
- **timeline_budget:** 1–2 sessions.

If exceeded, append `## Scope budget — addendum` with owner re-approval.

## Scope drift log

- **Item:** local-gate homing (original #555 design) replaced by GitHub-native
  gates · **Direction:** ↔reframed · **Justification:** the private-repo/no-CI
  premise died at the 2026-07-17 public flip; free Actions verified working.
  The local gate remains authoritative for the merge path; CI becomes the
  drift-proof mirror + the delta/SAST layers the gate can't provide.
- **Item:** image provenance cut · **Direction:** -cut · **Justification:**
  owner decision — keeping Fly remote builds and keeping the deploy token out
  of public-repo Actions outweighs `gh attestation verify` parity with AndroDR.
- **Item:** ci.yml fossil reconciliation absorbed into PR1 · **Direction:**
  +added · **Justification:** discovered on the first public runs (typecheck/
  sast red from gate drift); the gate-verbatim rewrite fixes it as a
  structural property rather than patching three jobs individually.
- **Item:** local pip-audit policy = FIXABILITY-based, not severity-based ·
  **Direction:** ↔reframed · **Justification:** pip-audit JSON has no severity
  field; the gate fails on any fixable unsuppressed vuln (strictly stronger),
  severity gating delegated to dependency-review on the PR path. (Plan-gate
  S-B1/M-I1 — the two docs previously disagreed silently.)
- **Item:** ci-success aggregator scoped to the deterministic core (gate,
  test-windows, secrets); e2e/docker-build/notebook-smoke advisory ·
  **Direction:** ↔reframed · **Justification:** plan-gate A-I5 — requiring
  flake-prone jobs contradicts the wedge lesson; --admin bypass exists but a
  wedging default is wrong.
- **Item:** CI uv version aligned to the dev toolchain (0.11.x line), not the
  stale 0.4.27 pin · **Direction:** +added · **Justification:** plan-gate A-I1 —
  uv.lock is `revision = 3` schema (authored by uv 0.11.x); a 0.4.27 CI/Docker
  uv may reject it and lacks `uv lock --check`.
- **Item:** gitleaks CI binary sha256-pinned · **Direction:** +added ·
  **Justification:** plan-gate Sec-I1 — a supply-chain epic must not
  curl|tar its own scanner unverified; follows the build_css.py precedent.
- **Item:** named blocking `sast` CI job (ruff-S + zizmor) added ·
  **Direction:** +added · **Justification:** owner decision 2026-07-17 —
  visible, blocking SAST check; ruff-S re-run has zero drift risk (same
  tool/config as the gate), zizmor covers workflow files (previously
  hand-audited only).
- **Item:** bandit `-ll` medium-finding triage absorbed into PR1 ·
  **Direction:** +added (small) · **Justification:** the fossil sast job
  surfaced 3 untriaged mediums; they must be dispositioned (fix or suppress
  with rationale) before the job that found them is deleted.
