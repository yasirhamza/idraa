# README + Evaluation Self-Hosting Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Idraa's evaluation self-hosting real and visible: WebAuthn defaults stop hardcoding idraa.fly.dev, a `.env.example` documents every operator knob, and the public README is rewritten around a balanced evaluator/engineering split.

**Architecture:** Three independent surfaces — a config-default change with a boot guard folded into the EXISTING `_check_webauthn_hardening` validator, a documentation file consumed by docker compose, and the README. No new modules, no new dependencies.

**Tech Stack:** pydantic-settings validators, pytest, docker compose env conventions, Playwright (screenshots only).

**Spec:** `docs/superpowers/specs/2026-07-22-readme-selfhost-rewrite-design.md`

## Global Constraints

- License section of README stays VERBATIM (all-rights-reserved) plus exactly one evaluation-scope sentence (owner decision 2026-07-22).
- No deployment-specific domain may remain as a code default (idraa.fly.dev, idraa.app anywhere in `src/`).
- No new runtime dependencies.
- **Prod continuity (supersedes spec §Deliverable-3 fly.toml item):** `WEBAUTHN_RP_ID` and `WEBAUTHN_ORIGINS` are ALREADY SET as Fly **secrets** on the prod app (verified `scripts/fly secrets list`, 2026-07-22) — secrets override any default, so prod is unaffected by the default change. Do NOT add them to `fly.toml [env]` (a second source of truth would shadow/conflict with the secrets). Record this in a fly.toml comment. Log this deviation in the spec's Scope drift log (Task 1 commit).
- Every task: run its named tests + `ruff check` / `ruff format --check` on touched files before committing. The pre-push gate runs the full suite at the end.
- Worktree: `/private/tmp/claude-501/-Users-yassirhamad-projects-RiskFlow/f5984542-2f38-4e7a-816c-5a92f2139c75/scratchpad/wt-readme-selfhost`, branch `docs/readme-selfhost-rewrite`, venv `.venv` (create with `~/.local/bin/uv sync --extra dev` if absent).

---

### Task 1: WebAuthn localhost defaults + explicit prod guard

**Files:**
- Modify: `src/idraa/config.py:291-295` (defaults) and `:384-402` (validator insert)
- Modify: `fly.toml` (comment under `[env]`)
- Modify: `docs/superpowers/specs/2026-07-22-readme-selfhost-rewrite-design.md` (drift-log entry)
- Test: `tests/test_config.py` (append)

**Interfaces:**
- Consumes: existing `_check_webauthn_hardening` model validator; `Settings.environment` semantics (`test` exempt, `prod` hardened).
- Produces: `Settings().webauthn_rp_id == "localhost"` and `Settings().webauthn_origins == "http://localhost:8000"` in dev/test; prod boot with the localhost default raises `ValidationError` whose message contains both `WEBAUTHN_RP_ID` and `WEBAUTHN_ORIGINS`.

- [ ] **Step 1: Write the failing tests.** Append to `tests/test_config.py`, mirroring the file's existing Settings-construction pattern (it already tests `_check_secret_hardening` — reuse the same helper/kwargs it uses for env/secret setup):

```python
def test_prod_refuses_localhost_webauthn_default():
    """Self-hoster forgets WEBAUTHN_* in prod -> loud boot failure naming both vars,
    not silently broken passkeys bound to a wrong RP-ID."""
    with pytest.raises(ValidationError) as exc:
        Settings(environment="prod", session_secret="x" * 40)
    msg = str(exc.value)
    assert "WEBAUTHN_RP_ID" in msg
    assert "WEBAUTHN_ORIGINS" in msg


def test_prod_boots_with_real_rp_id_and_origins():
    s = Settings(
        environment="prod",
        session_secret="x" * 40,
        webauthn_rp_id="risk.example.com",
        webauthn_origins="https://risk.example.com",
    )
    assert s.webauthn_rp_id == "risk.example.com"


def test_dev_and_test_accept_localhost_defaults():
    for env in ("dev", "test"):
        s = Settings(environment=env, session_secret="y" * 20)
        assert s.webauthn_rp_id == "localhost"
        assert s.webauthn_origins == "http://localhost:8000"
```

- [ ] **Step 2: Run to verify failure.** `pytest tests/test_config.py -k webauthn_default -x -q` — expected: `test_prod_refuses_localhost_webauthn_default` FAILS (no error raised — prod currently boots happily on the idraa.fly.dev defaults) and `test_dev_and_test_accept_localhost_defaults` FAILS (defaults are idraa.fly.dev).

