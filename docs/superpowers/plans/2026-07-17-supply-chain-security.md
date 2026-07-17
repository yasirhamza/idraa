# Supply-Chain Security Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the GitHub-native supply-chain posture from `docs/superpowers/specs/2026-07-17-supply-chain-security-design.md` across three risk-ordered PRs in the public `yasirhamza/idraa` repo.

**Architecture:** PR1 makes CI a drift-proof mirror of the local gate (the gate script runs verbatim in CI) and adds the scanning gates (CodeQL advisory, dependency-review, Dependabot config, SHA-pinned actions, live guard red-tests). PR2 pins the Docker base by digest and emits a CycloneDX SBOM per main push. PR3 adds local-gate SCA (pip-audit, fixability-based policy) and the posture doc, then arms branch protection.

**Tech Stack:** GitHub Actions (free, public repo), gitleaks v8.24.3, CodeQL (python), dependency-review-action, Dependabot (uv/github-actions/docker), cyclonedx-bom, pip-audit, uv.

## Global Constraints

- Work in `/Users/yassirhamad/projects/Idraa` (the public dev repo). Branch per PR off `main`; `epic/supply-chain` already carries the design+plan docs and is the base for PR1.
- **Python is pinned 3.11** (`.python-version`) — CI must use it, never a newer default.
- **CodeQL is ADVISORY** — never add a `code_scanning` required-check ruleset (AndroDR close-out lesson: it wedges on path-filtered workflows).
- Every `uses:` in every workflow is **SHA-pinned with a `# vN` comment**. Resolve SHAs at implementation time: `gh api repos/<owner>/<repo>/commits/<tag> --jq .sha`.
- Top-level workflow `permissions: contents: read`; broader scopes only per-job where required (CodeQL: `security-events: write`).
- The **local pre-push gate remains the merge authority**; CI is the mirror + the layers the gate can't run. Run all local verification in the FOREGROUND.
- The 3 bandit `-ll` mediums are **pre-triaged** (ruff-S noqa with rationale: `formatting.py:59` S704 — all text nodes escaped via markupsafe; `build_css.py:90` + `vendor_sync.py:57` S310 — https-pinned, sha256-verified downloads). No new suppressions needed; the standalone bandit job is deleted because ruff-S in the gate covers these rules and honors the noqas.
- Commit style: conventional commits + trailers `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>` / `Claude-Session: https://claude.ai/code/session_01QBaSMmrYC19RBbdYgQVE3o`.
- Merges: local gate green → `gh pr merge --admin --squash` (billing history makes required-checks unreliable until PR3 arms them; verify merged state after).

---

### Task 1: ci.yml — gate-verbatim rewrite (PR1)

**Files:**
- Modify: `.github/workflows/ci.yml` (full rewrite)

**Interfaces:**
- Produces: jobs named `gate`, `test-windows`, `secrets`, `docker-build`, `e2e`, `notebook-smoke`, `ci-success` (the aggregator later required by branch protection in Task 7). Tasks 2–3 add SHA pins and sibling workflows around this file.

- [ ] **Step 1: Rewrite `.github/workflows/ci.yml`**

Replace the whole file with (SHA placeholders `<SHA-*>` are resolved in Task 2 — this task may temporarily keep the existing tags):

