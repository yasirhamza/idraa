"""End-to-end form-authoring tests for scenario ATT&CK mappings (issue #475 T9)."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.attack import ScenarioAttackMapping
from idraa.models.scenario import Scenario
from tests.conftest import csrf_post

# Reuse the seeded_catalog fixture from Task 8's test module.
from tests.routes.test_attack_mapping_partial import seeded_catalog  # noqa: F401

# SC-I3: base payload mirrors tests/integration/test_scenario_form_with_controls.py's
# ``_FORM_BASE`` (a minimal valid ScenarioForm-shaped dict). ``name`` is fixed
# per-test since each test seeds its own org (via the ``authed_analyst``-backed
# ``analyst_client`` fixture), so there is no cross-test name collision.
_FORM_BASE: dict[str, Any] = {
    "name": "attack-mapping-form-test",
    "threat_category": "ransomware",
    "tef_low": "1",
    "tef_mode": "5",
    "tef_high": "12",
    "vuln_low": "0.2",
    "vuln_mode": "0.4",
    "vuln_high": "0.6",
    "pl_low": "100000",
    "pl_mode": "500000",
    "pl_high": "2000000",
}


def _scenario_form_payload(**extra) -> dict:
    """Minimal valid scenario-create payload.

    SC-I3: copy of the payload builder from
    tests/integration/test_scenario_form_with_controls.py's ``_FORM_BASE``.
    CSRF handling convention: this module's POSTs go through
    ``tests.conftest.csrf_post`` (GET-then-POST-with-token double-submit
    dance) rather than embedding a token in this dict — ``analyst_client``
    never issues an HTTP request during fixture setup (``authed_analyst``
    seeds the session cookie directly against the DB), so no CSRF cookie
    exists until the first GET. ``csrf_post`` handles that priming.
    """
    payload = dict(_FORM_BASE)
    payload.update(extra)
    return payload


@pytest.mark.asyncio
async def test_create_with_mappings_persists_all(
    analyst_client: AsyncClient,
    db_session: AsyncSession,
    seeded_catalog,  # noqa: F811
):
    t1, t2, t3 = seeded_catalog[0], seeded_catalog[1], seeded_catalog[2]
    payload = _scenario_form_payload()
    payload["attack_mappings[0][technique_id]"] = str(t1.id)
    payload["attack_mappings[1][technique_id]"] = str(t2.id)
    payload["attack_mappings[2][technique_id]"] = str(t3.id)
    resp = await csrf_post(analyst_client, "/scenarios", payload, follow_redirects=False)
    assert resp.status_code == 303
    scenario_id = uuid.UUID(resp.headers["location"].rstrip("/").split("/")[-1])
    rows = (
        (
            await db_session.execute(
                select(ScenarioAttackMapping).where(
                    ScenarioAttackMapping.scenario_id == scenario_id
                )
            )
        )
        .scalars()
        .all()
    )
    assert {r.technique_id for r in rows} == {t1.id, t2.id, t3.id}  # ALL N preserved
    assert all(r.source == "user" for r in rows)


@pytest.mark.asyncio
async def test_create_with_unknown_technique_renders_422_and_persists_nothing(
    analyst_client: AsyncClient,
    db_session: AsyncSession,
    seeded_catalog,  # noqa: F811
):
    payload = _scenario_form_payload()
    payload["attack_mappings[0][technique_id]"] = str(uuid.uuid4())
    resp = await csrf_post(analyst_client, "/scenarios", payload, follow_redirects=False)
    assert resp.status_code == 422
    assert "unknown ATT&amp;CK technique" in resp.text or "unknown ATT&CK technique" in resp.text
    # Sec2-I2: the rejection must run BEFORE ScenarioService.create — a 422
    # response with a committed scenario row is a half-applied write (the
    # session auto-commits on successful handler exit, incl. 422 renders).
    count = (
        (await db_session.execute(select(Scenario).where(Scenario.name == payload["name"])))
        .scalars()
        .all()
    )
    assert count == []


@pytest.mark.asyncio
async def test_update_diff_applies(
    analyst_client: AsyncClient,
    db_session: AsyncSession,
    seeded_catalog,  # noqa: F811
):
    """Create with {t1,t2}, edit-submit {t2,t3} → rows become {t2,t3}."""
    t1, t2, t3 = seeded_catalog[0], seeded_catalog[1], seeded_catalog[2]
    payload = _scenario_form_payload()
    payload["attack_mappings[0][technique_id]"] = str(t1.id)
    payload["attack_mappings[1][technique_id]"] = str(t2.id)
    resp = await csrf_post(analyst_client, "/scenarios", payload, follow_redirects=False)
    scenario_id = uuid.UUID(resp.headers["location"].rstrip("/").split("/")[-1])

    scenario = await db_session.get(Scenario, scenario_id)
    edit_payload = _scenario_form_payload()
    edit_payload["expected_row_version"] = str(scenario.row_version)
    edit_payload["attack_mappings[0][technique_id]"] = str(t2.id)
    edit_payload["attack_mappings[1][technique_id]"] = str(t3.id)
    resp = await csrf_post(
        analyst_client, f"/scenarios/{scenario_id}", edit_payload, follow_redirects=False
    )
    assert resp.status_code == 303
    db_session.expire_all()
    rows = (
        (
            await db_session.execute(
                select(ScenarioAttackMapping).where(
                    ScenarioAttackMapping.scenario_id == scenario_id
                )
            )
        )
        .scalars()
        .all()
    )
    assert {r.technique_id for r in rows} == {t2.id, t3.id}


@pytest.mark.asyncio
async def test_edit_form_renders_existing_rows(
    analyst_client: AsyncClient,
    db_session: AsyncSession,
    seeded_catalog,  # noqa: F811
):
    t1 = seeded_catalog[0]
    payload = _scenario_form_payload()
    payload["attack_mappings[0][technique_id]"] = str(t1.id)
    resp = await csrf_post(analyst_client, "/scenarios", payload, follow_redirects=False)
    scenario_id = resp.headers["location"].rstrip("/").split("/")[-1]
    resp = await analyst_client.get(f"/scenarios/{scenario_id}/edit")
    assert resp.status_code == 200
    assert "attack_mappings[0][technique_id]" in resp.text
    assert str(t1.id) in resp.text


@pytest.mark.asyncio
async def test_edit_resubmit_preserves_deprecated_survivor(
    analyst_client: AsyncClient,
    db_session: AsyncSession,
    seeded_catalog,  # noqa: F811
):
    """Arch-I2 end-to-end: a mapping to a (later-)deprecated technique survives
    an edit round-trip — the edit form renders it as a flagged option and
    resubmitting it does not delete the mapping."""
    dead = seeded_catalog[3]  # deprecated T9999 fixture technique
    payload = _scenario_form_payload()
    resp = await csrf_post(analyst_client, "/scenarios", payload, follow_redirects=False)
    scenario_id = uuid.UUID(resp.headers["location"].rstrip("/").split("/")[-1])
    # Seed the deprecated mapping directly (it can't be added via the form).
    db_session.add(
        ScenarioAttackMapping(
            organization_id=(await db_session.get(Scenario, scenario_id)).organization_id,
            scenario_id=scenario_id,
            technique_id=dead.id,
            source="user",
        )
    )
    await db_session.commit()  # SC3-I1: visible to the route's separate engine

    edit_page = await analyst_client.get(f"/scenarios/{scenario_id}/edit")
    assert "(deprecated)" in edit_page.text and str(dead.id) in edit_page.text

    scenario = await db_session.get(Scenario, scenario_id)
    edit_payload = _scenario_form_payload()
    edit_payload["expected_row_version"] = str(scenario.row_version)
    edit_payload["attack_mappings[0][technique_id]"] = str(dead.id)  # resubmit survivor
    resp = await csrf_post(
        analyst_client, f"/scenarios/{scenario_id}", edit_payload, follow_redirects=False
    )
    assert resp.status_code == 303
    db_session.expire_all()
    rows = (
        (
            await db_session.execute(
                select(ScenarioAttackMapping).where(
                    ScenarioAttackMapping.scenario_id == scenario_id
                )
            )
        )
        .scalars()
        .all()
    )
    assert [r.technique_id for r in rows] == [dead.id]


@pytest.mark.asyncio
async def test_update_with_unknown_technique_renders_422_and_persists_nothing(
    analyst_client: AsyncClient,
    db_session: AsyncSession,
    seeded_catalog,  # noqa: F811
):
    """Sec3-N1: the update-path twin of the create non-persistence test — a
    refactor that moves pre-validation below ScenarioService.update would
    otherwise commit the field diff + row_version bump + update-audit row
    while rendering 422, with no failing test."""
    payload = _scenario_form_payload()
    resp = await csrf_post(analyst_client, "/scenarios", payload, follow_redirects=False)
    scenario_id = uuid.UUID(resp.headers["location"].rstrip("/").split("/")[-1])
    scenario = await db_session.get(Scenario, scenario_id)
    original_name = scenario.name
    original_row_version = scenario.row_version

    edit_payload = _scenario_form_payload()
    edit_payload["name"] = "tampered-new-name"
    edit_payload["expected_row_version"] = str(original_row_version)
    edit_payload["attack_mappings[0][technique_id]"] = str(uuid.uuid4())  # unknown
    resp = await csrf_post(
        analyst_client, f"/scenarios/{scenario_id}", edit_payload, follow_redirects=False
    )
    assert resp.status_code == 422
    db_session.expire_all()
    scenario = await db_session.get(Scenario, scenario_id)
    assert scenario.name == original_name  # field diff NOT committed
    assert scenario.row_version == original_row_version  # no lock bump


@pytest.mark.asyncio
async def test_precurrency_422_preserves_submitted_mapping_rows(
    analyst_client: AsyncClient,
    seeded_catalog,  # noqa: F811
):
    """Arch3-I1: extraction runs BEFORE the pre-parse early returns — an
    ordinary entry-currency 422 must re-render the user's in-flight technique
    rows, or fix-and-resubmit silently wipes them."""
    t1 = seeded_catalog[0]
    payload = _scenario_form_payload()
    payload["entry_currency"] = "ZZZ"  # not a selectable currency → pre-parse 422
    payload["attack_mappings[0][technique_id]"] = str(t1.id)
    resp = await csrf_post(analyst_client, "/scenarios", payload, follow_redirects=False)
    assert resp.status_code == 422
    assert "attack_mappings[0][technique_id]" in resp.text
    assert str(t1.id) in resp.text  # the submitted row survives the re-render
