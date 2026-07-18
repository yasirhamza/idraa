"""Model-shape tests for register-import staging state (epic #34 P1c Task 2).

Covers two schema additions:
- ``CSVImportPreview.state_json`` — accumulating step-choice storage for the
  staged register-import flow (register-import-only today; other
  ``entity_type`` rows leave it NULL).
- ``RegisterBindingProfile`` — a saved (column_map, value_bindings) binding
  profile per org, with a ``mapping_versions_snapshot`` for drift detection
  in Task 3's ``apply_profile``.
"""

from __future__ import annotations

import datetime
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.csv_import_preview import PREVIEW_TTL_SECONDS, CSVImportPreview
from idraa.models.organization import Organization
from idraa.models.register_binding_profile import RegisterBindingProfile
from idraa.models.user import User


def _future_expiry() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC) + datetime.timedelta(seconds=PREVIEW_TTL_SECONDS)


@pytest.mark.asyncio
async def test_csv_import_preview_state_json_defaults_to_none(
    db_session: AsyncSession, seed_organization: Organization
) -> None:
    """A row created without state_json (e.g. an existing overlay/override
    importer row) stores NULL — the column is register-import-only today."""
    row = CSVImportPreview(
        organization_id=seed_organization.id,
        entity_type="overlay",
        csv_bytes=b"a,b\n1,2\n",
        expires_at=_future_expiry(),
    )
    db_session.add(row)
    await db_session.commit()
    await db_session.refresh(row)
    assert row.state_json is None


@pytest.mark.asyncio
async def test_csv_import_preview_state_json_roundtrips_dict(
    db_session: AsyncSession, seed_organization: Organization
) -> None:
    """A register-import row's state_json round-trips a nested dict."""
    row = CSVImportPreview(
        organization_id=seed_organization.id,
        entity_type="register:xlsx",
        csv_bytes=b"\x00binary",
        expires_at=_future_expiry(),
        state_json={"filename": "register.xlsx", "sheet_name": "Sheet1"},
    )
    db_session.add(row)
    await db_session.commit()
    await db_session.refresh(row)
    assert row.state_json == {"filename": "register.xlsx", "sheet_name": "Sheet1"}


@pytest.mark.asyncio
async def test_csv_import_preview_state_json_whole_dict_reassignment(
    db_session: AsyncSession, seed_organization: Organization
) -> None:
    """Regression guard for the Arch-I1 write rule documented on the model:
    a plain ``JSON`` column does not track in-place mutation, so callers
    MUST reassign the whole dict (mirrors ``wizard_state.py:248``). This
    test proves the reassignment pattern persists across a session
    boundary; it is NOT proving in-place mutation fails (that would need
    a same-session dirty-tracking assertion) but is the cross-request
    regression guard Task 3's integration tests build on."""
    row = CSVImportPreview(
        organization_id=seed_organization.id,
        entity_type="register:csv",
        csv_bytes=b"a,b\n1,2\n",
        expires_at=_future_expiry(),
        state_json={"filename": "r.csv"},
    )
    db_session.add(row)
    await db_session.commit()
    row_id = row.id

    # Simulate a later step's setter: re-read, reassign the WHOLE dict, flush.
    got = (
        await db_session.execute(select(CSVImportPreview).where(CSVImportPreview.id == row_id))
    ).scalar_one()
    got.state_json = {**(got.state_json or {}), "column_map": {"Header A": "title"}}
    await db_session.commit()

    reread = (
        await db_session.execute(select(CSVImportPreview).where(CSVImportPreview.id == row_id))
    ).scalar_one()
    assert reread.state_json == {
        "filename": "r.csv",
        "column_map": {"Header A": "title"},
    }


