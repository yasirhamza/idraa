"""Audit-F2: vuln_framing provenance — banner, confirm endpoint, update flip.

Per docs/superpowers/specs/2026-06-10-audit-remediation-f1-f2-design.md:
- legacy_residual scenarios banner on detail (with conversion guidance + the
  explicit "Confirm — values are already inherent" button) and on the edit
  form (text only, no button);
- POST /scenarios/{id}/confirm-vuln-framing: 303 + flip + audit +
  row_version bump; reviewer 403; cross-org 404 (no existence oracle);
  untokened POST 403 (CSRF); idempotent re-confirm (no second audit row);
- ScenarioService.update flips the stamp ONLY when the vulnerability
  (low, mode, high) numeric triple changes — wizard-shaped nodes (sidecar
  metadata, no "distribution" key) must NOT false-positive-flip (SC-B1).
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.audit_log import AuditLog
from idraa.models.enums import EntityStatus, ScenarioType, ThreatCategory
from idraa.models.scenario import Scenario
from idraa.models.user import User
from idraa.schemas.scenario import ScenarioForm
from idraa.services.scenarios import ScenarioService
from tests.conftest import csrf_post

_WIZARD_SHAPED_VULN = {
    # wizard-created shape: numeric triple + sidecar, NO "distribution" key.
    "low": 0.2,
    "mode": 0.4,
    "high": 0.6,
    "distribution_fit_metadata": {"fitter": "norm_trunc", "schema_version": 2},
}


async def _seed_legacy_scenario(
    db: AsyncSession,
    org_id: uuid.UUID,
    *,
    vulnerability: dict[str, Any] | None = None,
    name: str = "legacy vuln scenario",
) -> Scenario:
    s = Scenario(
        organization_id=org_id,
        name=name,
        scenario_type=ScenarioType.CUSTOM,
        threat_category=ThreatCategory.RANSOMWARE,
        threat_event_frequency={"distribution": "PERT", "low": 0.5, "mode": 1.0, "high": 2.0},
        vulnerability=vulnerability
        or {"distribution": "PERT", "low": 0.2, "mode": 0.4, "high": 0.6},
        primary_loss={"distribution": "PERT", "low": 50_000, "mode": 250_000, "high": 900_000},
        status=EntityStatus.ACTIVE,
        vuln_framing="legacy_residual",
    )
    db.add(s)
    await db.commit()
    await db.refresh(s)
    return s


async def _analyst_user(db: AsyncSession) -> User:
    row = (
        await db.execute(select(User).where(User.email == "analyst@test.local"))
    ).scalar_one_or_none()
    assert row is not None
    return row


# ---------------------------------------------------------------------------
# Banner rendering
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_legacy_scenario_detail_renders_review_banner_with_guidance(
    authed_analyst: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    client, org_id = authed_analyst
    s = await _seed_legacy_scenario(db_session, org_id)
    body = (await client.get(f"/scenarios/{s.id}")).text
    assert "Vulnerability needs review" in body
    # Meth-B1: the banner must TEACH the conversion, not just demand a click.
    assert "before considering your controls" in body
    assert "usually" in body  # "...usually HIGHER than the current value"
    assert "Confirm — values are already inherent" in body
    assert f"/scenarios/{s.id}/confirm-vuln-framing" in body


@pytest.mark.asyncio
async def test_inherent_scenario_detail_has_no_banner(
    authed_analyst: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    client, org_id = authed_analyst
    s = await _seed_legacy_scenario(db_session, org_id, name="inherent one")
    s.vuln_framing = "inherent"
    await db_session.commit()
    body = (await client.get(f"/scenarios/{s.id}")).text
    assert "Vulnerability needs review" not in body


@pytest.mark.asyncio
async def test_edit_form_shows_banner_text_without_button(
    authed_analyst: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    """SC-N2: edit page warns but carries no confirm button — the analyst
    there is already in the fix-it flow."""
    client, org_id = authed_analyst
    s = await _seed_legacy_scenario(db_session, org_id)
    body = (await client.get(f"/scenarios/{s.id}/edit")).text
    assert "Vulnerability needs review" in body
    assert "confirm-vuln-framing" not in body


# ---------------------------------------------------------------------------
# Confirm endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_confirm_flips_stamp_bumps_row_version_and_audits(
    authed_analyst: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    client, org_id = authed_analyst
    s = await _seed_legacy_scenario(db_session, org_id)
    rv_before = s.row_version
    r = await csrf_post(
        client, f"/scenarios/{s.id}/confirm-vuln-framing", {}, follow_redirects=False
    )
    assert r.status_code == 303
    assert r.headers["location"] == f"/scenarios/{s.id}"
    await db_session.refresh(s)
    assert s.vuln_framing == "inherent"
    assert s.row_version == rv_before + 1
    audits = (
        (
            await db_session.execute(
                select(AuditLog).where(AuditLog.action == "scenario.confirm_vuln_framing")
            )
        )
        .scalars()
        .all()
    )
    assert len(audits) == 1
    assert audits[0].entity_id == s.id
    assert audits[0].changes["vuln_framing"] == ["legacy_residual", "inherent"]


@pytest.mark.asyncio
async def test_confirm_is_idempotent_no_second_audit(
    authed_analyst: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    client, org_id = authed_analyst
    s = await _seed_legacy_scenario(db_session, org_id)
    await csrf_post(client, f"/scenarios/{s.id}/confirm-vuln-framing", {}, follow_redirects=False)
    r2 = await csrf_post(
        client, f"/scenarios/{s.id}/confirm-vuln-framing", {}, follow_redirects=False
    )
    assert r2.status_code == 303  # no-op re-confirm still redirects cleanly
    await db_session.refresh(s)
    assert s.row_version == 2  # bumped exactly once
    n = len(
        (
            await db_session.execute(
                select(AuditLog).where(AuditLog.action == "scenario.confirm_vuln_framing")
            )
        )
        .scalars()
        .all()
    )
    assert n == 1


@pytest.mark.asyncio
async def test_confirm_reviewer_forbidden(
    authed_reviewer: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    client, org_id = authed_reviewer
    s = await _seed_legacy_scenario(db_session, org_id)
    r = await csrf_post(
        client, f"/scenarios/{s.id}/confirm-vuln-framing", {}, follow_redirects=False
    )
    assert r.status_code == 403
    await db_session.refresh(s)
    assert s.vuln_framing == "legacy_residual"


@pytest.mark.asyncio
async def test_confirm_cross_org_returns_404_not_403(
    authed_analyst: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    """Sec-F2-I1: cross-org probing must get 404 (no existence oracle)."""
    from idraa.models.enums import IndustryType, OrganizationSize
    from idraa.models.organization import Organization

    client, _org_id = authed_analyst
    other_org = Organization(
        name="Other Org",
        industry_type=IndustryType.MANUFACTURING,
        organization_size=OrganizationSize.MEDIUM,
    )
    db_session.add(other_org)
    await db_session.flush()
    other_org_scenario = await _seed_legacy_scenario(db_session, other_org.id, name="other org")
    r = await csrf_post(
        client,
        f"/scenarios/{other_org_scenario.id}/confirm-vuln-framing",
        {},
        follow_redirects=False,
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_confirm_untokened_post_rejected_by_csrf(
    authed_analyst: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    client, org_id = authed_analyst
    s = await _seed_legacy_scenario(db_session, org_id)
    r = await client.post(f"/scenarios/{s.id}/confirm-vuln-framing", data={})
    assert r.status_code == 403
    await db_session.refresh(s)
    assert s.vuln_framing == "legacy_residual"


# ---------------------------------------------------------------------------
# Update-path stamp flip (service level — the rule lives in ScenarioService)
# ---------------------------------------------------------------------------


def _form_for(
    s: Scenario, *, vulnerability: dict[str, Any], name: str | None = None
) -> ScenarioForm:
    return ScenarioForm(
        name=name or s.name,
        threat_category=getattr(s.threat_category, "value", s.threat_category),
        threat_event_frequency=s.threat_event_frequency,
        vulnerability=vulnerability,
        primary_loss=s.primary_loss,
        secondary_loss=s.secondary_loss,
    )


@pytest.mark.asyncio
async def test_update_changing_vuln_numerics_flips_stamp(
    authed_analyst: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    _client, org_id = authed_analyst
    s = await _seed_legacy_scenario(db_session, org_id)
    user = await _analyst_user(db_session)
    form = _form_for(
        s, vulnerability={"distribution": "PERT", "low": 0.4, "mode": 0.6, "high": 0.8}
    )
    await ScenarioService(db_session).update(
        organization_id=org_id,
        scenario_id=s.id,
        form=form,
        expected_row_version=s.row_version,
        current_user=user,
    )
    await db_session.commit()
    await db_session.refresh(s)
    assert s.vuln_framing == "inherent"


@pytest.mark.asyncio
async def test_update_name_only_preserves_legacy_stamp(
    authed_analyst: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    _client, org_id = authed_analyst
    s = await _seed_legacy_scenario(db_session, org_id)
    user = await _analyst_user(db_session)
    form = _form_for(s, vulnerability=dict(s.vulnerability), name="renamed only")
    await ScenarioService(db_session).update(
        organization_id=org_id,
        scenario_id=s.id,
        form=form,
        expected_row_version=s.row_version,
        current_user=user,
    )
    await db_session.commit()
    await db_session.refresh(s)
    assert s.vuln_framing == "legacy_residual"


@pytest.mark.asyncio
async def test_update_wizard_shaped_vuln_roundtrip_does_not_false_flip(
    authed_analyst: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    """SC-B1 regression: wizard-created nodes carry a sidecar and no
    "distribution" key — a form round-trip of the SAME numeric triple (which
    re-shapes the dict) must NOT flip the stamp. Comparison is on
    (low, mode, high) only."""
    _client, org_id = authed_analyst
    s = await _seed_legacy_scenario(
        db_session, org_id, vulnerability=dict(_WIZARD_SHAPED_VULN), name="wizard shaped"
    )
    user = await _analyst_user(db_session)
    # Form path re-shapes to the PERT dict with the SAME numeric triple.
    form = _form_for(
        s,
        vulnerability={"distribution": "PERT", "low": 0.2, "mode": 0.4, "high": 0.6},
        name="wizard shaped renamed",
    )
    await ScenarioService(db_session).update(
        organization_id=org_id,
        scenario_id=s.id,
        form=form,
        expected_row_version=s.row_version,
        current_user=user,
    )
    await db_session.commit()
    await db_session.refresh(s)
    assert s.vuln_framing == "legacy_residual"  # same numbers -> no flip


@pytest.mark.asyncio
async def test_new_scenario_defaults_to_inherent(
    authed_analyst: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    """New rows (form create / wizard finalize / import all create NEW rows)
    take the 'inherent' ORM default."""
    _client, org_id = authed_analyst
    user = await _analyst_user(db_session)
    form = ScenarioForm(
        name="fresh inherent",
        threat_category="ransomware",
        threat_event_frequency={"distribution": "PERT", "low": 0.5, "mode": 1.0, "high": 2.0},
        vulnerability={"distribution": "PERT", "low": 0.1, "mode": 0.2, "high": 0.3},
        primary_loss={"distribution": "PERT", "low": 1000, "mode": 5000, "high": 9000},
    )
    s = await ScenarioService(db_session).create(
        organization_id=org_id, form=form, current_user=user
    )
    await db_session.commit()
    await db_session.refresh(s)
    assert s.vuln_framing == "inherent"
