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

Epic #34 P1c Task 8 adds: converted-row (source ==
QUALITATIVE_REGISTER_IMPORT) F2 banner copy variant — heading, two-path
structure, promote-refusal-message assertions live in test_draft_workflow.py.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.audit_log import AuditLog
from idraa.models.enums import EntityStatus, ScenarioSource, ScenarioType, ThreatCategory
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
# Converted-row (epic #34 P1c Task 8) banner variant — plan-gate M-1
# ---------------------------------------------------------------------------


async def _seed_converted_scenario(
    db: AsyncSession,
    org_id: uuid.UUID,
    *,
    name: str = "converted register row",
) -> Scenario:
    """A legacy_residual scenario sourced from the qualitative register
    converter, with a full conversion_metadata payload (M-4 provenance
    display)."""
    s = await _seed_legacy_scenario(db, org_id, name=name)
    s.source = ScenarioSource.QUALITATIVE_REGISTER_IMPORT
    s.conversion_metadata = {
        "source_file": "register.xlsx",
        "source_row": 1,
        "raw": {"likelihood": "High", "impact": "Severe", "category": "Ransomware"},
        "bindings": {
            "likelihood_label": "high",
            "magnitude_label": "very_high",
            "category": "ransomware",
        },
        "mapping_versions": {
            "canonical": {"frequency:high": 1, "magnitude:very_high": 1},
            "org": {},
        },
        "binding_profile_id": None,
        "converted_at": "2026-07-18T00:00:00+00:00",
    }
    await db.commit()
    await db.refresh(s)
    return s


