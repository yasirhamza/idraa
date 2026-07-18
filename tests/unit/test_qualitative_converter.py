"""Task 5 — QualitativeConverterService (epic #34 P1b).

Mirrors ``test_qualitative_bands_service.py`` style: local seed helpers
instead of fixtures, audit-row assertions via a direct ``AuditLog`` query.

Spec: docs/superpowers/specs/2026-07-18-qualitative-register-converter-design.md §3.
Plan: docs/superpowers/plans/2026-07-18-mapping-tables-converter-p1b.md Task 5
(+ the BINDING Task 5 plan-gate amendments).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest
from sqlalchemy import event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.audit_log import AuditLog
from idraa.models.enums import EntityStatus, ScenarioSource, ScenarioType, ThreatCategory
from idraa.models.organization import Organization
from idraa.models.qualitative_mapping import QualitativeMappingBand
from idraa.models.scenario import Scenario
from idraa.models.user import User
from idraa.services.qualitative_converter import (
    SL_NOTE,
    BoundRow,
    QualitativeConverterService,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _seed_band(
    db_session: AsyncSession,
    *,
    kind: str,
    label: str,
    low: float,
    mode: float,
    high: float,
    sort_order: int = 1,
) -> QualitativeMappingBand:
    band = QualitativeMappingBand(
        kind=kind,
        label=label,
        low=low,
        mode=mode,
        high=high,
        sort_order=sort_order,
        derivation="unit-test canonical band, not a real citation",
        version=1,
    )
    db_session.add(band)
    await db_session.flush()
    return band


async def _seed_bands(db_session: AsyncSession) -> None:
    """One frequency + one magnitude canonical band, labels used by tests."""
    await _seed_band(db_session, kind="frequency", label="moderate", low=1.0, mode=3.2, high=10.0)
    await _seed_band(
        db_session,
        kind="magnitude",
        label="high",
        low=1_000_000.0,
        mode=3_200_000.0,
        high=10_000_000.0,
    )


def _bound_row(
    *,
    source_row: int,
    title: str = "Phishing against finance",
    description: str | None = "Register-authored description.",
    owner: str | None = "Jane Analyst",
    likelihood_label: str = "moderate",
    magnitude_label: str = "high",
    category: ThreatCategory | None = ThreatCategory.SOCIAL_ENGINEERING,
    raw: dict[str, str] | None = None,
    carry_along: dict[str, str] | None = None,
) -> BoundRow:
    return BoundRow(
        source_row=source_row,
        title=title,
        description=description,
        owner=owner,
        likelihood_label=likelihood_label,
        magnitude_label=magnitude_label,
        category=category,
        raw=raw
        if raw is not None
        else {"likelihood": "Likely", "impact": "High", "category": "Phishing"},
        carry_along=carry_along if carry_along is not None else {},
    )


async def _audit_rows(db_session: AsyncSession, *, action: str) -> list[AuditLog]:
    rows = (
        (await db_session.execute(select(AuditLog).where(AuditLog.action == action)))
        .scalars()
        .all()
    )
    return list(rows)


SeedOrgUser = Callable[..., Awaitable[tuple[Organization, User]]]


# ---------------------------------------------------------------------------
# Happy path / adapter-iteration smoke (the homed N>=3 contract test lives
# in tests/contracts/test_qualitative_converter_iteration.py)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_three_row_happy_path_creates_three_drafts(
    db_session: AsyncSession, seed_org_user: SeedOrgUser
) -> None:
    org, user = await seed_org_user(db_session)
    await _seed_bands(db_session)

    rows = [_bound_row(source_row=i, title=f"Row {i}") for i in range(1, 4)]
    report = await QualitativeConverterService(db_session).convert(
        organization_id=org.id, user=user, source_file="register.xlsx", rows=rows
    )

    assert len(report.created) == 3
    assert report.parked == []
    assert report.skipped_duplicates == []
    assert report.errors == []
    assert report.sl_note == SL_NOTE

    scenarios = (
        (await db_session.execute(select(Scenario).where(Scenario.organization_id == org.id)))
        .scalars()
        .all()
    )
    assert len(scenarios) == 3
    for s in scenarios:
        assert s.status == EntityStatus.DRAFT
        assert s.vuln_framing == "legacy_residual"
        assert s.source == ScenarioSource.QUALITATIVE_REGISTER_IMPORT
        assert s.scenario_type == ScenarioType.CUSTOM
        assert s.threat_event_frequency == {
            "distribution": "PERT",
            "low": 1.0,
            "mode": 3.2,
            "high": 10.0,
        }
        assert s.vulnerability == {"distribution": "PERT", "low": 1.0, "mode": 1.0, "high": 1.0}
        assert s.primary_loss == {
            "distribution": "PERT",
            "low": 1_000_000.0,
            "mode": 3_200_000.0,
            "high": 10_000_000.0,
        }
        assert s.secondary_loss is None
        assert s.conversion_metadata is not None


# ---------------------------------------------------------------------------
# Parking (D5)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_parked_row_counted_not_errored_and_no_scenario_created(
    db_session: AsyncSession, seed_org_user: SeedOrgUser
) -> None:
    org, user = await seed_org_user(db_session)
    await _seed_bands(db_session)

    rows = [_bound_row(source_row=1, category=None)]
    report = await QualitativeConverterService(db_session).convert(
        organization_id=org.id, user=user, source_file="register.xlsx", rows=rows
    )

    assert report.parked == [1]
    assert report.created == []
    assert report.errors == []
    count = (
        (await db_session.execute(select(Scenario).where(Scenario.organization_id == org.id)))
        .scalars()
        .all()
    )
    assert count == []


# ---------------------------------------------------------------------------
# Dedup — both reasons, org-scoped (Sec-I1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dedup_name_reason_vs_all_statuses(
    db_session: AsyncSession, seed_org_user: SeedOrgUser
) -> None:
    org, user = await seed_org_user(db_session)
    await _seed_bands(db_session)
    # A pre-existing DRAFT scenario (not ACTIVE) must still be dedup-visible
    # (spec §3.1 — NOT the ACTIVE-only _existing_active_names precedent).
    db_session.add(
        Scenario(
            organization_id=org.id,
            name="Duplicate Title",
            threat_category=ThreatCategory.RANSOMWARE,
            threat_event_frequency={"distribution": "PERT", "low": 1, "mode": 2, "high": 3},
            vulnerability={"distribution": "PERT", "low": 1, "mode": 1, "high": 1},
            primary_loss={"distribution": "PERT", "low": 10, "mode": 20, "high": 30},
            status=EntityStatus.DRAFT,
        )
    )
    await db_session.flush()

    rows = [_bound_row(source_row=1, title="Duplicate Title")]
    report = await QualitativeConverterService(db_session).convert(
        organization_id=org.id, user=user, source_file="register.xlsx", rows=rows
    )

    assert report.created == []
    assert len(report.skipped_duplicates) == 1
    assert report.skipped_duplicates[0].reason == "name"
    assert report.skipped_duplicates[0].source_row == 1


@pytest.mark.asyncio
async def test_dedup_same_source_reason(
    db_session: AsyncSession, seed_org_user: SeedOrgUser
) -> None:
    org, user = await seed_org_user(db_session)
    await _seed_bands(db_session)

    rows = [_bound_row(source_row=7, title="First Import")]
    await QualitativeConverterService(db_session).convert(
        organization_id=org.id, user=user, source_file="register.xlsx", rows=rows
    )

    # Re-import: different title, same (source_file stem, source_row) — must
    # dedup on same_source even though the name-dedup check would pass.
    rows2 = [_bound_row(source_row=7, title="Renamed On Re-import")]
    report2 = await QualitativeConverterService(db_session).convert(
        organization_id=org.id, user=user, source_file="register.xlsx", rows=rows2
    )

    assert report2.created == []
    assert len(report2.skipped_duplicates) == 1
    assert report2.skipped_duplicates[0].reason == "same_source"


@pytest.mark.asyncio
async def test_dedup_is_org_scoped_not_global(
    db_session: AsyncSession,
    seed_org_user: SeedOrgUser,
) -> None:
    """Sec-I1: matching (source_file_stem, source_row) AND an identical name
    in a DIFFERENT org must NOT dedup."""
    org_a, user_a = await seed_org_user(db_session, org_name="Org A", email="a@example.com")
    org_b, user_b = await seed_org_user(db_session, org_name="Org B", email="b@example.com")
    await _seed_bands(db_session)

    rows = [_bound_row(source_row=3, title="Shared Title")]
    report_a = await QualitativeConverterService(db_session).convert(
        organization_id=org_a.id, user=user_a, source_file="register.xlsx", rows=rows
    )
    assert len(report_a.created) == 1

    rows_b = [_bound_row(source_row=3, title="Shared Title")]
    report_b = await QualitativeConverterService(db_session).convert(
        organization_id=org_b.id, user=user_b, source_file="register.xlsx", rows=rows_b
    )

    assert len(report_b.created) == 1
    assert report_b.skipped_duplicates == []


# ---------------------------------------------------------------------------
# Unknown-label RowError isolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unknown_label_row_error_does_not_abort_batch(
    db_session: AsyncSession, seed_org_user: SeedOrgUser
) -> None:
    org, user = await seed_org_user(db_session)
    await _seed_bands(db_session)

    rows = [
        _bound_row(source_row=1, title="Row 1"),
        _bound_row(source_row=2, title="Row 2 (bad label)", likelihood_label="not_a_real_label"),
        _bound_row(source_row=3, title="Row 3"),
    ]
    report = await QualitativeConverterService(db_session).convert(
        organization_id=org.id, user=user, source_file="register.xlsx", rows=rows
    )

    assert {c.title for c in report.created} == {"Row 1", "Row 3"}
    assert len(report.errors) == 1
    assert report.errors[0].source_row == 2
    assert "not_a_real_label" in report.errors[0].message


# ---------------------------------------------------------------------------
# Provenance block + conversion_metadata shape
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_provenance_block_content(
    db_session: AsyncSession, seed_org_user: SeedOrgUser
) -> None:
    org, user = await seed_org_user(db_session)
    await _seed_bands(db_session)

    row = _bound_row(
        source_row=5,
        title="Prov Row",
        description="Original analyst text.",
        owner="Owner McOwnerson",
        raw={"likelihood": "Likely", "impact": "High", "category": "Phishing"},
        carry_along={"business_unit": "Finance"},
    )
    report = await QualitativeConverterService(db_session).convert(
        organization_id=org.id, user=user, source_file="q3_register.xlsx", rows=[row]
    )
    assert len(report.created) == 1
    scenario = (
        await db_session.execute(
            select(Scenario).where(Scenario.id == report.created[0].scenario_id)
        )
    ).scalar_one()

    assert "Original analyst text." in scenario.description
    assert "--- Register provenance ---" in scenario.description
    assert "Owner McOwnerson" in scenario.description
    assert "Likely" in scenario.description
    assert "High" in scenario.description
    assert "Phishing" in scenario.description
    assert "business_unit=Finance" in scenario.description
    assert "q3_register.xlsx" in scenario.description
    assert "row 5" in scenario.description


@pytest.mark.asyncio
async def test_conversion_metadata_pinned_shape_and_versions(
    db_session: AsyncSession, seed_org_user: SeedOrgUser
) -> None:
    org, user = await seed_org_user(db_session)
    await _seed_bands(db_session)

    row = _bound_row(source_row=9, likelihood_label="moderate", magnitude_label="high")
    report = await QualitativeConverterService(db_session).convert(
        organization_id=org.id, user=user, source_file="register.xlsx", rows=[row]
    )
    scenario = (
        await db_session.execute(
            select(Scenario).where(Scenario.id == report.created[0].scenario_id)
        )
    ).scalar_one()

    cm = scenario.conversion_metadata
    assert cm["source_file"] == "register.xlsx"
    assert cm["source_row"] == 9
    assert cm["raw"] == {"likelihood": "Likely", "impact": "High", "category": "Phishing"}
    assert cm["bindings"] == {
        "likelihood_label": "moderate",
        "magnitude_label": "high",
        "category": ThreatCategory.SOCIAL_ENGINEERING.value,
    }
    assert cm["mapping_versions"] == {"canonical": 1, "org": {}}
    assert cm["binding_profile_id"] is None
    assert cm.get("converted_at")


# ---------------------------------------------------------------------------
# Batch audit row
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_batch_audit_row_written_once(
    db_session: AsyncSession, seed_org_user: SeedOrgUser
) -> None:
    org, user = await seed_org_user(db_session)
    await _seed_bands(db_session)

    rows = [
        _bound_row(source_row=1, title="Created Row"),
        _bound_row(source_row=2, title="Parked Row", category=None),
        _bound_row(source_row=3, title="Bad Row", likelihood_label="nope"),
    ]
    report = await QualitativeConverterService(db_session).convert(
        organization_id=org.id, user=user, source_file="register.xlsx", rows=rows
    )

    audits = await _audit_rows(db_session, action="scenario.convert_qualitative")
    assert len(audits) == 1
    row = audits[0]
    assert row.organization_id == org.id
    assert row.entity_type == "scenario"
    assert row.entity_id == org.id
    assert row.user_id == user.id
    assert row.changes["created"] == [str(c.scenario_id) for c in report.created]
    assert row.changes["parked"] == 1
    assert row.changes["skipped"] == 0
    assert row.changes["errors"] == 1
    assert row.changes["source_file"] == "register.xlsx"
    assert row.changes["vuln_framing"] == "legacy_residual"
    assert row.changes["conversion_metadata"] == "set"


# ---------------------------------------------------------------------------
# Sec-I3 input bounds — fail-closed, never truncate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_raw_value_over_bound_row_errors_siblings_created(
    db_session: AsyncSession, seed_org_user: SeedOrgUser
) -> None:
    org, user = await seed_org_user(db_session)
    await _seed_bands(db_session)

    rows = [
        _bound_row(source_row=1, title="Row 1"),
        _bound_row(
            source_row=2,
            title="Row 2 (oversized raw)",
            raw={"likelihood": "x" * 2001, "impact": "High", "category": "Phishing"},
        ),
        _bound_row(source_row=3, title="Row 3"),
    ]
    report = await QualitativeConverterService(db_session).convert(
        organization_id=org.id, user=user, source_file="register.xlsx", rows=rows
    )

    assert {c.title for c in report.created} == {"Row 1", "Row 3"}
    assert len(report.errors) == 1
    assert report.errors[0].source_row == 2


@pytest.mark.asyncio
async def test_carry_along_over_20_keys_row_errors(
    db_session: AsyncSession, seed_org_user: SeedOrgUser
) -> None:
    org, user = await seed_org_user(db_session)
    await _seed_bands(db_session)

    rows = [
        _bound_row(
            source_row=1,
            title="Too Many Carried Columns",
            carry_along={f"col_{i}": "v" for i in range(21)},
        )
    ]
    report = await QualitativeConverterService(db_session).convert(
        organization_id=org.id, user=user, source_file="register.xlsx", rows=rows
    )
    assert report.created == []
    assert len(report.errors) == 1


@pytest.mark.asyncio
async def test_raw_missing_a_key_row_errors(
    db_session: AsyncSession, seed_org_user: SeedOrgUser
) -> None:
    org, user = await seed_org_user(db_session)
    await _seed_bands(db_session)

    rows = [_bound_row(source_row=1, raw={"likelihood": "Likely", "impact": "High"})]
    report = await QualitativeConverterService(db_session).convert(
        organization_id=org.id, user=user, source_file="register.xlsx", rows=rows
    )
    assert report.created == []
    assert len(report.errors) == 1


@pytest.mark.asyncio
async def test_legal_but_large_carry_along_busts_description_cap_row_errors(
    db_session: AsyncSession, seed_org_user: SeedOrgUser
) -> None:
    """Arch-I4: carry_along values within the 2000-char/20-key bound can
    still compose (with the rest of the provenance block) into a
    description > ScenarioForm's 4000-char cap — that must RowError, never
    silently truncate, and must not abort sibling rows."""
    org, user = await seed_org_user(db_session)
    await _seed_bands(db_session)

    big_carry_along = {
        "note_a": "x" * 1800,
        "note_b": "y" * 1800,
        "note_c": "z" * 1800,
    }
    rows = [
        _bound_row(source_row=1, title="Row 1"),
        _bound_row(
            source_row=2,
            title="Row 2 (busts description cap)",
            carry_along=big_carry_along,
        ),
        _bound_row(source_row=3, title="Row 3"),
    ]
    report = await QualitativeConverterService(db_session).convert(
        organization_id=org.id, user=user, source_file="register.xlsx", rows=rows
    )

    assert {c.title for c in report.created} == {"Row 1", "Row 3"}
    assert len(report.errors) == 1
    assert report.errors[0].source_row == 2


# ---------------------------------------------------------------------------
# Poison-path: a post-flush DB failure on one row must not abort the batch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_poison_row_post_flush_failure_isolated(
    db_session: AsyncSession, seed_org_user: SeedOrgUser
) -> None:
    org, user = await seed_org_user(db_session)
    await _seed_bands(db_session)

    poison_title = "Poison Row"

    def _boom(mapper: object, connection: object, target: Scenario) -> None:
        if target.name == poison_title and target.organization_id == org.id:
            raise IntegrityError("forced", {}, Exception("forced failure"))

    event.listen(Scenario, "before_insert", _boom)
    try:
        rows = [
            _bound_row(source_row=1, title="Row 1"),
            _bound_row(source_row=2, title=poison_title),
            _bound_row(source_row=3, title="Row 3"),
        ]
        report = await QualitativeConverterService(db_session).convert(
            organization_id=org.id, user=user, source_file="register.xlsx", rows=rows
        )
    finally:
        event.remove(Scenario, "before_insert", _boom)

    assert {c.title for c in report.created} == {"Row 1", "Row 3"}
    assert len(report.errors) == 1
    assert report.errors[0].source_row == 2

    scenarios = (
        (await db_session.execute(select(Scenario).where(Scenario.organization_id == org.id)))
        .scalars()
        .all()
    )
    assert {s.name for s in scenarios} == {"Row 1", "Row 3"}


# ---------------------------------------------------------------------------
# Misc identity checks not covered above
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario_type_is_custom_explicitly(
    db_session: AsyncSession, seed_org_user: SeedOrgUser
) -> None:
    org, user = await seed_org_user(db_session)
    await _seed_bands(db_session)

    report = await QualitativeConverterService(db_session).convert(
        organization_id=org.id,
        user=user,
        source_file="register.xlsx",
        rows=[_bound_row(source_row=1)],
    )
    scenario = (
        await db_session.execute(
            select(Scenario).where(Scenario.id == report.created[0].scenario_id)
        )
    ).scalar_one()
    assert scenario.scenario_type == ScenarioType.CUSTOM
