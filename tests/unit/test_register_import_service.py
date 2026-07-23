"""Task 3 — RegisterImportService (epic #34 P1c).

Mirrors ``test_qualitative_converter.py``/``test_qualitative_bands_service.py``
style: local seed helpers, one behavior per test, audit-row assertions via a
direct ``AuditLog`` query where relevant.

Spec: docs/superpowers/specs/2026-07-18-qualitative-register-converter-design.md §5.
Plan: docs/superpowers/plans/2026-07-18-import-ui-p1c.md Task 3
(+ the BINDING Task 3 plan-gate amendments).
"""

from __future__ import annotations

import io
import uuid
from collections.abc import Awaitable, Callable

import openpyxl
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from idraa.errors import NotFoundError, ValidationError
from idraa.models.audit_log import AuditLog
from idraa.models.csv_import_preview import CSVImportPreview
from idraa.models.enums import ThreatCategory
from idraa.models.organization import Organization
from idraa.models.qualitative_mapping import QualitativeMappingBand
from idraa.models.scenario import Scenario
from idraa.models.user import User
from idraa.services.qualitative_bands import QualitativeBandService
from idraa.services.register_import import (
    PreviewExpiredError,
    RegisterImportService,
    preselect_bindings,
)
from idraa.services.scenario_import import ENTITY_TYPE as SCENARIO_ENTITY_TYPE
from idraa.services.scenario_import import _store_preview as store_scenario_preview

SeedOrgUser = Callable[..., Awaitable[tuple[Organization, User]]]

# ---------------------------------------------------------------------------
# Fixture content + helpers
# ---------------------------------------------------------------------------

_CSV_HEADERS = "Title,Likelihood,Impact,Category,Owner,Notes"
_CSV_ROWS = [
    "Phishing risk,Likely,High,Phishing,Jane,note-a",
    "Malware risk,Rare,Low,Malware,Bob,note-b",
    "Legal risk,Likely,High,Legal,Jane,note-c",
]


def _csv_bytes(headers: str = _CSV_HEADERS, rows: list[str] | None = None) -> bytes:
    body_rows = _CSV_ROWS if rows is None else rows
    return ("\n".join([headers, *body_rows]) + "\n").encode("utf-8")


def _xlsx_bytes(sheets: dict[str, list[list[object]]]) -> bytes:
    buf = io.BytesIO()
    wb = openpyxl.Workbook()
    first = True
    for name, rows in sheets.items():
        ws = wb.active if first else wb.create_sheet(name)
        if first:
            ws.title = name
            first = False
        for row in rows:
            ws.append(row)
    wb.save(buf)
    return buf.getvalue()


_FULL_COLUMN_MAP = {
    "Title": "title",
    "Likelihood": "likelihood",
    "Impact": "impact",
    "Category": "category",
    "Owner": "owner",
    "Notes": "carry_along",
}

_FULL_VALUE_BINDINGS = {
    "likelihood": {"Likely": "moderate", "Rare": "rare"},
    "impact": {"High": "high", "Low": "low"},
    "category": {"Phishing": "social_engineering", "Malware": "malware", "Legal": "__parked__"},
}


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
    await _seed_band(db_session, kind="frequency", label="moderate", low=1.0, mode=3.2, high=10.0)
    await _seed_band(
        db_session, kind="frequency", label="rare", low=0.1, mode=0.3, high=1.0, sort_order=2
    )
    await _seed_band(
        db_session,
        kind="magnitude",
        label="high",
        low=1_000_000.0,
        mode=3_200_000.0,
        high=10_000_000.0,
    )
    await _seed_band(
        db_session,
        kind="magnitude",
        label="low",
        low=10_000.0,
        mode=32_000.0,
        high=100_000.0,
        sort_order=2,
    )


async def _staged_and_mapped(
    db_session: AsyncSession,
    org: Organization,
    user: User,
    *,
    data: bytes | None = None,
) -> str:
    """Stage a CSV register and drive it through column-map + value-bind.
    Returns the token."""
    svc = RegisterImportService(db_session)
    staged = await svc.stage_upload(
        organization_id=org.id,
        filename="q3_register.csv",
        content_type="text/csv",
        data=data if data is not None else _csv_bytes(),
        user=user,
    )
    await svc.set_column_map(
        organization_id=org.id,
        token=staged.token,
        column_map=_FULL_COLUMN_MAP,
        created_by_user_id=user.id,
    )
    await svc.set_value_bindings(
        organization_id=org.id,
        token=staged.token,
        bindings=_FULL_VALUE_BINDINGS,
        created_by_user_id=user.id,
    )
    return staged.token


# ---------------------------------------------------------------------------
# stage_upload()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stage_upload_csv_no_sheet_names(
    db_session: AsyncSession, seed_org_user: SeedOrgUser
) -> None:
    org, user = await seed_org_user(db_session)
    svc = RegisterImportService(db_session)
    staged = await svc.stage_upload(
        organization_id=org.id,
        filename="register.csv",
        content_type="text/csv",
        data=_csv_bytes(),
        user=user,
    )
    assert staged.fmt == "csv"
    assert staged.sheet_names is None
    uuid.UUID(staged.token)  # does not raise

    row = (
        await db_session.execute(
            select(CSVImportPreview).where(CSVImportPreview.id == uuid.UUID(staged.token))
        )
    ).scalar_one()
    assert row.entity_type == "register:csv"
    assert row.organization_id == org.id
    assert row.state_json == {"filename": "register.csv"}


@pytest.mark.asyncio
async def test_stage_upload_xlsx_multi_sheet_returns_sheet_names(
    db_session: AsyncSession, seed_org_user: SeedOrgUser
) -> None:
    org, user = await seed_org_user(db_session)
    data = _xlsx_bytes(
        {
            "Q1": [["Title", "Likelihood", "Impact"], ["A", "Likely", "High"]],
            "Q2": [["Title", "Likelihood", "Impact"], ["B", "Rare", "Low"]],
        }
    )
    svc = RegisterImportService(db_session)
    staged = await svc.stage_upload(
        organization_id=org.id,
        filename="register.xlsx",
        content_type=None,
        data=data,
        user=user,
    )
    assert staged.fmt == "xlsx"
    assert staged.sheet_names == ["Q1", "Q2"]


