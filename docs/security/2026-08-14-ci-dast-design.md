# CI DAST — Phase 1 design (Schemathesis per-PR, spun-up ephemeral instance)

- **Date:** 2026-08-14 (rev 2 — applies 4-reviewer plan-gate findings + the spin-up pivot)
- **Status:** design, re-gating after plan-gate fixes
- **Related:** draft advisory GHSA-46jj-823j-mjj9 (findings), `docs/security/threat-model.md`, local baseline `~/idraa-security-scans/2026-08-13-baseline/`

## 0. Non-goals / safety (read first)

- **This gate NEVER targets a deployed environment.** It does not scan `idraa.fly.dev`, any Fly instance, or any shared/staging host. Ever.
- The DAST target is a **small, ephemeral instance CI spins up internally** — `uvicorn` bound to `127.0.0.1` inside the job runner, backed by a **throwaway SQLite DB** migrated + seeded fresh, and torn down at job end.
- Phase-2 dynamic scanning (Nuclei, deferred) follows the same rule: **ephemeral CI instances only, never prod/Fly.**
- The seed admin exists only in that throwaway DB (non-routable `@ci.local` email, runtime-generated password). No real credential is committed or used.
- **This is enforced in code, not just intent.** the `run.py` orchestrator (§5.2) constructs its *own* temp `sqlite+aiosqlite` `DATABASE_URL`, passes `ENVIRONMENT=test` + that URL + `PYTHONHASHSEED=0` **explicitly** into every subprocess env (an override — never inheriting an ambient `DATABASE_URL`), and **asserts** the resolved URL is a local temp `sqlite` path and the target host is `127.0.0.1` **before** it seeds or serves. A misconfigured local run aborts rather than touching anything real.

## 1. Goal

Add a **dynamic** security layer to CI. Today CI has SAST (CodeQL, ruff-security, zizmor), dependency review, and secret scanning, but no DAST — so a class of bug like **D1** (an unhandled `500` in the CSRF middleware on a non-ASCII token, found by Wapiti) can ship uncaught. Phase 1 adds a **per-PR API-fuzzing job** that fails on any **handler `500`** across the authenticated OpenAPI surface.

> **Scope of the "catch."** The fuzzer catches the *class* of unhandled-exception→`500` in **handlers reachable by authenticated fuzzing**. It does **not** catch D1 itself: D1 lives in CSRF *middleware* on the `_csrf` field, which is not a route parameter and which this suite deliberately supplies with a valid token. **D1 is closed in this PR by a dedicated fix + regression test (§5.1), not by the fuzzer.** The gate is green from day one because that fix lands alongside it.

## 2. Scope

**In scope (Phase 1):**
- A `schemathesis run` fuzzing pass over the app's OpenAPI surface against a **CI-spun-up ephemeral uvicorn instance**, authenticated as a seeded admin, with a hook that injects a valid `_csrf` into state-changing bodies.
- A new **advisory-first** `dast` CI job (visible, NOT yet in `ci-success` — see §8).
- A committed, reusable `security/dast/` harness (schema extractor, seed, auth/CSRF hook, config, orchestrator, README).
- The **D1 fix** (both `compare_digest` sites) bundled so the gate is green from day one.
- `docs/security/threat-model.md` §4 updated for the CSRF-middleware change.

**Out of scope (Phase 2+, tracked):** scheduled Nuclei; baseline-diff gating; promoting `dast` into `ci-success` (a follow-up PR after burn-in); ZAP/Dastardly deep scans.

## 3. Decisions

| Decision | Choice | Why |
|---|---|---|
| Phase-1 deliverable | Schemathesis per-PR + committed harness | Highest value/PR; deterministic; reuses CI infra |
| **Target** | **CI spins up a small ephemeral uvicorn instance** (127.0.0.1, throwaway DB) | Owner steer; and it dissolves the in-process coupling hazards the plan-gate found (schema-load 307, cached-singleton DB, cross-event-loop) — the server owns its own settings/engine/loop |
| Auth | Authenticated admin + `_csrf`-injecting hook | Covers the authenticated surface, not just pre-auth |
| D1 | Fixed (both sites) in this cycle | Gate green from day 1; closes a confirmed unauth 500 |
| **Gate wiring** | **Advisory-first**, promote after green burn-in | A flaky fuzzing job in `ci-success` would wedge all merges (the team already removed Playwright e2e for exactly this) |

