"""Register import route tests — staged upload / sheet-pick / column-map /
value-bind / binding profiles (Tasks 4 + 5).

Mirrors ``tests/integration/test_scenario_import_routes.py``'s route-test
patterns and the ``csrf_post`` double-submit helper. RBAC posture: every
route is ``require_role(ADMIN)`` — the analyst-403 tests pin this.

Epic #34 P1c Task 4 shipped upload/sheet-pick/column-map; Task 5 (this
extension) adds value-bind + binding profiles. Task 6 (preview/convert/
report) has not landed yet, so the bind-POST happy-path tests here only
assert the redirect TARGET (``.../preview`` does not exist until Task 6
lands in the same branch) — same posture Task 4 used for the bind step.
"""

from __future__ import annotations

import io
import re
import uuid
from collections.abc import AsyncIterator
from typing import Any

import openpyxl
import pytest
from httpx import AsyncClient, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.qualitative_mapping import QualitativeMappingBand, QualitativeMappingOrgBand
from idraa.models.register_binding_profile import RegisterBindingProfile
from idraa.routes.deps import MAX_UPLOAD_BYTES
from idraa.routes.register_import import _CATEGORY_OPTIONS, _PARK_VALUE, _TARGET_OPTIONS
from idraa.services.register_import import TARGETS
from tests.conftest import csrf_post

_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")

_CSV_HEADERS = "Title,Likelihood,Impact,Category,Owner,Notes"
_CSV_ROWS = [
    "Phishing risk,Likely,High,Phishing,Jane,note-a",
    "Malware risk,Rare,Low,Malware,Bob,note-b",
]

_FULL_COLUMN_MAP = {
    "Title": "title",
    "Likelihood": "likelihood",
    "Impact": "impact",
    "Category": "category",
    "Owner": "owner",
    "Notes": "carry_along",
}


def _csv_bytes(headers: str = _CSV_HEADERS, rows: list[str] | None = None) -> bytes:
    body_rows = _CSV_ROWS if rows is None else rows
    return ("\n".join([headers, *body_rows]) + "\n").encode("utf-8")


# ---- Task 5 helpers ---------------------------------------------------


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
    """Mirrors tests/unit/test_register_import_service.py's helper of the
    same name — duplicated locally (not cross-imported) per this codebase's
    per-test-file helper convention (see the `_csv_bytes`/`_xlsx_bytes`
    duplication between the unit and integration register-import test
    files)."""
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
    await db_session.commit()
    return band


async def _seed_common_bands(db_session: AsyncSession) -> None:
    """frequency: likely/rare; magnitude: high/low — labels chosen to
    exact-case-insensitive-match `_CSV_ROWS`' Likelihood/Impact cells
    ("Likely"/"Rare"/"High"/"Low") so `preselect_bindings` has something to
    match against."""
    await _seed_band(db_session, kind="frequency", label="likely", low=1.0, mode=3.2, high=10.0)
    await _seed_band(
        db_session, kind="frequency", label="rare", low=0.1, mode=0.3, high=1.0, sort_order=2
    )
    await _seed_band(
        db_session, kind="magnitude", label="high", low=1_000_000, mode=5_000_000, high=10_000_000
    )
    await _seed_band(
        db_session,
        kind="magnitude",
        label="low",
        low=1_000,
        mode=5_000,
        high=10_000,
        sort_order=2,
    )


async def _stage_to_bind(
    client: AsyncClient,
    *,
    headers: str = _CSV_HEADERS,
    rows: list[str] | None = None,
    column_map: dict[str, str] | None = None,
) -> str:
    """Upload a CSV + submit a valid column map, returning the resulting
    bind-step token. Reuses `_upload`/`_csv_bytes` (Task 4)."""
    cm = _FULL_COLUMN_MAP if column_map is None else column_map
    up = await _upload(client, data=_csv_bytes(headers, rows))
    assert up.status_code == 303, up.text
    token = _token_from_location(up)

    header_list = headers.split(",")
    form: dict[str, str] = {}
    for i, header in enumerate(header_list):
        form[f"header_{i}"] = header
        form[f"target_{i}"] = cm.get(header, "ignore")
    r = await csrf_post(client, f"/register-import/{token}/columns", form, follow_redirects=False)
    assert r.status_code == 303, r.text
    assert r.headers["location"].endswith(f"/register-import/{token}/bind")
    return token


def _bind_form(
    distinct: dict[str, list[str]], bindings: dict[str, dict[str, str]]
) -> dict[str, str]:
    """Build the bind POST body's `{group}_value_{i}`/`{group}_target_{i}`
    pairs from a known `distinct_values()`-shaped dict + desired bindings
    (a value absent from `bindings[group]` is submitted with a BLANK target,
    reproducing the "left unbound" case for 422 tests)."""
    form: dict[str, str] = {}
    for group in ("likelihood", "impact", "category"):
        for i, value in enumerate(distinct.get(group, [])):
            form[f"{group}_value_{i}"] = value
            target = bindings.get(group, {}).get(value)
            if target is not None:
                form[f"{group}_target_{i}"] = target
    return form