@pytest.mark.asyncio
async def test_stage_upload_oversized_data_rejected(
    db_session: AsyncSession, seed_org_user: SeedOrgUser, monkeypatch: pytest.MonkeyPatch
) -> None:
    org, user = await seed_org_user(db_session)
    monkeypatch.setattr("idraa.routes.deps.MAX_UPLOAD_BYTES", 10)
    svc = RegisterImportService(db_session)
    with pytest.raises(ValidationError, match="maximum allowed size"):
        await svc.stage_upload(
            organization_id=org.id,
            filename="register.csv",
            content_type="text/csv",
            data=_csv_bytes(),
            user=user,
        )


@pytest.mark.asyncio
async def test_stage_upload_oversized_filename_rejected(
    db_session: AsyncSession, seed_org_user: SeedOrgUser
) -> None:
    org, user = await seed_org_user(db_session)
    svc = RegisterImportService(db_session)
    with pytest.raises(ValidationError, match="255"):
        await svc.stage_upload(
            organization_id=org.id,
            filename="x" * 252 + ".csv",  # 256 chars total
            content_type="text/csv",
            data=_csv_bytes(),
            user=user,
        )


@pytest.mark.asyncio
async def test_stage_upload_format_conflict_rejected(
    db_session: AsyncSession, seed_org_user: SeedOrgUser
) -> None:
    org, user = await seed_org_user(db_session)
    svc = RegisterImportService(db_session)
    with pytest.raises(ValidationError, match="format conflict"):
        await svc.stage_upload(
            organization_id=org.id,
            filename="register.csv",
            content_type="text/csv",
            data=b"PK\x03\x04" + b"\x00" * 20,  # looks like xlsx zip magic
            user=user,
        )


# ---------------------------------------------------------------------------
# get_staged() — org-scoping, TTL, entity-type gate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_staged_malformed_token_raises_expired(
    db_session: AsyncSession, seed_org_user: SeedOrgUser
) -> None:
    org, user = await seed_org_user(db_session)
    svc = RegisterImportService(db_session)
    with pytest.raises(PreviewExpiredError):
        await svc.get_staged(organization_id=org.id, token="not-a-uuid", created_by_user_id=user.id)


@pytest.mark.asyncio
async def test_get_staged_missing_token_raises_expired(
    db_session: AsyncSession, seed_org_user: SeedOrgUser
) -> None:
    org, user = await seed_org_user(db_session)
    svc = RegisterImportService(db_session)
    with pytest.raises(PreviewExpiredError):
        await svc.get_staged(
            organization_id=org.id, token=str(uuid.uuid4()), created_by_user_id=user.id
        )


@pytest.mark.asyncio
async def test_get_staged_cross_org_raises_expired(
    db_session: AsyncSession, seed_org_user: SeedOrgUser
) -> None:
    org_a, user_a = await seed_org_user(db_session, org_name="Org A", email="ga@example.com")
    org_b, user_b = await seed_org_user(db_session, org_name="Org B", email="gb@example.com")
    svc = RegisterImportService(db_session)
    staged = await svc.stage_upload(
        organization_id=org_a.id,
        filename="register.csv",
        content_type="text/csv",
        data=_csv_bytes(),
        user=user_a,
    )
    with pytest.raises(PreviewExpiredError):
        await svc.get_staged(
            organization_id=org_b.id, token=staged.token, created_by_user_id=user_b.id
        )


@pytest.mark.asyncio
async def test_get_staged_same_org_different_user_raises_expired(
    db_session: AsyncSession, seed_org_user: SeedOrgUser
) -> None:
    """Issue #80 (L10): a second ADMIN in the SAME org must not be able to
    resume another admin's in-flight upload via its token — user-scope is
    enforced in addition to org-scope. Same uniform ``PreviewExpiredError``
    (no existence oracle) as the cross-org / expired / malformed cases."""
    from idraa.models.user import User

    org, user_a = await seed_org_user(db_session, org_name="Org A", email="ua@example.com")
    user_b = User(
        organization_id=org.id,
        email="ub@example.com",
        password_hash="x",
        full_name="ub",
        role=user_a.role,
    )
    db_session.add(user_b)
    await db_session.flush()

    svc = RegisterImportService(db_session)
    staged = await svc.stage_upload(
        organization_id=org.id,
        filename="register.csv",
        content_type="text/csv",
        data=_csv_bytes(),
        user=user_a,
    )
    # user_a can resolve its own token.
    row = await svc.get_staged(
        organization_id=org.id, token=staged.token, created_by_user_id=user_a.id
    )
    assert row.id == uuid.UUID(staged.token)

    # user_b — SAME org, different user — cannot, even though the token is
    # otherwise valid (not expired, correct org, correct entity_type).
    with pytest.raises(PreviewExpiredError):
        await svc.get_staged(
            organization_id=org.id, token=staged.token, created_by_user_id=user_b.id
        )


@pytest.mark.asyncio
async def test_get_staged_rejects_scenario_import_token(
    db_session: AsyncSession, seed_org_user: SeedOrgUser
) -> None:
    """Sec-N: a scenario-import token (entity_type='scenario:csv') must not
    be consumable by the register-import service."""
    org, user = await seed_org_user(db_session)
    token = await store_scenario_preview(
        db_session, org_id=org.id, user_id=user.id, data=b"name\nfoo\n", fmt="csv"
    )
    assert SCENARIO_ENTITY_TYPE == "scenario"

    svc = RegisterImportService(db_session)
    with pytest.raises(PreviewExpiredError):
        await svc.get_staged(organization_id=org.id, token=token, created_by_user_id=user.id)