```yaml
# CI — mirrors the local pre-push gate EXACTLY by running it verbatim, plus the
# layers the gate cannot run locally (full-history secret scan, Docker build,
# Playwright e2e, notebook smoke, Windows platform coverage).
#
# The local gate (scripts/run_local_gate.py) is the merge authority; this
# workflow is its drift-proof mirror. Never re-implement a gate step here with
# different flags — that is how the pre-2026-07 fossil drift happened.

name: CI

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]
  workflow_dispatch:

concurrency:
  group: ci-${{ github.ref }}
  cancel-in-progress: true

permissions:
  contents: read

env:
  UV_VERSION: "0.4.27"

jobs:
  gate:
    name: gate (local gate, verbatim)
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
        with:
          version: ${{ env.UV_VERSION }}
          enable-cache: true
      - name: Sync (dev extras, frozen)
        run: uv sync --frozen --extra dev
      - name: Run the local gate verbatim
        run: uv run --extra dev python scripts/run_local_gate.py

  test-windows:
    name: test (windows, platform coverage)
    runs-on: windows-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
        with:
          version: ${{ env.UV_VERSION }}
          enable-cache: true
      - run: uv sync --frozen --extra dev
      - name: Fast pytest suite (css gate is posix-only; covered by `gate`)
        run: uv run --extra dev python -m pytest -q --no-cov
        env:
          SESSION_SECRET: insecure-ci-only-dummy-session-secret

  secrets:
    name: secrets (gitleaks, full history)
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - name: Install gitleaks
        run: |
          GITLEAKS_VERSION=8.24.3
          curl -sSL "https://github.com/gitleaks/gitleaks/releases/download/v${GITLEAKS_VERSION}/gitleaks_${GITLEAKS_VERSION}_linux_x64.tar.gz" \
            | tar -xz -C /tmp gitleaks
          sudo install /tmp/gitleaks /usr/local/bin/gitleaks
          gitleaks version
      - run: gitleaks detect --source . --no-banner --verbose

  docker-build:
    name: docker-build
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: docker/setup-buildx-action@v3
      - uses: docker/build-push-action@v6
        with:
          context: .
          push: false
          tags: idraa:ci
          cache-from: type=gha
          cache-to: type=gha,mode=max

  e2e:
    name: e2e (Playwright)
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
        with:
          version: ${{ env.UV_VERSION }}
          enable-cache: true
      - run: uv sync --frozen --extra dev
      - run: uv run playwright install chromium --with-deps
      - run: uv run --extra dev python -m pytest -m e2e -q --no-cov tests/e2e/
        env:
          SESSION_SECRET: insecure-ci-only-dummy-session-secret

  notebook-smoke:
    name: notebook-smoke
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
        with:
          version: ${{ env.UV_VERSION }}
          enable-cache: true
      - run: uv sync --frozen --extra dev
      - run: uv run --extra dev python -m idraa.tasks notebook-smoke

  ci-success:
    name: ci-success
    if: always()
    needs: [gate, test-windows, secrets, docker-build, e2e, notebook-smoke]
    runs-on: ubuntu-latest
    steps:
      - name: All required jobs green?
        run: |
          results='${{ toJSON(needs) }}'
          echo "$results"
          echo "$results" | grep -qv '"result": "failure"' || exit 1
          echo "$results" | grep -q '"result": "cancelled"' && exit 1 || true
```