# `_CSV_ROWS`' distinct likelihood/impact/category values, in the SAME
# sorted order `RegisterImportService.distinct_values()` produces (verified
# against the shipped implementation: `sorted({...} - {""})`).
_DISTINCT = {
    "likelihood": ["Likely", "Rare"],
    "impact": ["High", "Low"],
    "category": ["Malware", "Phishing"],
}
# The full valid binding set for `_DISTINCT` — "Malware" preselects to
# "malware" (exact case-insensitive ThreatCategory match) but "Phishing"
# does not (no ThreatCategory member is spelled "phishing"), so it needs an
# explicit bind here.
_FULL_BINDINGS = {
    "likelihood": {"Likely": "likely", "Rare": "rare"},
    "impact": {"High": "high", "Low": "low"},
    "category": {"Malware": "malware", "Phishing": "social_engineering"},
}


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


def _token_from_location(resp: Response) -> str:
    loc = resp.headers["location"]
    m = _UUID_RE.search(loc)
    assert m, f"expected a token UUID in redirect Location {loc!r}"
    return m.group(0)


async def _upload(
    client: AsyncClient,
    *,
    filename: str = "register.csv",
    data: bytes | None = None,
    content_type: str = "text/csv",
    **kwargs: Any,
) -> Response:
    return await csrf_post(  # type: ignore[no-any-return]
        client,
        "/register-import",
        {},
        files={"file": (filename, data if data is not None else _csv_bytes(), content_type)},
        follow_redirects=False,
        **kwargs,
    )


async def _stage_csv_to_columns(client: AsyncClient) -> str:
    """Upload the fixture CSV (single implicit sheet) and return the token
    of the resulting `.../columns` redirect."""
    r = await _upload(client)
    assert r.status_code == 303
    return _token_from_location(r)


async def _manual_multipart_no_content_length(
    client: AsyncClient, *, csrf_token: str, size: int
) -> Response:
    """POST a multipart body via a streaming async generator (httpx omits
    Content-Length for a streamed ``content=`` body — see
    ``Headers({'host': ..., 'transfer-encoding': 'chunked'})``) so the
    route's pre-check (``content_length is not None``) is a no-op and only
    the POST-READ ``len(data)`` check (Sec-I3 layer 2/3) can reject it."""
    boundary = "----registerimporttestboundary"
    body = io.BytesIO()
    body.write(
        f'--{boundary}\r\nContent-Disposition: form-data; name="_csrf"\r\n\r\n'
        f"{csrf_token}\r\n".encode()
    )
    body.write(
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
        f'filename="big.csv"\r\nContent-Type: text/csv\r\n\r\n'.encode()
    )
    body.write(b"x" * size)
    body.write(f"\r\n--{boundary}--\r\n".encode())
    payload = body.getvalue()

    async def _gen() -> AsyncIterator[bytes]:
        chunk = 64 * 1024
        for i in range(0, len(payload), chunk):
            yield payload[i : i + chunk]

    return await client.post(
        "/register-import",
        content=_gen(),
        headers={"content-type": f"multipart/form-data; boundary={boundary}"},
    )


# ---- module-level sync guard --------------------------------------------


def test_target_options_match_targets() -> None:
    """`_TARGET_OPTIONS` (route rendering order/labels) must carry exactly
    the 8 values in `TARGETS` (service validation set) — a drift here would
    mean the column-map <select> offers a target the service rejects, or
    is missing one it accepts."""
    assert {value for value, _label in _TARGET_OPTIONS} == TARGETS
    assert len(_TARGET_OPTIONS) == len(TARGETS)


# ---- RBAC: GET upload form -----------------------------------------------


async def test_get_upload_form_admin_ok(admin_client: AsyncClient) -> None:
    r = await admin_client.get("/register-import")
    assert r.status_code == 200
    assert "register" in r.text.lower()


async def test_get_upload_form_analyst_forbidden(analyst_client: AsyncClient) -> None:
    r = await analyst_client.get("/register-import")
    assert r.status_code in (403, 302)


# ---- RBAC: POST upload ----------------------------------------------------


async def test_post_upload_analyst_forbidden(analyst_client: AsyncClient) -> None:
    r = await _upload(analyst_client)
    assert r.status_code in (403, 302)


# ---- CSRF ------------------------------------------------------------------


async def test_upload_without_csrf_rejected(admin_client: AsyncClient) -> None:
    r = await admin_client.post(
        "/register-import",
        files={"file": ("register.csv", _csv_bytes(), "text/csv")},
    )
    assert r.status_code in (403, 422)


# ---- oversize: both belt-and-suspenders layers -----------------------------


