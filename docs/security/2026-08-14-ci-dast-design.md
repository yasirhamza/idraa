# CI DAST — Phase 1 design (Schemathesis per-PR)

- **Date:** 2026-08-14
- **Status:** design, pending plan-gate
- **Author:** security-tooling initiative (follows the 2026-08-13 DAST campaign)
- **Related:** draft advisory GHSA-46jj-823j-mjj9 (findings), `docs/security/threat-model.md`, local baseline store `~/idraa-security-scans/2026-08-13-baseline/`

## 1. Goal

Add a **dynamic** security layer to CI. Today CI has SAST (CodeQL, ruff-security, zizmor), dependency review, and secret scanning, but no DAST — so a bug like **D1** (an unhandled `500` in the CSRF middleware on a non-ASCII token, found by Wapiti) ships uncaught. Phase 1 adds a **deterministic, per-PR API-fuzzing gate** that would have caught D1 on the PR that introduced it.

## 2. Scope

**In scope (Phase 1, this cycle):**
- A Schemathesis-based API fuzzing suite run **in-process (ASGI)**, authenticated as a seeded admin with valid CSRF, over the app's own OpenAPI surface.
- A new `dast` CI job wired into `ci-success` (so a regression blocks merge).
- A committed, reusable `security/dast/` harness (schema extraction + config + README).
- The **D1 fix** bundled in the same PR, so the new gate is green from day one.

**Out of scope (Phase 2+, tracked, not built here):**
- Scheduled/nightly **Nuclei** DAST against a spun-up instance.
- **Baseline-diff gating** (a committed baseline + "fail only on new findings"). Phase 1 is deterministic pass/fail and needs no baseline.
- Server (uvicorn) lifecycle in CI.
- ZAP/Dastardly deep release scans.

## 3. Decisions (locked at brainstorm)

| Decision | Choice | Why |
|---|---|---|
| Phase-1 deliverable | Schemathesis per-PR + committed harness | Highest value/PR, deterministic, reuses the pytest infra |
| Integration style | In-process ASGI via pytest (not CLI-against-uvicorn) | Deterministic, reuses test DB/seed, locally runnable, no server orchestration |
| Auth | Authenticated admin + CSRF injection hooks | Covers the full authenticated surface (~139 paths), not just pre-auth |
| D1 | Fix bundled in this cycle | Gate green from day 1; closes a confirmed unauth 500 |

## 4. Architecture

A new **`dast` CI job** runs a Schemathesis suite (`tests/dast/`) that:
1. builds the app in-process (`create_app()`, test env, temp SQLite),
2. seeds an MFA-enrolled admin and logs in (captures `idraa_session` + `csrf_token`),
3. loads the OpenAPI schema from the app (`/api/openapi.json`, served in non-prod),
4. for each in-scope operation, generates fuzzed cases, **injects the session cookie and overwrites `_csrf` with the valid token** so state-changing requests reach handlers, and
5. asserts **no server errors** (and status/response conformance where applicable).

Any check failure fails the job → `ci-success` fails → merge blocked. The reusable, non-secret pieces live committed under `security/dast/`; the runnable suite lives under `tests/dast/`.

```
PR ──> CI ──> dast job ──> pytest tests/dast/
                              │  app = create_app() [test env, temp DB]
                              │  seed admin (mfa_enrolled_at) + login
                              │  schema = from_asgi("/api/openapi.json", app)
                              │  per op: fuzz params, inject session cookie + valid _csrf
                              └─ check: not_a_server_error (+ status/response conformance)
                                   any failure → job red → ci-success red → merge blocked
```

## 5. Components

### 5.1 D1 fix — `src/idraa/middleware/csrf.py`
Before `hmac.compare_digest(submitted, inbound)` (currently ~line 276), reject a non-ASCII submitted token as a `403`:
```python
if not submitted.isascii():
    return self._forbid("token not ascii", request)
```
A valid token is always ASCII (hex + `.`), so non-ASCII can only be a malformed/forged token. This turns the current unhandled `TypeError → 500` into a clean `403`. Add a regression test (`tests/unit/test_csrf_*` or `tests/integration/test_csrf_*`) asserting a non-ASCII `_csrf` form field and `X-CSRF-Token` header both return `403`, not `500`.

### 5.2 `security/dast/` (committed harness — no secrets, no findings)
- `extract_openapi.py` — dumps `app.openapi()` with an injected `servers` entry and heavy/destructive paths filtered, for reuse (Phase-1 pytest reads the schema in-process; the extractor is the reusable artifact Phase-2 Nuclei will consume via `-im openapi`).
- `config.py` (or `constants.py`) — the **single source of truth** for `EXCLUDED_OPERATIONS` (path/method patterns) and the enabled checks, imported by the pytest suite so scope isn't duplicated.
- `README.md` — what this is, how the per-PR gate works, how to run it locally, the Phase-2 Nuclei/baseline plan, and a pointer to the local baseline store. Explicitly documents the coverage caveat (below).

