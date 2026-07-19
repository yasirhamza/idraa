"""Scenario import — two-step staging + apply (Task 5).

Covers ``validate_upload`` (sniff + parse + validate + stage under a 10-min
token) and ``apply_validated_preview`` (re-parse the staged bytes + create the
non-duplicate valid rows + one summary ``scenario.import`` audit row).

Invariants asserted here:

- happy path: validate → apply creates the scenarios, stamps source
  FILE_IMPORT, consumes the preview row.
- re-import of the same file skips duplicates (create-only + skip guard).
- expired token → ``PreviewExpiredError`` + the preview row is deleted.
- cross-org token → uniform ``PreviewExpiredError`` (no existence oracle).
- summary audit-row shape ``{imported, skipped, errors_count}`` + per-row
  ``scenario.create`` rows attributed to the importing admin (SC-I5).
- I4/Sec-I3: a file row claiming ``library_derived`` + ``library_entry_id``
  STILL lands as FILE_IMPORT with ``library_pin=None`` — the load-bearing
  ``model_copy`` provenance override wins.
- SC-I10 (TOCTOU): the confirm path re-parses the SAME staged bytes the
  preview validated, so the imported count matches the preview's create count.

The repo provides ``db_session`` / ``organization`` / ``admin_user`` fixtures
(tests/conftest.py); the plan's skeleton names ``seeded_org`` / ``other_org``
are adapted to ``organization`` and an inline ``create_org`` second org.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from idraa.models.audit_log import AuditLog
from idraa.models.csv_import_preview import CSVImportPreview
from idraa.models.enums import ScenarioSource
from idraa.models.scenario import Scenario
from idraa.services.scenario_import import (
    PreviewExpiredError,
    apply_validated_preview,
    validate_upload,
)
from tests.factories import create_org

_JSON = json.dumps(
    [
        {
            "name": "Imp1",
            "threat_category": "ransomware",
            "threat_event_frequency": {"distribution": "PERT", "low": 0.1, "mode": 0.5, "high": 2},
            "vulnerability": {"distribution": "PERT", "low": 0.2, "mode": 0.35, "high": 0.6},
            "primary_loss": {
                "distribution": "PERT",
                "low": 100000,
                "mode": 1000000,
                "high": 15000000,
            },
        },
        {
            "name": "Imp2",
            "threat_category": "malware",
            "threat_event_frequency": {"distribution": "PERT", "low": 1, "mode": 2, "high": 3},
            "vulnerability": {"distribution": "PERT", "low": 0.1, "mode": 0.2, "high": 0.3},
            "primary_loss": {"distribution": "PERT", "low": 10, "mode": 20, "high": 30},
        },
    ]
).encode()


@pytest.mark.asyncio
async def test_validate_then_apply_creates_scenarios(db_session, organization, admin_user) -> None:
    token, preview, errors = await validate_upload(
        db_session,
        org_id=organization.id,
        user_id=admin_user.id,
        data=_JSON,
        filename="s.json",
        content_type="application/json",
    )
    assert errors == []
    assert [p["action"] for p in preview] == ["create", "create"]

    imported, skipped, apply_errors = await apply_validated_preview(
        db_session,
        token=token,
        org_id=organization.id,
        user=admin_user,
        ip_address="1.2.3.4",
    )
    assert (imported, skipped, apply_errors) == (2, 0, [])
    names = (
        (
            await db_session.execute(
                select(Scenario.name).where(Scenario.organization_id == organization.id)
            )
        )
        .scalars()
        .all()
    )
    assert {"Imp1", "Imp2"} <= set(names)
    # source stamped FILE_IMPORT
    src = (
        await db_session.execute(select(Scenario.source).where(Scenario.name == "Imp1"))
    ).scalar_one()
    assert src == ScenarioSource.FILE_IMPORT
    # preview row consumed
    remaining = (
        await db_session.execute(select(func.count()).select_from(CSVImportPreview))
    ).scalar_one()
    assert remaining == 0


@pytest.mark.asyncio
async def test_reimport_same_file_skips_duplicates(db_session, organization, admin_user) -> None:
    imported = skipped = -1
    for _ in range(2):
        token, _p, _e = await validate_upload(
            db_session,
            org_id=organization.id,
            user_id=admin_user.id,
            data=_JSON,
            filename="s.json",
            content_type="application/json",
        )
        imported, skipped, _ = await apply_validated_preview(
            db_session,
            token=token,
            org_id=organization.id,
            user=admin_user,
        )
    assert (imported, skipped) == (0, 2)  # second run skips both


@pytest.mark.asyncio
async def test_expired_token_rejected_and_row_deleted(db_session, organization, admin_user) -> None:
    token, _p, _e = await validate_upload(
        db_session,
        org_id=organization.id,
        user_id=admin_user.id,
        data=_JSON,
        filename="s.json",
        content_type="application/json",
    )
    row = (
        await db_session.execute(
            select(CSVImportPreview).where(CSVImportPreview.id == uuid.UUID(token))
        )
    ).scalar_one()
    # The CHECK constraint enforces ``expires_at > created_at``; back-date both.
    past = datetime.now(UTC) - timedelta(seconds=10)
    row.created_at = past - timedelta(seconds=1)
    row.expires_at = past
    await db_session.flush()
    with pytest.raises(PreviewExpiredError):
        await apply_validated_preview(
            db_session, token=token, org_id=organization.id, user=admin_user
        )
    # The expired preview row was deleted.
    remaining = (
        await db_session.execute(select(func.count()).select_from(CSVImportPreview))
    ).scalar_one()
    assert remaining == 0


@pytest.mark.asyncio
async def test_cross_org_token_rejected(db_session, organization, admin_user) -> None:
    token, _p, _e = await validate_upload(
        db_session,
        org_id=organization.id,
        user_id=admin_user.id,
        data=_JSON,
        filename="s.json",
        content_type="application/json",
    )
    other_org = await create_org(db_session, name="Other Org")
    with pytest.raises(PreviewExpiredError):
        await apply_validated_preview(db_session, token=token, org_id=other_org.id, user=admin_user)


@pytest.mark.asyncio
async def test_summary_audit_row_shape_and_per_row_attribution(
    db_session, organization, admin_user
) -> None:
    # SC-I5: assert the summary changes shape AND per-row scenario.create rows
    # attributed to the importing admin (not just that the action string exists).
    token, _p, _e = await validate_upload(
        db_session,
        org_id=organization.id,
        user_id=admin_user.id,
        data=_JSON,
        filename="s.json",
        content_type="application/json",
    )
    await apply_validated_preview(db_session, token=token, org_id=organization.id, user=admin_user)
    rows = (await db_session.execute(select(AuditLog))).scalars().all()
    summary = [r for r in rows if r.action == "scenario.import"]
    assert len(summary) == 1
    assert set(summary[0].changes.keys()) == {"imported", "skipped", "errors_count"}
    assert summary[0].changes["imported"] == [None, 2]
    creates = [r for r in rows if r.action == "scenario.create"]
    assert len(creates) == 2
    assert all(r.user_id == admin_user.id for r in creates)


@pytest.mark.asyncio
async def test_imported_row_is_file_import_even_if_file_claims_library(
    db_session, organization, admin_user
) -> None:
    # I4/Sec-I3: a file row carrying library_entry_id + source must STILL land as
    # FILE_IMPORT with library_pin=None — the model_copy override wins.
    blob = json.dumps(
        [
            {
                "name": "ClaimsLibrary",
                "threat_category": "ransomware",
                "source": "library_derived",
                "library_entry_id": "11111111-1111-1111-1111-111111111111",
                "threat_event_frequency": {
                    "distribution": "PERT",
                    "low": 0.1,
                    "mode": 0.5,
                    "high": 2,
                },
                "vulnerability": {"distribution": "PERT", "low": 0.2, "mode": 0.35, "high": 0.6},
                "primary_loss": {
                    "distribution": "PERT",
                    "low": 100000,
                    "mode": 1000000,
                    "high": 15000000,
                },
            }
        ]
    ).encode()
    token, _p, _e = await validate_upload(
        db_session,
        org_id=organization.id,
        user_id=admin_user.id,
        data=blob,
        filename="s.json",
        content_type="application/json",
    )
    imported, _s, _err = await apply_validated_preview(
        db_session, token=token, org_id=organization.id, user=admin_user
    )
    assert imported == 1
    row = (
        await db_session.execute(select(Scenario).where(Scenario.name == "ClaimsLibrary"))
    ).scalar_one()
    assert row.source == ScenarioSource.FILE_IMPORT
    assert row.library_pin is None


@pytest.mark.asyncio
async def test_confirm_reparse_matches_preview(db_session, organization, admin_user) -> None:
    # SC-I10 (TOCTOU): the confirm path re-parses the SAME staged bytes the
    # preview validated — assert imported count matches the preview's create count.
    token, preview, errors = await validate_upload(
        db_session,
        org_id=organization.id,
        user_id=admin_user.id,
        data=_JSON,
        filename="s.json",
        content_type="application/json",
    )
    assert errors == []
    create_count = sum(1 for p in preview if p["action"] == "create")
    imported, _s, _e = await apply_validated_preview(
        db_session, token=token, org_id=organization.id, user=admin_user
    )
    assert imported == create_count == 2


# --- Epic B (#326) Step 3d: lognormal FULL-pipeline gate (parser → _validate_rows)
# These drive the REAL import pipeline end-to-end (parse → §2.5 structural guard
# → validate_fair_distributions), NOT just the parser. This is the security gate
# (Meth-B1 / Sec-I1 / Sec-I2): a malformed lognormal must reach action "error"
# and never be staged for create.

import csv as _csv_mod  # noqa: E402
import io as _io_mod  # noqa: E402
import math as _math_mod  # noqa: E402

from idraa.services.scenario_import import _validate_rows  # noqa: E402
from idraa.services.scenario_import_parsers import (  # noqa: E402
    CSV_HEADERS,
    parse_csv_flat,
    parse_json_nested,
)


def _csv_pipeline(pl_dist: str, pl_low: str, pl_high: str, pl_mode: str = "") -> list[dict]:
    """Drive a single-row CSV through parse_csv_flat → _validate_rows; return preview."""
    cells = dict.fromkeys(CSV_HEADERS, "")
    cells.update(
        {
            "name": "LN",
            "scenario_type": "custom",
            "threat_category": "ransomware",
            "version": "1.0",
            "status": "active",
            "distribution": "PERT",
            "tef_low": "1",
            "tef_mode": "2",
            "tef_high": "3",
            "vuln_low": "0.1",
            "vuln_mode": "0.2",
            "vuln_high": "0.3",
            "pl_dist": pl_dist,
            "pl_low": pl_low,
            "pl_mode": pl_mode,
            "pl_high": pl_high,
        }
    )
    buf = _io_mod.StringIO()
    w = _csv_mod.writer(buf)
    w.writerow(CSV_HEADERS)
    w.writerow([cells[h] for h in CSV_HEADERS])
    pairs, errs = parse_csv_flat(buf.getvalue().encode())
    assert errs == [] and pairs is not None
    preview, _errors, _forms, _meta, _attack_meta = _validate_rows(pairs, existing_names=set())
    return preview


def test_lognormal_csv_valid_reaches_create() -> None:
    # p5=100, p95=10000 → valid lognormal, full pipeline → create.
    preview = _csv_pipeline("lognormal", "100", "10000")
    assert preview[0]["action"] == "create"


def _json_pipeline(primary_loss: dict) -> list[dict]:
    obj = {
        "name": "LN",
        "threat_category": "ransomware",
        "threat_event_frequency": {"distribution": "PERT", "low": 1, "mode": 2, "high": 3},
        "vulnerability": {"distribution": "PERT", "low": 0.1, "mode": 0.2, "high": 0.3},
        "primary_loss": primary_loss,
    }
    pairs, errs = parse_json_nested(json.dumps([obj]).encode())
    assert errs == [] and pairs is not None
    preview, _errors, _forms, _meta, _attack_meta = _validate_rows(pairs, existing_names=set())
    return preview


def test_lognormal_json_valid_reaches_create() -> None:
    preview = _json_pipeline(
        {"distribution": "lognormal", "mean": _math_mod.log(1000), "sigma": 1.0}
    )
    assert preview[0]["action"] == "create"


def test_lognormal_json_mean_inf_is_error() -> None:  # Meth-B1
    preview = _json_pipeline({"distribution": "lognormal", "mean": float("1e999"), "sigma": 1.0})
    assert preview[0]["action"] == "error"


def test_lognormal_json_sigma_zero_is_error() -> None:  # Sec-I2
    preview = _json_pipeline({"distribution": "lognormal", "mean": 6.9, "sigma": 0})
    assert preview[0]["action"] == "error"


def test_lognormal_json_sigma_negative_is_error() -> None:  # Sec-I2
    preview = _json_pipeline({"distribution": "lognormal", "mean": 6.9, "sigma": -1})
    assert preview[0]["action"] == "error"


def test_lognormal_json_sigma_above_bound_is_error() -> None:  # Sec-I2 (50 > 10)
    preview = _json_pipeline({"distribution": "lognormal", "mean": 6.9, "sigma": 50})
    assert preview[0]["action"] == "error"


def test_lognormal_json_mean_non_numeric_is_error() -> None:  # Sec-I1
    preview = _json_pipeline({"distribution": "lognormal", "mean": "abc", "sigma": 1.0})
    assert preview[0]["action"] == "error"


def test_lognormal_json_sigma_list_is_error() -> None:  # Sec-I1
    preview = _json_pipeline({"distribution": "lognormal", "mean": 6.9, "sigma": [1, 2]})
    assert preview[0]["action"] == "error"


def test_lognormal_json_extra_key_is_error() -> None:  # anti-blob-smuggling
    preview = _json_pipeline(
        {"distribution": "lognormal", "mean": 6.9, "sigma": 1.0, "junk": "x" * 200}
    )
    assert preview[0]["action"] == "error"
