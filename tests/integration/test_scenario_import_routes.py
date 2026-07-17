"""Scenario import route tests — admin-only two-step CSV/JSON upload (Task 6).

Mirrors ``tests/integration/test_overlays_import.py`` route patterns and the
``csrf_post`` double-submit helper. CSRF posture matches overlays exactly: the
multipart upload POST AND the form-encoded confirm POST both carry the
``_csrf`` token (overlays' ``csrf_post`` injects it into the form ``data`` even
when ``files=`` is present). The negative ``test_confirm_without_csrf_rejected``
pins that the confirm POST is genuinely CSRF-protected.

RBAC posture: ALL import routes (form, both downloads, upload POST, confirm) are
``require_role(ADMIN)``. The viewer-403 + analyst-403 tests pin this.
"""

from __future__ import annotations

import json
import re
import uuid

from httpx import AsyncClient

from tests.conftest import csrf_post

_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")

_CSV = (
    "name,description,scenario_type,threat_category,threat_actor_type,attack_vector,"
    "asset_class,version,status,distribution,tef_low,tef_mode,tef_high,vuln_low,vuln_mode,"
    "vuln_high,pl_low,pl_mode,pl_high,sl_low,sl_mode,sl_high\n"
    "RouteCSV,,custom,ransomware,cybercriminals,,systems,1.0,active,PERT,"
    "0.1,0.5,2,0.2,0.35,0.6,100000,1000000,15000000,,,\n"
)


def _extract_token(html: str) -> str:
    """Pull the preview-token UUID out of the rendered preview page."""
    m = _UUID_RE.search(html)
    assert m, "expected a preview-token UUID in the preview body"
    return m.group(0)


# ---- RBAC: form GET ---------------------------------------------------


async def test_get_import_form_admin_ok(admin_client: AsyncClient) -> None:
    r = await admin_client.get("/scenarios/import")
    assert r.status_code == 200
    assert "import" in r.text.lower()


async def test_get_import_form_analyst_forbidden(analyst_client: AsyncClient) -> None:
    r = await analyst_client.get("/scenarios/import")
    assert r.status_code in (403, 302)


async def test_get_import_form_viewer_forbidden(viewer_client: AsyncClient) -> None:  # SC-B1
    r = await viewer_client.get("/scenarios/import")
    assert r.status_code in (403, 302)


# ---- RBAC: upload POST ------------------------------------------------


async def test_post_import_analyst_forbidden(analyst_client: AsyncClient) -> None:
    r = await csrf_post(
        analyst_client,
        "/scenarios/import",
        {},
        files={"file": ("s.csv", _CSV, "text/csv")},
        follow_redirects=False,
    )
    assert r.status_code in (403, 302)


async def test_post_import_viewer_forbidden(viewer_client: AsyncClient) -> None:  # SC-B1
    r = await csrf_post(
        viewer_client,
        "/scenarios/import",
        {},
        files={"file": ("s.csv", _CSV, "text/csv")},
        follow_redirects=False,
    )
    assert r.status_code in (403, 302)


# ---- template / sample downloads (SC-I6) ------------------------------


async def test_template_and_sample_download(admin_client: AsyncClient) -> None:  # SC-I6
    t = await admin_client.get("/scenarios/import/template.csv")
    assert t.status_code == 200 and "attachment" in t.headers["content-disposition"]
    s = await admin_client.get("/scenarios/import/sample.json")
    assert s.status_code == 200
    # The generated template/sample MUST parse cleanly (no comment lines — I6).
    from idraa.services.scenario_import_parsers import parse_csv_flat, parse_json_nested

    pairs_c, err_c = parse_csv_flat(t.content)
    assert err_c == [] and pairs_c is not None
    pairs_j, err_j = parse_json_nested(s.content)
    assert err_j == [] and pairs_j is not None


async def test_template_download_analyst_forbidden(analyst_client: AsyncClient) -> None:
    r = await analyst_client.get("/scenarios/import/template.csv")
    assert r.status_code in (403, 302)


async def test_sample_download_viewer_forbidden(viewer_client: AsyncClient) -> None:
    r = await viewer_client.get("/scenarios/import/sample.json")
    assert r.status_code in (403, 302)


# ---- CSRF negative (SC-I4/I8) -----------------------------------------


async def test_confirm_without_csrf_rejected(admin_client: AsyncClient) -> None:  # SC-I4/I8
    # Match the overlays CSRF posture: the confirm POST is CSRF-protected.
    # No _csrf field/cookie dance -> CSRFMiddleware rejects.
    r = await admin_client.post("/scenarios/import/confirm", data={"token": "x"})
    assert r.status_code in (403, 422)


# ---- preview + confirm happy paths ------------------------------------


async def test_post_import_csv_shows_preview(admin_client: AsyncClient) -> None:
    r = await csrf_post(
        admin_client,
        "/scenarios/import",
        {},
        files={"file": ("s.csv", _CSV, "text/csv")},
    )
    assert r.status_code == 200
    assert "RouteCSV" in r.text
    assert "create" in r.text.lower()
    # Mobile tranche 2e: the preview renders via the import_preview card-stack
    # macro — a desktop table (hidden md:block) AND a mobile card stack.
    assert "hidden md:block" in r.text
    assert "md:hidden" in r.text
    assert "badge-success" in r.text  # the "create" action badge


async def test_post_import_json_then_confirm_creates(admin_client: AsyncClient) -> None:
    payload = json.dumps(
        [
            {
                "name": "RouteJSON",
                "threat_category": "malware",
                "threat_event_frequency": {
                    "distribution": "PERT",
                    "low": 1,
                    "mode": 2,
                    "high": 3,
                },
                "vulnerability": {"distribution": "PERT", "low": 0.1, "mode": 0.2, "high": 0.3},
                "primary_loss": {"distribution": "PERT", "low": 10, "mode": 20, "high": 30},
            },
        ]
    )
    pr = await csrf_post(
        admin_client,
        "/scenarios/import",
        {},
        files={"file": ("s.json", payload, "application/json")},
    )
    assert pr.status_code == 200
    token = _extract_token(pr.text)
    cr = await csrf_post(
        admin_client,
        "/scenarios/import/confirm",
        {"token": token},
        follow_redirects=False,
    )
    assert cr.status_code in (200, 303)
    lst = await admin_client.get("/scenarios")
    assert "RouteJSON" in lst.text


# ---- guards -----------------------------------------------------------


async def test_oversize_upload_rejected(admin_client: AsyncClient) -> None:
    big = b"x" * (5 * 1024 * 1024 + 1)
    r = await csrf_post(
        admin_client,
        "/scenarios/import",
        {},
        files={"file": ("s.csv", big, "text/csv")},
        follow_redirects=False,
    )
    assert r.status_code == 413


async def test_confirm_unknown_token_renders_expired(admin_client: AsyncClient) -> None:
    r = await csrf_post(
        admin_client,
        "/scenarios/import/confirm",
        {"token": str(uuid.UUID("00000000-0000-0000-0000-000000000000"))},
        follow_redirects=False,
    )
    assert r.status_code == 409