### 5.3 `tests/dast/test_api_fuzz.py` (the Schemathesis suite)
- Module-level: `app = create_app()` (test env), `schema = schemathesis.openapi.from_asgi("/api/openapi.json", app)`.
- Session-scoped fixture: create the temp DB schema, seed one org + one **MFA-enrolled admin** (`mfa_enrolled_at` set so the enrollment guard passes), log in (capture `idraa_session` + `csrf_token`).
- `@schema.parametrize()` test with a **before-call hook** that, for every request: attaches the session cookie, and for state-changing methods **sets `_csrf` (form field) and `X-CSRF-Token` (header) to the valid token** — so the fuzzer explores business params instead of bouncing off CSRF.
- `case.call_and_validate(checks=(not_a_server_error, status_code_conformance))`.
- Operation filtering: exclude `EXCLUDED_OPERATIONS`.
- **Determinism:** a pinned Hypothesis profile — fixed seed / `derandomize=True`, `database=None` (no example DB), a per-operation `max_examples` cap, and a `deadline`. Combined with the repo's pinned `PYTHONHASHSEED`, the gate is reproducible run-to-run.
- **Non-vacuous meta-check:** a separate assertion that the schema loaded (>0 operations), the login actually authenticated (a known admin-only GET returns 200, not a login redirect), and a known-good operation passes — so a broken harness fails loudly rather than passing empty.

### 5.4 CI — `.github/workflows/ci.yml`
- New `dast` job (`ubuntu-latest`): checkout → `uv sync` (dev extras, frozen) → run the suite (`uv run pytest tests/dast/`). Time-boxed.
- Add `dast` to the `ci-success` job's `needs`.
- **Not** added to the fast local pre-push gate (`scripts/run_local_gate.py`) — it's CI-only + on-demand, like e2e, to keep the local gate fast.

### 5.5 Task runner — `python -m idraa.tasks dast`
A thin wrapper that runs `pytest tests/dast/` (Python-authoritative task-runner rule), so it's runnable identically on Windows/Linux/macOS.

### 5.6 Dependency
`schemathesis` pinned in the dev extras (`pyproject.toml`), lockfile updated. The exact hook API is version-specific; the implementation pins a version and adapts the hooks to it.

## 6. Checks — what actually gates (important nuance)

idraa is a **server-rendered HTML app**, so most routes return `text/html`, not schema'd JSON. Therefore:
- **Primary check: `not_a_server_error`** — catches the D1 class (unhandled exceptions → 500). This is the load-bearing gate.
- **Secondary: `status_code_conformance`** — flags responses whose status isn't declared in the operation's OpenAPI responses (catches undocumented error paths).
- **`response_schema_conformance`** has limited applicability (HTML bodies aren't schema-validated) and is **not** relied on as a primary gate; enabled only where a JSON response schema exists.

The README documents this so no one mistakes "few conformance checks" for "weak coverage" — the 500-detection over the authenticated fuzzing surface is the value.

## 7. Coverage caveat (documented, not hidden)

POST/PUT/PATCH/DELETE reach handlers **only because** the hook injects a valid `_csrf`; fuzzing then explores the business params. This is intentional and correct for finding handler bugs. The `_csrf` field itself is not fuzzed by this suite (D1 covers that class, with its own regression test). The suite is deterministic and in-process, so it exercises the app logic, not the network/proxy layer — that's Phase-2 Nuclei's job.

## 8. Failure semantics

A check failure is a pytest failure → the `dast` job is red → `ci-success` is red → merge blocked. Deterministic seeding + the D1 fix mean the gate is green on `main` from day one; a new handler 500 or undocumented status on any PR turns it red.

## 9. Testing / verification

- The suite is the test. Plus the §5.3 non-vacuous meta-check.
- The D1 regression test (§5.1).
- A one-time full local run demonstrating the gate is green post-D1-fix, and (as evidence the gate works) a demonstration that reverting the D1 fix turns it red.

## 10. Review ceremony

Per CLAUDE.md (cross-cutting security infra): **4-reviewer plan-gate** (architect / security-auditor / spec-compliance / methodology) on this spec + the implementation plan, iterated to zero; implementation with per-task review; **4-reviewer final PR-gate** iterated to zero; then PR, stop for owner sign-off before merge. (Methodology's surface is small — no FAIR math — but included per the cross-cutting-infra trigger.)

## 11. Risks / open questions

- **Schemathesis API drift:** hook/auth API differs across Schemathesis 3.x/4.x. Mitigation: pin a version; the plan adapts hooks to it.
- **Schema fidelity for Form routes:** FastAPI describes `Form(...)` params in the schema; verify the generated cases actually populate form bodies (not just JSON). If a route's params aren't well-described, the fuzzer may under-cover it — acceptable for Phase 1 (500-detection still fires on what it does send).
- **Runtime:** cap `max_examples` so the `dast` job stays within a reasonable CI budget (target: a few minutes). Tune during implementation.
- **State-mutating GETs (C7):** the wizard-draft GET creates rows; the temp DB is disposable, so acceptable, but exclude if it slows the run.
