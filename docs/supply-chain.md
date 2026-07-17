# Supply-chain security posture

This document describes the supply-chain controls on `yasirhamza/idraa`: what
runs, where, what it catches, and how to check whether an advisory affects a
checkout of this repo. It is a narrative companion to
`docs/superpowers/specs/2026-07-17-supply-chain-security-design.md` (the
design) — this doc is the posture as shipped, not the rationale trail.

## 1. Six layers

| # | Layer | Mechanism | Enforcement point |
|---|---|---|---|
| 1 | Dependency integrity | `uv.lock` pins every package by exact version + sha256 hash; `uv lock --check` fails the gate on pyproject/lock drift | local gate + CI `gate` job |
| 2 | Dependency vulns (SCA) | `dependency-review-action` on PRs (delta, severity-based) + Dependabot alerts (standing tree) + `pip-audit` in the local gate (offline, fixability-based) | PR checks + Security tab + pre-push |
| 3 | CI/build hardening | `ci.yml`'s `gate` job runs `scripts/run_local_gate.py` verbatim, so CI cannot drift from the local authority; every `uses:` is SHA-pinned with a version comment; top-level `permissions: contents: read` | GitHub Actions |
| 4 | Build/release transparency | Docker base image digest-pinned (both stages); CycloneDX SBOM generated from `uv.lock` on every `main` push, uploaded as a sha-keyed artifact | CI + `Dockerfile` |
| 5 | First-party SAST | `ruff check --select S` in the local gate, re-checked by the named blocking `sast` CI job (`ruff check --select S` — same tool + same pyproject config, so findings cannot diverge); `zizmor` lints the workflows; CodeQL (`python`) advisory on PRs + weekly cron | gate + `ci-success` + Security tab |
| 6 | Outbound-leak control | gitleaks (staged at commit; full-history at push via a dedicated pre-push hook; full-history again in CI) + a tracked-path denylist (`scripts/lint_tracked_paths.py`) independent of content scanning | pre-commit / pre-push / CI `secrets` |

This is the standard GitHub-native toolkit, wired so the local gate stays the
actual merge authority and CI is its drift-proof mirror, not a second,
independently-maintained copy of the same rules.

## 2. SCA triage policy

Two SCA layers, deliberately different rules — they will not always agree.

- **PR delta gate — `dependency-review-action`, severity-based.** Runs on
  every PR into `main`; fails when the PR *introduces* a dependency with a
  known vulnerability at `HIGH`+ (`fail-on-severity: high` in
  `.github/dependency-review-config.yml`). It only sees what changed in the
  PR, so it cannot wedge on a pre-existing finding — those are
  Dependabot-alert territory.
- **Local gate — `scripts/sca_gate.py`, fixability-based.** Runs
  `pip-audit` against the locked runtime set on every push.
  **Severity-data caveat:** pip-audit's JSON reports fixability, not
  severity, so the local gate cannot replicate the PR gate's severity rule —
  it fails on any *fixable, unsuppressed* vulnerability and warns on
  unfixable/suppressed ones. Strictly stronger on the dimension it can see
  (also catches standing-tree and MEDIUM/LOW fixables), but not a severity
  gate.

Both use the same suppression shape — one advisory id per line, immediately
preceded by a reason + review-by-date comment — in two separate,
non-interchangeable files: `.github/dependency-review-config.yml`
(`allow-ghsas`, PR gate) and `scripts/sca_suppressions.txt` (local gate,
format machine-enforced — `parse_suppressions()` raises on a bare id).

Expected response to a fixable local-gate finding: bump the dependency in the
same change. Suppress only when a bump is itself breaking. Offline hatch:
`IDRAA_GATE_SKIP_AUDIT=1` skips only the audit step; document why in the
commit that uses it.

## 3. Outbound-leak surface

- **gitleaks**, three points: staged-content at commit; a genuine full-history scan at push (a dedicated pre-push hook running `gitleaks git`); and full-history again in CI as the backstop against a bypassed local hook.
- **Tracked-path denylist** (`scripts/lint_tracked_paths.py`): fails if any
  *tracked* file matches local tool/agent state, secrets-shaped files, or
  databases — content-independent, catches whole-file/dir accidents gitleaks
  can miss.
- **Must never leave the machine:** `.env`/`.env.*` (`.env.example` is the
  sole allowlisted template), SQLite DB files and their WAL/SHM/journal
  siblings, agent/tool state dirs (`.claude/`, `.superpowers/`,
  `.memsearch/`, `.design-sync/`, `docs/memory/`), and key material (`.pem`,
  `.p12`, `.jks`, `.keystore`, `local.properties`).
