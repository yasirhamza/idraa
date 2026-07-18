"""tests/integration/test_draft_workflow.py — epic #34 P1a.

DRAFT scenarios are review-pending priors: visible and editable, but
excluded from run creation (server-side gate — the form filter is
convenience), dashboard metrics, and library coverage until promoted.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.audit_log import AuditLog
from idraa.models.enums import EntityStatus
from idraa.models.scenario import Scenario
from tests.conftest import csrf_post

# _seed_scenario lives in tests/integration/test_scenario_routes.py; move it
# to tests/factories.py if importing across test modules is awkward — it is
# already parameterized by status.
from tests.integration.test_scenario_routes import _seed_scenario


@pytest.mark.asyncio
async def test_run_create_rejects_draft_scenario(authed_analyst, db_session: AsyncSession):
    client, org_id = authed_analyst
    draft = _seed_scenario(db_session, org_id=org_id, name="Draft S", status=EntityStatus.DRAFT)
    await db_session.commit()
    r = await csrf_post(
        client,
        "/analyses",
        {"scenario_ids": [str(draft.id)], "mc_iterations": "1000"},
        follow_redirects=False,
    )
    assert r.status_code == 422
    assert "draft" in r.text.lower()


@pytest.mark.asyncio
async def test_run_create_rejects_mixed_active_and_draft(authed_analyst, db_session: AsyncSession):
    client, org_id = authed_analyst
    active = _seed_scenario(db_session, org_id=org_id, name="Active S", status=EntityStatus.ACTIVE)
    draft = _seed_scenario(db_session, org_id=org_id, name="Draft T", status=EntityStatus.DRAFT)
    await db_session.commit()
    r = await csrf_post(
        client,
        "/analyses",
        {"scenario_ids": [str(active.id), str(draft.id)], "mc_iterations": "1000"},
        follow_redirects=False,
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_new_analysis_picker_omits_drafts(authed_analyst, db_session: AsyncSession):
    client, org_id = authed_analyst
    _seed_scenario(db_session, org_id=org_id, name="Visible Active", status=EntityStatus.ACTIVE)
    _seed_scenario(db_session, org_id=org_id, name="Hidden Draft", status=EntityStatus.DRAFT)
    await db_session.commit()
    r = await client.get("/analyses/new")
    assert "Visible Active" in r.text and "Hidden Draft" not in r.text


@pytest.mark.asyncio
async def test_dashboard_counts_exclude_drafts(authed_analyst, db_session: AsyncSession):
    client, org_id = authed_analyst
    _seed_scenario(db_session, org_id=org_id, name="Counted", status=EntityStatus.ACTIVE)
    _seed_scenario(db_session, org_id=org_id, name="Not Counted", status=EntityStatus.DRAFT)
    await db_session.commit()
    from idraa.repositories.scenario_repo import ScenarioRepo

    repo = ScenarioRepo(db_session)
    # dashboard calls count_for_org(status=ACTIVE) after this task
    assert await repo.count_for_org(organization_id=org_id, status=EntityStatus.ACTIVE) == 1
    pinned = await repo.list_pinned_library_entry_ids_for_org(org_id)
    # neither seed pins a library entry; assertion is that the signature accepts the default
    assert pinned == []


# ---- Task 3: promote flow + UI -----------------------------------------


def _valid_update_payload_for(s: Scenario) -> dict[str, str]:
    """Build a valid, non-status-changing update payload from a seeded scenario.

    Mirrors the payload shape used by
    ``test_update_persists_descriptive_change`` in test_scenario_routes.py,
    parameterized by scenario. Includes ``status`` explicitly (mirroring the
    edit form's hidden mirror at form.html:69) so the payload is legitimate
    for scenarios of ANY status, including DRAFT — callers that want to
    exercise the status-change guard override ``status`` after merging.
    """
    return {
        "name": s.name,
        "threat_category": s.threat_category.value,
        "tef_low": str(s.threat_event_frequency["low"]),
        "tef_mode": str(s.threat_event_frequency["mode"]),
        "tef_high": str(s.threat_event_frequency["high"]),
        "vuln_low": str(s.vulnerability["low"]),
        "vuln_mode": str(s.vulnerability["mode"]),
        "vuln_high": str(s.vulnerability["high"]),
        "pl_low": str(s.primary_loss["low"]),
        "pl_mode": str(s.primary_loss["mode"]),
        "pl_high": str(s.primary_loss["high"]),
        "expected_row_version": str(s.row_version),
        "status": s.status.value,
    }


@pytest.mark.asyncio
async def test_promote_flips_draft_to_active_with_audit(authed_analyst, db_session: AsyncSession):
    client, org_id = authed_analyst
    draft = _seed_scenario(db_session, org_id=org_id, name="Promotable", status=EntityStatus.DRAFT)
    await db_session.commit()
    r = await csrf_post(client, f"/scenarios/{draft.id}/promote", {}, follow_redirects=False)
    assert r.status_code == 303
    await db_session.refresh(draft)
    assert draft.status == EntityStatus.ACTIVE
    row = (
        (await db_session.execute(select(AuditLog).where(AuditLog.action == "scenario.promote")))
        .scalars()
        .first()
    )
    assert row is not None and row.entity_id == draft.id


@pytest.mark.asyncio
async def test_promote_idempotent_on_active(authed_analyst, db_session: AsyncSession):
    client, org_id = authed_analyst
    s = _seed_scenario(db_session, org_id=org_id, name="Already Active", status=EntityStatus.ACTIVE)
    await db_session.commit()
    prev = s.row_version
    r = await csrf_post(client, f"/scenarios/{s.id}/promote", {}, follow_redirects=False)
    assert r.status_code == 303
    await db_session.refresh(s)
    assert s.row_version == prev  # no bump, no audit on no-op


@pytest.mark.asyncio
async def test_promote_refuses_unconfirmed_legacy_residual(
    authed_analyst, db_session: AsyncSession
):
    client, org_id = authed_analyst
    d = _seed_scenario(db_session, org_id=org_id, name="Residual Draft", status=EntityStatus.DRAFT)
    d.vuln_framing = "legacy_residual"
    await db_session.commit()
    r = await csrf_post(client, f"/scenarios/{d.id}/promote", {}, follow_redirects=False)
    assert r.status_code == 422
    # Epic #34 P1c Task 8: non-converted scenarios keep the original,
    # vuln-centric refusal string.
    assert (
        "confirm vulnerability framing before promoting — see the banner on this scenario" in r.text
    )
    await db_session.refresh(d)
    assert d.status == EntityStatus.DRAFT


@pytest.mark.asyncio
async def test_promote_refuses_converted_row_with_frequency_baseline_message(
    authed_analyst, db_session: AsyncSession
):
    """Epic #34 P1c Task 8: a converted (QUALITATIVE_REGISTER_IMPORT) row's
    promote-refusal string is frequency-baseline-worded, not
    vulnerability-worded — the F2 confirm gate on a converted row is an
    acceptance of the register-derived frequency, not a vulnerability
    review (spec §3 Meth-I1)."""
    from idraa.models.enums import ScenarioSource

    client, org_id = authed_analyst
    d = _seed_scenario(
        db_session, org_id=org_id, name="Converted Residual Draft", status=EntityStatus.DRAFT
    )
    d.vuln_framing = "legacy_residual"
    d.source = ScenarioSource.QUALITATIVE_REGISTER_IMPORT
    await db_session.commit()
    r = await csrf_post(client, f"/scenarios/{d.id}/promote", {}, follow_redirects=False)
    assert r.status_code == 422
    assert (
        "confirm the frequency baseline before promoting — see the banner on this scenario"
        in r.text
    )
    assert "confirm vulnerability framing" not in r.text
    await db_session.refresh(d)
    assert d.status == EntityStatus.DRAFT


@pytest.mark.asyncio
async def test_promote_forbidden_for_reviewer(authed_reviewer, db_session: AsyncSession):
    client, org_id = authed_reviewer
    d = _seed_scenario(db_session, org_id=org_id, name="RBAC Draft", status=EntityStatus.DRAFT)
    await db_session.commit()
    r = await csrf_post(client, f"/scenarios/{d.id}/promote", {}, follow_redirects=False)
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_edit_form_cannot_change_status(authed_analyst, db_session: AsyncSession):
    client, org_id = authed_analyst
    d = _seed_scenario(db_session, org_id=org_id, name="Sticky Draft", status=EntityStatus.DRAFT)
    await db_session.commit()
    # SEC-R2-1: rejection must leave the session CLEAN — no silent unaudited
    # commit of the co-mutated description.
    payload = _valid_update_payload_for(d) | {"status": "active", "description": "sneaky edit"}
    r = await csrf_post(client, f"/scenarios/{d.id}", payload, follow_redirects=False)
    assert r.status_code == 422
    await db_session.refresh(d)
    assert d.status == EntityStatus.DRAFT
    assert d.description != "sneaky edit"  # nothing committed
    upd = (
        (
            await db_session.execute(
                select(AuditLog).where(
                    AuditLog.action == "scenario.update", AuditLog.entity_id == d.id
                )
            )
        )
        .scalars()
        .all()
    )
    assert upd == []  # no audit row for the rejected edit


@pytest.mark.asyncio
async def test_create_as_draft_works(authed_analyst, db_session: AsyncSession):
    client, org_id = authed_analyst
    payload = {
        "name": "Draft-created scenario",
        "threat_category": "social_engineering",
        "tef_low": "0.1",
        "tef_mode": "0.5",
        "tef_high": "2.0",
        "vuln_low": "0.2",
        "vuln_mode": "0.4",
        "vuln_high": "0.6",
        "pl_low": "50000",
        "pl_mode": "250000",
        "pl_high": "2000000",
        "status": "draft",
    }
    r = await csrf_post(client, "/scenarios", payload, follow_redirects=False)
    assert r.status_code == 303
    s = (
        await db_session.execute(
            select(Scenario).where(
                Scenario.organization_id == org_id,
                Scenario.name == "Draft-created scenario",
            )
        )
    ).scalar_one()
    assert s.status == EntityStatus.DRAFT


@pytest.mark.asyncio
async def test_create_rejects_non_lifecycle_status(authed_analyst, db_session: AsyncSession):
    client, org_id = authed_analyst
    payload = {
        "name": "Bad-status scenario",
        "threat_category": "social_engineering",
        "tef_low": "0.1",
        "tef_mode": "0.5",
        "tef_high": "2.0",
        "vuln_low": "0.2",
        "vuln_mode": "0.4",
        "vuln_high": "0.6",
        "pl_low": "50000",
        "pl_mode": "250000",
        "pl_high": "2000000",
        "status": "deprecated",
    }
    r = await csrf_post(client, "/scenarios", payload, follow_redirects=False)
    assert r.status_code == 422
    rows = (
        (await db_session.execute(select(Scenario).where(Scenario.name == payload["name"])))
        .scalars()
        .all()
    )
    assert rows == []  # no row created


@pytest.mark.asyncio
async def test_scenario_list_has_draft_filter_chip(authed_analyst, db_session: AsyncSession):
    client, org_id = authed_analyst
    _seed_scenario(db_session, org_id=org_id, name="Chip Draft", status=EntityStatus.DRAFT)
    await db_session.commit()
    r = await client.get("/scenarios")
    assert "?status=draft" in r.text  # chip present
    r2 = await client.get("/scenarios?status=draft")
    assert "Chip Draft" in r2.text
