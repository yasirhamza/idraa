"""Route tests for the attack-mapping row partial + form context (issue #475 T8)."""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.routes.scenario_form_helpers import load_attack_form_context
from tests.models.test_attack_models import _tactic, _technique


@pytest_asyncio.fixture
async def seeded_catalog(db_session: AsyncSession):
    tactics = [
        _tactic(),
        _tactic(
            domain="ics",
            tactic_id="TA0108",
            shortname="impair-process-control",
            name="Impair Process Control",
            display_order=0,
        ),
    ]
    techs = [
        _technique(),  # enterprise T1566 initial-access
        _technique(technique_id="T1486", name="Data Encrypted for Impact", tactics=["impact"]),
        _technique(
            domain="ics",
            technique_id="T0836",
            name="Modify Parameter",
            tactics=["impair-process-control"],
        ),
        _technique(technique_id="T9999", name="Old Thing", deprecated=True),
        # I-1: a SECOND deprecated technique that is NEVER a scenario survivor.
        # Without this, "survivor included" and "all deprecated leaked" are
        # indistinguishable — T9999 alone can't tell those two apart.
        _technique(technique_id="T9998", name="Other Old Thing", deprecated=True),
    ]
    # NOTE: T1486's "impact" tactic needs a tactic row too for grouping.
    tactics.append(_tactic(tactic_id="TA0040", shortname="impact", name="Impact", display_order=1))
    db_session.add_all(tactics + techs)
    # SC3-I1: COMMIT, not flush — this fixture feeds ROUTE tests, and the app
    # client runs on a separate engine that can't see uncommitted rows.
    await db_session.commit()
    return techs


@pytest.mark.asyncio
async def test_groups_ordering_and_deprecated_exclusion(db_session: AsyncSession, seeded_catalog):
    ctx = await load_attack_form_context(db_session)
    labels = [g["label"] for g in ctx.groups_json]
    # enterprise groups first (tactic display_order), then ics
    assert labels == [
        "Enterprise — Initial Access",
        "Enterprise — Impact",
        "ICS — Impair Process Control",
    ]
    all_option_labels = [o["label"] for g in ctx.groups_json for o in g["options"]]
    assert "T1566 — Phishing" in all_option_labels
    assert not any("T9999" in label for label in all_option_labels)  # deprecated excluded
    assert not any("T9998" in label for label in all_option_labels)  # deprecated excluded


@pytest.mark.asyncio
async def test_partial_route_renders_row(analyst_client: AsyncClient, seeded_catalog):
    resp = await analyst_client.get("/scenarios/_attack_mapping_row?index=3")
    assert resp.status_code == 200
    assert "attack_mappings[3][technique_id]" in resp.text
    assert 'data-row-index="3"' in resp.text
    # Arch-I5 budget: the partial carries the flat option list, never the
    # grouped catalog island (which would be ~150KB per add-row response).
    assert len(resp.content) < 100_000
    assert "attack-technique-groups" in resp.text  # island referenced by id...
    assert '"description"' not in resp.text  # ...but its payload not re-shipped


@pytest.mark.asyncio
async def test_partial_route_bounds_index(analyst_client: AsyncClient):
    # Bound = MAX_ATTACK_MAPPINGS (200), Arch2-I1 — 101-200 must be legal.
    resp = await analyst_client.get("/scenarios/_attack_mapping_row?index=201")
    assert resp.status_code == 422
    resp = await analyst_client.get("/scenarios/_attack_mapping_row?index=150")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_partial_with_scenario_id_includes_survivor(
    analyst_client: AsyncClient,
    db_session: AsyncSession,
    seeded_catalog,
    scenario_factory,  # Arch4-N2: the module's authed-org OVERRIDE (Task-1 contract) — same name in Tasks 8/11/14
):
    """Arch2-I2: '+ Add' on the EDIT form passes scenario_id so the new row's
    hidden select can hold the scenario's deprecated survivors."""
    from idraa.models.attack import ScenarioAttackMapping

    dead = seeded_catalog[3]  # T9999 — the scenario's survivor
    other_dead = seeded_catalog[4]  # T9998 — deprecated but NEVER mapped here
    scenario = await scenario_factory()
    db_session.add(
        ScenarioAttackMapping(
            organization_id=scenario.organization_id,
            scenario_id=scenario.id,
            technique_id=dead.id,
            source="user",
        )
    )
    await db_session.commit()  # SC3-I1: route call below — must be visible
    resp = await analyst_client.get(
        f"/scenarios/_attack_mapping_row?index=5&scenario_id={scenario.id}"
    )
    assert resp.status_code == 200
    assert str(dead.id) in resp.text and "(deprecated)" in resp.text
    # I-1: the or_ filter must admit ONLY the scenario's actual survivor, not
    # every deprecated row. T9998 is deprecated but unmapped — it must never
    # leak into the response even though the survivor path is active.
    assert str(other_dead.id) not in resp.text
    assert "T9998" not in resp.text
    # Without scenario_id the survivor is absent.
    resp = await analyst_client.get("/scenarios/_attack_mapping_row?index=5")
    assert str(dead.id) not in resp.text
    assert str(other_dead.id) not in resp.text