- **Licensed-material rule.** Nothing enters this tree unless first-party or
  under a verifiably permissive license, vendored deliberately with
  provenance recorded (§7). This repo is source-visible with no license
  grant (`README.md` § License); material copied in from elsewhere must not
  carry terms this repo cannot honor. Review discipline, not an automated
  gate.

## 4. Deliberate keeps (with rationale)

- **Fly-built images are not provenance-attested.** Production images build
  on Fly's remote builder, not public Actions. Provenance would mean either
  running the build in public CI or putting a Fly deploy token in
  public-repo secrets — both judged worse than the status quo. The SBOM
  (layer 4) gives dependency transparency without image attestation;
  revisit if the deploy architecture changes.
- **CodeQL stays advisory.** A prior project's `code_scanning`
  required-check ruleset wedged permanently on a path-filtered workflow.
  CodeQL here runs via GitHub's default setup and posts to the Security tab;
  nothing in branch protection depends on it.
- **`/data/riskflow.db` filename in production** (`fly.toml`'s
  `DATABASE_URL`) **— a legacy name retained through the project rename to
  Idraa, not reverted.** WAL-mode SQLite keeps `-wal`/`-shm` sidecars matched
  to the base filename; the denylist covers `*.db` plus the `-journal`/`-shm`/`-wal` sidecars and `*.sqlite*` explicitly, so any
  name works with the guard — the specific name is a rename-cleanup item,
  not a security control.

## 5. "Am I affected?" runbook

1. **Security tab** —
   `github.com/yasirhamza/idraa/security/dependabot` lists every open
   Dependabot alert against the full lockfile (runtime + dev — see §6).
2. **Dependency graph** — `github.com/yasirhamza/idraa/network/dependencies`
   shows the resolved tree GitHub ingested from `uv.lock`; useful for
   direct-vs-transitive.
3. **SBOM for a specific commit** — every `main` push uploads
   `idraa-sbom-<sha>` (CycloneDX) from the `sbom` job. Download it and grep:
   `jq '.components[] | select(.name=="pillow")' idraa-sbom-<sha>.cdx.json`.
   This is the authoritative "what was actually in the image at commit X"
   answer; the dependency graph reflects the lockfile in general, the SBOM
   is pinned to one build.
4. No match in either → the advisory doesn't currently apply. Re-check after
   the next `uv lock --upgrade-package` bump.

## 6. Coverage notes

- **Dependabot alerts cover the full lockfile, dev deps included.** Local
  `pip-audit` only scans the runtime export (`uv export --frozen --no-dev
  --no-hashes`), so a vulnerability confined to a dev-only tool won't fail
  the local gate but will still surface as a Dependabot alert. A clean local
  `pip-audit` run is not "no vulnerable dev dependencies" — check the
  Security tab for that.
- **`setup-uv` cache poisoning is mitigated twice, independently.** GitHub
  Actions cache scoping isolates a fork-opened PR's cache namespace from
  `main`'s. Independently, every CI `uv sync` runs `--frozen`, verifying
  `uv.lock`'s sha256 hashes against whatever bytes were installed — a
  poisoned cache entry that doesn't match fails the sync rather than
  silently installing. Losing one control still leaves the other.

## 7. Build-asset transparency

- **Tailwind CLI binary** (`src/idraa/tasks/build_css.py`): downloaded from a
  pinned GitHub release, checked against a sha256 hardcoded in the script and
  cross-checked at authoring time against the release's `sha256sums.txt`.
  No upstream signature exists to verify beyond that file — re-hashing
  against the recorded value is the strongest check available. Mismatch
  fails the build closed.
- **Vendored front-end assets** (`static/vendor/` — htmx, Alpine.js,
  DaisyUI): sha384 SRI recorded in `static/vendor/integrity.json`. Trust
  model is explicit **trust-on-first-use**: `vendor_sync.py` fetches the
  declared version over HTTPS from the official CDN at vendor time and
  records the hash of whatever bytes came back — that fetch is the one
  moment integrity isn't independently cross-checked. From then on, the
  recorded sha384 is the tamper/drift guard, read by byte-pin tests on every
  run. Re-vendoring repeats trust-on-first-use and produces a new pin, with
  the diff as the human review checkpoint.

Neither mechanism substitutes for upstream-signed provenance (not offered by
Tailwind's release process or the vendored CDNs today); both are the best
verification achievable against what's actually published, and both fail
closed on a mismatch.