@pytest.mark.asyncio
async def test_get_staged_expired_row_raises_and_deletes(
    db_session: AsyncSession, seed_org_user: SeedOrgUser
) -> None:
    from datetime import UTC, datetime, timedelta

    org, user = await seed_org_user(db_session)
    svc = RegisterImportService(db_session)
    staged = await svc.stage_upload(
        organization_id=org.id,
        filename="register.csv",
        content_type="text/csv",
        data=_csv_bytes(),
        user=user,
    )
    token = staged.token

    # The CHECK constraint enforces expires_at > created_at at INSERT time,
    # so back-date BOTH after the row already exists (mirrors
    # test_scenario_import_apply.py::test_expired_token_rejected_and_row_deleted).
    row = (
        await db_session.execute(
            select(CSVImportPreview).where(CSVImportPreview.id == uuid.UUID(token))
        )
    ).scalar_one()
    past = datetime.now(UTC) - timedelta(seconds=10)
    row.created_at = past - timedelta(seconds=1)
    row.expires_at = past
    await db_session.flush()
    with pytest.raises(PreviewExpiredError):
        await svc.get_staged(organization_id=org.id, token=token, created_by_user_id=user.id)

    remaining = (
        await db_session.execute(select(CSVImportPreview).where(CSVImportPreview.id == row.id))
    ).scalar_one_or_none()
    assert remaining is None


# ---------------------------------------------------------------------------
# set_sheet()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_sheet_valid_updates_state(
    db_session: AsyncSession, seed_org_user: SeedOrgUser
) -> None:
    org, user = await seed_org_user(db_session)
    data = _xlsx_bytes(
        {
            "Q1": [["Title", "Likelihood", "Impact"], ["A", "Likely", "High"]],
            "Q2": [["Title", "Likelihood", "Impact"], ["B", "Rare", "Low"]],
        }
    )
    svc = RegisterImportService(db_session)
    staged = await svc.stage_upload(
        organization_id=org.id, filename="r.xlsx", content_type=None, data=data, user=user
    )
    await svc.set_sheet(
        organization_id=org.id, token=staged.token, sheet_name="Q2", created_by_user_id=user.id
    )

    row = await svc.get_staged(
        organization_id=org.id, token=staged.token, created_by_user_id=user.id
    )
    assert row.state_json is not None
    assert row.state_json["sheet_name"] == "Q2"
    assert row.state_json["filename"] == "r.xlsx"  # earlier key preserved


@pytest.mark.asyncio
async def test_set_sheet_unknown_name_rejected(
    db_session: AsyncSession, seed_org_user: SeedOrgUser
) -> None:
    org, user = await seed_org_user(db_session)
    data = _xlsx_bytes({"Q1": [["Title"], ["A"]]})
    svc = RegisterImportService(db_session)
    staged = await svc.stage_upload(
        organization_id=org.id, filename="r.xlsx", content_type=None, data=data, user=user
    )
    with pytest.raises(ValidationError, match="unknown sheet"):
        await svc.set_sheet(
            organization_id=org.id,
            token=staged.token,
            sheet_name="NoSuchSheet",
            created_by_user_id=user.id,
        )


@pytest.mark.asyncio
async def test_set_sheet_on_csv_rejected(
    db_session: AsyncSession, seed_org_user: SeedOrgUser
) -> None:
    org, user = await seed_org_user(db_session)
    svc = RegisterImportService(db_session)
    staged = await svc.stage_upload(
        organization_id=org.id,
        filename="r.csv",
        content_type="text/csv",
        data=_csv_bytes(),
        user=user,
    )
    with pytest.raises(ValidationError, match="only applies to xlsx"):
        await svc.set_sheet(
            organization_id=org.id,
            token=staged.token,
            sheet_name="Sheet1",
            created_by_user_id=user.id,
        )


# ---------------------------------------------------------------------------
# set_column_map()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_column_map_happy_path(
    db_session: AsyncSession, seed_org_user: SeedOrgUser
) -> None:
    org, user = await seed_org_user(db_session)
    svc = RegisterImportService(db_session)
    staged = await svc.stage_upload(
        organization_id=org.id,
        filename="r.csv",
        content_type="text/csv",
        data=_csv_bytes(),
        user=user,
    )
    await svc.set_column_map(
        organization_id=org.id,
        token=staged.token,
        column_map=_FULL_COLUMN_MAP,
        created_by_user_id=user.id,
    )
    row = await svc.get_staged(
        organization_id=org.id, token=staged.token, created_by_user_id=user.id
    )
    assert row.state_json is not None
    assert row.state_json["column_map"] == _FULL_COLUMN_MAP


@pytest.mark.asyncio
async def test_set_column_map_unknown_target_rejected(
    db_session: AsyncSession, seed_org_user: SeedOrgUser
) -> None:
    org, user = await seed_org_user(db_session)
    svc = RegisterImportService(db_session)
    staged = await svc.stage_upload(
        organization_id=org.id,
        filename="r.csv",
        content_type="text/csv",
        data=_csv_bytes(),
        user=user,
    )
    bad_map = dict(_FULL_COLUMN_MAP)
    bad_map["Title"] = "not_a_real_target"
    with pytest.raises(ValidationError, match="unknown column target"):
        await svc.set_column_map(
            organization_id=org.id,
            token=staged.token,
            column_map=bad_map,
            created_by_user_id=user.id,
        )


@pytest.mark.asyncio
async def test_set_column_map_missing_required_target_rejected(
    db_session: AsyncSession, seed_org_user: SeedOrgUser
) -> None:
    org, user = await seed_org_user(db_session)
    svc = RegisterImportService(db_session)
    staged = await svc.stage_upload(
        organization_id=org.id,
        filename="r.csv",
        content_type="text/csv",
        data=_csv_bytes(),
        user=user,
    )
    bad_map = dict(_FULL_COLUMN_MAP)
    bad_map["Likelihood"] = "ignore"  # no header maps to "likelihood" anymore
    with pytest.raises(ValidationError, match="exactly one column must map to 'likelihood'"):
        await svc.set_column_map(
            organization_id=org.id,
            token=staged.token,
            column_map=bad_map,
            created_by_user_id=user.id,
        )


@pytest.mark.asyncio
async def test_set_column_map_ambiguous_single_valued_target_rejected(
    db_session: AsyncSession, seed_org_user: SeedOrgUser
) -> None:
    org, user = await seed_org_user(db_session)
    svc = RegisterImportService(db_session)
    staged = await svc.stage_upload(
        organization_id=org.id,
        filename="r.csv",
        content_type="text/csv",
        data=_csv_bytes(),
        user=user,
    )
    bad_map = dict(_FULL_COLUMN_MAP)
    bad_map["Notes"] = "category"  # now TWO headers map to "category"
    # category joined _REQUIRED_EXACTLY_ONE (UAT fix): duplicates now trip the
    # exactly-one check rather than the optional at-most-one check.
    with pytest.raises(ValidationError, match="exactly one column must map to 'category'"):
        await svc.set_column_map(
            organization_id=org.id,
            token=staged.token,
            column_map=bad_map,
            created_by_user_id=user.id,
        )


