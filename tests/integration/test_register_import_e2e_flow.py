"""Full-journey register-import test — epic #34 P1c Task 6.

httpx-level (not Playwright): upload xlsx -> columns -> bind (one parked
category + one initially-unbindable-then-bound value) -> preview counts ->
convert -> report. Then asserts the created scenarios are DRAFT +
legacy_residual + excluded from /analyses/new, and that the token is
single-use (re-POST convert -> 409).

Mirrors tests/integration/test_register_import_routes.py's fixture/helper
conventions — duplicated locally rather than cross-imported, per this
codebase's per-test-file helper convention (see that file's own docstring
re: `_csv_bytes`/`_xlsx_bytes` duplication between the unit and integration
register-import test files).
"""

from __future__ import annotations

import io
import re
import uuid

import openpyxl
import pytest
from httpx import AsyncClient, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.enums import EntityStatus, ScenarioSource
from idraa.models.qualitative_mapping import QualitativeMappingBand
from idraa.models.scenario import Scenario
from idraa.routes.register_import import _PARK_VALUE
from tests.conftest import csrf_post

pytestmark = pytest.mark.asyncio

_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")

_HEADERS = ["Title", "Likelihood", "Impact", "Category", "Owner"]
_ROWS = [
    ["Phishing risk", "Likely", "High", "Phishing", "Jane"],
    ["Malware risk", "Rare", "Low", "Malware", "Bob"],
    ["Legal risk", "Likely", "High", "Legal", "Jane"],  # -> parked category
]

_COLUMN_MAP = {
    "Title": "title",
    "Likelihood": "likelihood",
    "Impact": "impact",
    "Category": "category",
    "Owner": "owner",
}


def _xlsx_bytes() -> bytes:
    buf = io.BytesIO()
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Register"
    ws.append(_HEADERS)
    for row in _ROWS:
        ws.append(row)
    wb.save(buf)
    return buf.getvalue()


async def _seed_band(
    db_session: AsyncSession,
    *,
    kind: str,
    label: str,
    low: float,
    mode: float,
    high: float,
    sort_order: int = 1,
) -> None:
    db_session.add(
        QualitativeMappingBand(
            kind=kind,
            label=label,
            low=low,
            mode=mode,
            high=high,
            sort_order=sort_order,
            derivation="unit-test canonical band, not a real citation",
            version=1,
        )
    )
    await db_session.flush()
    await db_session.commit()


async def _seed_bands(db_session: AsyncSession) -> None:
    await _seed_band(db_session, kind="frequency", label="likely", low=1.0, mode=3.2, high=10.0)
    await _seed_band(
        db_session, kind="frequency", label="rare", low=0.1, mode=0.3, high=1.0, sort_order=2
    )
    await _seed_band(
        db_session, kind="magnitude", label="high", low=1_000_000, mode=5_000_000, high=10_000_000
    )
    await _seed_band(
        db_session, kind="magnitude", label="low", low=1_000, mode=5_000, high=10_000, sort_order=2
    )


def _token_from_location(resp: Response) -> str:
    loc = resp.headers["location"]
    m = _UUID_RE.search(loc)
    assert m, f"expected a token UUID in redirect Location {loc!r}"
    return m.group(0)


