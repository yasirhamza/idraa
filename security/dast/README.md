# DAST harness (Phase 1)

Schemathesis-based API fuzzing over an **ephemeral, local-only** target: a
throwaway `uvicorn idraa.app:app` instance bound to `127.0.0.1`, backed by a
throwaway migrated + seeded SQLite database in a temp directory. The harness
extracts `app.openapi()` in-process, logs in as a seeded admin, and fuzzes the
authenticated surface with a `before_call` hook that injects a valid CSRF
token into state-changing form bodies.

**This suite never targets a deployed environment.** Before any subprocess,
DB, or network action, `run.py` code-enforces the safety guard: it refuses to
proceed unless the database URL starts with `sqlite+aiosqlite:///` *and* the
serve host is `127.0.0.1`. There is no argument or env var that can point it
at Fly/prod — the guard is a real `if`/`raise`, not an `assert` (which
`python -O` would silently strip).

## Local run

```
PYTHONHASHSEED=0 python -m idraa.tasks dast
```

`PYTHONHASHSEED=0` is required for determinism — it neutralizes hash-seed-
dependent ordering in schema-to-strategy construction (the repo otherwise
pins this only in the prod image / `fly.toml`, not for local/dev use). The CI
`dast` job sets it explicitly in its step `env:` for the same reason.

A full run takes **~30 s on the reference machine** (95 fuzzed paths / ~3,900
generated cases at the current `MAX_EXAMPLES`), comfortably inside the
workflow's ~5-minute budget. Two separate-process runs at `--seed 0
--generation-deterministic` reach an identical pass/fail verdict.

## Gating rationale: `not_a_server_error` only

idraa is server-rendered HTML, not a JSON API with a declared response
schema, so `not_a_server_error` (any `5xx` -> failure) is the **sole gating
check**. `status_code_conformance` is deliberately **not** gated: FastAPI's
auto-generated OpenAPI schema only declares `200`/`422` per operation, but
the app legitimately returns `303` (redirects), `403` (CSRF/auth), `404`
(not-found), `413` (body-too-large), and `429` (rate-limit) — gating on
schema conformance would flag those correct, intentional responses as
failures. `response_schema_conformance` is not used either, since responses
are HTML bodies, not typed JSON.

A HAR-sourced post-fuzz canary (not the JUnit report) additionally asserts at
least one state-changing request got a non-`403` response — this guards
against the CSRF hook silently failing to fire, which would otherwise leave
every POST returning `403` (a status that satisfies `not_a_server_error` and
would report as a fully green, but meaningless, run).

## Fixed-corpus honesty (false-negative property)

The harness runs with `--seed 0 --generation-deterministic` and a bounded
`MAX_EXAMPLES` (see `config.py`) — a fixed, deterministic set of generated
cases per operation, not an evolving/property-based fuzz campaign. This is a
deliberate CI-budget tradeoff, not a claim of completeness: **a bug in an
input region the fixed corpus never draws will pass, and keep passing, until
the OpenAPI schema itself changes.** The first run's 5 findings were what
that corpus happened to reach; the sibling issues in the same code-paths were
found afterward by manual pattern-sweep, not by the fuzzer re-running with a
different seed. `MAX_EXAMPLES` is a CI-budget floor, not a ceiling — raise it
toward the workflow's ~5-minute budget to widen the corpus; that is the real
coverage lever under fixed-corpus, deterministic generation.

## Excluded surface (Phase-1 blind spot)

`config.EXCLUDED_PATH_REGEX` drops two classes of routes from every run:

- Heavy Monte-Carlo/report/analysis routes (`/runs`, `/reports`, `/analyses`)
  — excluded for CI budget, not risk.
- The session/account-mutating surface (`/account`, `/users`, `/mfa`,
  `/settings`, `/auth`, plus `/…/delete|deactivate|cancel|purge-samples` and
  `/logout`, `/setup`) — excluded because a fuzzed hit there (e.g. a stray
  logout, MFA-policy flip, or account mutation) would not `5xx` (so the gate
  would stay green) but would silently kill the fuzzing session and collapse
  coverage for the rest of the run.

This excluded surface is a **named Phase-1 limitation, not a coverage
claim** — it is not fuzzed by this harness at all. Phase-2 scheduled Nuclei
scanning is the planned follow-up specifically for this surface (see
`docs/security/2026-08-14-ci-dast-design.md`, "Out of scope (Phase 2+,
tracked)").

## Authenticated-hook coverage boundary (CSRF token-validation surface)

Because the `before_call` hook injects a *valid* CSRF token into every
state-changing body, and the session + CSRF cookies are passed as static `-H`
headers, this authenticated pass **cannot exercise the CSRF token-validation
path itself**: Schemathesis never delivers a malformed or non-ASCII token to
the double-submit compare. Concretely, the D1 non-ASCII-token `500` (fixed at
both compare sites in `middleware/csrf.py`) is **structurally unreachable** by
this harness — it was originally caught by an *unauthenticated* scanner
(Wapiti), and reverting either D1 guard does **not** turn a `dast` run red
(verified). Treat token-validation / auth-mechanism bugs as **outside this
harness's coverage**; they need an unauthenticated or hook-disabled pass (a
Phase-2 Nuclei or dedicated-regression-test concern), not this gate. This is
why the design doc's gating self-test (§10) proves non-vacuousness by
reverting a *fuzzer-reachable* fix (an unbounded-`page` `500`), not a
CSRF-guard fix.

## Advisory-first in CI

The `dast` job in `.github/workflows/ci.yml` runs on every PR but is
**deliberately not** in `ci-success`'s `needs` — it does not block merges
yet. This is a burn-in period: promoting it into `ci-success` is a tracked
follow-up PR once the job has proven it doesn't flake.

## Module layout

- `config.py` — SSOT: excluded paths, example budget, seed identity.
- `extract_openapi.py` — offline `app.openapi()` -> filtered schema dict.
- `seed.py` — org + MFA-enrolled admin seed (runtime-generated password,
  never committed or logged).
- `hooks.py` — the `SCHEMATHESIS_HOOKS`-loaded `before_call` CSRF hook.
- `run.py` — the migrate -> seed -> serve -> login -> fuzz -> teardown
  orchestrator, used by both CI and `python -m idraa.tasks dast`.
