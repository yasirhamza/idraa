"""Library-bundle import route tests — admin-only two-step JSON upload (Task 4).

Mirrors ``tests/integration/test_scenario_import_routes.py`` route patterns and
the ``csrf_post`` double-submit helper. CSRF posture matches scenario-import /
overlays exactly: the multipart upload POST AND the form-encoded confirm POST
both carry the ``_csrf`` token. The negative
``test_confirm_without_csrf_rejected`` pins that the confirm POST is genuinely
CSRF-protected.

RBAC posture: ALL import routes (form, template download, upload POST, confirm)
are ``require_role(ADMIN)`` — imports mutate the GLOBAL catalog every org sees.
The viewer-403 + analyst-403 tests pin this.
"""

from __future__ import annotations

import json
import re
import uuid

from httpx import AsyncClient

from tests.conftest import csrf_post

_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def _bundle(slug: str = "route-imp-a", name: str = "RouteImpA") -> str:
    return json.dumps(
        [
            {
                "slug": slug,
                "name": name,
                "status": "published",
                "threat_event_type": "ransomware",
                "threat_actor_type": "cybercriminals",
                "asset_class": "systems",
                "description": "d" * 25,
                "canonical_fair_gap": "g" * 25,
                "threat_event_frequency": {
                    "distribution": "PERT",
                    "low": 1,
                    "mode": 2,
                    "high": 3,
                },
                "vulnerability": {"distribution": "PERT", "low": 0.1, "mode": 0.2, "high": 0.3},
                "primary_loss": {"distribution": "PERT", "low": 1, "mode": 2, "high": 3},
                "calibration_anchor": {"industry": "other", "revenue_tier": "100m_to_1b"},
            },
        ]
    )


def _extract_token(html: str) -> str:
    """Pull the preview-token UUID out of the rendered preview page."""
    m = _UUID_RE.search(html)
    assert m, "expected a preview-token UUID in the preview body"
    return m.group(0)


# ---- RBAC: form GET ---------------------------------------------------


async def test_get_import_form_admin_ok(admin_client: AsyncClient) -> None:
    r = await admin_client.get("/library/import")
    assert r.status_code == 200
    assert "import" in r.text.lower()


async def test_get_import_form_analyst_forbidden(analyst_client: AsyncClient) -> None:
    r = await analyst_client.get("/library/import")
    assert r.status_code in (403, 302)


async def test_get_import_form_viewer_forbidden(viewer_client: AsyncClient) -> None:
    r = await viewer_client.get("/library/import")
    assert r.status_code in (403, 302)


# ---- RBAC: upload POST ------------------------------------------------


async def test_post_import_analyst_forbidden(analyst_client: AsyncClient) -> None:
    r = await csrf_post(
        analyst_client,
        "/library/import",
        {},
        files={"file": ("b.json", _bundle(), "application/json")},
        follow_redirects=False,
    )
    assert r.status_code in (403, 302)


async def test_post_import_viewer_forbidden(viewer_client: AsyncClient) -> None:
    r = await csrf_post(
        viewer_client,
        "/library/import",
        {},
        files={"file": ("b.json", _bundle(), "application/json")},
        follow_redirects=False,
    )
    assert r.status_code in (403, 302)


# ---- RBAC: confirm POST -----------------------------------------------


async def test_confirm_analyst_forbidden(analyst_client: AsyncClient) -> None:
    r = await csrf_post(
        analyst_client,
        "/library/import/confirm",
        {"token": str(uuid.uuid4())},
        follow_redirects=False,
    )
    assert r.status_code in (403, 302)


async def test_confirm_viewer_forbidden(viewer_client: AsyncClient) -> None:
    r = await csrf_post(
        viewer_client,
        "/library/import/confirm",
        {"token": str(uuid.uuid4())},
        follow_redirects=False,
    )
    assert r.status_code in (403, 302)


# ---- template download ------------------------------------------------