@pytest.mark.asyncio
async def test_set_column_map_rejects_duplicated_header(
    db_session: AsyncSession, seed_org_user: SeedOrgUser
) -> None:
    """T1 review NTH: duplicate header names collapse in dict(zip(...)) at
    parse time — set_column_map must refuse to bind one to a real target."""
    org, user = await seed_org_user(db_session)
    svc = RegisterImportService(db_session)
    data = _csv_bytes(headers="Title,Likelihood,Likelihood,Impact,Category")
    staged = await svc.stage_upload(
        organization_id=org.id,
        filename="r.csv",
        content_type="text/csv",
        data=data,
        user=user,
    )
    column_map = {
        "Title": "title",
        "Likelihood": "likelihood",
        "Impact": "impact",
        "Category": "category",
    }
    with pytest.raises(ValidationError, match="blank or duplicated"):
        await svc.set_column_map(
            organization_id=org.id,
            token=staged.token,
            column_map=column_map,
            created_by_user_id=user.id,
        )


@pytest.mark.asyncio
async def test_set_column_map_rejects_blank_header(
    db_session: AsyncSession, seed_org_user: SeedOrgUser
) -> None:
    org, user = await seed_org_user(db_session)
    svc = RegisterImportService(db_session)
    data = _csv_bytes(headers="Title,,Impact,Category", rows=["A,Likely,High,Phishing"])
    staged = await svc.stage_upload(
        organization_id=org.id,
        filename="r.csv",
        content_type="text/csv",
        data=data,
        user=user,
    )
    column_map = {"Title": "title", "": "likelihood", "Impact": "impact", "Category": "category"}
    with pytest.raises(ValidationError, match="blank or duplicated"):
        await svc.set_column_map(
            organization_id=org.id,
            token=staged.token,
            column_map=column_map,
            created_by_user_id=user.id,
        )


# ---------------------------------------------------------------------------
# distinct_values()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_distinct_values_sorted_non_empty(
    db_session: AsyncSession, seed_org_user: SeedOrgUser
) -> None:
    org, user = await seed_org_user(db_session)
    svc = RegisterImportService(db_session)
    staged = await svc.stage_upload(
        organization_id=org.id,
        filename="r.csv",
        content_type="text/csv",
        data=_csv_bytes(),
        user=user,
    )
    await svc.set_column_map(
        organization_id=org.id,
        token=staged.token,
        column_map=_FULL_COLUMN_MAP,
        created_by_user_id=user.id,
    )

    distinct = await svc.distinct_values(
        organization_id=org.id, token=staged.token, created_by_user_id=user.id
    )
    assert distinct["likelihood"] == ["Likely", "Rare"]
    assert distinct["impact"] == ["High", "Low"]
    assert distinct["category"] == ["Legal", "Malware", "Phishing"]


@pytest.mark.asyncio
async def test_distinct_values_before_column_map_rejected(
    db_session: AsyncSession, seed_org_user: SeedOrgUser
) -> None:
    org, user = await seed_org_user(db_session)
    svc = RegisterImportService(db_session)
    staged = await svc.stage_upload(
        organization_id=org.id,
        filename="r.csv",
        content_type="text/csv",
        data=_csv_bytes(),
        user=user,
    )
    with pytest.raises(ValidationError, match="column map"):
        await svc.distinct_values(
            organization_id=org.id, token=staged.token, created_by_user_id=user.id
        )


@pytest.mark.asyncio
async def test_distinct_values_over_cap_rejected(
    db_session: AsyncSession, seed_org_user: SeedOrgUser
) -> None:
    org, user = await seed_org_user(db_session)
    headers = "Title,Likelihood,Impact,Category"
    rows = [f"Row {i},Val{i},High,Phishing" for i in range(51)]
    data = ("\n".join([headers, *rows]) + "\n").encode("utf-8")

    svc = RegisterImportService(db_session)
    staged = await svc.stage_upload(
        organization_id=org.id,
        filename="r.csv",
        content_type="text/csv",
        data=data,
        user=user,
    )
    column_map = {
        "Title": "title",
        "Likelihood": "likelihood",
        "Impact": "impact",
        "Category": "category",
    }
    await svc.set_column_map(
        organization_id=org.id,
        token=staged.token,
        column_map=column_map,
        created_by_user_id=user.id,
    )

    with pytest.raises(ValidationError, match="distinct values"):
        await svc.distinct_values(
            organization_id=org.id, token=staged.token, created_by_user_id=user.id
        )


# ---------------------------------------------------------------------------
# set_value_bindings()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_value_bindings_happy_path(
    db_session: AsyncSession, seed_org_user: SeedOrgUser
) -> None:
    org, user = await seed_org_user(db_session)
    await _seed_bands(db_session)
    svc = RegisterImportService(db_session)
    staged = await svc.stage_upload(
        organization_id=org.id,
        filename="r.csv",
        content_type="text/csv",
        data=_csv_bytes(),
        user=user,
    )
    await svc.set_column_map(
        organization_id=org.id,
        token=staged.token,
        column_map=_FULL_COLUMN_MAP,
        created_by_user_id=user.id,
    )
    await svc.set_value_bindings(
        organization_id=org.id,
        token=staged.token,
        bindings=_FULL_VALUE_BINDINGS,
        created_by_user_id=user.id,
    )
    row = await svc.get_staged(
        organization_id=org.id, token=staged.token, created_by_user_id=user.id
    )
    assert row.state_json is not None
    assert row.state_json["value_bindings"] == _FULL_VALUE_BINDINGS