- [ ] **Step 3: Change the defaults** in `src/idraa/config.py` (lines 291-295):

```python
    # WebAuthn Relying-Party identity — DEPLOYMENT-SPECIFIC, never hardcode a
    # real domain here (OSS rule): localhost works for dev/compose evaluation
    # out of the box; prod boot REFUSES the localhost default (see
    # _check_webauthn_hardening). Passkeys are permanently bound to the RP-ID
    # they were enrolled under — production values live in deployment config
    # (Fly secrets for the reference deployment).
    webauthn_rp_id: str = "localhost"
    webauthn_rp_name: str = "Idraa"
    # Comma-separated allowed origins; must be https:// and host-match the
    # RP-ID in prod (validator below).
    webauthn_origins: str = "http://localhost:8000"
```

(Preserve the existing single-registrable-domain comment block above these lines.)

- [ ] **Step 4: Insert the guard** in `_check_webauthn_hardening`, immediately after the `if not self.webauthn_rp_id.strip():` block:

```python
        if self.webauthn_rp_id == "localhost":
            raise ValueError(
                "WEBAUTHN_RP_ID is still the localhost default in "
                f"environment={self.environment!r}. Set WEBAUTHN_RP_ID to the "
                "deployment's registrable domain and WEBAUTHN_ORIGINS to its "
                "https:// origin(s). Passkeys enrolled under a wrong RP-ID are "
                "permanently bound to it — this must be correct before the "
                "first user enrolls."
            )
```

- [ ] **Step 5: fly.toml comment.** Under `[env]`, after the `FORWARDED_ALLOW_IPS` entry, add:

```toml
  # WEBAUTHN_RP_ID / WEBAUTHN_ORIGINS are Fly SECRETS (set 2026-07-22), NOT
  # [env] entries — do not add them here (two sources of truth would shadow).
  # Their values must NEVER drift from the RP-ID passkeys were enrolled under.
  # See docs/superpowers/specs/2026-07-22-readme-selfhost-rewrite-design.md.
```

- [ ] **Step 6: Spec drift-log entry.** Append to the spec's `## Scope drift log`:

```markdown
- CHANGED at plan time (Task 1): fly.toml gets a comment, NOT env entries —
  WEBAUTHN_RP_ID/ORIGINS were found already set as Fly secrets (2026-07-22),
  which override defaults; adding [env] duplicates would create a second,
  shadowed source of truth.
```

- [ ] **Step 7: Run.** `pytest tests/test_config.py -q` — expected: all pass (including pre-existing hardening tests). Then `ruff check src/idraa/config.py tests/test_config.py && ruff format --check src/idraa/config.py tests/test_config.py`.

- [ ] **Step 8: Grep-verify the OSS rule.** `grep -rn "idraa\.fly\.dev\|https://idraa\.app" src/` — expected: ZERO matches.

- [ ] **Step 9: Commit.** `git commit -m "fix(config): WebAuthn defaults localhost + prod boot guard — no hardcoded deploy domain"`

---

### Task 2: `.env.example`

**Files:**
- Create: `.env.example`

**Interfaces:**
- Consumes: variable names exactly as declared in `src/idraa/config.py` `Settings` (aliases where declared).
- Produces: a file `docker compose` picks up natively; README (Task 4) links to it by name.

- [ ] **Step 1: Create `.env.example`:**