@pytest.mark.asyncio
async def test_converted_row_shows_frequency_baseline_banner(
    authed_analyst: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    """M-1: a converted, unconfirmed row shows the converter-aware heading,
    both explicitly-labelled paths, and the converter-aware confirm button —
    NEVER the generic vulnerability-review copy (exclusivity)."""
    client, org_id = authed_analyst
    s = await _seed_converted_scenario(db_session, org_id)
    body = (await client.get(f"/scenarios/{s.id}")).text

    assert "Frequency baseline needs review." in body
    assert "Path A" in body and "Path B" in body
    assert "Confirm — accept frequency baseline" in body
    assert f"/scenarios/{s.id}/confirm-vuln-framing" in body

    # Exclusivity (Meth-R2): never co-render the generic vuln-banner heading
    # or its "re-enter Vulnerability … higher" instruction — wrong advice
    # for an intentionally-neutral (1,1,1) vuln triple.
    assert "Vulnerability needs review." not in body
    assert "re-enter Vulnerability" not in body
    assert "Confirm — values are already inherent" not in body

    # "inherent" is reserved for Path B's edit action — never describes what
    # the Confirm button does (M-1, BINDING).
    before_path_b, _, after_path_b = body.partition("Path B")
    assert "inherent" not in before_path_b.lower()
    assert "inherent" in after_path_b.lower()


@pytest.mark.asyncio
async def test_non_converted_row_keeps_existing_vuln_banner_copy(
    authed_analyst: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    """Non-converted (default EXPERT_JUDGMENT source) scenarios are
    unaffected by Task 8 — the original F2 copy renders verbatim."""
    client, org_id = authed_analyst
    s = await _seed_legacy_scenario(db_session, org_id, name="ordinary legacy scenario")
    body = (await client.get(f"/scenarios/{s.id}")).text

    assert "Vulnerability needs review." in body
    assert "Confirm — values are already inherent" in body
    assert "Frequency baseline needs review." not in body
    assert "Confirm — accept frequency baseline" not in body


@pytest.mark.asyncio
async def test_draft_banner_shows_raw_to_band_binding_arrows(
    authed_analyst: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    """M-4: the DRAFT banner on a converted row lists raw→bound arrows
    sourced from BOTH conversion_metadata.raw and .bindings — closing the
    P1a provenance-display deferral."""
    client, org_id = authed_analyst
    s = await _seed_converted_scenario(db_session, org_id)
    s.status = EntityStatus.DRAFT
    await db_session.commit()
    body = (await client.get(f"/scenarios/{s.id}")).text

    # &lsquo;/&rsquo;/&rarr; are literal HTML entities in the template source
    # (not values routed through Jinja escaping) — the raw response body
    # carries the entity text verbatim; a browser renders the curly quotes
    # and arrow. Exact fragments (not just "high band" — the ambient page
    # already shows the scenario's own threat_category "ransomware") pin the
    # arrow renders raw -> bound, not just that both substrings appear
    # somewhere on the page.
    assert "likelihood &lsquo;High&rsquo; &rarr; high band" in body
    assert "impact &lsquo;Severe&rsquo; &rarr; very_high band" in body
    assert "category &lsquo;Ransomware&rsquo; &rarr; ransomware" in body


@pytest.mark.asyncio
async def test_draft_banner_escapes_script_laden_raw_cell(
    authed_analyst: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    """Sec-R2-NTH: conversion_metadata.raw is attacker-controlled register
    text (the admin who uploads a register need not be the same admin who
    later reviews it) — it must render through Jinja's default autoescape
    ONLY, never |safe. A <script>-laden cell must reach the page escaped."""
    client, org_id = authed_analyst
    s = await _seed_converted_scenario(db_session, org_id, name="script cell row")
    s.status = EntityStatus.DRAFT
    metadata = dict(s.conversion_metadata)
    metadata["raw"] = dict(metadata["raw"])
    metadata["raw"]["likelihood"] = "<script>alert('xss')</script>"
    s.conversion_metadata = metadata
    await db_session.commit()
    body = (await client.get(f"/scenarios/{s.id}")).text

    assert "<script>alert" not in body
    assert "&lt;script&gt;" in body


@pytest.mark.asyncio
async def test_mitigating_controls_empty_state_states_zero_controls(
    authed_analyst: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    """Meth-R2-New-2 (consumer-side same-PR rule): the control-less empty
    state used to claim analyses "fall back to all controls in your org" —
    false since #89 (controls are strictly coupled to
    scenario.mitigating_controls; empty -> zero controls applied). The
    corrected copy must state the true zero-control behavior — this is what
    makes Path A ("do not attach controls") sound advice rather than a
    contradiction."""
    client, org_id = authed_analyst
    s = await _seed_legacy_scenario(db_session, org_id, name="no controls scenario")
    s.vuln_framing = "inherent"  # keep the F2 banner out of this assertion's way
    await db_session.commit()
    body = (await client.get(f"/scenarios/{s.id}")).text

    assert "fall back to all controls in your org" not in body
    assert "zero controls" in body


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
async def test_confirm_converted_scenario_writes_frequency_baseline_action(
    authed_analyst: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    """Task 5b (spec §3 Meth-I1): a converted register row's confirm writes
    the converter-aware audit action — the epistemic act is acceptance of
    the frequency baseline, not a review of (neutral, pass-through)
    vulnerability values."""
    client, org_id = authed_analyst
    s = await _seed_legacy_scenario(db_session, org_id, name="converted register row")
    s.source = ScenarioSource.QUALITATIVE_REGISTER_IMPORT
    s.conversion_metadata = {
        "source_file": "register.xlsx",
        "source_row": 1,
        "raw": {"likelihood": "Likely", "impact": "High", "category": "Phishing"},
        "bindings": {
            "likelihood_label": "moderate",
            "magnitude_label": "high",
            "category": "social_engineering",
        },
        "mapping_versions": {"canonical": 1, "org": {}},
        "binding_profile_id": None,
        "converted_at": "2026-07-18T00:00:00+00:00",
    }
    await db_session.commit()

    r = await csrf_post(
        client, f"/scenarios/{s.id}/confirm-vuln-framing", {}, follow_redirects=False
    )
    assert r.status_code == 303
    await db_session.refresh(s)
    assert s.vuln_framing == "inherent"

    freq_baseline_rows = (
        (
            await db_session.execute(
                select(AuditLog).where(
                    AuditLog.action == "scenario.confirm_frequency_baseline",
                    AuditLog.entity_id == s.id,
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(freq_baseline_rows) == 1
    assert freq_baseline_rows[0].changes["vuln_framing"] == ["legacy_residual", "inherent"]

    old_action_rows = (
        (
            await db_session.execute(
                select(AuditLog).where(
                    AuditLog.action == "scenario.confirm_vuln_framing",
                    AuditLog.entity_id == s.id,
                )
            )
        )
        .scalars()
        .all()
    )
    assert old_action_rows == []


@pytest.mark.asyncio
async def test_confirm_non_converted_scenario_keeps_original_action(
    authed_analyst: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    """Non-converted scenarios (default EXPERT_JUDGMENT source, or any
    other non-QUALITATIVE_REGISTER_IMPORT source) are unaffected by Task 5b —
    same action string as before."""
    client, org_id = authed_analyst
    s = await _seed_legacy_scenario(db_session, org_id, name="ordinary scenario")
    await db_session.commit()

    r = await csrf_post(
        client, f"/scenarios/{s.id}/confirm-vuln-framing", {}, follow_redirects=False
    )
    assert r.status_code == 303
    await db_session.refresh(s)
    assert s.vuln_framing == "inherent"

    rows = (
        (
            await db_session.execute(
                select(AuditLog).where(
                    AuditLog.action == "scenario.confirm_vuln_framing",
                    AuditLog.entity_id == s.id,
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1


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
