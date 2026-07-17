"""IDOR guard tests for run routes: cross-org access must return 404.

Phase 1.4 IDOR invariant: a run owned by org A must be indistinguishable
from a missing resource for a user authenticated as org B. All three
endpoints that take a run_id are covered.

Fixture topology:
- ``seed_completed_run`` (conftest) — a COMPLETED run in ``seed_organization``.
- ``authed_other_org_analyst`` (conftest) — analyst logged into a DIFFERENT org
  from ``seed_organization``; provided specifically for cross-org IDOR tests.
- ``seed_run_factory`` (conftest) — factory for runs in ``seed_organization``.

Template-gating: GET /runs/{id} is skip-gated on F10's detail template;
the status fragment and cancel POST do NOT require a template to exercise
the IDOR boundary (they return 404 before reaching any template render).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient

from idraa.models.risk_analysis_run import RiskAnalysisRun
from tests.conftest import csrf_post

_DETAIL_TEMPLATE = Path("src/idraa/templates/runs/detail.html")


# ---- GET /runs/{id} — cross-org IDOR ---------------------------------


@pytest.mark.skipif(not _DETAIL_TEMPLATE.exists(), reason="F10 template not yet created")
@pytest.mark.asyncio
async def test_get_run_cross_org_404(
    authed_other_org_analyst: tuple[AsyncClient, Any],
    seed_completed_run: RiskAnalysisRun,
) -> None:
    """Run owned by org A returns 404 for a user in org B (not 403 — no existence leak)."""
    client, _ = authed_other_org_analyst
    response = await client.get(f"/runs/{seed_completed_run.id}")
    assert response.status_code == 404
    # Body must not contain any run-specific identifiers.
    assert str(seed_completed_run.id) not in response.text


@pytest.mark.asyncio
async def test_get_status_fragment_cross_org_404(
    authed_other_org_analyst: tuple[AsyncClient, Any],
    seed_completed_run: RiskAnalysisRun,
) -> None:
    """Status fragment for a cross-org run returns 404 (no template needed)."""
    client, _ = authed_other_org_analyst
    response = await client.get(f"/runs/{seed_completed_run.id}/status")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_post_cancel_cross_org_404(
    authed_other_org_analyst: tuple[AsyncClient, Any],
    seed_completed_run: RiskAnalysisRun,
) -> None:
    """Cancel POST for a cross-org run returns 404 (no template needed)."""
    client, _ = authed_other_org_analyst
    response = await csrf_post(
        client,
        f"/runs/{seed_completed_run.id}/cancel",
        {},
        follow_redirects=False,
    )
    # 404 because the run doesn't exist in the user's org.
    # (403 would also be acceptable if require_role fires first, but in
    # this case authed_other_org_analyst is an analyst, so require_role
    # passes and the IDOR guard must catch it.)
    assert response.status_code == 404


# ---- POST /runs/{id}/purge-samples + GET .../control-matrix.csv — IDOR --
# Task 8 gap-close (plan-gate Sec-N3): both routes ARE already org-scoped
# server-side (RunService.get_for_org / purge_samples(org_id=...) ->
# RunNotFoundError -> 404), this just adds the missing regression coverage.


@pytest.mark.asyncio
async def test_post_purge_samples_cross_org_404(
    authed_other_org_analyst: tuple[AsyncClient, Any],
    seed_completed_run: RiskAnalysisRun,
) -> None:
    """Purge-samples POST for a cross-org run returns 404 (not 400/403).

    ``confirm=1`` is included so the mandatory-confirm check (400 if
    absent/falsey) doesn't short-circuit before the org-scoped lookup —
    this test is specifically about the IDOR boundary, not the confirm gate.
    """
    client, _ = authed_other_org_analyst
    response = await csrf_post(
        client,
        f"/runs/{seed_completed_run.id}/purge-samples",
        {"confirm": "1"},
        follow_redirects=False,
    )
    assert response.status_code == 404
    assert str(seed_completed_run.id) not in response.text


@pytest.mark.asyncio
async def test_get_control_matrix_csv_cross_org_404(
    authed_other_org_analyst: tuple[AsyncClient, Any],
    seed_completed_run: RiskAnalysisRun,
) -> None:
    """control-matrix.csv GET for a cross-org run returns 404 (not the
    AGGREGATE-only 400 — the org-scoped lookup runs first)."""
    client, _ = authed_other_org_analyst
    response = await client.get(f"/runs/{seed_completed_run.id}/control-matrix.csv")
    assert response.status_code == 404
    assert str(seed_completed_run.id) not in response.text