@pytest.mark.asyncio
async def test_set_value_bindings_invalid_category_target_rejected(
    db_session: AsyncSession, seed_org_user: SeedOrgUser
) -> None:
    """Sec-I2: an invalid target is rejected at bind time — never allowed to
    reach build_bound_rows (which would 500 on ThreatCategory() coercion)."""
    org, user = await seed_org_user(db_session)
    await _seed_bands(db_session)
    svc = RegisterImportService(db_session)
    staged = await svc.stage_upload(
        organization_id=org.id,
        filename="r.csv",
        content_type="text/csv",
        data=_csv_bytes(),
        user=user,
    )
    await svc.set_column_map(
        organization_id=org.id,
        token=staged.token,
        column_map=_FULL_COLUMN_MAP,
        created_by_user_id=user.id,
    )

    bad_bindings = {
        "likelihood": {"Likely": "moderate", "Rare": "rare"},
        "impact": {"High": "high", "Low": "low"},
        "category": {
            "Phishing": "not_a_real_category",
            "Malware": "malware",
            "Legal": "__parked__",
        },
    }
    with pytest.raises(ValidationError, match="not a valid target"):
        await svc.set_value_bindings(
            organization_id=org.id,
            token=staged.token,
            bindings=bad_bindings,
            created_by_user_id=user.id,
        )

    # And it never made it into state_json.
    row = await svc.get_staged(
        organization_id=org.id, token=staged.token, created_by_user_id=user.id
    )
    assert "value_bindings" not in (row.state_json or {})


@pytest.mark.asyncio
async def test_set_value_bindings_invalid_likelihood_label_rejected(
    db_session: AsyncSession, seed_org_user: SeedOrgUser
) -> None:
    org, user = await seed_org_user(db_session)
    await _seed_bands(db_session)
    svc = RegisterImportService(db_session)
    staged = await svc.stage_upload(
        organization_id=org.id,
        filename="r.csv",
        content_type="text/csv",
        data=_csv_bytes(),
        user=user,
    )
    await svc.set_column_map(
        organization_id=org.id,
        token=staged.token,
        column_map=_FULL_COLUMN_MAP,
        created_by_user_id=user.id,
    )

    bad_bindings = {
        "likelihood": {"Likely": "not_a_real_band", "Rare": "rare"},
        "impact": {"High": "high", "Low": "low"},
        "category": _FULL_VALUE_BINDINGS["category"],
    }
    with pytest.raises(ValidationError, match="not a valid target"):
        await svc.set_value_bindings(
            organization_id=org.id,
            token=staged.token,
            bindings=bad_bindings,
            created_by_user_id=user.id,
        )


@pytest.mark.asyncio
async def test_set_value_bindings_unbound_distinct_value_rejected(
    db_session: AsyncSession, seed_org_user: SeedOrgUser
) -> None:
    org, user = await seed_org_user(db_session)
    await _seed_bands(db_session)
    svc = RegisterImportService(db_session)
    staged = await svc.stage_upload(
        organization_id=org.id,
        filename="r.csv",
        content_type="text/csv",
        data=_csv_bytes(),
        user=user,
    )
    await svc.set_column_map(
        organization_id=org.id,
        token=staged.token,
        column_map=_FULL_COLUMN_MAP,
        created_by_user_id=user.id,
    )

    incomplete_bindings = {
        "likelihood": {"Likely": "moderate"},  # "Rare" never bound
        "impact": {"High": "high", "Low": "low"},
        "category": _FULL_VALUE_BINDINGS["category"],
    }
    with pytest.raises(ValidationError, match=r"Rare.*not bound"):
        await svc.set_value_bindings(
            organization_id=org.id,
            token=staged.token,
            bindings=incomplete_bindings,
            created_by_user_id=user.id,
        )


# ---------------------------------------------------------------------------
# preselect_bindings() — pure, exact case-insensitive match ONLY
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_preselect_bindings_exact_case_insensitive_only(
    db_session: AsyncSession, seed_org_user: SeedOrgUser
) -> None:
    org, _user = await seed_org_user(db_session)
    await _seed_bands(db_session)
    effective = await QualitativeBandService(db_session).effective_bands(org.id)

    distinct = {
        "likelihood": ["Moderate", "Hi", "RARE"],
        "impact": ["High", "medium"],
        "category": ["Malware", "not-a-category"],
    }
    result = preselect_bindings(distinct, effective, ThreatCategory)

    # "Moderate" -> "moderate" (case-insensitive exact match).
    assert result["likelihood"]["Moderate"] == "moderate"
    # "RARE" -> "rare" (case-insensitive exact match).
    assert result["likelihood"]["RARE"] == "rare"
    # "Hi" does NOT match "high" or anything else — no heuristics.
    assert "Hi" not in result["likelihood"]

    assert result["impact"]["High"] == "high"
    assert "medium" not in result["impact"]  # no band named "medium" seeded

    assert result["category"]["Malware"] == ThreatCategory.MALWARE.value
    assert "not-a-category" not in result["category"]


# ---------------------------------------------------------------------------
# build_bound_rows()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_bound_rows_happy_path(
    db_session: AsyncSession, seed_org_user: SeedOrgUser
) -> None:
    org, user = await seed_org_user(db_session)
    await _seed_bands(db_session)
    token = await _staged_and_mapped(db_session, org, user)

    svc = RegisterImportService(db_session)
    rows = await svc.build_bound_rows(
        organization_id=org.id, token=token, created_by_user_id=user.id
    )
    assert [r.source_row for r in rows] == [2, 3, 4]

    phishing = rows[0]
    assert phishing.title == "Phishing risk"
    assert phishing.owner == "Jane"
    assert phishing.description is None  # no header mapped to "description"
    assert phishing.likelihood_label == "moderate"
    assert phishing.magnitude_label == "high"
    assert phishing.category == ThreatCategory.SOCIAL_ENGINEERING
    assert phishing.raw == {"likelihood": "Likely", "impact": "High", "category": "Phishing"}
    assert phishing.carry_along == {"Notes": "note-a"}

    malware = rows[1]
    assert malware.likelihood_label == "rare"
    assert malware.magnitude_label == "low"
    assert malware.category == ThreatCategory.MALWARE

    legal = rows[2]
    assert legal.category is None  # explicitly bound to "__parked__"
    assert legal.raw["category"] == "Legal"