Adaptation notes for the implementer (verify against the CURRENT file before deleting):
- The `SESSION_SECRET` dummy is deliberately LOW-ENTROPY (word-based) — a random-looking value trips gitleaks' generic-api-key rule in the `secrets` job and the pre-commit hook (verified live: the first version of this very plan document was blocked by the hook). Keep it word-based.
- Preserve the existing file's `notebook-smoke` invocation if it differs (read the old job first; keep its exact command).
- The old `lint`/`typecheck`/`sast` jobs and the 3.12 `test` matrix are DELETED — coverage now: gate job (ruff+format+mypy+css+pytest at the gate's exact scope) + `test-windows` (3.11 via `.python-version`, platform coverage per the cross-platform rule).
- If the old ci.yml sets env vars the suite needs (check its `test` job `env:` block), carry them into `gate`/`test-windows`.

- [ ] **Step 2: Validate + local sanity**

```bash
cd /Users/yassirhamad/projects/Idraa
uv run python -c "import yaml,sys; yaml.safe_load(open('.github/workflows/ci.yml')); print('yaml OK')"
uv run --extra dev python scripts/run_local_gate.py   # foreground; must be green
```

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: run the local gate verbatim — kill CI/gate drift structurally (#555)"
```

---

### Task 2: SHA-pin sweep + dependabot.yml (PR1)

**Files:**
- Modify: `.github/workflows/ci.yml` (pin every `uses:`)
- Create: `.github/dependabot.yml`

**Interfaces:**
- Consumes: Task 1's ci.yml. Produces: the pin format `uses: owner/repo@<40-hex-sha> # vN` that Task 3's new workflows must also follow; the Dependabot config that keeps pins current.

- [ ] **Step 1: Resolve each action tag to a commit SHA**

```bash
for a in actions/checkout@v4 astral-sh/setup-uv@v3 docker/setup-buildx-action@v3 docker/build-push-action@v6; do
  repo=${a%@*}; tag=${a#*@}
  echo "$a -> $(gh api repos/$repo/commits/$tag --jq .sha)"
done
```

Edit ci.yml: every `uses: owner/repo@vN` becomes `uses: owner/repo@<sha> # vN`.

- [ ] **Step 2: Create `.github/dependabot.yml`**

```yaml
# Grouped, monthly, deliberately low-noise. The github-actions updater
# understands SHA pins and keeps the trailing version comment current.
version: 2
updates:
  - package-ecosystem: "uv"
    directory: "/"
    schedule:
      interval: "monthly"
    groups:
      python-deps:
        patterns: ["*"]
    open-pull-requests-limit: 3
  - package-ecosystem: "github-actions"
    directory: "/"
    schedule:
      interval: "monthly"
    groups:
      actions:
        patterns: ["*"]
    open-pull-requests-limit: 3
  - package-ecosystem: "docker"
    directory: "/"
    schedule:
      interval: "monthly"
    open-pull-requests-limit: 2
```

- [ ] **Step 3: Verify + commit**

```bash
grep -nE "uses: [^@]+@v[0-9]" .github/workflows/ci.yml && echo "UNPINNED ✗" || echo "all pinned ✓"
grep -cE "uses: [^@]+@[0-9a-f]{40} # v" .github/workflows/ci.yml   # expect = number of uses: lines
git add .github/workflows/ci.yml .github/dependabot.yml
git commit -m "ci: SHA-pin all actions; add grouped monthly Dependabot config (#555)"
```

---

### Task 3: CodeQL (advisory) + dependency-review (PR1)

**Files:**
- Create: `.github/workflows/codeql.yml`
- Create: `.github/workflows/dependency-review.yml`
- Create: `.github/dependency-review-config.yml`

**Interfaces:**
- Consumes: Task 2's pin format. Produces: advisory CodeQL alerts in the Security tab; a PR-blocking dependency-review job (its own check, NOT part of `ci-success`); the `allow-ghsas` suppressions convention.

- [ ] **Step 1: `.github/workflows/codeql.yml`**

Resolve `github/codeql-action` v3 SHA first (`gh api repos/github/codeql-action/commits/v3 --jq .sha`), then:

```yaml
# CodeQL — ADVISORY by deliberate decision. Findings land in the Security tab;
# there is NO code_scanning required-check ruleset (AndroDR #252 close-out:
# the ruleset has no "analysis not expected" handling for path-filtered
# workflows and wedges docs-only PRs). Do not "harden" this into a ruleset.
name: CodeQL

on:
  pull_request:
    branches: [main]
    paths: ["**.py", ".github/workflows/*.yml"]
  schedule:
    - cron: "24 5 * * 1"   # weekly, Monday 05:24 UTC
  workflow_dispatch:

permissions:
  contents: read

jobs:
  analyze:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      security-events: write
    steps:
      - uses: actions/checkout@<SHA-checkout> # v4
      - uses: github/codeql-action/init@<SHA-codeql> # v3
        with:
          languages: python
          build-mode: none
      - uses: github/codeql-action/analyze@<SHA-codeql> # v3
```

- [ ] **Step 2: `.github/workflows/dependency-review.yml` + config**

Resolve `actions/dependency-review-action` v4 SHA, then:

```yaml
# Delta SCA gate: fails a PR that INTRODUCES a known-vulnerable dependency at
# HIGH+. Standing-tree CVEs are Dependabot-alert territory (Security tab), so
# this cannot wedge on pre-existing findings. The dependency graph is populated
# by GitHub's automatic pip/uv submission on main — no base-ref pin needed.
name: Dependency Review

on:
  pull_request:
    branches: [main]

permissions:
  contents: read

jobs:
  dependency-review:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@<SHA-checkout> # v4
      - uses: actions/dependency-review-action@<SHA-dep-review> # v4
        with:
          config-file: ./.github/dependency-review-config.yml
```

`.github/dependency-review-config.yml`:

```yaml
# Suppressions convention: every allow-ghsas entry MUST carry a comment with
# (a) why it is unfixable/accepted and (b) a review-by date. Empty by default.
fail-on-severity: high
allow-ghsas: []
```

- [ ] **Step 3: Validate YAML, commit**

```bash
for f in .github/workflows/codeql.yml .github/workflows/dependency-review.yml .github/dependency-review-config.yml; do
  uv run python -c "import yaml; yaml.safe_load(open('$f')); print('$f OK')"
done
git add .github/workflows/codeql.yml .github/workflows/dependency-review.yml .github/dependency-review-config.yml
git commit -m "ci: CodeQL (python, advisory) + delta dependency-review gate with allow-ghsas convention (#555)"
```

---

### Task 4: Live red-tests of both outbound guards (PR1, procedure — no committed files)

**Files:** none committed — evidence goes in the PR body.

**Interfaces:** consumes the seeded guards (`scripts/lint_tracked_paths.py`, gitleaks pre-commit hook).

- [ ] **Step 1: Red-test the tracked-path denylist**

```bash
cd /Users/yassirhamad/projects/Idraa
touch .env.redtest && git add -f .env.redtest
python3 scripts/lint_tracked_paths.py; echo "exit=$?"        # MUST be exit=1 naming .env.redtest
git rm --cached -q .env.redtest && rm .env.redtest
python3 scripts/lint_tracked_paths.py && echo "clean again ✓"
```

- [ ] **Step 2: Red-test gitleaks (staged secret)**

```bash
printf 'aws_secret="AKIAIOSFODNN7EXAMPLE1234"\n' > redtest_secret.txt && git add -f redtest_secret.txt
uv run pre-commit run gitleaks --files redtest_secret.txt; echo "exit=$?"   # MUST be nonzero (leak detected)
git rm --cached -q redtest_secret.txt && rm redtest_secret.txt
```

If the gitleaks hook passes the fake key, STOP — the hook is miswired; fix `.pre-commit-config.yaml` before proceeding.

- [ ] **Step 3: Record both refusal outputs verbatim in the PR1 body** (this is the acceptance evidence AndroDR's close-out modeled).

---

### Task 5: Digest-pin + SBOM + lock-freshness (PR2)

**Files:**
- Modify: `Dockerfile` (both `FROM python:3.11-slim` lines)
- Modify: `.github/workflows/ci.yml` (add `sbom` job)
- Modify: `scripts/run_local_gate.py` (uv-lock freshness step)
- Modify: `pyproject.toml` (add `cyclonedx-bom` to the dev extra)

**Interfaces:**
- Produces: `sbom` job uploading artifact `idraa-sbom-<sha>`; gate step label `uv lock --check`.

- [ ] **Step 1: Digest-pin the base image**

```bash
DIGEST=$(curl -s https://hub.docker.com/v2/repositories/library/python/tags/3.11-slim | uv run python -c "import json,sys; print(json.load(sys.stdin)['digest'])")
echo "$DIGEST"   # sha256:...
```

Edit both FROM lines:

```dockerfile
# Digest-pinned (supply-chain: a mutable tag is the container analog of an
# unpinned action). Dependabot's docker ecosystem keeps this current.
FROM python:3.11-slim@sha256:<digest> AS builder
...
FROM python:3.11-slim@sha256:<digest> AS runtime
```

- [ ] **Step 2: `sbom` job in ci.yml** (main pushes only; SHA-pin `actions/upload-artifact` v4):

```yaml
  sbom:
    name: sbom (CycloneDX from uv.lock)
    if: github.event_name == 'push' && github.ref == 'refs/heads/main'
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@<SHA-checkout> # v4
      - uses: astral-sh/setup-uv@<SHA-setup-uv> # v3
        with:
          version: ${{ env.UV_VERSION }}
      - run: uv sync --frozen --extra dev
      - name: Export locked runtime deps + generate CycloneDX SBOM
        run: |
          uv export --frozen --no-dev --no-hashes -o /tmp/requirements-locked.txt
          uv run cyclonedx-py requirements /tmp/requirements-locked.txt --of JSON -o idraa-sbom.cdx.json
          uv run python -c "import json; d=json.load(open('idraa-sbom.cdx.json')); assert d['bomFormat']=='CycloneDX'; print('components:', len(d.get('components', [])))"
      - uses: actions/upload-artifact@<SHA-upload> # v4
        with:
          name: idraa-sbom-${{ github.sha }}
          path: idraa-sbom.cdx.json
```

(`cyclonedx-py` CLI flags: verify with `uv run cyclonedx-py requirements --help` and adjust `--of/-o` spellings to the installed major version BEFORE committing; run the export+generate+assert block locally as the test.)

- [ ] **Step 3: Lock-freshness in the gate** — in `scripts/run_local_gate.py`, add before the `GATE_STEPS` loop in `main()`:

```python
    # Dev-path lockfile freshness — matches Docker's `uv sync --frozen`.
    # Runs the uv BINARY (not python -m), so it sits outside GATE_STEPS.
    print("local gate: uv lock --check")
    lock = subprocess.run(["uv", "lock", "--check"], cwd=REPO_ROOT, check=False)  # noqa: S607
    if lock.returncode != 0:
        print("local gate: FAILED at uv lock --check (pyproject/uv.lock drift)")
        return lock.returncode
```

- [ ] **Step 4: Verify + commit**

```bash
uv add --optional dev cyclonedx-bom && uv sync --extra dev
uv export --frozen --no-dev --no-hashes -o /tmp/req.txt && uv run cyclonedx-py requirements /tmp/req.txt --of JSON -o /tmp/sbom.json && uv run python -c "import json; d=json.load(open('/tmp/sbom.json')); assert d['bomFormat']=='CycloneDX'; print('SBOM OK,', len(d.get('components',[])), 'components')"
uv run --extra dev python scripts/run_local_gate.py   # gate incl. new lock check, foreground
git add -A && git commit -m "build: digest-pin base image; CycloneDX SBOM per main push; uv lock --check in the gate (#555)"
```

---

### Task 6: pip-audit in the local gate (PR3)

**Files:**
- Create: `scripts/sca_gate.py` (policy wrapper, keep <60 lines)
- Create: `scripts/sca_suppressions.txt`
- Modify: `scripts/run_local_gate.py` (audit step + `IDRAA_GATE_SKIP_AUDIT`)
- Modify: `pyproject.toml` (add `pip-audit` to dev extra)
- Test: `tests/unit/test_sca_gate.py`

**Interfaces:**
- Produces: `python scripts/sca_gate.py` exit 0/1; policy function `evaluate(vulns: list[dict], suppressed: set[str]) -> tuple[list, list]` returning `(failures, warnings)`.

**Policy (adapted, document verbatim in the file):** pip-audit's JSON provides fixability but NOT severity → the local gate FAILS on any **fixable** vulnerability not suppressed, WARNS on unfixable ones. Severity-aware gating (Crit/High) lives in `dependency-review-action` on the PR path. Suppressions: one id per line, `# reason + review-by date` comment above it.

- [ ] **Step 1: Failing test first** — `tests/unit/test_sca_gate.py`:

```python
"""Policy tests for scripts/sca_gate.py (supply-chain epic #555)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))
from sca_gate import evaluate, parse_suppressions  # noqa: E402


def _vuln(pkg, vid, fixes):
    return {"name": pkg, "vulns": [{"id": vid, "fix_versions": fixes}]}


def test_fixable_unsuppressed_fails():
    failures, warnings = evaluate([_vuln("foo", "GHSA-xxxx", ["1.2.3"])], set())
    assert len(failures) == 1 and not warnings


def test_unfixable_warns():
    failures, warnings = evaluate([_vuln("foo", "GHSA-yyyy", [])], set())
    assert not failures and len(warnings) == 1


def test_suppressed_fixable_warns_not_fails():
    failures, warnings = evaluate([_vuln("foo", "GHSA-xxxx", ["1.2.3"])], {"GHSA-xxxx"})
    assert not failures and len(warnings) == 1


def test_parse_suppressions_ignores_comments(tmp_path):
    f = tmp_path / "s.txt"
    f.write_text("# reason: unfixable transitive; review-by 2026-10-01\nGHSA-zzzz\n\n")
    assert parse_suppressions(f) == {"GHSA-zzzz"}
```

Run: `uv run --extra dev python -m pytest -q --no-cov tests/unit/test_sca_gate.py` → FAIL (module missing).

- [ ] **Step 2: `scripts/sca_gate.py`**

```python
"""pip-audit policy gate (#555). FAIL on fixable+unsuppressed, WARN otherwise.

pip-audit's JSON has fixability but not severity; severity-aware gating is
dependency-review-action's job on the PR path. Suppressions file: one GHSA/PYSEC
id per line; comment lines (#) must state the reason + a review-by date.
Offline hatch: IDRAA_GATE_SKIP_AUDIT=1 (document the reason in the next commit).
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SUPPRESSIONS = REPO_ROOT / "scripts" / "sca_suppressions.txt"


def parse_suppressions(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {
        line.strip()
        for line in path.read_text().splitlines()
        if line.strip() and not line.strip().startswith("#")
    }


def evaluate(deps: list[dict], suppressed: set[str]) -> tuple[list[str], list[str]]:
    failures, warnings = [], []
    for dep in deps:
        for v in dep.get("vulns", []):
            label = f"{dep['name']}: {v['id']} (fixes: {v.get('fix_versions') or 'none'})"
            if v["id"] in suppressed or not v.get("fix_versions"):
                warnings.append(label)
            else:
                failures.append(label)
    return failures, warnings


def main() -> int:
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as tf:
        req = tf.name
    subprocess.run(
        ["uv", "export", "--frozen", "--no-dev", "--no-hashes", "-o", req],
        cwd=REPO_ROOT, check=True,
    )
    proc = subprocess.run(
        ["uv", "run", "pip-audit", "-r", req, "--format", "json", "--progress-spinner", "off"],
        cwd=REPO_ROOT, capture_output=True, text=True, check=False,
    )
    deps = json.loads(proc.stdout).get("dependencies", [])
    failures, warnings = evaluate(deps, parse_suppressions(SUPPRESSIONS))
    for w in warnings:
        print(f"sca_gate WARN: {w}")
    for f in failures:
        print(f"sca_gate FAIL (fixable, unsuppressed): {f}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
```

`scripts/sca_suppressions.txt`:

```text
# SCA suppressions (#555). One GHSA/PYSEC id per line. Every entry MUST carry a
# comment stating why it is accepted/unfixable and a review-by date.
```

- [ ] **Step 3: Gate wiring** — in `run_local_gate.py` `main()`, after the lock check (mirror its shape):

```python
    if os.environ.get("IDRAA_GATE_SKIP_AUDIT") == "1":
        print("local gate: IDRAA_GATE_SKIP_AUDIT=1 — SKIPPING pip-audit")
    else:
        print("local gate: pip-audit (fixable-vuln policy)")
        audit = subprocess.run(
            [sys.executable, "scripts/sca_gate.py"], cwd=REPO_ROOT, check=False
        )
        if audit.returncode != 0:
            print("local gate: FAILED at pip-audit — fix, or suppress with rationale")
            return audit.returncode
```

- [ ] **Step 4: Verify**

```bash
uv add --optional dev pip-audit && uv sync --extra dev
uv run --extra dev python -m pytest -q --no-cov tests/unit/test_sca_gate.py   # 4 pass
uv run python scripts/sca_gate.py; echo "exit=$?"    # inspect real output; triage any finding NOW
uv run --extra dev python scripts/run_local_gate.py  # full gate green (network needed)
git add -A && git commit -m "gate: pip-audit SCA step — fail fixable, warn unfixable, suppressions with rationale (#555)"
```

If the real run FAILS on a current fixable vuln: bump the dependency in this task (that IS the policy working), or suppress with rationale if a bump is breaking — never skip silently.

---

### Task 7: docs/supply-chain.md + branch protection arming (PR3)

**Files:**
- Create: `docs/supply-chain.md`
- Procedure: branch-protection API call (no file)

**Interfaces:** consumes everything; produces the public posture narrative + the armed ruleset.

- [ ] **Step 1: Write `docs/supply-chain.md`** covering, in order (write real prose, ~120 lines):
  1. The five layers table from the design (mechanism + enforcement point per layer).
  2. SCA triage policy: delta PR gate at HIGH+ (dependency-review), standing tree via Dependabot alerts, local pip-audit fixability policy + the severity-data caveat, both suppressions files and their reason+review-by convention.
  3. Outbound-leak surface: gitleaks (commit staged / push full-history / CI full-history), the tracked-path denylist, what must never leave the machine (.env, DBs, agent state, keys), and the licensed-material rule (first-party or verifiably-permissive only).
  4. Deliberate keeps with rationale: Fly-built images are NOT provenance-attested (owner decision — no deploy token in public Actions); CodeQL advisory (wedging lesson); `/data/riskflow.db` filename (WAL-safe).
  5. "Am I affected?" runbook: Security tab → Dependabot alerts; the dependency graph; downloading the latest `idraa-sbom-<sha>` artifact and grepping it.

- [ ] **Step 2: Arm branch protection (only after ≥3 consecutive green `gate` runs on main)**

```bash
gh api "repos/yasirhamza/idraa/actions/workflows/ci.yml/runs?branch=main&per_page=5" --jq '.workflow_runs[] | "\(.head_sha[0:8]) \(.conclusion)"'
# require the aggregator + secrets; enforce_admins=false keeps the agentic
# --admin merge path (local gate remains the authority; this is the backstop)
gh api -X PUT repos/yasirhamza/idraa/branches/main/protection \
  -F 'required_status_checks[strict]=false' \
  -f 'required_status_checks[contexts][]=ci-success' \
  -f 'required_status_checks[contexts][]=secrets (gitleaks, full history)' \
  -f 'required_status_checks[contexts][]=dependency-review' \
  -F 'enforce_admins=false' \
  -F 'required_pull_request_reviews=null' \
  -F 'restrictions=null'
# dependency-review runs on EVERY PR (no path filter) so it cannot wedge the way
# a path-filtered CodeQL ruleset does — safe to require. CodeQL stays advisory.
gh api repos/yasirhamza/idraa/branches/main/protection --jq '.required_status_checks.contexts'
```

If the green-streak precondition isn't met when PR3 is ready, merge PR3 and leave THIS step as a tracked follow-up on #555 — do not arm against a red or unproven CI.

- [ ] **Step 3: Commit + PR**

```bash
git add docs/supply-chain.md
git commit -m "docs: supply-chain posture — layers, triage policy, outbound surface, am-I-affected runbook (#555)"
```

---

## PR boundaries & ceremony

- **PR1** = design+plan docs (already on `epic/supply-chain`) + Tasks 1–4. **PR2** = Task 5. **PR3** = Tasks 6–7. Each: local gate green → PR → admin-squash → verify CI on main.
- Cross-cutting infra → **4-reviewer plan-gate on the design+plan BEFORE Task 1**, iterated to 0/0; per-task spec-compliance review; **4-reviewer final PR-gate on PR3** (the epic close), iterated to 0/0.
- After PR1 merges, verify the public CI is FULLY green on main (the fossil reds must be gone) before starting PR2.