async def test_oversize_upload_content_length_precheck_413(admin_client: AsyncClient) -> None:
    """Sec-I3 layer 1/3: a normal (accurate Content-Length) oversized upload
    is rejected BEFORE the body is read — the common real-world case."""
    big = b"x" * (MAX_UPLOAD_BYTES + 1)
    r = await _upload(admin_client, data=big)
    assert r.status_code == 413


async def test_oversize_upload_post_read_layer_413(admin_client: AsyncClient) -> None:
    """Sec-I3 layer 2/3: no Content-Length header (chunked transfer) skips
    the pre-check entirely; the post-read ``len(data)`` check must still
    independently reject the oversized body."""
    bootstrap = await admin_client.get("/setup")
    assert bootstrap.status_code in (200, 303)
    csrf_token = admin_client.cookies.get("csrf_token")
    assert csrf_token

    r = await _manual_multipart_no_content_length(
        admin_client, csrf_token=csrf_token, size=MAX_UPLOAD_BYTES + 1
    )
    assert r.status_code == 413


# ---- bad filename ----------------------------------------------------------


async def test_upload_blank_filename_rejected_422(admin_client: AsyncClient) -> None:
    r = await _upload(admin_client, filename="   ")
    assert r.status_code == 422
    assert "filename" in r.text.lower()


# ---- happy paths: csv / xlsx single sheet -> columns -----------------------


async def test_upload_csv_redirects_to_columns(admin_client: AsyncClient) -> None:
    r = await _upload(admin_client)
    assert r.status_code == 303
    assert r.headers["location"].endswith("/columns")