@pytest.mark.asyncio
async def test_build_bound_rows_blank_likelihood_auto_parks(
    db_session: AsyncSession, seed_org_user: SeedOrgUser
) -> None:
    org, user = await seed_org_user(db_session)
    await _seed_bands(db_session)
    data = _csv_bytes(rows=["Blank risk,,High,Phishing,Jane,note-x"])
    token = await _staged_and_mapped(db_session, org, user, data=data)

    svc = RegisterImportService(db_session)
    rows = await svc.build_bound_rows(
        organization_id=org.id, token=token, created_by_user_id=user.id
    )
    assert len(rows) == 1
    assert rows[0].category is None
    assert rows[0].park_reason == "blank_cells"  # UAT fix: reason pinned for report labels
    assert rows[0].raw == {"likelihood": "", "impact": "High", "category": "Phishing"}


@pytest.mark.asyncio
async def test_build_bound_rows_before_value_bindings_rejected(
    db_session: AsyncSession, seed_org_user: SeedOrgUser
) -> None:
    org, user = await seed_org_user(db_session)
    await _seed_bands(db_session)
    svc = RegisterImportService(db_session)
    staged = await svc.stage_upload(
        organization_id=org.id,
        filename="r.csv",
        content_type="text/csv",
        data=_csv_bytes(),
        user=user,
    )
    await svc.set_column_map(
        organization_id=org.id,
        token=staged.token,
        column_map=_FULL_COLUMN_MAP,
        created_by_user_id=user.id,
    )
    with pytest.raises(ValidationError, match="value bindings"):
        await svc.build_bound_rows(
            organization_id=org.id, token=staged.token, created_by_user_id=user.id
        )


@pytest.mark.asyncio
async def test_build_bound_rows_stale_binding_after_band_deleted_rejected(
    db_session: AsyncSession, seed_org_user: SeedOrgUser
) -> None:
    """Defense-in-depth: build_bound_rows re-validates against the CURRENT
    effective bands, even if value_bindings in state_json is stale (e.g. an
    org band was deleted after this token bound to it)."""
    org, user = await seed_org_user(db_session)
    await _seed_bands(db_session)
    token = await _staged_and_mapped(db_session, org, user)

    # Delete the canonical "rare" band's org-effective visibility is not
    # directly deletable (canonical is code-managed), so simulate drift by
    # tombstoning state_json's binding to point at a non-existent label via
    # a fresh org band override then removing it — simplest: patch the
    # frequency:rare band's label out of existence by soft-deleting an org
    # override is not applicable to canonical rows, so instead assert the
    # positive case is already covered and drift is exercised through
    # apply_profile's own drift test below. This test instead exercises the
    # in-band defense by binding to a value that is valid at bind time but
    # forging state_json to a NOW-invalid label directly.
    svc = RegisterImportService(db_session)
    row = await svc.get_staged(organization_id=org.id, token=token, created_by_user_id=user.id)
    forged = dict(row.state_json or {})
    forged["value_bindings"] = {
        **forged["value_bindings"],
        "likelihood": {"Likely": "no_longer_a_real_band", "Rare": "rare"},
    }
    row.state_json = forged
    await db_session.flush()

    with pytest.raises(ValidationError, match="not bound to a valid frequency band"):
        await svc.build_bound_rows(organization_id=org.id, token=token, created_by_user_id=user.id)


# ---------------------------------------------------------------------------
# preview()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_preview_classifies_without_writing(
    db_session: AsyncSession, seed_org_user: SeedOrgUser
) -> None:
    org, user = await seed_org_user(db_session)
    await _seed_bands(db_session)
    token = await _staged_and_mapped(db_session, org, user)

    svc = RegisterImportService(db_session)
    classified = await svc.preview(organization_id=org.id, token=token, created_by_user_id=user.id)

    assert [r.source_row for r in classified.would_create] == [2, 3]
    assert [(p.source_row, p.reason) for p in classified.parked] == [(4, "category")]
    assert classified.duplicates == []
    assert classified.errors == []

    scenarios = (await db_session.execute(select(Scenario))).scalars().all()
    assert scenarios == []  # preview never writes


# ---------------------------------------------------------------------------
# apply() — creates scenarios, single-use token
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_creates_scenarios_and_deletes_token(
    db_session: AsyncSession, seed_org_user: SeedOrgUser
) -> None:
    org, user = await seed_org_user(db_session)
    await _seed_bands(db_session)
    token = await _staged_and_mapped(db_session, org, user)

    svc = RegisterImportService(db_session)
    report = await svc.apply(organization_id=org.id, user=user, token=token)

    assert len(report.created) == 2
    assert [(p.source_row, p.reason) for p in report.parked] == [(4, "category")]
    assert report.source_file == "q3_register.csv"

    scenarios = (
        (await db_session.execute(select(Scenario).where(Scenario.organization_id == org.id)))
        .scalars()
        .all()
    )
    assert len(scenarios) == 2

    # Single-use: the token is gone, re-applying 409s.
    with pytest.raises(PreviewExpiredError):
        await svc.apply(organization_id=org.id, user=user, token=token)


# ---------------------------------------------------------------------------
# save_profile() / list_profiles() / apply_profile()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_save_profile_happy_path_and_audit(
    db_session: AsyncSession, seed_org_user: SeedOrgUser
) -> None:
    org, user = await seed_org_user(db_session)
    await _seed_bands(db_session)
    token = await _staged_and_mapped(db_session, org, user)

    svc = RegisterImportService(db_session)
    profile = await svc.save_profile(
        organization_id=org.id, name="Quarterly export", token=token, user=user
    )
    assert profile.name == "Quarterly export"
    assert profile.column_map == _FULL_COLUMN_MAP
    assert profile.value_bindings == _FULL_VALUE_BINDINGS
    assert profile.mapping_versions_snapshot["canonical"]  # non-empty per-(kind,label) dict

    audits = (
        (
            await db_session.execute(
                select(AuditLog).where(AuditLog.action == "register_binding_profile.create")
            )
        )
        .scalars()
        .all()
    )
    assert len(audits) == 1
    assert audits[0].entity_id == profile.id
    assert audits[0].user_id == user.id