@pytest.mark.asyncio
async def test_deprecated_survivor_included_flagged(
    db_session: AsyncSession, seeded_catalog, scenario_factory
):
    """Arch-I2: a scenario's existing mapping to a deprecated technique must
    render as a flagged option — else the hidden select has no matching
    <option>, the row submits blank, and an unrelated edit deletes the mapping."""
    from idraa.models.attack import ScenarioAttackMapping

    dead = seeded_catalog[3]  # the deprecated T9999 fixture technique
    scenario = await scenario_factory()
    db_session.add(
        ScenarioAttackMapping(
            organization_id=scenario.organization_id,
            scenario_id=scenario.id,
            technique_id=dead.id,
            source="user",
        )
    )
    await db_session.flush()
    # Reload so scenario.attack_mappings is populated.
    from sqlalchemy import select as sa_select

    from idraa.models.scenario import Scenario

    scenario = (
        await db_session.execute(sa_select(Scenario).where(Scenario.id == scenario.id))
    ).scalar_one()

    ctx = await load_attack_form_context(db_session, scenario=scenario)
    option_labels = [o["label"] for o in ctx.options]
    assert any("T9999" in label and "(deprecated)" in label for label in option_labels)
    # I-1: T9998 (deprecated, NOT mapped to this scenario) must stay excluded
    # even though T9999 (deprecated, mapped) is included — proves the or_
    # filter targets the survivor id specifically, not "any deprecated row".
    assert not any("T9998" in label for label in option_labels)

    # I-2: the groups-island (grouped picker rendered once per page) must
    # ALSO carry the flagged survivor option — options-list-only coverage
    # would miss a bug that wires the flat list but leaves groups_json
    # filtering deprecated rows unconditionally.
    group_option_labels = [o["label"] for g in ctx.groups_json for o in g["options"]]
    assert any("T9999" in label and "(deprecated)" in label for label in group_option_labels)
    assert not any("T9998" in label for label in group_option_labels)

    # Without a scenario, the deprecated technique stays excluded — from
    # both the flat option list AND every group.
    ctx_blank = await load_attack_form_context(db_session)
    assert not any("T9999" in o["label"] for o in ctx_blank.options)
    blank_group_option_labels = [o["label"] for g in ctx_blank.groups_json for o in g["options"]]
    assert not any("T9999" in label for label in blank_group_option_labels)


@pytest.mark.asyncio
async def test_partial_row_404s_on_unknown_scenario_id(analyst_client: AsyncClient):
    """I-3: a scenario_id that doesn't exist at all must 404, not be
    silently ignored (which would render the row as if no scenario_id had
    been passed at all — a confusing degrade for a caller with a typo'd id)."""
    resp = await analyst_client.get(
        f"/scenarios/_attack_mapping_row?index=0&scenario_id={uuid.uuid4()}"
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_partial_row_404s_on_other_org_scenario_id(
    analyst_client: AsyncClient,
    seed_scenario_factory,
):
    """I-3: org-scoped 404 — a scenario that exists but belongs to a
    DIFFERENT org than the authenticated analyst must 404, not leak
    existence (mirrors scenario_export_one's B9/B10 precedent: 404, not
    403, so a cross-org id doesn't tell the caller the row exists).

    Uses the PARENT conftest's ``seed_scenario_factory`` (defaults to
    ``seed_organization`` / ``seed_user``) rather than this module's
    ``scenario_factory`` OVERRIDE — the override is pinned to the authed
    analyst's own org (Arch4-N2), so it can never produce an "other org"
    row. ``seed_scenario_factory`` commits internally (SC3-I1: the route
    call below runs on the app's separate engine and needs the row visible)."""
    other_org_scenario = await seed_scenario_factory(name="other-org-scenario")
    resp = await analyst_client.get(
        f"/scenarios/_attack_mapping_row?index=0&scenario_id={other_org_scenario.id}"
    )
    assert resp.status_code == 404