async def test_upload_xlsx_single_sheet_redirects_to_columns(admin_client: AsyncClient) -> None:
    data = _xlsx_bytes({"Sheet1": [["Title", "Likelihood", "Impact"], ["A", "Likely", "High"]]})
    r = await _upload(
        admin_client,
        filename="register.xlsx",
        data=data,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    assert r.status_code == 303
    assert r.headers["location"].endswith("/columns")


async def test_columns_get_renders_headers(admin_client: AsyncClient) -> None:
    token = await _stage_csv_to_columns(admin_client)
    r = await admin_client.get(f"/register-import/{token}/columns")
    assert r.status_code == 200
    for header in _CSV_HEADERS.split(","):
        assert header in r.text


# ---- happy path: xlsx multi-sheet -> sheet -> columns ----------------------


async def test_upload_xlsx_multi_sheet_redirects_to_sheet(admin_client: AsyncClient) -> None:
    data = _xlsx_bytes(
        {
            "Q1": [["Title", "Likelihood", "Impact"], ["A", "Likely", "High"]],
            "Q2": [["Title", "Likelihood", "Impact"], ["B", "Rare", "Low"]],
        }
    )
    r = await _upload(
        admin_client,
        filename="register.xlsx",
        data=data,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    assert r.status_code == 303
    assert r.headers["location"].endswith("/sheet")


async def test_sheet_get_renders_sheet_names(admin_client: AsyncClient) -> None:
    data = _xlsx_bytes(
        {
            "Q1": [["Title", "Likelihood", "Impact"], ["A", "Likely", "High"]],
            "Q2": [["Title", "Likelihood", "Impact"], ["B", "Rare", "Low"]],
        }
    )
    r = await _upload(
        admin_client,
        filename="register.xlsx",
        data=data,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    token = _token_from_location(r)
    sheet_page = await admin_client.get(f"/register-import/{token}/sheet")
    assert sheet_page.status_code == 200
    assert "Q1" in sheet_page.text
    assert "Q2" in sheet_page.text


async def test_sheet_post_valid_redirects_to_columns(admin_client: AsyncClient) -> None:
    data = _xlsx_bytes(
        {
            "Q1": [["Title", "Likelihood", "Impact"], ["A", "Likely", "High"]],
            "Q2": [["Title", "Likelihood", "Impact"], ["B", "Rare", "Low"]],
        }
    )
    up = await _upload(
        admin_client,
        filename="register.xlsx",
        data=data,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    token = _token_from_location(up)
    r = await csrf_post(
        admin_client,
        f"/register-import/{token}/sheet",
        {"sheet_name": "Q2"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"].endswith("/columns")


async def test_sheet_post_invalid_sheet_name_422_rerender(admin_client: AsyncClient) -> None:
    data = _xlsx_bytes(
        {
            "Q1": [["Title", "Likelihood", "Impact"], ["A", "Likely", "High"]],
            "Q2": [["Title", "Likelihood", "Impact"], ["B", "Rare", "Low"]],
        }
    )
    up = await _upload(
        admin_client,
        filename="register.xlsx",
        data=data,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    token = _token_from_location(up)
    r = await csrf_post(
        admin_client,
        f"/register-import/{token}/sheet",
        {"sheet_name": "NoSuchSheet"},
        follow_redirects=False,
    )
    assert r.status_code == 422
    assert "Q1" in r.text and "Q2" in r.text  # re-rendered with the real options


# ---- column-map: validation 422 + successful 303 chain ---------------------


async def test_columns_post_missing_required_target_422(admin_client: AsyncClient) -> None:
    token = await _stage_csv_to_columns(admin_client)
    headers = _CSV_HEADERS.split(",")
    # Map only "title" — likelihood/impact left "ignore" (the form_field
    # default) -> set_column_map's "exactly one column must map to X" guard.
    form: dict[str, str] = {}
    for i, header in enumerate(headers):
        form[f"header_{i}"] = header
        form[f"target_{i}"] = "title" if header == "Title" else "ignore"
    r = await csrf_post(
        admin_client,
        f"/register-import/{token}/columns",
        form,
        follow_redirects=False,
    )
    assert r.status_code == 422
    assert "exactly one column" in r.text.lower()
    # Re-render still shows the mapping form (headers present), not a redirect.
    for header in headers:
        assert header in r.text


async def test_columns_post_valid_redirects_to_bind(admin_client: AsyncClient) -> None:
    token = await _stage_csv_to_columns(admin_client)
    headers = _CSV_HEADERS.split(",")
    form: dict[str, str] = {}
    for i, header in enumerate(headers):
        form[f"header_{i}"] = header
        form[f"target_{i}"] = _FULL_COLUMN_MAP[header]
    r = await csrf_post(
        admin_client,
        f"/register-import/{token}/columns",
        form,
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"].endswith(f"/register-import/{token}/bind")


# ---- successful 303 chain: upload -> columns -> (bind target) -------------


async def test_full_303_chain_csv_upload_to_bind_target(admin_client: AsyncClient) -> None:
    up = await _upload(admin_client)
    assert up.status_code == 303
    token = _token_from_location(up)
    assert up.headers["location"] == f"/register-import/{token}/columns"

    columns_get = await admin_client.get(f"/register-import/{token}/columns")
    assert columns_get.status_code == 200

    headers = _CSV_HEADERS.split(",")
    form: dict[str, str] = {}
    for i, header in enumerate(headers):
        form[f"header_{i}"] = header
        form[f"target_{i}"] = _FULL_COLUMN_MAP[header]
    columns_post = await csrf_post(
        admin_client,
        f"/register-import/{token}/columns",
        form,
        follow_redirects=False,
    )
    assert columns_post.status_code == 303
    assert columns_post.headers["location"] == f"/register-import/{token}/bind"


async def test_full_303_chain_xlsx_multi_sheet(admin_client: AsyncClient) -> None:
    data = _xlsx_bytes(
        {
            "Q1": [["Title", "Likelihood", "Impact"], ["A", "Likely", "High"]],
            "Q2": [["Title", "Likelihood", "Impact"], ["B", "Rare", "Low"]],
        }
    )
    up = await _upload(
        admin_client,
        filename="register.xlsx",
        data=data,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    assert up.status_code == 303
    token = _token_from_location(up)
    assert up.headers["location"] == f"/register-import/{token}/sheet"

    sheet_post = await csrf_post(
        admin_client,
        f"/register-import/{token}/sheet",
        {"sheet_name": "Q1"},
        follow_redirects=False,
    )
    assert sheet_post.status_code == 303
    assert sheet_post.headers["location"] == f"/register-import/{token}/columns"


# ---- expired / unknown token -> 409 ----------------------------------------


_UNKNOWN_TOKEN = "00000000-0000-0000-0000-000000000000"


async def test_columns_get_unknown_token_expired_409(admin_client: AsyncClient) -> None:
    r = await admin_client.get(f"/register-import/{_UNKNOWN_TOKEN}/columns")
    assert r.status_code == 409
    assert "expired" in r.text.lower() or "not" in r.text.lower()


async def test_columns_post_unknown_token_expired_409(admin_client: AsyncClient) -> None:
    r = await csrf_post(
        admin_client,
        f"/register-import/{_UNKNOWN_TOKEN}/columns",
        {"header_0": "Title", "target_0": "title"},
        follow_redirects=False,
    )
    assert r.status_code == 409


async def test_sheet_get_unknown_token_expired_409(admin_client: AsyncClient) -> None:
    r = await admin_client.get(f"/register-import/{_UNKNOWN_TOKEN}/sheet")
    assert r.status_code == 409


async def test_sheet_post_unknown_token_expired_409(admin_client: AsyncClient) -> None:
    r = await csrf_post(
        admin_client,
        f"/register-import/{_UNKNOWN_TOKEN}/sheet",
        {"sheet_name": "Sheet1"},
        follow_redirects=False,
    )
    assert r.status_code == 409


@pytest.mark.parametrize("bad_token", ["not-a-uuid", ""])
async def test_columns_get_malformed_token_expired_409(
    admin_client: AsyncClient, bad_token: str
) -> None:
    url = f"/register-import/{bad_token}/columns" if bad_token else "/register-import//columns"
    r = await admin_client.get(url)
    # A truly empty path segment 404s at the router; a non-UUID string
    # reaches the handler and is rejected as an expired/malformed token.
    assert r.status_code in (404, 409)


# ===========================================================================
# Task 5: value-bind step + binding profiles
# ===========================================================================


# ---- module-level sync guard --------------------------------------------


def test_park_option_is_last_category_option() -> None:
    """`_CATEGORY_OPTIONS` (route rendering) carries the exact park value +
    grammar the plan pins verbatim (Meth-R2-NTH-1)."""
    assert _CATEGORY_OPTIONS[-1] == (
        _PARK_VALUE,
        "Parked — out of scope (neither information- nor OT-risk; see #39)",
    )


# ---- RBAC -----------------------------------------------------------------


async def test_bind_get_analyst_forbidden(analyst_client: AsyncClient) -> None:
    r = await analyst_client.get(f"/register-import/{_UNKNOWN_TOKEN}/bind")
    assert r.status_code in (403, 302)


async def test_bind_post_analyst_forbidden(analyst_client: AsyncClient) -> None:
    r = await csrf_post(
        analyst_client,
        f"/register-import/{_UNKNOWN_TOKEN}/bind",
        {},
        follow_redirects=False,
    )
    assert r.status_code in (403, 302)


async def test_apply_profile_analyst_forbidden(analyst_client: AsyncClient) -> None:
    r = await csrf_post(
        analyst_client,
        f"/register-import/{_UNKNOWN_TOKEN}/apply-profile",
        {"profile_id": _UNKNOWN_TOKEN},
        follow_redirects=False,
    )
    assert r.status_code in (403, 302)


# ---- expired / unknown token -----------------------------------------------


async def test_bind_get_unknown_token_expired_409(admin_client: AsyncClient) -> None:
    r = await admin_client.get(f"/register-import/{_UNKNOWN_TOKEN}/bind")
    assert r.status_code == 409


async def test_bind_post_unknown_token_expired_409(admin_client: AsyncClient) -> None:
    r = await csrf_post(
        admin_client,
        f"/register-import/{_UNKNOWN_TOKEN}/bind",
        {},
        follow_redirects=False,
    )
    assert r.status_code == 409


async def test_apply_profile_unknown_token_expired_409(admin_client: AsyncClient) -> None:
    r = await csrf_post(
        admin_client,
        f"/register-import/{_UNKNOWN_TOKEN}/apply-profile",
        {"profile_id": _UNKNOWN_TOKEN},
        follow_redirects=False,
    )
    assert r.status_code == 409


# ---- GET bind: renders distinct values + info callout ----------------------


async def test_bind_get_renders_distinct_values_and_park_callout(
    admin_client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_common_bands(db_session)
    token = await _stage_to_bind(admin_client)

    r = await admin_client.get(f"/register-import/{token}/bind")
    assert r.status_code == 200
    for value in ("Likely", "Rare", "High", "Low", "Malware", "Phishing"):
        assert value in r.text

    # D5 park-semantics copy: counted + reported, never errors; link to band mgmt.
    lowered = r.text.lower()
    assert "counted" in lowered
    assert "reported" in lowered
    assert "never" in lowered
    assert 'href="/qualitative-bands"' in r.text


# ---- pre-selection exactness: "High" matches, "Hi" does NOT ----------------


async def test_bind_get_preselects_exact_case_insensitive_match_only(
    admin_client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_common_bands(db_session)
    headers = "Title,Likelihood,Impact,Category"
    rows = ["A,Likely,Hi,Phishing", "B,Rare,High,Phishing"]
    token = await _stage_to_bind(
        admin_client,
        headers=headers,
        rows=rows,
        column_map={
            "Title": "title",
            "Likelihood": "likelihood",
            "Impact": "impact",
            "Category": "category",
        },
    )

    r = await admin_client.get(f"/register-import/{token}/bind")
    assert r.status_code == 200

    # distinct.impact sorts to ["Hi", "High"] (shorter prefix sorts first).
    hi_select = re.search(r'id="impact_target_0".*?</select>', r.text, re.S)
    high_select = re.search(r'id="impact_target_1".*?</select>', r.text, re.S)
    assert hi_select and high_select, r.text

    # "High" exact-case-insensitive-matches the "high" magnitude band label.
    assert 'value="high" selected' in high_select.group(0)
    # "Hi" must NOT preselect a real band — no heuristics, no partial match.
    # Only the blank placeholder option is selected (form_field's <select>
    # defaults to it when `value` matches no real option — the whole point
    # of prepending a blank option, otherwise the browser would silently
    # default to the first real option and LOOK bound when it isn't).
    assert 'value="" selected' in hi_select.group(0)
    assert 'value="high" selected' not in hi_select.group(0)
    assert 'value="low" selected' not in hi_select.group(0)


# ---- POST bind: happy path -------------------------------------------------


async def test_bind_post_happy_path_redirects_to_preview(
    admin_client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_common_bands(db_session)
    token = await _stage_to_bind(admin_client)

    form = _bind_form(_DISTINCT, _FULL_BINDINGS)
    r = await csrf_post(
        admin_client, f"/register-import/{token}/bind", form, follow_redirects=False
    )
    assert r.status_code == 303, r.text
    assert r.headers["location"] == f"/register-import/{token}/preview"


async def test_bind_post_park_category_value_accepted(
    admin_client: AsyncClient, db_session: AsyncSession
) -> None:
    """Behavioral sync guard (see the `_PARK_VALUE` docstring in
    routes/register_import.py): if the route's park value ever drifted from
    the service's `_PARKED_CATEGORY`, this POST would 422 with "is not a
    valid target" instead of redirecting."""
    await _seed_common_bands(db_session)
    token = await _stage_to_bind(admin_client)

    bindings = {
        "likelihood": _FULL_BINDINGS["likelihood"],
        "impact": _FULL_BINDINGS["impact"],
        "category": {"Malware": "malware", "Phishing": _PARK_VALUE},
    }
    form = _bind_form(_DISTINCT, bindings)
    r = await csrf_post(
        admin_client, f"/register-import/{token}/bind", form, follow_redirects=False
    )
    assert r.status_code == 303, r.text


# ---- POST bind: unbound value -> 422 with per-field errors ------------------


async def test_bind_post_unbound_value_422_per_field_errors(
    admin_client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_common_bands(db_session)
    token = await _stage_to_bind(admin_client)

    # Leave "Low" (impact) unbound; everything else fully bound.
    bindings = {
        "likelihood": _FULL_BINDINGS["likelihood"],
        "impact": {"High": "high"},  # "Low" omitted -> blank target
        "category": _FULL_BINDINGS["category"],
    }
    form = _bind_form(_DISTINCT, bindings)
    r = await csrf_post(
        admin_client, f"/register-import/{token}/bind", form, follow_redirects=False
    )
    assert r.status_code == 422
    assert "must be bound" in r.text.lower()
    # Re-render still shows the form (not a redirect) with the other
    # already-picked values still present.
    assert "Low" in r.text
    assert 'value="high" selected' in r.text


async def test_bind_post_all_unbound_422(
    admin_client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_common_bands(db_session)
    token = await _stage_to_bind(admin_client)
    form = _bind_form(_DISTINCT, {})
    r = await csrf_post(
        admin_client, f"/register-import/{token}/bind", form, follow_redirects=False
    )
    assert r.status_code == 422


# ---- POST bind: save as profile + duplicate name ----------------------------


async def test_bind_post_save_profile_then_duplicate_name_422(
    admin_client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_common_bands(db_session)

    token_a = await _stage_to_bind(admin_client)
    form_a = _bind_form(_DISTINCT, _FULL_BINDINGS)
    form_a["profile_name"] = "Quarterly export"
    r_a = await csrf_post(
        admin_client, f"/register-import/{token_a}/bind", form_a, follow_redirects=False
    )
    assert r_a.status_code == 303, r_a.text

    profile = (
        await db_session.execute(
            select(RegisterBindingProfile).where(RegisterBindingProfile.name == "Quarterly export")
        )
    ).scalar_one_or_none()
    assert profile is not None

    token_b = await _stage_to_bind(admin_client)
    form_b = _bind_form(_DISTINCT, _FULL_BINDINGS)
    form_b["profile_name"] = "Quarterly export"
    r_b = await csrf_post(
        admin_client, f"/register-import/{token_b}/bind", form_b, follow_redirects=False
    )
    assert r_b.status_code == 422
    assert "already exists" in r_b.text.lower()


# ---- apply-profile: drift warning flashed ----------------------------------


async def test_apply_profile_drift_warning_flashed(
    authed_admin: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    client, org_id = authed_admin
    await _seed_common_bands(db_session)

    # Save a profile with NO org-band overrides in play yet.
    token_a = await _stage_to_bind(client)
    form_a = _bind_form(_DISTINCT, _FULL_BINDINGS)
    form_a["profile_name"] = "Drift export"
    r_a = await csrf_post(
        client, f"/register-import/{token_a}/bind", form_a, follow_redirects=False
    )
    assert r_a.status_code == 303, r_a.text

    profile = (
        await db_session.execute(
            select(RegisterBindingProfile).where(RegisterBindingProfile.name == "Drift export")
        )
    ).scalar_one_or_none()
    assert profile is not None

    # Drift trigger: a NEW org-band override created after the profile was
    # saved (per _drift_warnings' "present now but absent from snapshot"
    # branch — services/register_import.py's docstring).
    db_session.add(
        QualitativeMappingOrgBand(
            organization_id=org_id,
            kind="frequency",
            label="critical",
            low=10.0,
            mode=20.0,
            high=50.0,
            reason="test drift trigger",
            version=1,
        )
    )
    await db_session.commit()

    token_b = await _stage_to_bind(client)
    apply_resp = await csrf_post(
        client,
        f"/register-import/{token_b}/apply-profile",
        {"profile_id": str(profile.id)},
        follow_redirects=False,
    )
    assert apply_resp.status_code == 303, apply_resp.text
    location = apply_resp.headers["location"]
    assert location.startswith(f"/register-import/{token_b}/bind?drift=")

    follow = await client.get(location)
    assert follow.status_code == 200
    assert "alert-warning" in follow.text
    assert "is new since this profile was saved" in follow.text


async def test_apply_profile_unknown_profile_404(
    admin_client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_common_bands(db_session)
    token = await _stage_to_bind(admin_client)
    r = await csrf_post(
        admin_client,
        f"/register-import/{token}/apply-profile",
        {"profile_id": str(uuid.uuid4())},
        follow_redirects=False,
    )
    assert r.status_code == 404


# ===========================================================================
# Task 6: preview, convert, report
# ===========================================================================


async def _stage_to_preview(
    client: AsyncClient,
    *,
    headers: str = _CSV_HEADERS,
    rows: list[str] | None = None,
    column_map: dict[str, str] | None = None,
    distinct: dict[str, list[str]] | None = None,
    bindings: dict[str, dict[str, str]] | None = None,
) -> str:
    """Drive a fixture all the way through stage -> columns -> bind, and
    return the resulting preview-step token (Task 6 extends the Task 5
    `_stage_to_bind` helper one step further)."""
    token = await _stage_to_bind(client, headers=headers, rows=rows, column_map=column_map)
    form = _bind_form(
        distinct if distinct is not None else _DISTINCT,
        bindings if bindings is not None else _FULL_BINDINGS,
    )
    r = await csrf_post(client, f"/register-import/{token}/bind", form, follow_redirects=False)
    assert r.status_code == 303, r.text
    assert r.headers["location"] == f"/register-import/{token}/preview"
    return token


def _convert_button(html: str) -> str:
    m = re.search(r'<button type="submit"[^>]*>[\s\S]*?</button>', html)
    assert m, html
    return m.group(0)


# ---- RBAC -------------------------------------------------------------


async def test_preview_get_analyst_forbidden(analyst_client: AsyncClient) -> None:
    r = await analyst_client.get(f"/register-import/{_UNKNOWN_TOKEN}/preview")
    assert r.status_code in (403, 302)


async def test_convert_post_analyst_forbidden(analyst_client: AsyncClient) -> None:
    r = await csrf_post(
        analyst_client,
        f"/register-import/{_UNKNOWN_TOKEN}/convert",
        {},
        follow_redirects=False,
    )
    assert r.status_code in (403, 302)


# ---- expired / unknown token -------------------------------------------


async def test_preview_get_unknown_token_expired_409(admin_client: AsyncClient) -> None:
    r = await admin_client.get(f"/register-import/{_UNKNOWN_TOKEN}/preview")
    assert r.status_code == 409


async def test_convert_post_unknown_token_expired_409(admin_client: AsyncClient) -> None:
    r = await csrf_post(
        admin_client,
        f"/register-import/{_UNKNOWN_TOKEN}/convert",
        {},
        follow_redirects=False,
    )
    assert r.status_code == 409


# ---- GET preview: counts, badges, sl_note, epistemic callout -----------


async def test_preview_get_renders_counts_badges_and_callout(
    admin_client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_common_bands(db_session)
    token = await _stage_to_preview(admin_client)

    r = await admin_client.get(f"/register-import/{token}/preview")
    assert r.status_code == 200
    lowered = r.text.lower()

    # _CSV_ROWS has 2 rows, both fully bound to real categories -> 2
    # would-create, 0 parked/duplicate/error.
    assert "2" in r.text  # would-create count rendered somewhere
    assert "badge-success" in r.text  # would_create -> "create" badge key

    # Epistemic callout (methodology-owned copy — DRAFTS, never results).
    assert "draft" in lowered
    assert "never" in lowered

    # sl_note (services.qualitative_converter.SL_NOTE) surfaced verbatim.
    assert "sl not derivable" in lowered

    # Convert button enabled (would_create > 0).
    assert "disabled" not in _convert_button(r.text)


async def test_preview_get_convert_disabled_at_zero_would_create(
    admin_client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_common_bands(db_session)
    # Both category values parked -> 0 would-create rows.
    bindings = {
        "likelihood": _FULL_BINDINGS["likelihood"],
        "impact": _FULL_BINDINGS["impact"],
        "category": {"Malware": _PARK_VALUE, "Phishing": _PARK_VALUE},
    }
    token = await _stage_to_preview(admin_client, bindings=bindings)

    r = await admin_client.get(f"/register-import/{token}/preview")
    assert r.status_code == 200
    assert "disabled" in _convert_button(r.text)
    assert "badge-ghost" in r.text  # parked rows -> "parked" badge key


async def test_preview_get_before_bind_step_422(
    admin_client: AsyncClient, db_session: AsyncSession
) -> None:
    """GET preview on a token that has only reached the column-map step (no
    /bind POST yet) 422s — preview()'s build_bound_rows requires
    value_bindings to already be set in state_json."""
    await _seed_common_bands(db_session)
    token = await _stage_to_bind(admin_client)
    r = await admin_client.get(f"/register-import/{token}/preview")
    assert r.status_code == 422


# ---- POST convert: happy path + report content --------------------------


async def test_convert_post_happy_path_report_content(
    admin_client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_common_bands(db_session)
    token = await _stage_to_preview(admin_client)

    r = await csrf_post(
        admin_client, f"/register-import/{token}/convert", {}, follow_redirects=False
    )
    assert r.status_code == 200, r.text
    assert "Phishing risk" in r.text
    assert "Malware risk" in r.text
    assert "/scenarios/" in r.text  # created rows link to the scenario detail
    lowered = r.text.lower()
    assert "what next" in lowered
    assert "mapping version" in lowered


async def test_convert_post_report_shows_parked_and_skipped_sections(
    admin_client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_common_bands(db_session)
    bindings = {
        "likelihood": _FULL_BINDINGS["likelihood"],
        "impact": _FULL_BINDINGS["impact"],
        "category": {"Malware": "malware", "Phishing": _PARK_VALUE},
    }
    token = await _stage_to_preview(admin_client, bindings=bindings)
    r = await csrf_post(
        admin_client, f"/register-import/{token}/convert", {}, follow_redirects=False
    )
    assert r.status_code == 200, r.text
    lowered = r.text.lower()
    assert "parked" in lowered


async def test_convert_post_single_use_second_post_409(
    admin_client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_common_bands(db_session)
    token = await _stage_to_preview(admin_client)

    r1 = await csrf_post(
        admin_client, f"/register-import/{token}/convert", {}, follow_redirects=False
    )
    assert r1.status_code == 200

    r2 = await csrf_post(
        admin_client, f"/register-import/{token}/convert", {}, follow_redirects=False
    )
    assert r2.status_code == 409


async def _seed_profile(db_session: AsyncSession, *, org_id, name: str):
    from idraa.models.register_binding_profile import RegisterBindingProfile

    profile = RegisterBindingProfile(
        organization_id=org_id,
        name=name,
        column_map={"T": "title"},
        value_bindings={"likelihood": {}, "impact": {}, "category": {}},
        mapping_versions_snapshot={"canonical": {}, "org": {}},
    )
    db_session.add(profile)
    await db_session.commit()
    return profile


@pytest.mark.asyncio
async def test_delete_profile_removes_row_and_audits(
    authed_admin: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    admin_client, org_id = authed_admin
    profile = await _seed_profile(db_session, org_id=org_id, name="stale")
    r = await csrf_post(
        admin_client,
        f"/register-import/profiles/{profile.id}/delete",
        {},
        follow_redirects=False,
    )
    assert r.status_code == 303
    from sqlalchemy import select as _sel

    from idraa.models.audit_log import AuditLog
    from idraa.models.register_binding_profile import RegisterBindingProfile

    db_session.expire_all()
    rows = (await db_session.execute(_sel(RegisterBindingProfile))).scalars().all()
    assert rows == []
    audit = (
        (
            await db_session.execute(
                _sel(AuditLog).where(AuditLog.action == "register_binding_profile.delete")
            )
        )
        .scalars()
        .all()
    )
    assert len(audit) == 1


@pytest.mark.asyncio
async def test_delete_profile_cross_org_404(
    authed_admin: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    from tests.factories import create_org

    admin_client, _admin_org = authed_admin
    other_org = await create_org(db_session, name="Other Org For Profile")
    other_profile = await _seed_profile(db_session, org_id=other_org.id, name="other-org")
    r = await csrf_post(
        admin_client,
        f"/register-import/profiles/{other_profile.id}/delete",
        {},
        follow_redirects=False,
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_delete_profile_rbac_analyst_403(authed_analyst, db_session: AsyncSession) -> None:
    analyst_client, org_id = authed_analyst
    profile = await _seed_profile(db_session, org_id=org_id, name="rbac-check")
    r = await csrf_post(
        analyst_client,
        f"/register-import/profiles/{profile.id}/delete",
        {},
        follow_redirects=False,
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_profile_delete_confirm_is_js_safe(
    authed_admin: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    """JS-context XSS regression (PR #49 review BLOCKER): a profile name with
    quotes must render via | tojson, never as a bare Jinja interpolation
    inside the onsubmit JS string (HTML autoescape entity-decodes BEFORE the
    JS engine parses, so &#39; breaks out)."""
    admin_client, org_id = authed_admin
    await _seed_profile(db_session, org_id=org_id, name='O\'Brien "Q3" profile')
    r = await admin_client.get("/register-import")
    assert r.status_code == 200
    # tojson renders the name as a JSON string literal concatenated into the
    # confirm() call — the raw single-quote interpolation must be gone.
    assert "Delete profile {{" not in r.text
    assert '+ "?");' in r.text
    assert "O\\u0027Brien" in r.text or 'O\'Brien \\"Q3\\"' in r.text or "\\u0022" in r.text