@pytest.mark.asyncio
async def test_save_profile_blank_name_rejected(
    db_session: AsyncSession, seed_org_user: SeedOrgUser
) -> None:
    org, user = await seed_org_user(db_session)
    await _seed_bands(db_session)
    token = await _staged_and_mapped(db_session, org, user)

    svc = RegisterImportService(db_session)
    with pytest.raises(ValidationError, match="required"):
        await svc.save_profile(organization_id=org.id, name="   ", token=token, user=user)


@pytest.mark.asyncio
async def test_save_profile_over_100_chars_rejected(
    db_session: AsyncSession, seed_org_user: SeedOrgUser
) -> None:
    org, user = await seed_org_user(db_session)
    await _seed_bands(db_session)
    token = await _staged_and_mapped(db_session, org, user)

    svc = RegisterImportService(db_session)
    with pytest.raises(ValidationError, match="100"):
        await svc.save_profile(organization_id=org.id, name="x" * 101, token=token, user=user)


@pytest.mark.asyncio
async def test_save_profile_duplicate_name_rejected(
    db_session: AsyncSession, seed_org_user: SeedOrgUser
) -> None:
    org, user = await seed_org_user(db_session)
    await _seed_bands(db_session)
    svc = RegisterImportService(db_session)

    token1 = await _staged_and_mapped(db_session, org, user)
    await svc.save_profile(organization_id=org.id, name="Dup", token=token1, user=user)

    token2 = await _staged_and_mapped(db_session, org, user)
    with pytest.raises(ValidationError, match="already exists"):
        await svc.save_profile(organization_id=org.id, name="Dup", token=token2, user=user)


@pytest.mark.asyncio
async def test_save_profile_before_bindings_rejected(
    db_session: AsyncSession, seed_org_user: SeedOrgUser
) -> None:
    org, user = await seed_org_user(db_session)
    await _seed_bands(db_session)
    svc = RegisterImportService(db_session)
    staged = await svc.stage_upload(
        organization_id=org.id,
        filename="r.csv",
        content_type="text/csv",
        data=_csv_bytes(),
        user=user,
    )
    with pytest.raises(ValidationError, match="column map"):
        await svc.save_profile(
            organization_id=org.id, name="Too Early", token=staged.token, user=user
        )


@pytest.mark.asyncio
async def test_list_profiles_org_scoped(
    db_session: AsyncSession, seed_org_user: SeedOrgUser
) -> None:
    org_a, user_a = await seed_org_user(db_session, org_name="Org A", email="pa@example.com")
    org_b, user_b = await seed_org_user(db_session, org_name="Org B", email="pb@example.com")
    await _seed_bands(db_session)
    svc = RegisterImportService(db_session)

    token_a = await _staged_and_mapped(db_session, org_a, user_a)
    await svc.save_profile(
        organization_id=org_a.id, name="Org A Profile", token=token_a, user=user_a
    )

    profiles_a = await svc.list_profiles(org_a.id)
    profiles_b = await svc.list_profiles(org_b.id)
    assert [p.name for p in profiles_a] == ["Org A Profile"]
    assert profiles_b == []


@pytest.mark.asyncio
async def test_apply_profile_cross_org_raises_not_found(
    db_session: AsyncSession, seed_org_user: SeedOrgUser
) -> None:
    org_a, user_a = await seed_org_user(db_session, org_name="Org A", email="xa@example.com")
    org_b, user_b = await seed_org_user(db_session, org_name="Org B", email="xb@example.com")
    await _seed_bands(db_session)
    svc = RegisterImportService(db_session)

    token_a = await _staged_and_mapped(db_session, org_a, user_a)
    profile = await svc.save_profile(
        organization_id=org_a.id, name="Org A Only", token=token_a, user=user_a
    )

    token_b = await _staged_and_mapped(db_session, org_b, user_b)
    with pytest.raises(NotFoundError):
        await svc.apply_profile(
            organization_id=org_b.id,
            token=token_b,
            profile_id=profile.id,
            created_by_user_id=user_b.id,
        )


@pytest.mark.asyncio
async def test_apply_profile_no_drift_pre_fills_state(
    db_session: AsyncSession, seed_org_user: SeedOrgUser
) -> None:
    org, user = await seed_org_user(db_session)
    await _seed_bands(db_session)
    svc = RegisterImportService(db_session)

    token1 = await _staged_and_mapped(db_session, org, user)
    profile = await svc.save_profile(organization_id=org.id, name="Saved", token=token1, user=user)

    staged2 = await svc.stage_upload(
        organization_id=org.id,
        filename="q4_register.csv",
        content_type="text/csv",
        data=_csv_bytes(),
        user=user,
    )
    warnings = await svc.apply_profile(
        organization_id=org.id,
        token=staged2.token,
        profile_id=profile.id,
        created_by_user_id=user.id,
    )
    assert warnings == []

    row = await svc.get_staged(
        organization_id=org.id, token=staged2.token, created_by_user_id=user.id
    )
    assert row.state_json is not None
    assert row.state_json["column_map"] == _FULL_COLUMN_MAP
    assert row.state_json["value_bindings"] == _FULL_VALUE_BINDINGS
    assert row.state_json["applied_profile_id"] == str(profile.id)

    # And the pre-filled state is immediately usable end to end.
    bound_rows = await svc.build_bound_rows(
        organization_id=org.id, token=staged2.token, created_by_user_id=user.id
    )
    assert len(bound_rows) == 3


