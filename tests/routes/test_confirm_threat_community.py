from __future__ import annotations

from sqlalchemy import select

from idraa.models.audit_log import AuditLog
from idraa.models.enums import EntityStatus, ScenarioType, ThreatCategory
from idraa.models.scenario import Scenario
from idraa.models.threat_community import canonical_threat_community_id
from tests.conftest import csrf_post

# used by the edit-route tests appended below (hoisted here: a mid-file import is E402)
from tests.integration.test_draft_workflow import _valid_update_payload_for
from tests.unit.test_threat_communities_service import org_row_from

_DIST = {"distribution": "PERT", "low": 1.0, "mode": 2.0, "high": 3.0}


async def _make(
    db_session,
    org_id,
    *,
    slug="privileged_insider",
    prov="migrated_split_default",
    status=EntityStatus.ACTIVE,
) -> Scenario:
    s = Scenario(
        organization_id=org_id,
        name="S",
        scenario_type=ScenarioType.CUSTOM,
        threat_category=ThreatCategory.MALWARE,
        threat_event_frequency=_DIST,
        vulnerability={"distribution": "PERT", "low": 0.1, "mode": 0.2, "high": 0.3},
        primary_loss=_DIST,
        status=status,
        version="1.0",
        row_version=1,
        threat_community_id=canonical_threat_community_id(slug) if slug else None,
        threat_community_version=1 if slug else None,
        threat_community_provenance=prov,
    )
    db_session.add(s)
    await db_session.commit()
    return s


async def _audit_count(db_session) -> int:
    return len((await db_session.execute(select(AuditLog))).scalars().all())


async def test_analyst_confirms_audit_written(authed_analyst, db_session) -> None:
    client, org_id = authed_analyst
    s = await _make(db_session, org_id)
    r = await csrf_post(
        client,
        f"/scenarios/{s.id}/confirm-threat-community",
        {"threat_community": "nonprivileged_insider"},
    )
    assert r.status_code == 303
    await db_session.refresh(s)
    assert (
        s.threat_community.slug == "nonprivileged_insider"
        and s.threat_community_provenance == "assigned"
        and s.row_version == 2
    )
    row = (
        (
            await db_session.execute(
                select(AuditLog).where(AuditLog.action == "scenario.threat_community_confirmed")
            )
        )
        .scalars()
        .one()
    )
    assert row.entity_id == s.id and row.changes["threat_community"] == [
        "privileged_insider",
        "nonprivileged_insider",
    ]


async def test_admin_confirms(authed_admin, db_session) -> None:
    client, org_id = authed_admin
    s = await _make(db_session, org_id)
    assert (
        await csrf_post(
            client,
            f"/scenarios/{s.id}/confirm-threat-community",
            {"threat_community": "hacktivists"},
        )
    ).status_code == 303


async def test_reviewer_403(authed_reviewer, db_session) -> None:
    client, org_id = authed_reviewer
    s = await _make(db_session, org_id)
    assert (
        await csrf_post(
            client,
            f"/scenarios/{s.id}/confirm-threat-community",
            {"threat_community": "hacktivists"},
        )
    ).status_code == 403


async def test_viewer_403(authed_viewer, db_session) -> None:
    client, org_id = authed_viewer
    s = await _make(db_session, org_id)
    assert (
        await csrf_post(
            client,
            f"/scenarios/{s.id}/confirm-threat-community",
            {"threat_community": "hacktivists"},
        )
    ).status_code == 403


async def test_confirm_rejects_unknown_and_org_sourced_slug(
    authed_analyst, db_session, seed_threat_communities
) -> None:
    """Review Focus #4 — the source='org' row IS inserted and committed so the app engine sees it."""
    client, org_id = authed_analyst
    db_session.add(org_row_from(seed_threat_communities["hacktivists"]))
    await db_session.commit()
    s = await _make(db_session, org_id)
    for slug in ("martians", "org_only"):
        r = await csrf_post(
            client, f"/scenarios/{s.id}/confirm-threat-community", {"threat_community": slug}
        )
        assert r.status_code == 422
        assert "Choose a published threat community" in r.text
        assert slug not in r.text
    await db_session.refresh(s)
    assert s.threat_community_provenance == "migrated_split_default"


async def test_confirm_refuses_when_already_assigned(authed_analyst, db_session) -> None:
    client, org_id = authed_analyst
    s = await _make(db_session, org_id, slug="hacktivists", prov="assigned")
    n = await _audit_count(db_session)
    r = await csrf_post(
        client,
        f"/scenarios/{s.id}/confirm-threat-community",
        {"threat_community": "competitors"},
    )
    assert r.status_code == 409
    assert "already assigned" in r.text
    await db_session.refresh(s)
    assert (
        s.threat_community.slug == "hacktivists"
        and s.row_version == 1
        and await _audit_count(db_session) == n
    )


async def test_deleted_is_404(authed_analyst, db_session) -> None:
    client, org_id = authed_analyst
    s = await _make(db_session, org_id, status=EntityStatus.DELETED)
    assert (
        await csrf_post(
            client,
            f"/scenarios/{s.id}/confirm-threat-community",
            {"threat_community": "hacktivists"},
        )
    ).status_code == 404


async def test_cross_org_is_404(authed_analyst, db_session, seed_organization_factory) -> None:
    client, _ = authed_analyst
    other = await seed_organization_factory(name="Other Org")
    theirs = await _make(db_session, other.id)
    assert (
        await csrf_post(
            client,
            f"/scenarios/{theirs.id}/confirm-threat-community",
            {"threat_community": "hacktivists"},
        )
    ).status_code == 404


