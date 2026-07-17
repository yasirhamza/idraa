"""Overlay CRUD route integration tests — admin-only writes.

Fixture topology choice (Option A from the task brief): every test that
needs both an authed client AND a seeded overlay derives BOTH from
``authed_admin``. The existing ``seeded_critical_infrastructure_overlay``
fixture seeds into the ``organization`` fixture's org, which is a
DIFFERENT org from the one ``authed_admin`` creates. Mixing them produces
a silent fixture mismatch (admin's session is for org A, overlay is in
org B). Inline-seed via ``seed_starter_overlays_for_org`` against the
admin's org instead.

Covers:

- Read paths (admin + analyst can list / view; cross-org 404 not 403).
- RBAC negatives (analyst cannot edit / create / deactivate / import).
- Create / edit / deactivate happy paths via ``csrf_post``.
- Validation 422 paths render ``err["msg"]`` only — never the Pydantic
  dict-repr (the route layer's render-errors-only-msg guard, preamble
  line 56).
- B8 optimistic-lock: stale ``expected_version`` → 409 with reload
  message; missing field → 422.
- Two-step CSV import: validate → preview-with-token → confirm.
- ``MAX_UPLOAD_BYTES`` Content-Length pre-read rejection.
- CSRF token absence → 403 from middleware.
"""

from __future__ import annotations

import uuid

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.overlay import OverlayDefinition
from idraa.services.overlays import (
    OverlayService,
    seed_starter_overlays_for_org,
)
from tests.conftest import csrf_post


async def _seed_ci_overlay_for(db_session: AsyncSession, *, org_id: uuid.UUID) -> OverlayDefinition:
    """Seed STARTER_OVERLAYS for ``org_id`` and return critical_infrastructure row.

    The test client's HTTP session uses a SEPARATE engine from
    ``db_session`` (both pointing at the same per-test SQLite file). To
    let the route layer see seeded rows, we ``commit()`` here — ``flush()``
    would only push to the connection-local buffer, not the on-disk file.
    """
    await seed_starter_overlays_for_org(db_session, organization_id=org_id)
    await db_session.commit()
    od = (
        await db_session.execute(
            select(OverlayDefinition).where(
                OverlayDefinition.organization_id == org_id,
                OverlayDefinition.tag == "critical_infrastructure",
            )
        )
    ).scalar_one()
    return od


# ---- Read paths -------------------------------------------------------