@pytest.mark.asyncio
async def test_register_binding_profile_roundtrip(
    db_session: AsyncSession, seed_organization: Organization, seed_user: User
) -> None:
    profile = RegisterBindingProfile(
        organization_id=seed_organization.id,
        name="Quarterly IT register",
        column_map={"Threat": "title", "Notes": "description", "Impact": "impact"},
        value_bindings={
            "likelihood": {"High": "high", "Low": "low"},
            "impact": {"Severe": "very_high"},
            "category": {"Malware": "malicious_external"},
        },
        mapping_versions_snapshot={
            "canonical": {"frequency:high": 1, "magnitude:very_high": 1},
            "org": {},
        },
        created_by=seed_user.id,
    )
    db_session.add(profile)
    await db_session.commit()
    await db_session.refresh(profile)

    got = (
        await db_session.execute(
            select(RegisterBindingProfile).where(RegisterBindingProfile.id == profile.id)
        )
    ).scalar_one()
    assert got.name == "Quarterly IT register"
    assert got.column_map == {"Threat": "title", "Notes": "description", "Impact": "impact"}
    assert got.value_bindings["likelihood"] == {"High": "high", "Low": "low"}
    assert got.mapping_versions_snapshot["canonical"]["frequency:high"] == 1
    assert got.created_by == seed_user.id
    assert got.organization_id == seed_organization.id


@pytest.mark.asyncio
async def test_register_binding_profile_unique_org_name(
    db_session: AsyncSession, seed_organization: Organization, seed_user: User
) -> None:
    """UniqueConstraint(organization_id, name) blocks a duplicate profile name
    within the same org."""
    a = RegisterBindingProfile(
        organization_id=seed_organization.id,
        name="Dup name",
        column_map={"A": "title"},
        value_bindings={"likelihood": {}, "impact": {}, "category": {}},
        mapping_versions_snapshot={"canonical": {}, "org": {}},
        created_by=seed_user.id,
    )
    db_session.add(a)
    await db_session.commit()

    b = RegisterBindingProfile(
        organization_id=seed_organization.id,
        name="Dup name",
        column_map={"B": "title"},
        value_bindings={"likelihood": {}, "impact": {}, "category": {}},
        mapping_versions_snapshot={"canonical": {}, "org": {}},
        created_by=seed_user.id,
    )
    db_session.add(b)
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_register_binding_profile_same_name_different_org_allowed(
    db_session: AsyncSession,
    seed_organization: Organization,
    seed_user: User,
    seed_organization_factory,
) -> None:
    """The unique constraint is scoped to (organization_id, name) — a second
    org may reuse the same profile name."""
    other_org = await seed_organization_factory(name=f"Other org {uuid.uuid4().hex[:8]}")

    a = RegisterBindingProfile(
        organization_id=seed_organization.id,
        name="Shared name",
        column_map={"A": "title"},
        value_bindings={"likelihood": {}, "impact": {}, "category": {}},
        mapping_versions_snapshot={"canonical": {}, "org": {}},
        created_by=seed_user.id,
    )
    b = RegisterBindingProfile(
        organization_id=other_org.id,
        name="Shared name",
        column_map={"B": "title"},
        value_bindings={"likelihood": {}, "impact": {}, "category": {}},
        mapping_versions_snapshot={"canonical": {}, "org": {}},
        created_by=None,
    )
    db_session.add_all([a, b])
    await db_session.commit()  # must not raise

    count = (
        await db_session.execute(
            select(RegisterBindingProfile).where(RegisterBindingProfile.name == "Shared name")
        )
    ).scalars()
    assert len(list(count)) == 2


@pytest.mark.asyncio
async def test_register_binding_profile_created_by_nullable(
    db_session: AsyncSession, seed_organization: Organization
) -> None:
    """created_by is nullable (ON DELETE SET NULL semantics; the profile
    outlives its creator user)."""
    profile = RegisterBindingProfile(
        organization_id=seed_organization.id,
        name="No creator",
        column_map={"A": "title"},
        value_bindings={"likelihood": {}, "impact": {}, "category": {}},
        mapping_versions_snapshot={"canonical": {}, "org": {}},
        created_by=None,
    )
    db_session.add(profile)
    await db_session.commit()
    await db_session.refresh(profile)
    assert profile.created_by is None