# (`_valid_update_payload_for` is already imported in the module's import block above -- do NOT re-import here: E402)


async def _audit_rows(db_session, action: str) -> list[AuditLog]:
    return list(
        (await db_session.execute(select(AuditLog).where(AuditLog.action == action)))
        .scalars()
        .all()
    )


async def test_edit_unknown_slug_leaves_row_untouched(authed_analyst, db_session) -> None:
    """Review Focus #4: resolve-before-apply — nothing committed, nothing audited, no bump."""
    client, org_id = authed_analyst
    s = await _make(db_session, org_id, slug="hacktivists", prov="assigned")
    n = await _audit_count(db_session)
    r = await csrf_post(
        client,
        f"/scenarios/{s.id}",
        _valid_update_payload_for(s) | {"name": "RENAMED", "threat_community": "martians"},
    )  # `_valid_update_payload_for` is imported in the module's import block (a mid-file import trips ruff E402, which `--fix` cannot clear)
    assert r.status_code == 422
    await db_session.refresh(s)
    assert s.name == "S" and s.row_version == 1 and await _audit_count(db_session) == n


async def test_edit_change_audits_slugs(authed_analyst, db_session) -> None:
    client, org_id = authed_analyst
    s = await _make(db_session, org_id, slug="hacktivists", prov="assigned")
    r = await csrf_post(
        client,
        f"/scenarios/{s.id}",
        _valid_update_payload_for(s) | {"threat_community": "competitors"},
    )
    assert r.status_code == 303
    rows = await _audit_rows(db_session, "scenario.update")
    assert rows[-1].changes["threat_community"] == ["hacktivists", "competitors"]


async def test_flagged_save_with_placeholder_audits_provenance(authed_analyst, db_session) -> None:
    client, org_id = authed_analyst
    s = await _make(db_session, org_id)  # privileged_insider / migrated_split_default
    r = await csrf_post(
        client,
        f"/scenarios/{s.id}",
        _valid_update_payload_for(s) | {"threat_community": "privileged_insider"},
    )
    assert r.status_code == 303
    await db_session.refresh(s)
    assert s.threat_community_provenance == "assigned" and s.row_version == 2
    assert (await _audit_rows(db_session, "scenario.update"))[-1].changes[
        "threat_community_provenance"
    ] == ["migrated_split_default", "assigned"]


async def test_noop_edit_of_assigned_writes_nothing(authed_analyst, db_session) -> None:
    client, org_id = authed_analyst
    s = await _make(db_session, org_id, slug="hacktivists", prov="assigned")
    n = await _audit_count(db_session)
    r = await csrf_post(
        client,
        f"/scenarios/{s.id}",
        _valid_update_payload_for(s) | {"threat_community": "hacktivists"},
    )
    assert r.status_code == 303
    await db_session.refresh(s)
    assert s.row_version == 1 and await _audit_count(db_session) == n


async def test_migrated_confirm_refused_and_noop_edit_keeps_migrated(
    authed_analyst, db_session
) -> None:
    """M6-N1: 'migrated' is non-review (unlike 'migrated_split_default') -- confirm
    refuses exactly like an already-'assigned' scenario (409, audit count unchanged),
    and a no-op edit-form save of the SAME community leaves the provenance at
    'migrated' rather than bumping it to 'assigned' (services/scenarios.py
    `_assign_threat_community`'s `changed or threat_community_needs_review` gate --
    neither is true here)."""
    client, org_id = authed_analyst
    s = await _make(db_session, org_id, slug="hacktivists", prov="migrated")
    n = await _audit_count(db_session)
    r = await csrf_post(
        client,
        f"/scenarios/{s.id}/confirm-threat-community",
        {"threat_community": "competitors"},
    )
    assert r.status_code == 409
    assert "already assigned" in r.text
    await db_session.refresh(s)
    assert (
        s.threat_community.slug == "hacktivists"
        and s.threat_community_provenance == "migrated"
        and s.row_version == 1
        and await _audit_count(db_session) == n
    )
    r = await csrf_post(
        client,
        f"/scenarios/{s.id}",
        _valid_update_payload_for(s) | {"threat_community": "hacktivists"},
    )
    assert r.status_code == 303
    await db_session.refresh(s)
    assert s.threat_community.slug == "hacktivists" and s.threat_community_provenance == "migrated"


async def test_create_form_audits_threat_community_assignment(authed_analyst, db_session) -> None:
    """spec N4 (PR-gate r1): a scenario CREATE via the expert form audits the
    threat_community assignment with the same ``[None, slug]`` diff shape the
    confirm/edit paths use (services/scenarios.py:302), alongside the
    threat_community_provenance key (:303)."""
    from tests.routes.test_scenario_form_attack_mappings import _scenario_form_payload

    client, org_id = authed_analyst
    r = await csrf_post(
        client,
        "/scenarios",
        _scenario_form_payload(name="N4 audit scenario"),
        follow_redirects=False,
    )
    assert r.status_code == 303
    row = (
        (await db_session.execute(select(AuditLog).where(AuditLog.action == "scenario.create")))
        .scalars()
        .one()
    )
    assert row.changes["threat_community"] == [None, "cybercriminals"]
    assert row.changes["threat_community_provenance"] == [None, "assigned"]
