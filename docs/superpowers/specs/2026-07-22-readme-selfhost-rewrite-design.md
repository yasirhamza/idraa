# README rewrite + evaluation self-hosting hardening — design

**Date:** 2026-07-22 · **Owner call:** license stays all-rights-reserved (README
scopes self-hosting to *evaluation*); scope = README + config fix + env docs;
audience = balanced split (evaluator + engineering).

## Problem

1. The public README buries the working container deployment (`Dockerfile`,
   `docker-compose.yml`, `docker-entrypoint.sh` all exist and are healthy) as a
   "run the dev server" snippet, and its only "Deployment" line is the owner's
   private Fly Keychain wrapper. A reader concludes there is no self-host path.
2. Two hardcoded deployment domains remain in product code as `Settings`
   *defaults* (`config.py:291,295`): `webauthn_rp_id = "idraa.fly.dev"`,
   `webauthn_origins = "https://idraa.fly.dev"`. A self-hoster who does not know
   to override them gets silently broken passkeys on their domain — the exact
   failure the OSS no-hardcoded-domains rule exists to prevent. `fly.toml [env]`
   does NOT currently set these vars; prod rides the code defaults.
3. There is no `.env.example` / operator configuration reference.

## Non-goals

- No license change (owner decision 2026-07-22). No GHCR image, no
  `docs/SELF_HOSTING.md` production walkthrough (deferred with the "full
  self-hosting push" option). No RP-ID change for the production deployment.
- The verification-workbook help-link base-url fix ships separately
  (`fix/verification-workbook-labels`, in flight).

## Deliverable 1 — README rewrite (~180 lines, balanced split)

Section order:

1. **Header** — name, one-line pitch, honest status line ("in production UAT on
   Fly.io; evaluation self-hosting supported via Docker Compose"), link to the
   idraa.org deck (GitHub Pages).
2. **Screenshots** — 2–3 in-repo PNGs (dashboard, loss-exceedance chart, wizard)
   under `docs/readme/`, captured via Playwright from a locally seeded instance
   at a fixed viewport; alt text mandatory.
3. **What it does** — existing six product bullets, tightened; keep the
   v1/v2 lineage paragraph but compress to two sentences.
4. **Try it (evaluation)** — promoted compose quickstart: generate
   `SESSION_SECRET`, `docker compose up -d --build`, `/setup` first login,
   `WEBAUTHN_RP_ID`/`WEBAUTHN_ORIGINS` note for non-localhost hosts, explicit
   sentence that the current license permits evaluation use only.
5. **How it's built** — architecture (FastAPI + Jinja2 + HTMX/Alpine, no JS
   build step; SQLAlchemy 2 + Alembic; SQLite/Postgres), the native fair_cam
   engine (FAIR-CAM Boolean composition, κ meta-reliability coupling, Shapley
   attribution, weight-robustness ensemble), and the verification discipline
   (independent in-Excel LET/RANDARRAY Monte Carlo workbook, pinned hand-math
   test anchors, pre-push local gate ≈ CI).
6. **Configuration reference** — table of operator-relevant env vars, sourced
   from `Settings` (see Deliverable 2), pointing at `.env.example`.
7. **Development** — current content trimmed; REMOVE the Fly Keychain wrapper
   line from the README (it moves to a comment at the top of `fly.toml`).
8. **License + Trademarks** — kept verbatim, plus one evaluation-scope sentence
   in License.

## Deliverable 2 — `.env.example`

Repo root, read natively by `docker compose`. Grouped, commented, safe
placeholders. Variables (all verified against `src/idraa/config.py`):

- Core: `DATABASE_URL`, `SESSION_SECRET` (empty + generate command),
  `ENVIRONMENT` (`dev`/`prod` semantics + prod hardening note).
- Auth/MFA: `AUTH_MFA_POLICY` (default `required` — note for eval instances),
  `MFA_ENCRYPTION_KEY`, `AUTH_MAX_FAILED_LOGINS`, `AUTH_LOCKOUT_SECONDS`,
  `WEBAUTHN_RP_ID` (=`localhost` placeholder), `WEBAUTHN_RP_NAME`,
  `WEBAUTHN_ORIGINS` (=`http://localhost:8000`).
- Simulation envelope: `MC_ITERATIONS_DEFAULT`, `MC_ITERATIONS_MAX`,
  `HIGH_FIDELITY_ITERATIONS_THRESHOLD`, `MAX_CONCURRENT_HIGH_FIDELITY_RUNS`.
- Retention/disk: `RETENTION_SAMPLE_PURGE_DAYS`, `RETENTION_RUN_DELETE_DAYS`,
  `RETENTION_SWEEP_INTERVAL_HOURS`, `MIN_FREE_DISK_BYTES`.
- Exports: `EXPORT_RATE_LIMIT_COUNT`, `EXPORT_RATE_LIMIT_WINDOW_SECONDS`.
- Deliberately omitted: internal tuning knobs (quantile fit budgets, weight
  ensemble internals, reaper intervals) — documented in `config.py` docstrings,
  not operator-facing.

## Deliverable 3 — WebAuthn default-domain fix

- `webauthn_rp_id` default → `"localhost"`; `webauthn_origins` default →
  `"http://localhost:8000"`. Dev and compose-eval work out of the box;
  no deployment domain remains in code.
- **Boot guard** (mirrors `_check_secret_hardening`): when `ENVIRONMENT` is not
  dev/test and `webauthn_rp_id == "localhost"`, refuse to boot with a message
  naming both env vars. Rationale: silent-passkey-breakage is worse than a
  loud boot failure.
- **Continuity (CRITICAL):** add to `fly.toml [env]`:
  `WEBAUTHN_RP_ID = "idraa.fly.dev"`, `WEBAUTHN_ORIGINS = "https://idraa.fly.dev"`
  — byte-identical to today's effective values. Registered passkeys are bound
  to the RP-ID; the value must not change, only its home. Per-tester instances
  (hatem/halim) deploy from temp configs — their next deploy needs the same two
  vars or the boot guard stops them loudly (acceptable: loud > silent).
- Tests: guard-refuses-boot test (prod env + localhost RP-ID) and guard-passes
  tests (prod + real RP-ID; dev + localhost). fly.toml continuity is not
  test-enforced — a comment in fly.toml citing this spec records why the values
  must never drift from the RP-ID passkeys were enrolled under.

## Sequencing & ceremony

One branch (`docs/readme-selfhost-rewrite`), three commits in order:
(1) webauthn fix + fly.toml + tests; (2) `.env.example`; (3) README + screenshots.
Bundled SWE reviewer on the full diff; the reviewer brief explicitly includes
the passkey RP-ID continuity check. Not a milestone PR-gate (docs + config
default change, no adapter/math surface).

## Scope budget

- target_task_count: 4 (webauthn fix + tests; .env.example; README text;
  screenshots capture)
- Review budget: 1 bundled SWE reviewer pass over the full branch diff +
  re-review loop on findings; no milestone 4-reviewer gate (docs + config
  default, no adapter/math surface).
- Timeline budget: single session; screenshots are the only step with
  environment risk (local seeded instance) and may be deferred to a follow-up
  commit on the same PR if capture stalls.

## Scope drift log

- Originating prompt: "The Readme in the Idraa public repo needs serious
  rewriting" + "Hardcoding the domain is a serious problem" (owner,
  2026-07-22).
