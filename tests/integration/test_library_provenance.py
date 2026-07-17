"""P3 Task 6 — provenance UI (badge + source filter) + guarded delete-imported.

Covers:
- Browse badge: imported entries render an "Imported" badge; seed entries do
  not (they render "Built-in").
- Source filter: ``?source=imported`` / ``?source=seed`` narrow the listing;
  no filter returns both. count_published mirrors list_published (parallel
  maintenance contract, SC-I2).
- Delete-imported (Option B + guards):
  - admin deletes an imported entry -> all rows for the logical id gone +
    a ``library_bundle.delete`` audit row (admin user_id + ip + slug).
  - delete of a seed entry -> 403/refused (Arch-I2 per-row guard).
  - delete refused when a ScenarioLibraryOverride references the entry —
    INCLUDING a tombstoned (deleted_at set) override (Arch-I1).
  - Option B: delete of an imported entry a scenario is pinned to SUCCEEDS;
    warning notes the pinned-scenario count; the pinned scenario survives.
  - analyst/viewer cannot delete (403).
  - CSRF-negative on the delete POST.
- Meth-I1: a scenario cloned from an imported entry records
  ``library_source_provenance == [None, "imported"]`` in its scenario.create
  audit changes (keeps imported-origin scenarios traceable post-delete).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.audit_log import AuditLog
from idraa.models.enums import AssetClass, ThreatActorType, ThreatCategory
from idraa.models.scenario import Scenario
from idraa.models.scenario_library import (
    ScenarioLibraryEntry,
    ScenarioLibraryOverride,
)
from tests.conftest import csrf_post


def _entry_kwargs(*, slug: str, name: str, source: str) -> dict[str, Any]:
    return {
        "id": uuid.uuid4(),
        "version": 1,
        "slug": slug,
        "name": name,
        "status": "published",
        "source": source,
        "threat_event_type": ThreatCategory.RANSOMWARE,
        "threat_actor_type": ThreatActorType.CYBERCRIMINALS,
        "asset_class": AssetClass.SYSTEMS,
        "tags": [],
        "description": "A provenance-test entry. " * 4,
        "canonical_fair_gap": "g" * 25,
        "source_citations": [],
        "threat_event_frequency": {"distribution": "PERT", "low": 1.0, "mode": 4.0, "high": 12.0},
        "vulnerability": {"distribution": "PERT", "low": 0.05, "mode": 0.20, "high": 0.50},
        "primary_loss": {
            "distribution": "PERT",
            "low": 100_000.0,
            "mode": 1_000_000.0,
            "high": 10_000_000.0,
        },
        "secondary_loss": None,
        "suggested_control_ids": [],
        "calibration_anchor": {"industry": "other", "revenue_tier": "100m_to_1b"},
    }


async def _add_entry(
    db: AsyncSession, *, slug: str, name: str, source: str
) -> ScenarioLibraryEntry:
    entry = ScenarioLibraryEntry(**_entry_kwargs(slug=slug, name=name, source=source))
    db.add(entry)
    await db.commit()
    await db.refresh(entry)
    return entry


# --------------------------------------------------------------------------
# Browse badge
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_browse_shows_imported_badge(
    admin_client: AsyncClient, db_session: AsyncSession
) -> None:
    await _add_entry(db_session, slug="prov-imp", name="ProvImported", source="imported")
    r = await admin_client.get("/library")
    assert r.status_code == 200
    assert "ProvImported" in r.text
    assert "Imported" in r.text


@pytest.mark.asyncio
async def test_browse_seed_entry_no_imported_badge(
    admin_client: AsyncClient, db_session: AsyncSession
) -> None:
    await _add_entry(db_session, slug="prov-seed", name="ProvSeedOnly", source="seed")
    r = await admin_client.get("/library")
    assert r.status_code == 200
    assert "ProvSeedOnly" in r.text
    # Seed entries show "Built-in", not "Imported", for their provenance.
    assert "Built-in" in r.text


# --------------------------------------------------------------------------
# Source filter (+ count/list sync, SC-I2)
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_source_filter_imported_only(
    admin_client: AsyncClient, db_session: AsyncSession
) -> None:
    await _add_entry(db_session, slug="sf-imp", name="SFImportedOne", source="imported")
    await _add_entry(db_session, slug="sf-seed", name="SFSeedOne", source="seed")

    r = await admin_client.get("/library?source=imported")
    assert r.status_code == 200
    assert "SFImportedOne" in r.text
    assert "SFSeedOne" not in r.text


@pytest.mark.asyncio
async def test_source_filter_seed_only(admin_client: AsyncClient, db_session: AsyncSession) -> None:
    await _add_entry(db_session, slug="sf2-imp", name="SF2Imported", source="imported")
    await _add_entry(db_session, slug="sf2-seed", name="SF2Seed", source="seed")

    r = await admin_client.get("/library?source=seed")
    assert r.status_code == 200
    assert "SF2Seed" in r.text
    assert "SF2Imported" not in r.text


@pytest.mark.asyncio
async def test_no_source_filter_returns_both(
    admin_client: AsyncClient, db_session: AsyncSession
) -> None:
    await _add_entry(db_session, slug="sf3-imp", name="SF3Imported", source="imported")
    await _add_entry(db_session, slug="sf3-seed", name="SF3Seed", source="seed")

    r = await admin_client.get("/library")
    assert r.status_code == 200
    assert "SF3Imported" in r.text
    assert "SF3Seed" in r.text


@pytest.mark.asyncio
async def test_count_published_respects_source_filter(db_session: AsyncSession) -> None:
    """SC-I2: count_published must apply the source filter in lock-step with
    list_published so the badge/pagination count never desyncs from the list.
    """
    from idraa.repositories.scenario_library_repo import ScenarioLibraryRepo

    await _add_entry(db_session, slug="cnt-imp1", name="CntImp1", source="imported")
    await _add_entry(db_session, slug="cnt-imp2", name="CntImp2", source="imported")
    await _add_entry(db_session, slug="cnt-seed1", name="CntSeed1", source="seed")

    repo = ScenarioLibraryRepo(db_session)
    imp_rows = await repo.list_published(source="imported")
    imp_count = await repo.count_published(source="imported")
    assert imp_count == len(imp_rows) == 2

    seed_rows = await repo.list_published(source="seed")
    seed_count = await repo.count_published(source="seed")
    assert seed_count == len(seed_rows) == 1


# --------------------------------------------------------------------------
# Delete-imported (Option B + guards)
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_admin_deletes_imported_entry(
    admin_client: AsyncClient, db_session: AsyncSession
) -> None:
    entry = await _add_entry(db_session, slug="del-imp", name="DelImported", source="imported")
    entry_id = entry.id

    r = await csrf_post(
        admin_client,
        f"/library/entries/{entry_id}/delete",
        {},
        follow_redirects=False,
    )
    assert r.status_code in (200, 303)

    db_session.expire_all()
    remaining = (
        await db_session.execute(
            select(func.count())
            .select_from(ScenarioLibraryEntry)
            .where(ScenarioLibraryEntry.id == entry_id)
        )
    ).scalar_one()
    assert remaining == 0, "all rows for the logical id must be gone"

    audit = (
        await db_session.execute(select(AuditLog).where(AuditLog.action == "library_bundle.delete"))
    ).scalar_one()
    assert audit.user_id is not None
    assert audit.ip_address is not None
    # The deleted slug is recorded for forensic traceability.
    flat = str(audit.changes)
    assert "del-imp" in flat


@pytest.mark.asyncio
async def test_delete_seed_entry_refused(
    admin_client: AsyncClient, db_session: AsyncSession
) -> None:
    entry = await _add_entry(db_session, slug="del-seed", name="DelSeed", source="seed")
    entry_id = entry.id

    r = await csrf_post(
        admin_client,
        f"/library/entries/{entry_id}/delete",
        {},
        follow_redirects=False,
    )
    assert r.status_code == 403

    db_session.expire_all()
    still = await db_session.get(ScenarioLibraryEntry, (entry_id, 1))
    assert still is not None, "seed entry must remain after refused delete"


@pytest.mark.asyncio
async def test_delete_refused_when_live_override_references_entry(
    admin_client: AsyncClient, db_session: AsyncSession
) -> None:
    from tests.factories import create_org

    entry = await _add_entry(db_session, slug="del-ov-live", name="DelOvLive", source="imported")
    org = await create_org(db_session, name="Override Org Live")
    override = ScenarioLibraryOverride(
        organization_id=org.id,
        library_entry_id=entry.id,
        library_entry_version=entry.version,
        threat_event_frequency=None,
        vulnerability=None,
        primary_loss=None,
        secondary_loss=None,
        reason="test override",
        version=1,
        row_version=1,
    )
    db_session.add(override)
    await db_session.commit()

    r = await csrf_post(
        admin_client,
        f"/library/entries/{entry.id}/delete",
        {},
        follow_redirects=False,
    )
    assert r.status_code == 403

    # Entry survives the refused delete — verify via HTTP export (avoids the
    # two-engines-one-sqlite-file read-back fragility).
    export = await admin_client.get("/library/export.csv")
    assert "DelOvLive" in export.text


@pytest.mark.asyncio
async def test_delete_refused_when_tombstoned_override_references_entry(
    admin_client: AsyncClient, db_session: AsyncSession
) -> None:
    """Arch-I1: a soft-deleted (deleted_at set) override still holds the
    composite FK, so the delete MUST refuse — else IntegrityError.
    """
    from tests.factories import create_org

    entry = await _add_entry(db_session, slug="del-ov-tomb", name="DelOvTomb", source="imported")
    org = await create_org(db_session, name="Override Org Tomb")
    override = ScenarioLibraryOverride(
        organization_id=org.id,
        library_entry_id=entry.id,
        library_entry_version=entry.version,
        threat_event_frequency=None,
        vulnerability=None,
        primary_loss=None,
        secondary_loss=None,
        reason="tombstoned override",
        version=1,
        row_version=1,
        deleted_at=datetime.now(UTC),
    )
    db_session.add(override)
    await db_session.commit()

    r = await csrf_post(
        admin_client,
        f"/library/entries/{entry.id}/delete",
        {},
        follow_redirects=False,
    )
    assert r.status_code == 403

    # Tombstoned-override refusal still leaves the entry intact.
    export = await admin_client.get("/library/export.csv")
    assert "DelOvTomb" in export.text


@pytest.mark.asyncio
async def test_option_b_delete_succeeds_with_pinned_scenario(
    admin_client: AsyncClient,
    authed_admin: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
    db_url: str,
) -> None:
    """Option B (final): deleting an imported entry a scenario was cloned from
    SUCCEEDS. The pinned scenario survives unchanged; the response notes the
    pinned-scenario count.
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    _client, org_id = authed_admin
    entry = await _add_entry(db_session, slug="del-pinned", name="DelPinned", source="imported")

    # A scenario pinned to this entry (library_pin.entry_id is the hyphenated
    # str(entry.id), matching resolve_for_clone's pin construction).
    scenario = Scenario(
        organization_id=org_id,
        name="Pinned Scenario",
        threat_category=ThreatCategory.RANSOMWARE,
        threat_event_frequency={"distribution": "PERT", "low": 1.0, "mode": 4.0, "high": 12.0},
        vulnerability={"distribution": "PERT", "low": 0.05, "mode": 0.20, "high": 0.50},
        primary_loss={"distribution": "PERT", "low": 1.0, "mode": 2.0, "high": 3.0},
        secondary_loss=None,
        library_pin={"entry_id": str(entry.id), "version": entry.version},
        row_version=1,
    )
    db_session.add(scenario)
    await db_session.commit()
    scen_id = scenario.id

    r = await csrf_post(
        admin_client,
        f"/library/entries/{entry.id}/delete",
        {},
        follow_redirects=True,
    )
    assert r.status_code == 200
    # The warning surfaces the pinned-scenario count.
    assert "1" in r.text

    # Verify post-delete state through a FRESH engine/session on the same
    # SQLite file — the test's own ``db_session`` is a second engine left
    # mid-transaction by the HTTP write, and re-reading it raises MissingGreenlet.
    engine = create_async_engine(db_url, future=True)
    try:
        sm = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with sm() as fresh:
            gone = (
                await fresh.execute(
                    select(func.count())
                    .select_from(ScenarioLibraryEntry)
                    .where(ScenarioLibraryEntry.id == entry.id)
                )
            ).scalar_one()
            assert gone == 0, "imported entry must be deleted (Option B does not block on pins)"

            survivor = await fresh.get(Scenario, scen_id)
            assert survivor is not None, "the pinned scenario must survive entry deletion"
            assert survivor.library_pin == {
                "entry_id": str(entry.id),
                "version": entry.version,
            }

            audit = (
                await fresh.execute(
                    select(AuditLog).where(AuditLog.action == "library_bundle.delete")
                )
            ).scalar_one()
            assert "1" in str(audit.changes)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_delete_analyst_forbidden(
    analyst_client: AsyncClient, db_session: AsyncSession
) -> None:
    entry = await _add_entry(db_session, slug="del-rbac-an", name="DelRbacAn", source="imported")
    r = await csrf_post(
        analyst_client,
        f"/library/entries/{entry.id}/delete",
        {},
        follow_redirects=False,
    )
    # require_role(ADMIN) rejects before the service runs — no DB mutation.
    assert r.status_code in (403, 302)