```bash
# Idraa — operator configuration (evaluation self-hosting)
# Copy to .env next to docker-compose.yml; compose reads it automatically.
# Full reference incl. internal tuning knobs: src/idraa/config.py docstrings.

# ── Core ────────────────────────────────────────────────────────────────────
# REQUIRED. Generate: python -c 'import secrets; print(secrets.token_urlsafe(48))'
# compose refuses to start without it; prod requires 32+ chars.
SESSION_SECRET=

# dev | prod | test.  prod turns on secret + WebAuthn hardening at boot.
ENVIRONMENT=dev

# compose default points at the bundled Postgres service; SQLite also works:
# sqlite+aiosqlite:////data/idraa.db
DATABASE_URL=postgresql+asyncpg://idraa:idraa@db:5432/idraa

# ── Reverse proxy ───────────────────────────────────────────────────────────
# If Idraa runs behind a TLS-terminating proxy (required for passkeys), tell
# uvicorn which proxy IPs to trust for X-Forwarded-Proto/Host so generated
# absolute URLs say https:// + your public host. "*" is safe ONLY when the
# app port is reachable exclusively via the proxy.
#FORWARDED_ALLOW_IPS=*

# ── Auth / MFA ──────────────────────────────────────────────────────────────
# required (default): every user must enroll a second factor at first login.
# optional: sensible for throwaway evaluation instances.
AUTH_MFA_POLICY=required

# Encrypts stored TOTP secrets at rest. Generate like SESSION_SECRET.
MFA_ENCRYPTION_KEY=

# Login throttle: failures before lockout (0 disables) / lockout seconds.
AUTH_MAX_FAILED_LOGINS=5
AUTH_LOCKOUT_SECONDS=900

# ── Passkeys (WebAuthn) ─────────────────────────────────────────────────────
# RP-ID = your registrable domain (no scheme/port). Passkeys are PERMANENTLY
# bound to it — set it correctly before the first user enrolls. In prod the
# app refuses to boot on the localhost default.
WEBAUTHN_RP_ID=localhost
WEBAUTHN_ORIGINS=http://localhost:8000
WEBAUTHN_RP_NAME=Idraa

# ── Simulation envelope ─────────────────────────────────────────────────────
MC_ITERATIONS_DEFAULT=10000
MC_ITERATIONS_MAX=1000000
HIGH_FIDELITY_ITERATIONS_THRESHOLD=250000
MAX_CONCURRENT_HIGH_FIDELITY_RUNS=2

# ── Retention / disk ────────────────────────────────────────────────────────
# 0 disables the sweeps. Full per-iteration sample arrays are the disk hog —
# purging keeps runs + summaries, drops re-derivable chart detail.
RETENTION_SAMPLE_PURGE_DAYS=0
RETENTION_RUN_DELETE_DAYS=0
RETENTION_SWEEP_INTERVAL_HOURS=6
MIN_FREE_DISK_BYTES=300000000

# ── Export rate limiting ────────────────────────────────────────────────────
EXPORT_RATE_LIMIT_COUNT=30
EXPORT_RATE_LIMIT_WINDOW_SECONDS=60
```

- [ ] **Step 2: Verify every name.** `for v in SESSION_SECRET ENVIRONMENT DATABASE_URL AUTH_MFA_POLICY MFA_ENCRYPTION_KEY AUTH_MAX_FAILED_LOGINS AUTH_LOCKOUT_SECONDS WEBAUTHN_RP_ID WEBAUTHN_ORIGINS WEBAUTHN_RP_NAME MC_ITERATIONS_DEFAULT MC_ITERATIONS_MAX HIGH_FIDELITY_ITERATIONS_THRESHOLD MAX_CONCURRENT_HIGH_FIDELITY_RUNS RETENTION_SAMPLE_PURGE_DAYS RETENTION_RUN_DELETE_DAYS RETENTION_SWEEP_INTERVAL_HOURS MIN_FREE_DISK_BYTES EXPORT_RATE_LIMIT_COUNT EXPORT_RATE_LIMIT_WINDOW_SECONDS; do grep -qiE "(alias=\"$v\"|^    $(echo $v | tr 'A-Z' 'a-z'):)" src/idraa/config.py || echo "MISSING: $v"; done` — expected: no output. (`FORWARDED_ALLOW_IPS` is uvicorn's, not Settings' — exempt.) Verify each DEFAULT VALUE matches config.py's declared default; fix any mismatch in .env.example, never in config.

- [ ] **Step 3: Boot check.** `SESSION_SECRET=$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))') docker compose config -q` — expected: exit 0 (compose parses with the example env shape).

- [ ] **Step 4: Commit.** `git commit -m "docs(ops): .env.example — operator configuration reference"`

---

### Task 3: README screenshots

**Files:**
- Create: `docs/readme/dashboard-2026-07.png`, `docs/readme/loss-exceedance-2026-07.png`, `docs/readme/wizard-2026-07.png`
- Create (scratch, not committed): capture script in the session scratchpad

**Interfaces:**
- Consumes: `tests/e2e/conftest.py::live_server_url` fixture (session-scoped dev server; e2e env uses `AUTH_MFA_POLICY=optional`) and the run/scenario seeding already used by the chart e2e tests (DISCOVERY STEP: `grep -rln "seed\|_make_run\|scenario" tests/e2e/ | head` and reuse the helper the loss-exceedance chart test uses — do not write new seeding).
- Produces: three PNGs, 1440×900 viewport, LIGHT theme, no personal data (seeded fixture data only) — exact paths above, referenced verbatim by Task 4's README.