- ADDED via owner Q&A: `.env.example` + configuration reference + webauthn
  default fix bundled with the README (option "README + config fix + env
  docs").
- CUT via owner Q&A: full self-hosting push (docs/SELF_HOSTING.md, GHCR
  image) — explicitly deferred.
- CONSTRAINED via owner Q&A: license stays all-rights-reserved; README scopes
  self-hosting claims to evaluation.
- CHANGED at plan time (Task 1): fly.toml gets a comment, NOT env entries —
  WEBAUTHN_RP_ID/ORIGINS were found already set as Fly secrets (2026-07-22),
  which override defaults; adding [env] duplicates would create a second,
  shadowed source of truth.
- ADDED by security-audit wave (2026-07-22): env_file delivery + commented-optional
  .env.example restructure (B1); corrected proxy-trust guidance + loopback 5432
  (I1/I4); MFA_ENCRYPTION_KEY prod boot guard + docs (I2); network⇒prod guidance
  (I3); .dockerignore .env.
- ADDED during Task-2 review loop (9ed268f): compose threading of FORWARDED_ALLOW_IPS into the app container + DATABASE_URL compose-inert scoping note.
- ADDED at Task 4 (3f4c42d): MFA key min-length parity guard + Compose >=2.24 and bridge-gateway-IP notes; and final-review fix: MFA key distinctness guard.

## Risks

- **RP-ID continuity**: mitigated by fly.toml pin above; verified in review.
- **Screenshot staleness**: PNGs date-stamped in filename; acceptable for a
  README (refresh opportunistically).
- **`AUTH_MFA_POLICY=required` friction for evaluators**: documented in
  `.env.example` (set `optional` for throwaway eval instances) rather than
  changing the secure default.
