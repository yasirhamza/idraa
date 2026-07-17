# UAT — ad-hoc browser-level smoke tests

## What this is

A bare-bones Playwright + Chromium harness for walking the Idraa v3
MVP exit criterion against a live local server. Built ad-hoc on
2026-05-18 to do a UAT pass; immediately caught issue #150 (P0:
`hx-params="none"` wiping `hx-vals` → unit-aware widget swap silently
broken) within ~10 minutes of running.

## What this isn't

NOT the canonical Phase 1.5b E2E infrastructure. That's still on the
post-UAT backlog and would include:

- pytest-integrated fixtures (vs. these standalone scripts)
- Ephemeral per-run SQLite per `tests/e2e/conftest.py` skip notes
- Reusable HTMX-aware Locator wrappers
- CI integration
- Probably an actual page-object pattern

This harness is the SEED — promote pieces into proper E2E
infrastructure when that work starts.

## Why HTMX needed special handling

`<body hx-boost="true">` intercepts form submits and turns them into
AJAX. Playwright's `click()` + `wait_for_load_state("networkidle")`
race against HTMX's request lifecycle. Two patterns matter:

1. **For form submits**: use `page.expect_response(POST_path)` wrapping
   the click. Without it, Playwright proceeds before the AJAX submit
   processes (see `hx_submit_form_and_wait_url` in `test_mvp_smoke.py`).

2. **For HTMX swaps on `<select>` change**: capture `htmx:configRequest`
   events to see what HTMX evaluated the request parameters as — this
   is how #150 was diagnosed (HTMX got the right `event.target.value`
   but `parameters: {}` because `hx-params="none"` wiped hx-vals).

## How to run

```bash
# From repo root:
./uat/run_uat.sh
```

The script:
1. Creates a fresh ephemeral SQLite DB at `/tmp/idraa-uat-<timestamp>.db`
2. Generates a random `SESSION_SECRET`
3. Runs `alembic upgrade head` to create schema
4. Starts uvicorn on port 8000
5. Runs the Playwright script
6. Captures screenshots to `/tmp/uat-screenshots/`
7. Tears down the server

Pass/fail per check is printed; failures get a screenshot path.

## Findings format

The script collects findings with severity (BLOCKER / FAIL / WARN /
INFO) and prints them as a final summary block. File GH issues for
genuine product bugs using the steps-to-reproduce and screenshot.

## Prerequisites

```bash
.venv/bin/playwright install chromium  # one-time
```