- [ ] **Step 1: Locate the chart-test seeding.** Find the e2e test that renders the loss-exceedance chart; note its seed helper + the page routes it visits.
- [ ] **Step 2: Write a pytest capture "test"** in the scratchpad (imports `live_server_url` + that seed helper; logs in the way e2e tests do; visits dashboard, run-detail LEC section, wizard step 1; `page.screenshot(path=...)` at `viewport={"width": 1440, "height": 900}`, `color_scheme="light"`).
- [ ] **Step 3: Run it.** `pytest <scratchpad>/capture_readme_shots.py -q` with the repo's e2e env vars (copy from how `idraa.tasks e2e` invokes pytest). Expected: 3 PNGs written into `docs/readme/`.
- [ ] **Step 4: Eyeball all three.** Open each PNG — no empty states, no debug chrome, charts populated. If the seeded data renders an empty dashboard, STOP and report back rather than committing filler screenshots (fallback recorded in spec: README ships without the gallery, screenshots become a follow-up commit).
- [ ] **Step 5: Commit.** `git add docs/readme/*.png && git commit -m "docs(readme): evaluation screenshots (seeded fixture data, light theme)"`

---

### Task 4: README rewrite

**Files:**
- Modify: `README.md` (full rewrite; License + Trademarks sections preserved verbatim with the one added sentence)

**Interfaces:**
- Consumes: `.env.example` (Task 2), `docs/readme/*.png` (Task 3), existing `ROADMAP.md`, existing License/Trademarks text.
- Produces: the public repo front page.

- [ ] **Step 1: Replace README.md body** with the structure below. RULES: keep every claim checkable against the repo; License section = current text verbatim + the single sentence "Evaluation self-hosting (running your own instance to assess the product) is welcome; any other use needs a license grant."; Trademarks section verbatim; do NOT mention the Fly Keychain wrapper; link the deck at `https://yasirhamza.github.io/idraa`.

Sections in order (write full prose for each, using the current README's six product bullets as the base for "What it does"):
1. `# Idraa` + one-liner + status: "In production UAT on Fly.io; evaluation self-hosting supported via Docker Compose." + deck link.
2. Screenshot gallery: three images, meaningful alt text, side-by-side markdown table.
3. `## What it does` — six bullets (Scenarios / Library / Controls / Analysis / Reporting / Platform), tightened from current; two-sentence v1/v2 lineage close.
4. `## Try it (evaluation)`:
```bash
git clone https://github.com/yasirhamza/idraa && cd idraa
cp .env.example .env   # then set SESSION_SECRET + MFA_ENCRYPTION_KEY (see file)
docker compose up -d --build
open http://localhost:8000/setup   # first-run admin creation
```
plus one paragraph: non-localhost hosts need `WEBAUTHN_RP_ID`/`WEBAUTHN_ORIGINS` + a TLS proxy with `FORWARDED_ALLOW_IPS`; evaluation-scope license note.
5. `## How it's built` — three short paragraphs: stack (FastAPI + Jinja2 + HTMX/Alpine, no JS build; SQLAlchemy 2 + Alembic; SQLite/Postgres); engine (native Monte Carlo, FAIR-CAM Boolean composition, κ meta-reliability coupling, Shapley attribution, weight-robustness ensemble ranges); verification discipline (independent in-Excel LET/RANDARRAY workbook, hand-math pinned anchors, pre-push gate = CI mirror).
6. `## Configuration` — 8-row table of the highest-leverage vars (SESSION_SECRET, ENVIRONMENT, DATABASE_URL, AUTH_MFA_POLICY, MFA_ENCRYPTION_KEY, WEBAUTHN_RP_ID, WEBAUTHN_ORIGINS, FORWARDED_ALLOW_IPS) → "full reference: `.env.example`".
7. `## Development` — current dev section trimmed (uv sync, pre-commit both stages, local gate, tasks, migrations); keep CLAUDE.md pointer.
8. `## License` + `## Trademarks` per rules above.

- [ ] **Step 2: Verify claims.** `docker compose config -q` (quickstart block is truthful), `ls docs/readme/` (image paths), link check on the deck URL and `.env.example` reference.
- [ ] **Step 3: Markdown render check.** Preview README (any renderer); images display; no broken relative links.
- [ ] **Step 4: Commit.** `git commit -m "docs: README rewrite — evaluator/engineering split + evaluation self-hosting"`
- [ ] **Step 5: Push branch** (`git push -u origin docs/readme-selfhost-rewrite` — full gate runs) and open the PR titled "docs+config: evaluation self-hosting — README rewrite, .env.example, WebAuthn localhost defaults".
