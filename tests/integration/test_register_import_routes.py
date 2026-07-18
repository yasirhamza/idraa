"""Register import route tests — staged upload / sheet-pick / column-map (Task 4).

Mirrors ``tests/integration/test_scenario_import_routes.py``'s route-test
patterns and the ``csrf_post`` double-submit helper. RBAC posture: every
route is ``require_role(ADMIN)`` — the analyst-403 tests pin this.

Epic #34 P1c Task 4. Later tasks (5/6) extend ``routes/register_import.py``
with the value-bind step and preview/convert/report, so the "successful
303 chain" tests here only assert the redirect TARGET for the bind step
(``.../bind`` does not exist until Task 5 lands in the same branch).
"""

from __future__ import annotations

import io
import re
from collections.abc import AsyncIterator
from typing import Any

import openpyxl
import pytest
from httpx import AsyncClient, Response

from idraa.routes.deps import MAX_UPLOAD_BYTES
from idraa.routes.register_import import _TARGET_OPTIONS
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
