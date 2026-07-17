"""PR λ full route lifecycle tests for the 6 new/changed controls routes.

Coverage matrix:
  - GET /controls/new (admin, analyst, viewer/reviewer→403)
  - POST /controls/new (valid; invalid → 422; viewer→403; XSS regression;
                        duplicate sub_function → 422 global error)
  - GET /controls/{id}/edit (admin, missing→404, soft-deleted→404)
  - POST /controls/{id}/edit (valid; save = confirm flips confirmed_by_user_at)
  - POST /controls/{id}/duplicate (admin; missing→404)
  - POST /controls/import (admin OK, analyst→403 — Q8b harden)
  - GET /controls/_assignment_row?index=N (admin/analyst OK)

Uses the project's existing fixture surface:
  - admin_client / analyst_client / reviewer_client / viewer_client (AsyncClient)
  - csrf_post helper (tests/conftest.py:171) — module-level async function,
    NOT a fixture. Import directly; do NOT add it to test signatures as a param.
  - organization / admin_user / db_session fixtures (existing global conftest)
  - existing_control_with_2_assignments / ..._unconfirmed / soft_deleted_control
    (tests/integration/conftest.py — added in F11 Step 2)
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.control import Control
from tests.conftest import csrf_post

# ---------------------------------------------------------------------------
# GET /controls/new
# ---------------------------------------------------------------------------


async def test_get_new_admin_returns_form(admin_client: AsyncClient) -> None:
    r = await admin_client.get("/controls/new")
    assert r.status_code == 200
    assert "control-form" in r.text
    assert "Sub-function" in r.text


async def test_get_new_analyst_returns_form(analyst_client: AsyncClient) -> None:
    r = await analyst_client.get("/controls/new")
    assert r.status_code == 200


async def test_get_new_viewer_403(viewer_client: AsyncClient) -> None:
    r = await viewer_client.get("/controls/new")
    assert r.status_code == 403


async def test_get_new_reviewer_403(reviewer_client: AsyncClient) -> None:
    r = await reviewer_client.get("/controls/new")
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# POST /controls/new
# ---------------------------------------------------------------------------


async def test_post_new_valid_creates_and_redirects(admin_client: AsyncClient) -> None:
    form_data = {
        "name": "Test Control",
        "description": "A test",
        "domain": "loss_event",
        "type": "technical",
        "status": "active",
        "assignments[0][sub_function]": "lec_prev_resistance",
        "assignments[0][capability_value]": "0.8",
        "assignments[0][coverage]": "0.85",
        "assignments[0][reliability]": "0.9",
    }
    r = await csrf_post(admin_client, "/controls/new", form_data, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith("/controls/")


async def test_post_new_invalid_returns_422(admin_client: AsyncClient) -> None:
    form_data = {
        "name": "",  # required — will fail validation
        "description": "",
        "domain": "loss_event",
        "type": "technical",
        "status": "active",
        # No assignments → fails ControlForm min_length=1 check
    }
    r = await csrf_post(admin_client, "/controls/new", form_data, follow_redirects=False)
    assert r.status_code == 422
    assert "control-form" in r.text  # form re-rendered, not redirected


async def test_post_new_duplicate_subfn_returns_422_global_error(
    admin_client: AsyncClient,
) -> None:
    """Cross-field uniqueness validator surfaces as a global error message.

    The friendly 'duplicate sub_function' text must appear in the rendered page;
    promotion to _global is driven by err type + loc match in _format_errors
    (NOT a substring heuristic — paranoid-review fix).
    """
    form_data = {
        "name": "Dup Test",
        "description": "",
        "domain": "loss_event",
        "type": "technical",
        "status": "active",
        "assignments[0][sub_function]": "lec_prev_resistance",
        "assignments[0][capability_value]": "0.8",
        "assignments[0][coverage]": "0.8",
        "assignments[0][reliability]": "0.8",
        "assignments[1][sub_function]": "lec_prev_resistance",  # duplicate!
        "assignments[1][capability_value]": "0.7",
        "assignments[1][coverage]": "0.8",
        "assignments[1][reliability]": "0.8",
    }
    r = await csrf_post(admin_client, "/controls/new", form_data, follow_redirects=False)
    assert r.status_code == 422
    assert "duplicate sub_function" in r.text


async def test_post_new_xss_in_name_field_is_html_escaped(admin_client: AsyncClient) -> None:
    """XSS regression — <script> in form name field must be HTML-escaped when
    re-rendered after a validation error.

    Defends against _format_errors / template autoescape regressions
    (paranoid-review fix). The literal <script> tag must NOT appear unescaped
    in the response; the Jinja2 autoescape must produce &lt;script&gt; or
    &#x3C;script&#x3E;.
    """
    form_data = {
        "name": "<script>alert('xss')</script>",
        "description": "",
        "domain": "loss_event",
        "type": "technical",
        "status": "active",
        # No assignments → triggers re-render preserving the name value
    }
    r = await csrf_post(admin_client, "/controls/new", form_data, follow_redirects=False)
    assert r.status_code == 422
    # The literal <script> tag must NOT appear unescaped in the response
    assert "<script>alert('xss')</script>" not in r.text
    # The escaped form must appear (either HTML entity encoding variant is acceptable)
    assert "&lt;script&gt;" in r.text or "&#x3C;script&#x3E;" in r.text


async def test_post_new_viewer_403(viewer_client: AsyncClient) -> None:
    r = await csrf_post(viewer_client, "/controls/new", {}, follow_redirects=False)
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# GET /controls/{id}/edit
# ---------------------------------------------------------------------------


async def test_get_edit_admin_returns_form_with_existing_assignments(
    admin_client: AsyncClient,
    existing_control_with_2_assignments: Control,
) -> None:
    cid = existing_control_with_2_assignments.id
    r = await admin_client.get(f"/controls/{cid}/edit")
    assert r.status_code == 200
    # Two pre-rendered assignment rows must be present
    assert r.text.count('class="assignment-row') == 2


async def test_get_edit_form_disinherits_hx_select(
    admin_client: AsyncClient,
    existing_control_with_2_assignments: Control,
) -> None:
    """UAT 2026-05-21 regression: PR #199 added hx-select="#control-form" to
    the form to make 422 validation responses swap correctly. HTMX 1.9
    inherits hx-select to all descendants, which silently broke the
    "+ Add assignment" button (and the sub-function combobox row-swap):
    HTMX runs hx-select against the row-partial response, finds no
    #control-form, swaps nothing.

    Fix: hx-disinherit="hx-select" on the form. The form itself still uses
    its hx-select (descendant requests do not)."""
    cid = existing_control_with_2_assignments.id
    r = await admin_client.get(f"/controls/{cid}/edit")
    assert r.status_code == 200
    # Both attributes must coexist on the form: hx-select keeps the 422
    # path working; hx-disinherit prevents descendants from inheriting it.
    assert 'hx-select="#control-form"' in r.text
    assert 'hx-disinherit="hx-select"' in r.text


async def test_get_edit_missing_404(admin_client: AsyncClient) -> None:
    r = await admin_client.get(f"/controls/{uuid.uuid4()}/edit")
    assert r.status_code == 404


async def test_get_edit_soft_deleted_404(
    admin_client: AsyncClient,
    soft_deleted_control: Control,
) -> None:
    r = await admin_client.get(f"/controls/{soft_deleted_control.id}/edit")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# POST /controls/{id}/edit
# ---------------------------------------------------------------------------


async def test_post_edit_save_confirms_assignments(
    admin_client: AsyncClient,
    db_session: AsyncSession,
    existing_control_with_2_assignments_unconfirmed: Control,
) -> None:
    """Q5(a) save = confirm: editing flips all assignments to confirmed.

    Re-fetches via db_session.get (NOT refresh) after the POST so we read
    the committed row written by the route's own session, not the fixture's
    now-stale session state (paranoid-review M8).
    """
    ctrl_id = existing_control_with_2_assignments_unconfirmed.id
    form_data = _form_dict_from_control(existing_control_with_2_assignments_unconfirmed)
    # Use naive UTC for comparison — SQLite stores DateTime(timezone=True) as
    # naive strings when using the SQLite dialect, so the hydrated value may
    # be tz-naive. Comparing naive pre_now avoids TypeError on >=.
    pre_now = datetime.now(UTC).replace(tzinfo=None)

    r = await csrf_post(
        admin_client,
        f"/controls/{ctrl_id}/edit",
        form_data,
        follow_redirects=False,
    )
    assert r.status_code == 303

    # Expire all cached state so the next .get actually hits the DB
    await db_session.rollback()

    refreshed = await db_session.get(Control, ctrl_id)
    assert refreshed is not None
    assert len(refreshed.assignments) == 2
    for a in refreshed.assignments:
        assert a.confirmed_by_user_at is not None
        # Strip tz from the stored value for the >= comparison (SQLite
        # may hydrate as naive even though the column is timezone=True)
        stored = a.confirmed_by_user_at
        if stored.tzinfo is not None:
            stored = stored.replace(tzinfo=None)
        assert stored >= pre_now


async def test_post_edit_analyst_allowed(
    analyst_client: AsyncClient,
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """Analyst can POST /edit (Q8b: admin OR analyst on author paths)."""
    from tests.integration.conftest import _make_control_with_two_assignments

    _client, org_id = authed_analyst
    ctrl = await _make_control_with_two_assignments(db_session, org_id)

    form_data = _form_dict_from_control(ctrl)
    r = await csrf_post(
        analyst_client, f"/controls/{ctrl.id}/edit", form_data, follow_redirects=False
    )
    assert r.status_code == 303


async def test_post_edit_viewer_403(
    viewer_client: AsyncClient,
    authed_viewer: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """Viewer cannot POST /edit (Q8b)."""
    from tests.integration.conftest import _make_control_with_two_assignments

    _client, org_id = authed_viewer
    ctrl = await _make_control_with_two_assignments(db_session, org_id)

    r = await csrf_post(viewer_client, f"/controls/{ctrl.id}/edit", {}, follow_redirects=False)
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# POST /controls/{id}/duplicate
# ---------------------------------------------------------------------------


async def test_post_duplicate_creates_clone_and_redirects_to_edit(
    admin_client: AsyncClient,
    existing_control_with_2_assignments: Control,
) -> None:
    cid = existing_control_with_2_assignments.id
    r = await csrf_post(
        admin_client,
        f"/controls/{cid}/duplicate",
        {},
        follow_redirects=False,
    )
    assert r.status_code == 303
    location = r.headers["location"]
    assert "/edit" in location
    # Clone must be a different UUID from the original
    clone_id = uuid.UUID(location.split("/")[2])
    assert clone_id != cid


async def test_post_duplicate_analyst_allowed(
    analyst_client: AsyncClient,
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """Analyst can POST /duplicate (Q8b: admin OR analyst on author paths)."""
    from tests.integration.conftest import _make_control_with_two_assignments

    _client, org_id = authed_analyst
    ctrl = await _make_control_with_two_assignments(db_session, org_id)

    r = await csrf_post(
        analyst_client, f"/controls/{ctrl.id}/duplicate", {}, follow_redirects=False
    )
    assert r.status_code == 303
    assert "/edit" in r.headers["location"]


async def test_post_duplicate_missing_404(admin_client: AsyncClient) -> None:
    r = await csrf_post(
        admin_client,
        f"/controls/{uuid.uuid4()}/duplicate",
        {},
        follow_redirects=False,
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# POST /controls/import  (custom CSV upload — Q8b admin-only harden)
# ---------------------------------------------------------------------------


async def test_post_import_csv_analyst_403_per_q8b(analyst_client: AsyncClient) -> None:
    """Q8(b): bulk CSV import is admin-only — harden the existing /import route.

    File uploads cannot go through csrf_post (form-encoded only), so we use
    the manual CSRF cookie injection pattern:
      1. Bootstrap the cookie via a GET (mirrors csrf_post pattern at conftest:191).
      2. Read the 'csrf_token' cookie from the jar.
      3. POST with _csrf field + files= multipart body.

    Without the bootstrap GET the cookie jar is empty and the test would 403
    from CSRF rejection (not role-gate) — a false-positive that would survive
    a future role-gate weakening.

    Defensive assertion: confirm the 403 is from RBAC, not CSRF, so a future
    role-gate weakening does not slip through. The CSRF middleware response body
    contains 'csrf'; the role-gate response does not.
    """
    # Bootstrap the CSRF cookie
    await analyst_client.get("/setup")
    csrf_token = analyst_client.cookies.get("csrf_token")
    assert csrf_token, "csrf_token cookie missing — bootstrap GET failed"

    r = await analyst_client.post(
        "/controls/import",
        files={"file": ("dummy.csv", b"name,domain\nFoo,loss_event\n", "text/csv")},
        data={"_csrf": csrf_token},
        follow_redirects=False,
    )
    assert r.status_code == 403
    # Confirm 403 is from role gate, not CSRF rejection
    assert "csrf" not in r.text.lower(), "403 should be from role gate, not CSRF rejection"


# ---------------------------------------------------------------------------
# GET /controls/_assignment_row?index=N
# ---------------------------------------------------------------------------


async def test_get_assignment_row_returns_partial(admin_client: AsyncClient) -> None:
    r = await admin_client.get("/controls/_assignment_row?index=3")
    assert r.status_code == 200
    assert "assignments[3]" in r.text


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _form_dict_from_control(ctrl: Control) -> dict[str, str]:
    """Build a form-data dict from a Control and its assignments.

    Used by test_post_edit_save_confirms_assignments to re-POST the existing
    control data so the route's three-way merge logic runs and sets
    confirmed_by_user_at on every assignment (Q5a save=confirm).
    """
    out: dict[str, str] = {
        "name": ctrl.name,
        "description": ctrl.description or "",
        "type": ctrl.type.value,
        "status": ctrl.status.value,
    }
    for i, a in enumerate(ctrl.assignments):
        out[f"assignments[{i}][sub_function]"] = a.sub_function.value
        out[f"assignments[{i}][capability_value]"] = str(
            a.capability_value if a.capability_value is not None else ""
        )
        out[f"assignments[{i}][coverage]"] = str(a.coverage)
        out[f"assignments[{i}][reliability]"] = str(a.reliability)
    return out
