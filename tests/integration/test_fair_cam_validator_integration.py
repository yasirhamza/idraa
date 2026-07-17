"""FAIRCAMValidator boundary integration test. Closes GH #2 at the route level.

Only the expert-form test is included here (F12 scope).

Wizard step 3 test (test_wizard_step3_post_with_invalid_distribution_returns_422)
is DEFERRED to F18: the wizard routes (/scenarios/new/wizard/step/N) do not exist
yet. F18 will ship the wizard routes and the corresponding integration test.
See plan §F18 for the deferred test body.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from tests.conftest import csrf_post


@pytest.mark.asyncio
async def test_expert_form_post_with_invalid_distribution_returns_422(
    analyst_client: AsyncClient,
) -> None:
    """POST /scenarios with low > mode -> FAIRCAMValidationError -> 422 re-render.

    The existing ``except ValidationError`` handler at routes/scenarios.py:240
    catches ``FAIRCAMValidationError`` via inheritance (FAIRCAMValidationError
    is a subclass of ValidationError). No route change was required — inheritance
    fallthrough is sufficient for the 422 contract.

    Fields match ``parse_scenario_form`` expectations:
    - tef_low/tef_mode/tef_high -> threat_event_frequency PERT
    - vuln_low/vuln_mode/vuln_high -> vulnerability PERT
    - pl_low/pl_mode/pl_high -> primary_loss PERT

    industry/revenue_tier are no longer ScenarioForm fields (issue #88 Task 9);
    they are derived from the org at service layer.

    Invalid data: tef_low=10.0 > tef_mode=4.0 -> fair_cam ERROR -> 422.
    """
    r = await csrf_post(
        analyst_client,
        "/scenarios",
        data={
            "name": "bad-tef-scenario",
            "threat_category": "malware",
            "tef_low": "10.0",
            "tef_mode": "4.0",
            "tef_high": "12.0",
            "vuln_low": "0.05",
            "vuln_mode": "0.20",
            "vuln_high": "0.50",
            "pl_low": "1.0",
            "pl_mode": "2.0",
            "pl_high": "3.0",
        },
    )
    assert r.status_code == 422
    assert "low" in r.text.lower()
    assert "text/html" in r.headers["content-type"]