@pytest.mark.asyncio
async def test_delete_viewer_forbidden(
    viewer_client: AsyncClient, db_session: AsyncSession
) -> None:
    entry = await _add_entry(db_session, slug="del-rbac-vw", name="DelRbacVw", source="imported")
    r = await csrf_post(
        viewer_client,
        f"/library/entries/{entry.id}/delete",
        {},
        follow_redirects=False,
    )
    assert r.status_code in (403, 302)


@pytest.mark.asyncio
async def test_delete_without_csrf_rejected(
    admin_client: AsyncClient, db_session: AsyncSession
) -> None:
    entry = await _add_entry(db_session, slug="del-csrf", name="DelCsrf", source="imported")
    r = await admin_client.post(f"/library/entries/{entry.id}/delete", data={})
    assert r.status_code in (403, 422)

    # No-CSRF delete must not mutate — verify via the HTTP export (avoids the
    # two-engines-one-sqlite-file read-back fragility).
    export = await admin_client.get("/library/export.csv")
    assert "DelCsrf" in export.text


@pytest.mark.asyncio
async def test_delete_unknown_entry_404(admin_client: AsyncClient) -> None:
    r = await csrf_post(
        admin_client,
        f"/library/entries/{uuid.uuid4()}/delete",
        {},
        follow_redirects=False,
    )
    assert r.status_code == 404


# --------------------------------------------------------------------------
# Meth-I1: clone-provenance audit
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_clone_from_imported_records_source_provenance(
    db_session: AsyncSession,
) -> None:
    """Meth-I1: cloning a scenario from an imported library entry records the
    entry's source in the scenario.create audit changes so the scenario stays
    traceable even after the source entry is deleted (Option B safety).
    """
    from idraa.models.enums import EntityStatus, ScenarioSource
    from idraa.schemas.scenario import ScenarioForm
    from idraa.services.scenarios import ScenarioService
    from tests.factories import create_org, create_user

    org = await create_org(db_session, name="Meth-I1 Org")
    user = await create_user(db_session, org, email="methi1@test.local")

    entry = await _add_entry(db_session, slug="methi1-imp", name="MethI1Imp", source="imported")

    form = ScenarioForm(
        name="Cloned from imported",
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
        organization_id=org.id,
        form=form,
        current_user=user,
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
    assert "library_source_provenance" in audit.changes
    assert audit.changes["library_source_provenance"] == [None, "imported"]