## 4. Architecture

The `dast` CI job (advisory-visible) runs an orchestrator that:
1. migrates a throwaway SQLite DB (`alembic upgrade head`),
2. seeds one org + one MFA-enrolled admin (runtime-generated password),
3. starts `uvicorn idraa.app:app --host 127.0.0.1` in the background (test env),
4. waits for `/healthz`,
5. logs in (httpx) → captures `idraa_session` + `csrf_token`,
6. runs `schemathesis run <extracted-schema.json> --url http://127.0.0.1:8000 -c not_a_server_error` with the session cookie header and `SCHEMATHESIS_HOOKS=security.dast.hooks` (injects a valid `_csrf` into state-changing form bodies),
7. runs a post-run **canary** (HAR cassette shows ≥1 state-changing request returned non-`403` — proves the CSRF hook fired) and a **session-liveness** re-check; tears down uvicorn; emits a JUnit report (CI display) + a HAR cassette (the canary's source).

The schema is **extracted offline** from `app.openapi()` (no HTTP, no `/setup` redirect) so schema source and call target are decoupled. The same orchestration runs locally via `python -m idraa.tasks dast`.

```
dast job (advisory) ─ alembic upgrade head (temp sqlite)
                    ─ seed org + admin (mfa_enrolled_at, runtime pw)
                    ─ uvicorn 127.0.0.1:8000 &  ── wait /healthz
                    ─ login (httpx) → idraa_session + csrf_token
                    ─ schemathesis run extracted-schema.json --url 127.0.0.1
                        -c not_a_server_error  --exclude-path-regex <heavy/destructive>
                        -H "Cookie: idraa_session=…; csrf_token=…"
                        SCHEMATHESIS_HOOKS=security.dast.hooks  (inject valid _csrf)
                    ─ canary (HAR: >=1 state-changing req non-403) + session-liveness
                    ─ kill uvicorn ; JUnit (display) + HAR (canary) reports
                    (RED = visible failed check; does NOT block merge yet)
```

## 5. Components

### 5.1 D1 fix — `src/idraa/middleware/csrf.py` (BOTH compare sites)
The plan-gate found the fix must cover **two** `hmac.compare_digest` calls, since a non-ASCII str raises `TypeError`:
- **Submitted token** (`dispatch`, ~`:276`): add `if not submitted.isascii(): return self._forbid("token not ascii", request)` before the compare. Covers the form `_csrf` and `X-CSRF-Token` header (both converge through `_extract_submitted_token`).
- **Inbound cookie** (`verify_csrf_token`, `:147`): `sig_hex` is never ASCII/hex-validated before `hmac.compare_digest(expected, sig_hex)` — and this runs on the cookie for **every request incl. safe GETs**, so `GET /anything` with `Cookie: csrf_token=<hex>.<non-ASCII>` is an unauth `500` on all endpoints. Add `if not sig_hex.isascii(): return False` before the compare (a non-ASCII sig can never be a valid hexdigest; not a timing oracle on the secret).
- **Regression tests** for all three inputs: form `_csrf`, `X-CSRF-Token` header, and the cookie path — each asserts `403`/rejection, not `500`.

### 5.2 `security/dast/` (committed harness — no secrets)
- `config.py` — single source of truth: `EXCLUDED_PATH_REGEX` (heavy + **destructive verbs**, see §7), `MAX_EXAMPLES`, `SEED_EMAIL`, `SEED_ORG`. The seed **password is generated at runtime** (`secrets.token_urlsafe`), never committed — sidesteps gitleaks/ruff-S.
- `extract_openapi.py` — `build_schema(base_url) -> dict` from `app.openapi()` (servers-injected, excluded paths filtered), CLI-dumpable. Reused by Phase-2 Nuclei.
- `seed.py` — create org + MFA-enrolled admin into the env DB; return/emit the generated password to the orchestrator (never to stdout logs).
- `hooks.py` — a Schemathesis `before_call` hook that injects the valid `_csrf` into state-changing form bodies (the session cookie is a static `-H` header). Loaded via `SCHEMATHESIS_HOOKS`.
- `run.py` — the orchestrator (migrate → seed → start uvicorn → wait healthz → login → `schemathesis run` → teardown), used by both CI and the tasks runner.
- `README.md` — purpose, local run, the checks nuance, the coverage/false-negative honesty (§7), the excluded surface as a named Phase-1 blind spot, and the Phase-2 plan.

### 5.3 CI — `.github/workflows/ci.yml`
A new **advisory** `dast` job (`ubuntu-latest`) mirroring the existing jobs' full step blocks — `actions/checkout` **with `persist-credentials: false`**, `astral-sh/setup-uv` **with the pinned `env.UV_VERSION` + cache**, `uv sync --extra dev --frozen` — then `PYTHONHASHSEED=0 python -m idraa.tasks dast`. **Not** added to `ci-success` `needs` (advisory-first). **Not** in the fast local gate.

### 5.4 Task runner — `python -m idraa.tasks dast`
Calls `security.dast.run` (Python-authoritative; identical on all platforms).

### 5.5 Dependency
`schemathesis~=4.24` pinned in **dev** extras; lockfile updated.

## 6. Checks — what gates

idraa is server-rendered HTML, so **`not_a_server_error` is the SOLE gating check** (maps `5xx` → defect cleanly). `status_code_conformance` is **NOT** gated (FastAPI auto-declares only `200`/`422`, but the app legitimately returns `303`/`403`/`404`/`413`/`429` — gating on it would false-RED); it may be run **report-only**. `response_schema_conformance` is not used (HTML bodies).

## 7. Coverage — honest characterization

- **Fixed-corpus, not evolving fuzzing.** `--generation-deterministic` + `--seed 0` (deterministic mode already implies no example DB) make this a *fixed* set of `MAX_EXAMPLES` deterministically-generated cases per operation that changes only when the schema changes. A bug in an unexplored input region passes and keeps passing until the schema changes — an accepted Phase-1 tradeoff. **`MAX_EXAMPLES` is a CI-budget floor** and should be set as high as the ~5-min budget allows (it is the real coverage lever). Phase-2 scheduled Nuclei recovers breadth.
- **CSRF-overwrite** (§5.2 hook): state-changing requests reach handlers only because a valid `_csrf` is injected — the correct way to fuzz handler logic (CSRF is origin-verification, not input validation). The `_csrf` field is not fuzzed here; its class is covered by the D1 fix + regression (§5.1).
- **Excluded surface = named blind spot.** `EXCLUDED_PATH_REGEX` drops (a) the Monte-Carlo/report/analysis routes (CI budget), and (b) the **session/account-mutating surface** — `^/(account|users|mfa|settings|auth)(/|$)` plus the `/…/delete|deactivate|purge-samples` and `/logout|/setup` routes. This matters because idraa is a **form-POST** app: destructive/session-killing operations are almost all `POST` (not the `DELETE` verb), so excluding by path is required — a fuzzed hit that logs out, rotates the seed admin, or flips `effective_mfa_policy` would not `500` (so the gate stays correctly green) but would **silently** kill the run session and collapse coverage. Because a mid-run session death is silent, `run.py` adds a **post-fuzz session-liveness re-check** (repeat the admin-only GET after the run; a dead session fails the job as a self-inflicted coverage hole). This excluded surface (esp. the resource-heavy routes, relevant to the threat-model's resource-exhaustion boundary) is a named Phase-1 blind spot covered later by scheduled Nuclei; the README lists it.

## 8. Failure semantics (advisory-first)

The `dast` job runs on every PR and shows a visible pass/fail check, but is **NOT in `ci-success` `needs`** — a failure does not block merge. After a burn-in of consecutive green runs across CI runners proves determinism, a **follow-up PR** promotes `dast` into `ci-success` (merge-blocking). This mirrors the repo's own lesson (Playwright e2e was removed for a login-bootstrap flake that red-X'd otherwise-green runs).

## 9. Determinism / reproducibility

`--generation-deterministic` + `--seed 0` fix Hypothesis's draw (deterministic mode implies no example DB — do **not** also pass `--generation-database none`; schemathesis 4.24.3 rejects the combination as a hard error); **`PYTHONHASHSEED=0` is set in the `dast` job `env` and the tasks runner** (the repo pins it only in the prod image/`fly.toml`, so it must be set here explicitly) to neutralize hash-seed-dependent set ordering in schema→strategy construction. The gating check (`not_a_server_error`) is order-independent, so pass/fail is stable regardless; the pin additionally stabilizes *which* examples run. Verified by two **separate-process** runs (not two calls in one process).

## 10. Testing / verification

- D1 regression tests (§5.1, three inputs).
- **Pre-fuzz smoke** in `run.py` (fail loudly): `/healthz` up; the login session reaches an admin-only GET (`/organization` → 200, not a redirect); the *extracted* schema (the same artifact fed to `schemathesis run`) has > 50 operations; and one known **non-step-up** admin POST reaches its handler when `_csrf` is injected **manually** (a `400`/`422`, not `403`). NB the smoke is an httpx call, so it proves the CSRF *mechanism*, not that the Schemathesis `before_call` *hook* fires — hence the canary below.
- **Post-run canary** (closes the pivot's main false-green path): parse a **HAR cassette** (`schemathesis run --report-har-path <har>`, which records per-interaction request method + response status) and assert **≥ 1 state-changing request returned a non-`403` status**. This must NOT be sourced from the JUnit report: under a `not_a_server_error`-only run a `403` *passes* (403 < 5xx), so all-403 POSTs are all-passing testcases carrying no status — JUnit cannot distinguish `403` from `400`. If the `SCHEMATHESIS_HOOKS` hook silently fails to fire (wrong module path, 4.24 API drift, env not propagated to the subprocess), every POST is `403` and the whole state-changing surface is silently unexercised — the HAR-sourced canary turns that into a loud failure. (The T1 spike verifies the HAR format actually carries statuses.)
- **Server lifecycle:** on a `/healthz`-timeout or non-zero uvicorn exit, capture and surface uvicorn stdout/stderr (a bare `/healthz` is DB-free and would 200 even on a mis-migrated server — the login smoke and the captured stderr are the real diagnostics). Teardown is **terminate → wait → kill-after-timeout** in a `finally`, guarded so a login/schema exception cannot skip it.
- **Demonstrate the gate is non-vacuous** by reverting a **fuzzer-reachable** fix and confirming `schemathesis run` reports a `not_a_server_error` failure + non-zero exit (do not commit the revert). Use the unbounded-`page` fix (revert the `le=` bound on `routes/library.py` → the fuzzer generates a huge `page`, hits the `OFFSET`-overflow `500` on `GET /library` + `/library/_partials/cards`). **Do NOT use a D1 CSRF-guard revert for this demo:** the `before_call` hook injects a *valid* `_csrf` into every state-changing body and the cookies are static `-H` headers, so Schemathesis structurally never delivers a malformed/non-ASCII token to either compare site — reverting a D1 guard leaves the run green (verified in T7). The CSRF token-validation surface is a coverage boundary of this authenticated harness, documented in `security/dast/README.md` ("Authenticated-hook coverage boundary"); D1 is covered by its own regression tests (§5.1) and was originally found by an unauthenticated scanner, not this gate.
- Two separate-process determinism runs; confirm identical result within the CI budget.

## 11. Review ceremony

4-reviewer **plan-gate** applied (this revision, iterated across two rounds to 0 blocker/important). The **final PR-gate is also 4-reviewer** (architect / security-auditor / spec-compliance / **methodology**), iterated to zero — this is a cross-cutting-security-infra milestone, and per CLAUDE.md the 4-reviewer floor applies at BOTH gates and cannot be waived by this spec (PR #306 precedent: a lighter final review missed a methodology blocker). Methodology's surface is small here, but the floor is non-negotiable. Then PR, stop for owner sign-off before merge.

## 12. Risks / open questions

- **Schemathesis CLI drift:** exact `--max-examples` flag name + the `before_call` hook signature are pinned + smoke-verified in the first implementation task.
- **Server startup flakiness in CI:** mitigated by the `/healthz` retry-wait and advisory-first wiring; burn-in gates promotion.
- **Schema fidelity for `Form(...)` routes:** verify the generated cases populate form bodies; 500-detection still fires on what is sent.
