"""Scenario-library bundle import — two-step staging + apply (Task 3).

Covers ``validate_upload`` (parse + validate + stage under a 10-min token) and
``apply_validated_preview`` (re-parse the staged bytes + insert the non-dup
valid entries as ``source='imported'`` published rows + one summary
``library_bundle.import`` audit row).

Invariants asserted here:

- happy path: validate → apply inserts the entry, stamps ``source='imported'``,
  ``status='published'``, ``version=1``, consumes the preview row.
- re-import of the same bundle skips the duplicate slug (add-only + skip guard).
- an existing (seed-source) slug → preview action "skip".
- expired token → ``PreviewExpiredError`` + the preview row is deleted.
- cross-org token → uniform ``PreviewExpiredError`` (no existence oracle).
- summary audit-row shape ``{imported, skipped, errors_count}`` attributed to
  the importing admin.

The repo provides ``db_session`` / ``organization`` / ``admin_user`` fixtures
(tests/conftest.py); there is no ``other_org`` fixture, so this module defines
one inline via ``tests.factories.create_org``. The test DB is built via
``Base.metadata.create_all`` (NOT Alembic), so it is NOT pre-seeded with the 44
library entries — ``test_existing_seed_slug_is_skipped`` inserts a seed-source
row itself before asserting the dup slug skips.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import func, select

from idraa.models.csv_import_preview import CSVImportPreview
from idraa.models.enums import AssetClass, ThreatActorType, ThreatCategory
from idraa.models.organization import Organization
from idraa.models.scenario_library import ScenarioLibraryEntry
from idraa.services.library_bundle_import import (
    PreviewExpiredError,
    apply_validated_preview,
    validate_upload,
)

_BUNDLE = json.dumps(
    [
        {
            "slug": "imp-a",
            "name": "Imp A",
            "status": "published",
            "threat_event_type": "ransomware",
            "threat_actor_type": "cybercriminals",
            "asset_class": "systems",
            "description": "d" * 25,
            "canonical_fair_gap": "g" * 25,
            "threat_event_frequency": {"distribution": "PERT", "low": 1, "mode": 2, "high": 3},
            "vulnerability": {"distribution": "PERT", "low": 0.1, "mode": 0.2, "high": 0.3},
            "primary_loss": {"distribution": "PERT", "low": 1, "mode": 2, "high": 3},
            "calibration_anchor": {"industry": "other", "revenue_tier": "100m_to_1b"},
        },
    ]
).encode()


@pytest_asyncio.fixture
async def other_org(db_session) -> Organization:
    """A second org for the cross-org-token rejection test."""
    from tests.factories import create_org

    return await create_org(db_session, name="Other Org P3")


@pytest.mark.asyncio
async def test_validate_then_apply_inserts_imported(db_session, organization, admin_user) -> None:
    token, preview, errors = await validate_upload(
        db_session, org_id=organization.id, user_id=admin_user.id, data=_BUNDLE
    )
    assert errors == []
    assert [p["action"] for p in preview] == ["add"]
    imported, skipped, apply_errors = await apply_validated_preview(
        db_session, token=token, org_id=organization.id, user=admin_user, ip_address="1.2.3.4"
    )
    assert (imported, skipped, apply_errors) == (1, 0, [])
    row = (
        await db_session.execute(
            select(ScenarioLibraryEntry).where(ScenarioLibraryEntry.slug == "imp-a")
        )
    ).scalar_one()
    assert row.source == "imported"
    assert row.status == "published"
    assert row.version == 1
    assert row.row_version == 1
    remaining = (
        await db_session.execute(select(func.count()).select_from(CSVImportPreview))
    ).scalar_one()
    assert remaining == 0


@pytest.mark.asyncio
async def test_reimport_skips_existing_slug(db_session, organization, admin_user) -> None:
    imported = skipped = -1
    for _ in range(2):
        token, _p, _e = await validate_upload(
            db_session, org_id=organization.id, user_id=admin_user.id, data=_BUNDLE
        )
        imported, skipped, _ = await apply_validated_preview(
            db_session, token=token, org_id=organization.id, user=admin_user
        )
    assert (imported, skipped) == (0, 1)


@pytest.mark.asyncio
async def test_existing_seed_slug_is_skipped(db_session, organization, admin_user) -> None:
    # The test DB is NOT pre-seeded with the 44 library entries (schema built via
    # Base.metadata.create_all, not Alembic), so insert a seed-source entry
    # first, then assert the bundle's dup slug previews as "skip".
    seed = ScenarioLibraryEntry(
        id=uuid.uuid4(),
        version=1,
        slug="seed-slug-dup",
        name="Seed Dup",
        status="published",
        source="seed",
        threat_event_type=ThreatCategory.RANSOMWARE,
        threat_actor_type=ThreatActorType.CYBERCRIMINALS,
        asset_class=AssetClass.SYSTEMS,
        tags=[],
        description="A seed entry to collide against.",
        canonical_fair_gap="Seed entry FAIR gap placeholder.",
        source_citations=[],
        threat_event_frequency={"distribution": "PERT", "low": 1.0, "mode": 2.0, "high": 3.0},
        vulnerability={"distribution": "PERT", "low": 0.1, "mode": 0.2, "high": 0.3},
        primary_loss={"distribution": "PERT", "low": 1.0, "mode": 2.0, "high": 3.0},
        suggested_control_ids=[],
    )
    db_session.add(seed)
    await db_session.flush()

    bundle = json.dumps([{**json.loads(_BUNDLE)[0], "slug": "seed-slug-dup"}]).encode()
    token, preview, _e = await validate_upload(
        db_session, org_id=organization.id, user_id=admin_user.id, data=bundle
    )
    assert preview[0]["action"] == "skip"


@pytest.mark.asyncio
async def test_expired_token_rejected(db_session, organization, admin_user) -> None:
    token, _p, _e = await validate_upload(
        db_session, org_id=organization.id, user_id=admin_user.id, data=_BUNDLE
    )
    row = (
        await db_session.execute(
            select(CSVImportPreview).where(CSVImportPreview.id == uuid.UUID(token))
        )
    ).scalar_one()
    # The CHECK constraint enforces ``expires_at > created_at``; back-date both.
    row.created_at = datetime.now(UTC) - timedelta(hours=1)
    row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await db_session.flush()
    with pytest.raises(PreviewExpiredError):
        await apply_validated_preview(
            db_session, token=token, org_id=organization.id, user=admin_user
        )
    remaining = (
        await db_session.execute(select(func.count()).select_from(CSVImportPreview))
    ).scalar_one()
    assert remaining == 0


@pytest.mark.asyncio
async def test_cross_org_token_rejected(db_session, organization, admin_user, other_org) -> None:
    token, _p, _e = await validate_upload(
        db_session, org_id=organization.id, user_id=admin_user.id, data=_BUNDLE
    )
    with pytest.raises(PreviewExpiredError):
        await apply_validated_preview(db_session, token=token, org_id=other_org.id, user=admin_user)


@pytest.mark.asyncio
async def test_summary_audit_written(db_session, organization, admin_user) -> None:
    from idraa.models.audit_log import AuditLog

    token, _p, _e = await validate_upload(
        db_session, org_id=organization.id, user_id=admin_user.id, data=_BUNDLE
    )
    await apply_validated_preview(db_session, token=token, org_id=organization.id, user=admin_user)
    rows = (await db_session.execute(select(AuditLog))).scalars().all()
    summ = [r for r in rows if r.action == "library_bundle.import"]
    assert len(summ) == 1
    assert set(summ[0].changes.keys()) == {"imported", "skipped", "errors_count"}
    assert summ[0].user_id == admin_user.id


# ---------------------------------------------------------------------------
# Task 4: loss_tier round-trip (Epic C-i #335)
# ---------------------------------------------------------------------------

_BUNDLE_VENDOR_TIER = json.dumps(
    [
        {
            "slug": "lt-vendor-rt",
            "name": "Loss Tier Vendor Round-Trip",
            "status": "published",
            "threat_event_type": "ransomware",
            "threat_actor_type": "cybercriminals",
            "asset_class": "systems",
            "description": "A 25+ character description for the loss_tier round-trip test.",
            "canonical_fair_gap": "A 25+ character canonical FAIR gap for the loss_tier test.",
            "threat_event_frequency": {"distribution": "PERT", "low": 1, "mode": 2, "high": 3},
            "vulnerability": {"distribution": "PERT", "low": 0.1, "mode": 0.2, "high": 0.3},
            "primary_loss": {
                "distribution": "PERT",
                "low": 100000,
                "mode": 1000000,
                "high": 5000000,
            },
            "calibration_anchor": {"industry": "other", "revenue_tier": "100m_to_1b"},
            "loss_tier": "vendor",
        },
    ]
).encode()


@pytest.mark.asyncio
async def test_loss_tier_round_trips_through_bundle(db_session, organization, admin_user) -> None:
    """loss_tier='vendor' in bundle export must survive apply and be readable on the ORM row.

    Asserts loss_tier on the FETCHED ORM ROW (not just the in-memory seed) —
    this is the safety net that catches the import-write gap: if _INSERT_LIBRARY_ENTRY
    omits the loss_tier column, the DB falls back to server_default='anecdotal' and
    this assertion fails even though the in-memory seed would show 'vendor'.
    """
    # Step 1: validate upload (must parse cleanly — loss_tier is a seed field).
    token, preview, errors = await validate_upload(
        db_session,
        org_id=organization.id,
        user_id=admin_user.id,
        data=_BUNDLE_VENDOR_TIER,
    )
    assert errors == [], f"unexpected validation errors: {errors}"
    assert [p["action"] for p in preview] == ["add"]

    # Step 2: apply — inserts the entry.
    imported, skipped, apply_errors = await apply_validated_preview(
        db_session,
        token=token,
        org_id=organization.id,
        user=admin_user,
        ip_address="1.2.3.4",
    )
    assert (imported, skipped, apply_errors) == (1, 0, [])

    # Step 3: fetch the ORM row and assert loss_tier on the *persisted* value,
    # not the in-memory seed dict — this is the gap the plan-gate arch-#1 safety
    # net exists to catch.
    row = (
        await db_session.execute(
            select(ScenarioLibraryEntry).where(ScenarioLibraryEntry.slug == "lt-vendor-rt")
        )
    ).scalar_one()
    assert row.loss_tier == "vendor", (
        f"loss_tier was not written to the DB: got {row.loss_tier!r}. "
        "Check that _INSERT_LIBRARY_ENTRY includes loss_tier and _insert_params maps it."
    )