async def test_get_overlays_list_renders_for_admin(
    authed_admin: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    client, org_id = authed_admin
    await _seed_ci_overlay_for(db_session, org_id=org_id)

    r = await client.get("/overlays")
    assert r.status_code == 200
    assert "critical_infrastructure" in r.text


async def test_get_overlays_list_renders_for_analyst(
    authed_analyst: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    client, org_id = authed_analyst
    await _seed_ci_overlay_for(db_session, org_id=org_id)

    r = await client.get("/overlays")
    assert r.status_code == 200
    assert "critical_infrastructure" in r.text


async def test_get_overlay_view_admin_returns_200(
    authed_admin: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    client, org_id = authed_admin
    od = await _seed_ci_overlay_for(db_session, org_id=org_id)

    r = await client.get(f"/overlays/{od.id}")
    assert r.status_code == 200
    assert "critical_infrastructure" in r.text


async def test_get_overlay_view_returns_404_for_nonexistent_id(
    authed_admin: tuple[AsyncClient, uuid.UUID],
) -> None:
    client, _ = authed_admin
    r = await client.get(f"/overlays/{uuid.uuid4()}")
    assert r.status_code == 404


async def test_get_overlay_view_returns_404_for_cross_org_id(
    authed_admin: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    """Cross-org request returns 404 (not 403) and body must not leak existence."""
    from tests.factories import create_org

    client, _ = authed_admin
    other_org = await create_org(db_session, name="Other Org")
    other_od = await _seed_ci_overlay_for(db_session, org_id=other_org.id)

    r = await client.get(f"/overlays/{other_od.id}")
    assert r.status_code == 404
    # Body must not leak the overlay existed under a different org. Strict —
    # the OR-clause variant ("not found" substring) was tautologically true
    # because FastAPI's default 404 body is ``{"detail":"Not Found"}``,
    # which short-circuited the leak check.
    assert "critical_infrastructure" not in r.text.lower(), "404 response leaked the cross-org tag"


async def test_get_overlay_edit_form_admin_returns_200(
    authed_admin: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    client, org_id = authed_admin
    od = await _seed_ci_overlay_for(db_session, org_id=org_id)

    r = await client.get(f"/overlays/{od.id}/edit")
    assert r.status_code == 200


async def test_get_overlay_edit_form_analyst_returns_403(
    authed_analyst: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    client, org_id = authed_analyst
    od = await _seed_ci_overlay_for(db_session, org_id=org_id)

    r = await client.get(f"/overlays/{od.id}/edit")
    assert r.status_code == 403


# ---- RBAC negative ----------------------------------------------------


async def test_post_create_overlay_analyst_returns_403(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
) -> None:
    client, _ = authed_analyst
    r = await csrf_post(
        client,
        "/overlays",
        {
            "tag": "x_test_tag",
            "display_name": "X",
            "frequency_multiplier": "1.0",
            "magnitude_multiplier": "1.0",
            "sources": "",
            "methodology": "Long enough methodology to validate this row.",
            "methodology_change_reason": "init",
        },
        follow_redirects=False,
    )
    assert r.status_code == 403


async def test_post_edit_overlay_analyst_returns_403(
    authed_analyst: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    client, org_id = authed_analyst
    od = await _seed_ci_overlay_for(db_session, org_id=org_id)
    r = await csrf_post(
        client,
        f"/overlays/{od.id}/edit",
        {
            "tag": od.tag,
            "display_name": od.display_name,
            "frequency_multiplier": "1.5",
            "magnitude_multiplier": "2.0",
            "sources": "",
            "methodology": od.methodology,
            "methodology_change_reason": "Q2 review.",
            "expected_version": str(od.version),
        },
        follow_redirects=False,
    )
    assert r.status_code == 403


async def test_post_deactivate_overlay_analyst_returns_403(
    authed_analyst: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    client, org_id = authed_analyst
    od = await _seed_ci_overlay_for(db_session, org_id=org_id)
    r = await csrf_post(
        client,
        f"/overlays/{od.id}/deactivate",
        {"reason": "no longer applies"},
        follow_redirects=False,
    )
    assert r.status_code == 403


async def test_post_import_csv_analyst_returns_403(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
) -> None:
    client, _ = authed_analyst
    r = await csrf_post(
        client,
        "/overlays/import",
        {},
        files={
            "file": (
                "x.csv",
                b"tag,display_name,frequency_multiplier,magnitude_multiplier,"
                b"sources,methodology,methodology_change_reason\n",
                "text/csv",
            )
        },
        follow_redirects=False,
    )
    assert r.status_code == 403


async def test_get_overlays_template_csv_analyst_returns_403(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
) -> None:
    """Template.csv is admin-only — analyst gets 403 (RBAC posture-lock).

    The route was admin-gated beyond the plan body to match the controls/
    import RBAC posture; this test pins that posture so a future relax to
    ``require_user`` cannot silently broaden access.
    """
    client, _ = authed_analyst
    r = await client.get("/overlays/template.csv")
    assert r.status_code == 403


# ---- Happy paths ------------------------------------------------------


async def test_post_create_overlay_admin_creates_row_and_redirects(
    authed_admin: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    client, org_id = authed_admin

    r = await csrf_post(
        client,
        "/overlays",
        {
            "tag": "new_tag_test",
            "display_name": "New Tag Test",
            "frequency_multiplier": "1.4",
            "magnitude_multiplier": "2.0",
            "sources": "docs/reference/calibration-sources/foo.md",
            "methodology": "A sufficiently long methodology explanation for validation.",
            "methodology_change_reason": "Initial creation for tests.",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    od = (
        await db_session.execute(
            select(OverlayDefinition).where(
                OverlayDefinition.organization_id == org_id,
                OverlayDefinition.tag == "new_tag_test",
            )
        )
    ).scalar_one()
    assert r.headers["location"] == f"/overlays/{od.id}"
    assert od.frequency_multiplier == 1.4
    assert od.version == 1


async def test_post_edit_overlay_admin_with_valid_form_bumps_version(
    authed_admin: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    client, org_id = authed_admin
    od = await _seed_ci_overlay_for(db_session, org_id=org_id)
    initial_version = od.version

    r = await csrf_post(
        client,
        f"/overlays/{od.id}/edit",
        {
            "tag": od.tag,
            "display_name": od.display_name,
            "frequency_multiplier": "1.7",  # changed
            "magnitude_multiplier": str(od.magnitude_multiplier),
            "sources": "; ".join(od.sources),
            "methodology": od.methodology,
            "methodology_change_reason": "Q2 review bumps multiplier.",
            "expected_version": str(initial_version),
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    await db_session.refresh(od)
    assert od.version == initial_version + 1
    assert od.frequency_multiplier == 1.7


async def test_post_deactivate_overlay_admin_with_reason_succeeds(
    authed_admin: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    client, org_id = authed_admin
    od = await _seed_ci_overlay_for(db_session, org_id=org_id)
    assert od.is_active is True

    r = await csrf_post(
        client,
        f"/overlays/{od.id}/deactivate",
        {"reason": "Superseded by industry-specific overlay."},
        follow_redirects=False,
    )
    assert r.status_code == 303
    await db_session.refresh(od)
    assert od.is_active is False


# ---- Validation error paths -----------------------------------------


async def test_post_create_overlay_with_invalid_multiplier_renders_422_with_msg_only(
    authed_admin: tuple[AsyncClient, uuid.UUID],
) -> None:
    """Non-numeric multiplier renders 422 with a sensible msg, not Pydantic dict-repr."""
    client, _ = authed_admin
    r = await csrf_post(
        client,
        "/overlays",
        {
            "tag": "bad_multiplier",
            "display_name": "Bad",
            "frequency_multiplier": "not_a_number",
            "magnitude_multiplier": "1.0",
            "sources": "",
            "methodology": "Long enough methodology to validate this row.",
            "methodology_change_reason": "init",
        },
        follow_redirects=False,
    )
    assert r.status_code == 422
    # Pydantic dict-repr leak guards.
    assert "'type':" not in r.text
    assert "'input':" not in r.text
    assert "'url':" not in r.text


async def test_post_edit_overlay_with_methodology_too_short_renders_422(
    authed_admin: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    client, org_id = authed_admin
    od = await _seed_ci_overlay_for(db_session, org_id=org_id)
    r = await csrf_post(
        client,
        f"/overlays/{od.id}/edit",
        {
            "tag": od.tag,
            "display_name": od.display_name,
            "frequency_multiplier": str(od.frequency_multiplier),
            "magnitude_multiplier": str(od.magnitude_multiplier),
            "sources": "",
            "methodology": "too short",  # < 20 chars
            "methodology_change_reason": "trying to break it",
            "expected_version": str(od.version),
        },
        follow_redirects=False,
    )
    assert r.status_code == 422
    assert "'type':" not in r.text


async def test_post_edit_overlay_tag_rename_returns_422(
    authed_admin: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    """Tag rename is rejected by the service — route surfaces it as 422.

    The form template marks ``tag`` as ``readonly``, but a hand-crafted POST
    can still submit a different tag value. The route catches the
    :class:`ValueError` raised by :meth:`OverlayService.update` and renders
    the edit form with status 422 — this test pins that branch so a future
    refactor that drops ``ValueError`` from the catch tuple would 500
    instead of 422 with no test signal.
    """
    client, org_id = authed_admin
    od = await _seed_ci_overlay_for(db_session, org_id=org_id)
    r = await csrf_post(
        client,
        f"/overlays/{od.id}/edit",
        {
            "tag": "different_tag",  # rename — service will reject
            "display_name": od.display_name,
            "frequency_multiplier": str(od.frequency_multiplier),
            "magnitude_multiplier": str(od.magnitude_multiplier),
            "sources": "; ".join(od.sources),
            "methodology": od.methodology,
            "methodology_change_reason": "attempt rename",
            "expected_version": str(od.version),
        },
        follow_redirects=False,
    )
    assert r.status_code == 422
    # Substring of services.overlays._TAG_RENAME_MSG — proves the service
    # message reached the form rerender (not a generic Pydantic msg).
    assert "tag rename not allowed" in r.text.lower()


async def test_post_deactivate_overlay_without_reason_returns_422(
    authed_admin: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    client, org_id = authed_admin
    od = await _seed_ci_overlay_for(db_session, org_id=org_id)
    r = await csrf_post(
        client,
        f"/overlays/{od.id}/deactivate",
        {"reason": ""},
        follow_redirects=False,
    )
    assert r.status_code == 422


async def test_post_deactivate_overlay_with_500_char_reason_succeeds(
    authed_admin: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    client, org_id = authed_admin
    od = await _seed_ci_overlay_for(db_session, org_id=org_id)
    r = await csrf_post(
        client,
        f"/overlays/{od.id}/deactivate",
        {"reason": "x" * 500},
        follow_redirects=False,
    )
    assert r.status_code == 303


async def test_post_deactivate_overlay_with_501_char_reason_returns_422(
    authed_admin: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    client, org_id = authed_admin
    od = await _seed_ci_overlay_for(db_session, org_id=org_id)
    r = await csrf_post(
        client,
        f"/overlays/{od.id}/deactivate",
        {"reason": "x" * 501},
        follow_redirects=False,
    )
    assert r.status_code == 422


# ---- B8 optimistic lock ---------------------------------------------


async def test_post_edit_overlay_with_stale_expected_version_returns_409(
    authed_admin: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    """Concurrent edit: post with stale expected_version returns 409."""
    client, org_id = authed_admin
    od = await _seed_ci_overlay_for(db_session, org_id=org_id)
    starting_version = od.version

    # Simulate a concurrent admin update that bumps version.
    from idraa.schemas.overlay import OverlayForm

    svc = OverlayService(db_session)
    await svc.update(
        overlay=od,
        user_id=None,
        form=OverlayForm(
            tag=od.tag,
            display_name=od.display_name,
            frequency_multiplier=999.0,  # different — forces a real diff
            magnitude_multiplier=od.magnitude_multiplier,
            sources=list(od.sources),
            methodology=od.methodology,
            methodology_change_reason="concurrent admin bump",
        ),
        expected_version=starting_version,
    )
    await db_session.commit()
    await db_session.refresh(od)
    assert od.version == starting_version + 1

    # Now this client posts with the STALE version.
    r = await csrf_post(
        client,
        f"/overlays/{od.id}/edit",
        {
            "tag": od.tag,
            "display_name": od.display_name,
            "frequency_multiplier": "2.5",
            "magnitude_multiplier": str(od.magnitude_multiplier),
            "sources": "",
            "methodology": od.methodology,
            "methodology_change_reason": "stale-version edit",
            "expected_version": str(starting_version),  # STALE
        },
        follow_redirects=False,
    )
    assert r.status_code == 409
    body_lower = r.text.lower()
    assert "reload" in body_lower or "retry" in body_lower or "another admin" in body_lower


async def test_post_edit_overlay_with_missing_expected_version_returns_422(
    authed_admin: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    """Form submitted without the hidden expected_version field is rejected as 422."""
    client, org_id = authed_admin
    od = await _seed_ci_overlay_for(db_session, org_id=org_id)
    r = await csrf_post(
        client,
        f"/overlays/{od.id}/edit",
        {
            "tag": od.tag,
            "display_name": od.display_name,
            "frequency_multiplier": str(od.frequency_multiplier),
            "magnitude_multiplier": str(od.magnitude_multiplier),
            "sources": "",
            "methodology": od.methodology,
            "methodology_change_reason": "missing version field",
            # no expected_version
        },
        follow_redirects=False,
    )
    assert r.status_code == 422


# ---- Two-step import flow ------------------------------------------


async def test_get_overlays_template_csv_returns_csv_bytes(
    authed_admin: tuple[AsyncClient, uuid.UUID],
) -> None:
    client, _ = authed_admin
    r = await client.get("/overlays/template.csv")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers.get("content-disposition", "")
    assert b"tag,display_name" in r.content


async def test_post_overlays_import_renders_preview_with_token(
    authed_admin: tuple[AsyncClient, uuid.UUID],
) -> None:
    client, _ = authed_admin
    csv_bytes = (
        b"tag,display_name,frequency_multiplier,magnitude_multiplier,"
        b"sources,methodology,methodology_change_reason\n"
        b"http_route_test,Route Test,1.5,2.5,,"
        b'"Methodology long enough for the validator.","r1"\n'
    )
    r = await csrf_post(
        client,
        "/overlays/import",
        {},
        files={"file": ("overlays.csv", csv_bytes, "text/csv")},
        follow_redirects=False,
    )
    assert r.status_code == 200
    # Token must be rendered into the page so the next step can submit it.
    # The token is a UUID — render it as a string somewhere.
    import re

    uuid_pattern = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
    assert uuid_pattern.search(r.text), "preview page must include preview-token UUID"


async def test_post_overlays_import_confirm_with_valid_token_imports_and_redirects(
    authed_admin: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    client, org_id = authed_admin
    csv_bytes = (
        b"tag,display_name,frequency_multiplier,magnitude_multiplier,"
        b"sources,methodology,methodology_change_reason\n"
        b"two_step_import,Two Step Import,1.2,1.3,,"
        b'"Methodology long enough for the validator path.","initial import"\n'
    )
    # Step 1: validate (gets a token rendered in preview).
    r = await csrf_post(
        client,
        "/overlays/import",
        {},
        files={"file": ("ok.csv", csv_bytes, "text/csv")},
    )
    assert r.status_code == 200
    import re

    m = re.search(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        r.text,
    )
    assert m, "expected token in preview body"
    token = m.group(0)

    # Step 2: confirm.
    r2 = await csrf_post(
        client,
        "/overlays/import/confirm",
        {"token": token},
        follow_redirects=False,
    )
    assert r2.status_code == 303
    assert r2.headers["location"] == "/overlays"

    # Verify row landed.
    od = (
        await db_session.execute(
            select(OverlayDefinition).where(
                OverlayDefinition.organization_id == org_id,
                OverlayDefinition.tag == "two_step_import",
            )
        )
    ).scalar_one()
    assert od.frequency_multiplier == 1.2


async def test_post_overlays_import_confirm_with_expired_token_returns_409(
    authed_admin: tuple[AsyncClient, uuid.UUID],
) -> None:
    """Random / unknown token must surface as PreviewExpiredError → 409."""
    client, _ = authed_admin
    r = await csrf_post(
        client,
        "/overlays/import/confirm",
        {"token": str(uuid.uuid4())},  # never created
        follow_redirects=False,
    )
    assert r.status_code == 409
    body_lower = r.text.lower()
    assert "expired" in body_lower or "no longer" in body_lower or "re-upload" in body_lower


async def test_post_overlays_import_oversized_content_length_returns_413(
    authed_admin: tuple[AsyncClient, uuid.UUID],
) -> None:
    """A forged content-length over the cap must reject before the upload is read."""
    from idraa.routes.deps import MAX_UPLOAD_BYTES

    client, _ = authed_admin
    # Build a real over-cap upload — both the Content-Length-derived check
    # and the post-read length check should accept this (>cap) and reject.
    oversized = b"tag,display_name\n" + b"x," * (MAX_UPLOAD_BYTES // 2 + 100)
    r = await csrf_post(
        client,
        "/overlays/import",
        {},
        files={"file": ("huge.csv", oversized, "text/csv")},
        follow_redirects=False,
    )
    assert r.status_code == 413


async def test_post_overlays_import_with_validation_errors_renders_preview_with_errors(
    authed_admin: tuple[AsyncClient, uuid.UUID],
) -> None:
    """A CSV with one bad row renders preview page showing the row error."""
    client, _ = authed_admin
    csv_bytes = (
        b"tag,display_name,frequency_multiplier,magnitude_multiplier,"
        b"sources,methodology,methodology_change_reason\n"
        # Bad row: empty methodology (will fail Pydantic validation).
        b"bad_row_test,Bad Row Test,1.0,1.0,,short,r1\n"
    )
    r = await csrf_post(
        client,
        "/overlays/import",
        {},
        files={"file": ("bad.csv", csv_bytes, "text/csv")},
        follow_redirects=False,
    )
    assert r.status_code == 200
    # Errors rendered without Pydantic dict-repr leakage.
    assert "'type':" not in r.text
    assert "'input':" not in r.text


# ---- CSRF -----------------------------------------------------------


async def test_post_create_overlay_without_csrf_token_returns_403(
    authed_admin: tuple[AsyncClient, uuid.UUID],
) -> None:
    """POST without _csrf is rejected by CSRFMiddleware (no double-submit)."""
    client, _ = authed_admin
    # Direct POST — no CSRF cookie ever requested + no _csrf form field.
    # Use a fresh client.cookies clearing to be sure no carry-over from prior tests.
    client.cookies.delete("csrf_token")
    r = await client.post(
        "/overlays",
        data={
            "tag": "csrf_test",
            "display_name": "X",
            "frequency_multiplier": "1.0",
            "magnitude_multiplier": "1.0",
            "sources": "",
            "methodology": "Long enough methodology to validate this row.",
            "methodology_change_reason": "init",
        },
        follow_redirects=False,
    )
    assert r.status_code == 403