async def test_full_journey_upload_to_report_and_draft_gates(
    authed_admin: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    client, org_id = authed_admin
    await _seed_bands(db_session)

    # ---- 1. upload -------------------------------------------------------
    up = await csrf_post(
        client,
        "/register-import",
        {},
        files={
            "file": (
                "register.xlsx",
                _xlsx_bytes(),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
        follow_redirects=False,
    )
    assert up.status_code == 303, up.text
    token = _token_from_location(up)
    assert up.headers["location"] == f"/register-import/{token}/columns"

    # ---- 2. columns --------------------------------------------------------
    columns_get = await client.get(f"/register-import/{token}/columns")
    assert columns_get.status_code == 200
    for header in _HEADERS:
        assert header in columns_get.text

    form: dict[str, str] = {}
    for i, header in enumerate(_HEADERS):
        form[f"header_{i}"] = header
        form[f"target_{i}"] = _COLUMN_MAP[header]
    columns_post = await csrf_post(
        client, f"/register-import/{token}/columns", form, follow_redirects=False
    )
    assert columns_post.status_code == 303, columns_post.text
    assert columns_post.headers["location"] == f"/register-import/{token}/bind"

    # ---- 3. bind -------------------------------------------------------
    bind_get = await client.get(f"/register-import/{token}/bind")
    assert bind_get.status_code == 200

    # distinct_values() sorts: likelihood ["Likely", "Rare"], impact
    # ["High", "Low"], category ["Legal", "Malware", "Phishing"].
    distinct = {
        "likelihood": ["Likely", "Rare"],
        "impact": ["High", "Low"],
        "category": ["Legal", "Malware", "Phishing"],
    }

    def _bind_form(bindings: dict[str, dict[str, str]]) -> dict[str, str]:
        out: dict[str, str] = {}
        for group in ("likelihood", "impact", "category"):
            for i, value in enumerate(distinct[group]):
                out[f"{group}_value_{i}"] = value
                target = bindings.get(group, {}).get(value)
                if target is not None:
                    out[f"{group}_target_{i}"] = target
        return out

    # 3a. Submit with ONE value ("Phishing") left unbound -> 422, per-field
    # error, nothing persisted to state_json yet.
    incomplete = {
        "likelihood": {"Likely": "likely", "Rare": "rare"},
        "impact": {"High": "high", "Low": "low"},
        "category": {"Legal": _PARK_VALUE, "Malware": "malware"},  # "Phishing" omitted
    }
    bind_post_incomplete = await csrf_post(
        client, f"/register-import/{token}/bind", _bind_form(incomplete), follow_redirects=False
    )
    assert bind_post_incomplete.status_code == 422
    assert "must be bound" in bind_post_incomplete.text.lower()

    # 3b. Resubmit with the previously-unbindable value now bound -> 303 to preview.
    complete = {
        "likelihood": {"Likely": "likely", "Rare": "rare"},
        "impact": {"High": "high", "Low": "low"},
        "category": {"Legal": _PARK_VALUE, "Malware": "malware", "Phishing": "social_engineering"},
    }
    bind_post = await csrf_post(
        client, f"/register-import/{token}/bind", _bind_form(complete), follow_redirects=False
    )
    assert bind_post.status_code == 303, bind_post.text
    assert bind_post.headers["location"] == f"/register-import/{token}/preview"

    # ---- 4. preview: counts -------------------------------------------------
    preview_get = await client.get(f"/register-import/{token}/preview")
    assert preview_get.status_code == 200
    # 2 would-create (Phishing risk, Malware risk), 1 parked (Legal risk).
    assert "Phishing risk" in preview_get.text
    assert "Malware risk" in preview_get.text
    assert "badge-success" in preview_get.text  # would_create -> create badge
    assert "badge-ghost" in preview_get.text  # parked -> parked badge
    convert_button = re.search(r'<button type="submit"[^>]*>[\s\S]*?</button>', preview_get.text)
    assert convert_button and "disabled" not in convert_button.group(0)

    # ---- 5. convert -> report ----------------------------------------------
    convert_post = await csrf_post(
        client, f"/register-import/{token}/convert", {}, follow_redirects=False
    )
    assert convert_post.status_code == 200, convert_post.text
    assert "Phishing risk" in convert_post.text
    assert "Malware risk" in convert_post.text
    assert "/scenarios/" in convert_post.text

    # ---- 6. created scenarios are DRAFT + legacy_residual -------------------
    scenarios = (
        (
            await db_session.execute(
                select(Scenario).where(Scenario.organization_id == org_id).order_by(Scenario.name)
            )
        )
        .scalars()
        .all()
    )
    assert {s.name for s in scenarios} == {"Malware risk", "Phishing risk"}
    for s in scenarios:
        assert s.status == EntityStatus.DRAFT
        assert s.vuln_framing == "legacy_residual"
        assert s.source == ScenarioSource.QUALITATIVE_REGISTER_IMPORT
        assert s.conversion_metadata is not None

    # ---- 7. excluded from /analyses/new -------------------------------------
    analyses_new = await client.get("/analyses/new")
    assert analyses_new.status_code == 200
    assert "Phishing risk" not in analyses_new.text
    assert "Malware risk" not in analyses_new.text

    # ---- 8. single-use: re-POST convert -> 409 -------------------------------
    reconvert = await csrf_post(
        client, f"/register-import/{token}/convert", {}, follow_redirects=False
    )
    assert reconvert.status_code == 409