@pytest.mark.asyncio
async def test_apply_profile_drift_warns_and_leaves_stale_binding_unbound(
    db_session: AsyncSession, seed_org_user: SeedOrgUser
) -> None:
    org, user = await seed_org_user(db_session)
    await _seed_bands(db_session)
    svc = RegisterImportService(db_session)
    band_svc = QualitativeBandService(db_session)

    token1 = await _staged_and_mapped(db_session, org, user)
    profile = await svc.save_profile(
        organization_id=org.id, name="Drifting", token=token1, user=user
    )

    # Org-override the "moderate" frequency band AFTER the profile was
    # saved — this both bumps its effective version (drift on an existing
    # (kind,label)) while leaving the (kind,label) key itself resolvable.
    await band_svc.create_org_band(
        organization_id=org.id,
        kind="frequency",
        label="moderate",
        low=2.0,
        mode=4.0,
        high=12.0,
        reason="recalibrated after this profile was saved",
        user=user,
    )

    staged2 = await svc.stage_upload(
        organization_id=org.id,
        filename="q4_register.csv",
        content_type="text/csv",
        data=_csv_bytes(),
        user=user,
    )
    warnings = await svc.apply_profile(
        organization_id=org.id,
        token=staged2.token,
        profile_id=profile.id,
        created_by_user_id=user.id,
    )
    assert any("frequency:moderate" in w for w in warnings)

    # The likelihood binding for "moderate" is STILL valid (org override
    # replaces, doesn't remove, the (kind,label) — value carries forward).
    row = await svc.get_staged(
        organization_id=org.id, token=staged2.token, created_by_user_id=user.id
    )
    assert row.state_json is not None
    assert row.state_json["value_bindings"]["likelihood"]["Likely"] == "moderate"


# ---------------------------------------------------------------------------
# Arch-I1 regression guard: state_json reassignment survives a fresh session
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_state_json_reassignment_persists_across_fresh_session(
    db_session: AsyncSession, db_url: str, seed_org_user: SeedOrgUser
) -> None:
    """Every setter reassigns the WHOLE state_json dict rather than mutating
    a key in place, because a plain JSON column doesn't track in-place
    ``dict`` mutation. Verify by committing then re-reading through a
    completely FRESH engine/session on the same SQLite file (mirrors
    test_library_provenance.py's fresh-session pattern) — an in-place
    mutation could still look correct within the SAME session (SQLAlchemy's
    identity map masks it) but would silently fail to persist."""
    org, user = await seed_org_user(db_session)
    svc = RegisterImportService(db_session)
    staged = await svc.stage_upload(
        organization_id=org.id,
        filename="r.csv",
        content_type="text/csv",
        data=_csv_bytes(),
        user=user,
    )
    await svc.set_column_map(
        organization_id=org.id,
        token=staged.token,
        column_map=_FULL_COLUMN_MAP,
        created_by_user_id=user.id,
    )
    await db_session.commit()

    engine = create_async_engine(db_url, future=True)
    try:
        sm = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with sm() as fresh:
            fresh_svc = RegisterImportService(fresh)
            row = await fresh_svc.get_staged(
                organization_id=org.id, token=staged.token, created_by_user_id=user.id
            )
            assert row.state_json is not None
            assert row.state_json["column_map"] == _FULL_COLUMN_MAP
            assert row.state_json["filename"] == "r.csv"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_apply_profile_with_incomplete_column_map_does_not_clobber(
    db_session: AsyncSession, seed_org_user: SeedOrgUser
) -> None:
    """UAT regression 2026-07-19: a legacy profile saved WITHOUT a category
    mapping must not silently replay its broken column_map over the admin's
    manually-set one — the column map is left alone and a warning names the
    missing required target. Value bindings still pre-fill."""
    org, user = await seed_org_user(db_session)
    await _seed_bands(db_session)
    svc = RegisterImportService(db_session)
    staged = await svc.stage_upload(
        organization_id=org.id,
        filename="r.csv",
        content_type="text/csv",
        data=_csv_bytes(),
        user=user,
    )
    await svc.set_column_map(
        organization_id=org.id,
        token=staged.token,
        column_map=dict(_FULL_COLUMN_MAP),
        created_by_user_id=user.id,
    )
    await svc.set_value_bindings(
        organization_id=org.id,
        token=staged.token,
        bindings=_FULL_VALUE_BINDINGS,
        created_by_user_id=user.id,
    )
    legacy_cm = {k: ("carry_along" if v == "category" else v) for k, v in _FULL_COLUMN_MAP.items()}
    profile = await svc.save_profile(
        organization_id=org.id, name="legacy", token=staged.token, user=user
    )
    profile.column_map = legacy_cm  # simulate a pre-fix saved profile
    await db_session.flush()

    warnings = await svc.apply_profile(
        organization_id=org.id,
        token=staged.token,
        profile_id=profile.id,
        created_by_user_id=user.id,
    )
    assert any("'category'" in w and "NOT applied" in w for w in warnings)
    row = await svc.get_staged(
        organization_id=org.id, token=staged.token, created_by_user_id=user.id
    )
    cm = (row.state_json or {}).get("column_map") or {}
    assert "category" in cm.values()  # the manual mapping SURVIVED


def test_category_keyword_preselection_word_boundary_and_ambiguity() -> None:
    """Owner-approved category keyword pre-selection (UAT round 3): word-boundary
    containment, category group only; absent/ambiguous stays unselected."""
    from idraa.services.register_import import _category_keyword_match

    assert _category_keyword_match("Cyber – Ransomware") == "ransomware"
    assert _category_keyword_match("Insider Threat") == "insider_misuse"
    assert _category_keyword_match("Third Party") == "supply_chain"
    assert _category_keyword_match("Cyber – Availability") == "denial_of_service"
    assert _category_keyword_match("Compliance – Data Privacy") == "data_disclosure"
    assert _category_keyword_match("Cyber – Social Engineering") == "social_engineering"
    # deliberately UNMAPPED: HSE / generic OT / commercial supplier / park
    assert _category_keyword_match("HSE") is None
    assert _category_keyword_match("Process Safety") is None
    assert _category_keyword_match("OT Security") is None
    assert _category_keyword_match("Supplier / Commercial") is None
    assert _category_keyword_match("Market / Financial") is None
    # word boundary: 'sis' must not fire inside other words
    assert _category_keyword_match("Analysis backlog") is None
    assert _category_keyword_match("SIS bypass") == "ot_safety_tampering"
    # review round: bare tampering/scada dropped (OT-ambiguous); explicit
    # two-word forms map, single-signal generic forms stay manual
    assert _category_keyword_match("Physical Tampering") == "physical_tampering"
    assert _category_keyword_match("Data Tampering") == "data_tampering"
    assert _category_keyword_match("Equipment tampering") is None
    assert _category_keyword_match("SCADA outage") is None
    # ambiguity (two different categories) -> None
    assert _category_keyword_match("Phishing then Ransomware") is None
