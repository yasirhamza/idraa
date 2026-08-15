"""CI DAST Phase 1 — Schemathesis-based per-PR API-fuzzing harness.

This package is a **dev-only** tool, never imported by ``src/idraa/`` at
runtime. It is invoked exclusively via ``python -m idraa.tasks dast`` (both
locally and from the advisory ``dast`` CI job) and orchestrates: extracting
``app.openapi()`` offline, migrating + seeding a throwaway SQLite DB, spinning
up an ephemeral ``uvicorn`` instance bound to ``127.0.0.1``, logging in as a
seeded admin, and running ``schemathesis run`` against the authenticated
surface with a ``before_call`` hook that injects a valid CSRF token into
state-changing form bodies.

**This suite NEVER targets a deployed environment** — see
``docs/security/2026-08-14-ci-dast-design.md`` §0 for the safety invariants
(ephemeral target only, asserted in code before any seed/serve step).

Module layout (populated across the CI-DAST-Phase-1 task series):

- ``config.py`` — SSOT for excluded paths, example budget, seed identity.
- ``extract_openapi.py`` — offline ``app.openapi()`` -> filtered schema dict.
- ``seed.py`` — org + MFA-enrolled admin seed (runtime-generated password).
- ``hooks.py`` — the ``SCHEMATHESIS_HOOKS``-loaded ``before_call`` CSRF hook.
- ``run.py`` — the migrate -> seed -> serve -> login -> fuzz -> teardown
  orchestrator, used by both CI and ``python -m idraa.tasks dast``.

This task (schemathesis dep pin + end-to-end orchestration spike) added only
this file; the modules above land in the harness task that follows. The spike
itself (extract -> migrate -> seed -> serve -> login -> ``schemathesis run``
-> assert exit 0) was run by hand against a throwaway temp SQLite DB and is
NOT committed — see the CI DAST Phase-1 task-1 report for the transcript and
the exact CLI flags / hook signature / HAR field paths it confirmed.
"""

from __future__ import annotations
