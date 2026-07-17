"""E2E stub for AGGREGATE run create-and-view flow (PR xi F11).

Placeholder for PR pi to implement with Playwright. Skipped (NOT deselected)
so the unimplemented contract surfaces in test counts.

Activation in PR pi: replace pytest.skip body with Playwright sequence:
  1. Login as analyst
  2. Navigate /analyses/new
  3. Select 2+ scenarios
  4. POST → wait for run completion (poll status)
  5. Assert /runs/{id} renders aggregate panel with 4 charts + table
  6. Assert constituent scenarios' /scenarios/{id}/runs each show this AGGREGATE run
"""

from __future__ import annotations

import pytest


def test_aggregate_run_create_and_view_e2e_stub() -> None:
    """E2E: analyst creates AGGREGATE run from /analyses/new, views detail page,
    constituent scenarios surface the run in their history.

    Activated in PR pi (Playwright integration). Skipped here so the surface
    contract is visible in test counts.
    """
    pytest.skip("PR pi: Playwright E2E for AGGREGATE create-and-view flow")
