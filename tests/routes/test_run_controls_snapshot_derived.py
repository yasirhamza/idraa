"""#268 Task 5 -- the run page's "Controls applied at run time" table (derived rows).

A V3 snapshot's derived ``dsc_corr_misaligned`` row carries the inert sentinels
(capability NULL, coverage 0.0, reliability 0.0) plus ``derived_from_sub_function``, the
SOURCE row's slug. The component must show the source's capability / coverage /
reliability (in the source's unit), never 0.000; a derived row with no resolvable source
shows "derived (source unavailable)" and no numbers.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from bs4 import BeautifulSoup
from bs4.element import Tag
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.risk_analysis_run import RiskAnalysisRun, RunStatus
from idraa.models.user import User
from tests.integration.test_run_detail_v2_banner_and_log import (
    _minimal_completed_simulation_results,
    _seed_analyst_org_scenario,
)

PREV = "lec_prev_resistance"
ET = "lec_resp_event_termination"
DSC = "dsc_corr_misaligned"


def _asn(
    sf: str, unit: str, cap: float | None, cov: float, rel: float, **extra: Any
) -> dict[str, Any]:
    return {
        "sub_function": sf,
        "capability_value": cap,
        "coverage": cov,
        "reliability": rel,
        "unit_type": unit,
        "confirmed_by_user_at": None,
        "derived_from_assignment_id": None,
        "measured_at": None,
        "measured_by": None,
        "derived_from_sub_function": None,
        **extra,
    }


def _control(name: str, assignments: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "snapshot_version": 3,
        "control_id": str(uuid.uuid4()),
        "name": name,
        "domains": ["loss_event"],
        "type": "technical",
        "assignments": assignments,
    }


def _linked_control() -> dict[str, Any]:
    return _control(
        "Linked response control",
        [
            _asn(PREV, "probability", 0.75, 0.9, 0.85),
            _asn(ET, "elapsed_time", 30.0, 0.83, 0.61),
            _asn(
                DSC,
                "probability",
                None,
                0.0,
                0.0,
                derived_from_assignment_id=str(uuid.uuid4()),
                derived_from_sub_function=ET,
            ),
        ],
    )


def _orphan_control() -> dict[str, Any]:
    return _control(
        "Orphaned derived control",
        [
            _asn(PREV, "probability", 0.75, 0.9, 0.85),
            _asn(DSC, "probability", None, 0.0, 0.0, derived_from_assignment_id=str(uuid.uuid4())),
        ],
    )


async def _run_page(
    client: AsyncClient,
    db_session: AsyncSession,
    org_id: uuid.UUID,
    seed_user: User,
    seed_run_factory: Any,
    snapshot: list[dict[str, Any]],
) -> BeautifulSoup:
    scenario = await _seed_analyst_org_scenario(db_session, org_id, seed_user.id)
    run: RiskAnalysisRun = await seed_run_factory(
        scenario=scenario,
        status=RunStatus.COMPLETED,
        organization_id=org_id,
        simulation_results=_minimal_completed_simulation_results(),
        controls_snapshot=snapshot,
        completed_at=datetime.now(UTC),
    )
    response = await client.get(f"/runs/{run.id}")
    assert response.status_code == 200
    return BeautifulSoup(response.text, "html.parser")


def _inner_rows(section: Tag, needle: str) -> list[Tag]:
    """Innermost ``<tr>`` rows containing ``needle`` (a control row nests its assignment table)."""
    return [
        tr for tr in section.find_all("tr") if tr.find("tr") is None and needle in tr.get_text()
    ]


def _row_cells(soup: BeautifulSoup, needle: str) -> list[str]:
    section = soup.find(id="controls-snapshot")
    assert isinstance(section, Tag)
    rows = _inner_rows(section, needle)
    assert len(rows) == 1, f"expected exactly one row containing {needle!r}, got {len(rows)}"
    return [" ".join(td.get_text().split()) for td in rows[0].find_all("td")]


@pytest.mark.asyncio
async def test_a_derived_row_shows_the_sources_values_in_the_sources_unit(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
    seed_user: User,
    seed_run_factory: Any,
) -> None:
    client, org_id = authed_analyst
    soup = await _run_page(
        client, db_session, org_id, seed_user, seed_run_factory, [_linked_control()]
    )
    cells = _row_cells(soup, "derived from")
    assert cells[0] == "Correcting Misaligned Decisions — derived from LEC Resp Event Termination"
    # capability in the SOURCE's unit (30 days), then the source's coverage / reliability
    assert cells[1:4] == ["30.0 days", "0.830", "0.610"]
    section = soup.find(id="controls-snapshot")
    assert isinstance(section, Tag)
    (derived_row,) = _inner_rows(section, "derived from")
    assert "0.000" not in derived_row.get_text()
    # the stored raw slug stays auditable in the title tooltip
    assert derived_row.find("td", attrs={"title": DSC}) is not None


@pytest.mark.asyncio
async def test_an_unresolvable_derived_row_shows_source_unavailable_and_no_numbers(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
    seed_user: User,
    seed_run_factory: Any,
) -> None:
    client, org_id = authed_analyst
    soup = await _run_page(
        client, db_session, org_id, seed_user, seed_run_factory, [_orphan_control()]
    )
    cells = _row_cells(soup, "derived (source unavailable)")
    assert cells[0] == "Correcting Misaligned Decisions — derived (source unavailable)"
    assert cells[1:4] == ["—", "—", "—"]
    assert "0.000" not in " ".join(cells)