async def test_template_download(admin_client: AsyncClient) -> None:
    t = await admin_client.get("/library/import/template.json")
    assert t.status_code == 200
    assert "attachment" in t.headers["content-disposition"]
    # The generated template MUST round-trip through the bundle parser.
    from idraa.services.library_bundle_import import parse_bundle

    pairs, errors = parse_bundle(t.content)
    assert errors == [] and pairs is not None


async def test_template_download_analyst_forbidden(analyst_client: AsyncClient) -> None:
    r = await analyst_client.get("/library/import/template.json")
    assert r.status_code in (403, 302)


async def test_template_download_viewer_forbidden(viewer_client: AsyncClient) -> None:
    r = await viewer_client.get("/library/import/template.json")
    assert r.status_code in (403, 302)


# ---- CSRF negative ----------------------------------------------------


async def test_confirm_without_csrf_rejected(admin_client: AsyncClient) -> None:
    # Match the scenario-import / overlays CSRF posture: the confirm POST is
    # CSRF-protected. No _csrf field/cookie dance -> CSRFMiddleware rejects.
    r = await admin_client.post("/library/import/confirm", data={"token": "x"})
    assert r.status_code in (403, 422)


# ---- preview + confirm happy paths ------------------------------------


async def test_post_import_shows_add_preview(admin_client: AsyncClient) -> None:
    r = await csrf_post(
        admin_client,
        "/library/import",
        {},
        files={"file": ("b.json", _bundle(), "application/json")},
    )
    assert r.status_code == 200
    assert "RouteImpA" in r.text
    assert "badge-success" in r.text  # the "add" badge class


async def test_post_import_dup_slug_shows_skip(admin_client: AsyncClient) -> None:
    # First import the slug, then re-upload the same slug -> previews as "skip".
    pr = await csrf_post(
        admin_client,
        "/library/import",
        {},
        files={"file": ("b.json", _bundle(slug="dup-slug", name="DupSlug"), "application/json")},
    )
    token = _extract_token(pr.text)
    await csrf_post(
        admin_client,
        "/library/import/confirm",
        {"token": token},
        follow_redirects=False,
    )
    r = await csrf_post(
        admin_client,
        "/library/import",
        {},
        files={"file": ("b.json", _bundle(slug="dup-slug", name="DupSlug"), "application/json")},
    )
    assert r.status_code == 200
    assert "badge-ghost" in r.text  # the "skip" badge class


async def test_post_import_then_confirm_creates(admin_client: AsyncClient) -> None:
    pr = await csrf_post(
        admin_client,
        "/library/import",
        {},
        files={
            "file": (
                "b.json",
                _bundle(slug="route-confirm", name="RouteConfirm"),
                "application/json",
            )
        },
    )
    assert pr.status_code == 200
    token = _extract_token(pr.text)
    cr = await csrf_post(
        admin_client,
        "/library/import/confirm",
        {"token": token},
        follow_redirects=False,
    )
    assert cr.status_code in (200, 303)
    # The entry now appears in the global catalog (published export lists it).
    export = await admin_client.get("/library/export.csv")
    assert export.status_code == 200
    assert "RouteConfirm" in export.text

    # Preview token consumed -> re-confirm renders expired (409).
    rc = await csrf_post(
        admin_client,
        "/library/import/confirm",
        {"token": token},
        follow_redirects=False,
    )
    assert rc.status_code == 409


# ---- guards -----------------------------------------------------------


async def test_oversize_upload_rejected(admin_client: AsyncClient) -> None:
    big = b"x" * (5 * 1024 * 1024 + 1)
    r = await csrf_post(
        admin_client,
        "/library/import",
        {},
        files={"file": ("b.json", big, "application/json")},
        follow_redirects=False,
    )
    assert r.status_code == 413


async def test_confirm_unknown_token_renders_expired(admin_client: AsyncClient) -> None:
    r = await csrf_post(
        admin_client,
        "/library/import/confirm",
        {"token": str(uuid.UUID("00000000-0000-0000-0000-000000000000"))},
        follow_redirects=False,
    )
    assert r.status_code == 409
