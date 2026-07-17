"""Route/template tests for /scenarios/attack-coverage (issue #475 T14)."""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.attack import ScenarioAttackMapping
from tests.models.test_attack_models import _tactic, _technique


@pytest.mark.asyncio
async def test_page_renders_domains_and_counts(
    analyst_client: AsyncClient, db_session: AsyncSession, scenario_factory
):
    db_session.add_all(
        [
            _tactic(),
            _tactic(
                domain="ics",
                tactic_id="TA0108",
                shortname="impair-process-control",
                name="Impair Process Control",
                display_order=0,
            ),
            _technique(),
            _technique(
                domain="ics",
                technique_id="T0836",
                name="Modify Parameter",
                tactics=["impair-process-control"],
            ),
        ]
    )
    scenario = await scenario_factory()
    await db_session.commit()  # SC3-I1: route runs on a separate engine

    resp = await analyst_client.get("/scenarios/attack-coverage")
    assert resp.status_code == 200
    assert "Enterprise" in resp.text and "ICS" in resp.text
    assert "0 of 1" in resp.text  # both domains unmodeled
    # Meth-I2: modeled/not-modeled wording, never covered/gap.
    assert "techniques modeled" in resp.text
    assert "not modeled" in resp.text
    assert ">gap<" not in resp.text
    # Meth-I2 (tightened): "covered" must not appear anywhere in visible page
    # text either — not just the ">gap<" tag-boundary check above.
    assert "covered" not in resp.text.lower()

    # Map one technique and re-render.
    from sqlalchemy import select

    from idraa.models.attack import AttackTechnique

    t1566 = (
        await db_session.execute(
            select(AttackTechnique).where(AttackTechnique.technique_id == "T1566")
        )
    ).scalar_one()
    db_session.add(
        ScenarioAttackMapping(
            organization_id=scenario.organization_id,
            scenario_id=scenario.id,
            technique_id=t1566.id,
            source="user",
        )
    )
    await db_session.commit()  # SC3-I1
    resp = await analyst_client.get("/scenarios/attack-coverage")
    assert "1 of 1" in resp.text
    assert scenario.name in resp.text  # modeling scenario listed


@pytest.mark.asyncio
async def test_empty_catalog_renders_empty_state(analyst_client: AsyncClient):
    resp = await analyst_client.get("/scenarios/attack-coverage")
    assert resp.status_code == 200
    assert "catalog" in resp.text.lower()  # honest empty state, no fabricated figures


@pytest.mark.asyncio
async def test_unmapped_pinned_banner_points_to_atlas_not_stale_pr2(
    analyst_client: AsyncClient, db_session: AsyncSession, scenario_factory
):
    """An ACTIVE library-pinned scenario with no technique mappings triggers the
    banner. Post-#494 the ONLY such case is a scenario pinned to the entry
    awaiting the ATLAS domain (#482), so the banner must cite that — never the
    shipped '#475 PR 2 / library curation is partial' copy."""
    import uuid

    db_session.add_all([_tactic(), _technique()])
    await scenario_factory(library_pin={"entry_id": str(uuid.uuid4()), "version": 1})
    await db_session.commit()

    resp = await analyst_client.get("/scenarios/attack-coverage")
    assert resp.status_code == 200
    assert "1 library-based" in resp.text
    assert "MITRE ATLAS domain (issue #482)" in resp.text
    # Stale copy retired by this fix.
    assert "PR 2" not in resp.text
    assert "curation is partial" not in resp.text


@pytest.mark.asyncio
async def test_viewer_can_read(viewer_client: AsyncClient):
    """Coverage is a read — any authenticated role (require_user, the export B3 precedent)."""
    resp = await viewer_client.get("/scenarios/attack-coverage")
    assert resp.status_code == 200
