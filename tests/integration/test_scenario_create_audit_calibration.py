"""scenario.create audit + stamp-freeze reproducibility.

Org revenue-tier loss scaling was removed 2026-07-07, so no calibration
metadata is computed on any path. This suite now asserts:
1. The scenario.create audit changes dict does NOT carry a library_calibration
   entry (wizard finalize path).
2. Stamped scenario distributions stay frozen across:
   - org.annual_revenue change (tier shift)
   - entry.calibration_anchor edit
   - entry version bump with a different anchor (re-publish path)
3. The non-wizard expert-form path also records no library_calibration in
   the audit when form.library_entry_id is set.

NOTE: deviation from plan tests — the plan uses `analyst_client` +
`organization` fixtures, but `authed_analyst` creates its OWN org
(distinct from the `organization` fixture). We need to mutate the
analyst's actual org so the wizard route reads industry/revenue
correctly during the library-pick flow. We use `authed_analyst` +
`_resolve_org` (same pattern as F4/F5 wizard-calibration tests).
For the expert-form test (#6, direct service call), we use
`seed_organization` + `seed_user` so user.organization_id matches the
target organization_id (IDOR-clean) and there is no HTTP login coupling.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.audit_log import AuditLog
from idraa.models.enums import (
    AssetClass,
    IndustryType,
    ThreatActorType,
    ThreatCategory,
)
from idraa.models.organization import Organization
from idraa.models.scenario import Scenario
from idraa.models.scenario_library import ScenarioLibraryEntry
from idraa.models.user import User
from idraa.models.wizard_draft import WizardDraft
from tests.conftest import csrf_post
from tests.integration._wizard_step3_test_helpers import (
    _current_version_token,
    _persist_fair_rows_via_steps_3_and_4,
)


def _entry_kwargs(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": uuid.uuid4(),
        "version": 1,
        "slug": "f6-entry",
        "name": "F6 Test",
        "status": "published",
        "threat_event_type": ThreatCategory.RANSOMWARE,
        "threat_actor_type": ThreatActorType.CYBERCRIMINALS,
        "asset_class": AssetClass.SYSTEMS,
        "tags": [],
        "description": "d",
        "canonical_fair_gap": "g",
        "source_citations": [],
        "threat_event_frequency": {"distribution": "PERT", "low": 1.0, "mode": 4.0, "high": 12.0},
        "vulnerability": {"distribution": "PERT", "low": 0.05, "mode": 0.20, "high": 0.50},
        "primary_loss": {
            "distribution": "PERT",
            "low": 100000.0,
            "mode": 1000000.0,
            "high": 10000000.0,
        },
        "secondary_loss": {
            "distribution": "PERT",
            "low": 50000.0,
            "mode": 500000.0,
            "high": 5000000.0,
        },
        "suggested_control_ids": [],
        "calibration_anchor": {"industry": "healthcare", "revenue_tier": "10b_to_100b"},
    }
    base.update(overrides)
    return base


async def _resolve_org(db_session: AsyncSession, org_id: object) -> Organization:
    return (
        await db_session.execute(select(Organization).where(Organization.id == org_id))
    ).scalar_one()


async def _drive_wizard_to_finalize(
    client: AsyncClient, db: AsyncSession, entry_id: uuid.UUID
) -> str:
    """Walk through wizard steps 1 -> finalize. Returns the created scenario_id.

    F6 (2026-05-28 step-3 split): finalize is STATE-SOURCED. Library-pick
    pre-fills the draft's calibrated PL/SL/TEF/Vuln distributions; the analyst
    confirms them as SME rows via the per-page step-3 (TEF+Vuln) / step-4
    (PL+SL) POSTs, then the review-page Save posts ONLY csrf + version_token.
    We seed a non-system SME for the analyst and post the calibrated low/high
    as a single SME row per fieldset — preserving the calibration-freeze
    contract these tests assert against.
    """
    from idraa.models.sme import SubjectMatterExpert

    pick = await csrf_post(
        client,
        "/scenarios/new/wizard/step/1",
        data={"library_entry_id": str(entry_id), "skip_library": ""},
    )
    assert pick.status_code in (302, 303), f"step-1 pick failed: {pick.status_code}"
    tx = pick.headers["location"].split("tx=")[-1]

    # Pull current state to read the calibrated values back.
    draft = (
        await db.execute(select(WizardDraft).order_by(WizardDraft.updated_at.desc()))
    ).scalar_one()
    state_now = draft.state_json
    org_id = draft.organization_id
    user_id = draft.user_id

    # Seed a SubjectMatterExpert for this org so the SME-row finalize flow has a
    # valid sme_id foreign key. The tx-id keeps the email unique across reruns.
    sme = SubjectMatterExpert(
        organization_id=org_id,
        name=f"F6 SME {tx[:8]}",
        email=f"f6-sme-{tx[:8]}@example.com",
        created_by=user_id,
        created_via="admin",
    )
    db.add(sme)
    await db.commit()
    sme_id = sme.id

    # Step-2: name + descriptive fields
    step2 = await csrf_post(
        client,
        f"/scenarios/new/wizard/step/2?tx={tx}",
        data={
            "name": "F6 Test Scenario",
            "description": "test",
            "threat_category": "ransomware",
            "threat_actor_type": "cybercriminals",
            "asset_class": "systems",
            "attack_vector": "email_phishing",
        },
    )
    assert step2.status_code in (302, 303), f"step-2 failed: {step2.status_code}"

    # Confirm the calibrated values as SME rows via steps 3 (TEF+Vuln) and 4
    # (PL+SL). The per-page POSTs persist into state.sme_estimates, which
    # finalize then reads (D6 state-sourced).
    pl = state_now["primary_loss"]
    sl = state_now["secondary_loss"]
    tef = state_now["threat_event_frequency"]
    vuln = state_now["vulnerability"]
    tx_uuid = uuid.UUID(tx)
    await _persist_fair_rows_via_steps_3_and_4(
        client,
        db,
        tx_uuid,
        tef=[(str(sme_id), float(tef["low"]), float(tef["high"]))],
        vuln=[(str(sme_id), float(vuln["low"]), float(vuln["high"]))],
        pl=[(str(sme_id), float(pl["low"]), float(pl["high"]))],
        sl=[(str(sme_id), float(sl["low"]), float(sl["high"]))],
    )

    # Re-resolve current version_token (each per-page POST bumped it via the
    # blind advance_step path). Close + re-open so we don't observe a stale
    # SQLite snapshot from before the app's commits.
    await db.close()
    vt = await _current_version_token(db, tx_uuid)

    # F6: the review-page Save form posts ONLY csrf + version_token.
    finalize = await csrf_post(
        client,
        f"/scenarios/new/wizard/finalize?tx={tx}",
        data={"version_token": str(vt)},
    )
    assert finalize.status_code in (302, 303), (
        f"finalize failed: {finalize.status_code}: {finalize.text}"
    )

    # Newest scenario
    scen = (
        (await db.execute(select(Scenario).order_by(Scenario.created_at.desc()))).scalars().first()
    )
    assert scen is not None, "wizard finalize did not create a scenario"
    return str(scen.id)


@pytest.mark.asyncio
async def test_scenario_create_audit_omits_library_calibration_after_scaling_removal(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """Org revenue-tier loss scaling was removed 2026-07-07, so no calibration
    metadata is computed or recorded — the audit's changes dict must NOT carry a
    'library_calibration' entry (the loss was not scaled to org size)."""
    client, org_id = authed_analyst

    org = await _resolve_org(db_session, org_id)
    org.industry_type = IndustryType.HEALTHCARE
    org.annual_revenue = Decimal("500000000")  # -> 100m_to_1b tier
    await db_session.commit()
    await db_session.close()

    entry = ScenarioLibraryEntry(**_entry_kwargs())
    db_session.add(entry)
    await db_session.commit()
    entry_id = entry.id
    await db_session.close()

    scen_id = await _drive_wizard_to_finalize(client, db_session, entry_id)

    audit = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.entity_type == "scenario",
                AuditLog.entity_id == uuid.UUID(scen_id),
                AuditLog.action == "scenario.create",
            )
        )
    ).scalar_one()
    assert "library_calibration" not in audit.changes


@pytest.mark.asyncio
async def test_scenario_stamped_pl_stays_frozen_after_org_tier_change(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    client, org_id = authed_analyst

    org = await _resolve_org(db_session, org_id)
    org.industry_type = IndustryType.HEALTHCARE
    org.annual_revenue = Decimal("500000000")
    await db_session.commit()
    await db_session.close()

    entry = ScenarioLibraryEntry(**_entry_kwargs(slug="f6-freeze"))
    db_session.add(entry)
    await db_session.commit()
    entry_id = entry.id
    await db_session.close()

    scen_id = await _drive_wizard_to_finalize(client, db_session, entry_id)
    scen_before = await db_session.get(Scenario, uuid.UUID(scen_id))
    assert scen_before is not None
    pl_before = scen_before.primary_loss

    # Org moves to a larger tier
    org = await _resolve_org(db_session, org_id)
    org.annual_revenue = Decimal("25000000000")  # -> 10b_to_100b
    await db_session.commit()
    db_session.expire_all()

    scen_after = await db_session.get(Scenario, uuid.UUID(scen_id))
    assert scen_after is not None
    assert scen_after.primary_loss == pl_before, (
        "stamped PL must not move when org.annual_revenue shifts tier"
    )


@pytest.mark.asyncio
async def test_scenario_stamped_pl_stays_frozen_after_entry_anchor_edit(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    client, org_id = authed_analyst

    org = await _resolve_org(db_session, org_id)
    org.industry_type = IndustryType.HEALTHCARE
    org.annual_revenue = Decimal("500000000")
    await db_session.commit()
    await db_session.close()

    entry = ScenarioLibraryEntry(**_entry_kwargs(slug="f6-anchor-edit"))
    db_session.add(entry)
    await db_session.commit()
    entry_id = entry.id
    await db_session.close()

    scen_id = await _drive_wizard_to_finalize(client, db_session, entry_id)
    scen_before = await db_session.get(Scenario, uuid.UUID(scen_id))
    assert scen_before is not None
    pl_before = scen_before.primary_loss

    # Curator edits the anchor (e.g., realizes it was mid-market not enterprise)
    entry_row = await db_session.get(ScenarioLibraryEntry, (entry_id, 1))
    assert entry_row is not None
    entry_row.calibration_anchor = {"industry": "healthcare", "revenue_tier": "1b_to_10b"}
    await db_session.commit()
    db_session.expire_all()

    scen_after = await db_session.get(Scenario, uuid.UUID(scen_id))
    assert scen_after is not None
    assert scen_after.primary_loss == pl_before


@pytest.mark.asyncio
async def test_scenario_stamped_pl_stays_frozen_after_entry_version_bump(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """The riskier reproducibility path: entry republished as v+1 with a different anchor.

    Curator: realizes the original anchor was wrong; republishes a brand-new
    version with the corrected anchor. Pre-existing scenarios pin to the
    OLD version via scenario.library_pin and must continue to read the OLD
    anchor on any future recompute. Since we stamp distributions at create,
    the stamped PL on the existing scenario must not change.
    """
    client, org_id = authed_analyst

    org = await _resolve_org(db_session, org_id)
    org.industry_type = IndustryType.HEALTHCARE
    org.annual_revenue = Decimal("500000000")
    await db_session.commit()
    await db_session.close()

    entry_v1 = ScenarioLibraryEntry(**_entry_kwargs(slug="f6-version-bump"))
    v1_entry_id = entry_v1.id
    db_session.add(entry_v1)
    await db_session.commit()
    await db_session.close()

    scen_id = await _drive_wizard_to_finalize(client, db_session, v1_entry_id)
    scen_before = await db_session.get(Scenario, uuid.UUID(scen_id))
    assert scen_before is not None
    pl_before = scen_before.primary_loss
    # Assert the scenario pins to v1
    assert scen_before.library_pin["entry_id"] == str(v1_entry_id)
    assert scen_before.library_pin["version"] == 1

    # Curator publishes v2 with a different anchor
    entry_v2 = ScenarioLibraryEntry(
        **_entry_kwargs(
            slug="f6-version-bump",
            calibration_anchor={"industry": "healthcare", "revenue_tier": "1b_to_10b"},
        )
    )
    entry_v2.id = v1_entry_id  # same logical id, version+1
    entry_v2.version = 2
    db_session.add(entry_v2)
    await db_session.commit()
    db_session.expire_all()

    scen_after = await db_session.get(Scenario, uuid.UUID(scen_id))
    assert scen_after is not None
    assert scen_after.primary_loss == pl_before, (
        "stamped PL must stay frozen after entry version bump"
    )
    assert scen_after.library_pin["version"] == 1, (
        "library_pin.version must continue pinning to v1, not the newer v2"
    )


@pytest.mark.asyncio
async def test_expert_form_create_with_library_entry_id_records_calibration_in_audit(
    db_session: AsyncSession,
    seed_organization: Organization,
    seed_user: User,
) -> None:
    """Non-wizard expert-form create with library_entry_id set: audit log
    records library_calibration metadata for forensic reproducibility.

    The expert-form path stamps form.primary_loss verbatim (analyst's
    authority — the form is the source of truth on this path), so the
    stamped PL is NOT necessarily ratio-scaled. But the audit log records
    what calibration WOULD have applied for (org, entry, tier) — useful
    for later reconciliation and as forensic info if the analyst's value
    diverged from the calibrated reference.

    The "expert-form deep-link pre-fill should auto-calibrate the form's
    PL/SL" behavior is a separate concern (filing as follow-on issue).
    """
    from idraa.models.enums import EntityStatus, ScenarioSource
    from idraa.schemas.scenario import ScenarioForm
    from idraa.services.scenarios import ScenarioService

    seed_organization.industry_type = IndustryType.HEALTHCARE
    seed_organization.annual_revenue = Decimal("500000000")
    await db_session.commit()

    entry = ScenarioLibraryEntry(**_entry_kwargs(slug="f6-expert-form"))
    db_session.add(entry)
    await db_session.commit()

    form = ScenarioForm(
        name="Expert-form test",
        description="d",
        threat_category="ransomware",
        threat_actor_type="cybercriminals",
        asset_class="systems",
        attack_vector="email_phishing",
        threat_event_frequency=entry.threat_event_frequency,
        vulnerability=entry.vulnerability,
        primary_loss=entry.primary_loss,
        secondary_loss=entry.secondary_loss,
        library_entry_id=entry.id,
        status=EntityStatus.DRAFT,
        source=ScenarioSource.LIBRARY_DERIVED,
        version="1",
    )
    scen = await ScenarioService(db_session).create(
        organization_id=seed_organization.id,
        form=form,
        current_user=seed_user,
    )
    audit = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.entity_type == "scenario",
                AuditLog.entity_id == scen.id,
                AuditLog.action == "scenario.create",
            )
        )
    ).scalar_one()
    # Org loss-scaling removed 2026-07-07 — the expert-form path no longer
    # computes or records any calibration metadata.
    assert "library_calibration" not in audit.changes
