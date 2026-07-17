"""ControlForm cap relaxation — multi-assignment now permitted (spec §6.1).

Renamed from test_controls_pr_iota.py via git mv to preserve history.

PR iota OQ3 cap-1 tests replaced with:
  - multi-assignment acceptance
  - uniqueness rejection (model_validator defense-in-depth)

HTTP gate tests updated for PR lambda (F9): /new and /edit now return real
responses (200 for GET; 422 for POSTs with incomplete form data) instead of
503 maintenance gates.

15 tests: 4 Pydantic schema + 5 route-gate + 2 confirm + 4 importer.
"""

from __future__ import annotations

import csv
import io
import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from pydantic import ValidationError

from idraa.models.enums import ControlDomain, ControlType, FairCamSubFunction
from idraa.schemas.control import ControlForm, ControlFunctionAssignmentDTO
from tests.conftest import csrf_post

# ---------------------------------------------------------------------------
# Pydantic schema tests (4 tests) — cap relaxation + uniqueness validator
# ---------------------------------------------------------------------------


def _dto(
    sub_function: FairCamSubFunction = FairCamSubFunction.LEC_PREV_RESISTANCE,
) -> ControlFunctionAssignmentDTO:
    return ControlFunctionAssignmentDTO(
        sub_function=sub_function,
        capability_value=0.85,
        coverage=0.88,
        reliability=0.92,
    )


def _form(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "name": "EDR",
        "domain": ControlDomain.LOSS_EVENT,
        "type": ControlType.TECHNICAL,
        "assignments": [_dto()],
    }
    base.update(overrides)
    return base


def test_form_accepts_single_assignment() -> None:
    """Single assignment still accepted (min_length=1 satisfied)."""
    form = ControlForm(**_form())
    assert len(form.assignments) == 1


def test_form_accepts_multi_assignment() -> None:
    """Three distinct sub_functions — PR kappa cap relaxation (spec §6.1)."""
    form = ControlForm(
        **_form(
            assignments=[
                _dto(FairCamSubFunction.LEC_PREV_RESISTANCE),
                _dto(FairCamSubFunction.LEC_DET_VISIBILITY),
                _dto(FairCamSubFunction.LEC_DET_RECOGNITION),
            ]
        )
    )
    assert len(form.assignments) == 3


def test_form_rejects_zero_assignments() -> None:
    """min_length=1 still enforced — empty assignments list is invalid."""
    with pytest.raises(ValidationError):
        ControlForm(**_form(assignments=[]))


def test_form_rejects_duplicate_sub_function() -> None:
    """model_validator rejects same sub_function appearing twice (spec §6.1)."""
    with pytest.raises(ValidationError, match=r"duplicate sub_function|unique"):
        ControlForm(
            **_form(
                assignments=[
                    _dto(FairCamSubFunction.LEC_PREV_RESISTANCE),
                    _dto(FairCamSubFunction.LEC_PREV_RESISTANCE),
                ]
            )
        )


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


async def _bootstrap_app(client: AsyncClient, suffix: str) -> tuple[str, str]:
    """Bootstrap the app and return (email, password)."""
    email = f"iota-{suffix}@test.local"
    password = "TestPass1!"
    await csrf_post(
        client,
        "/setup",
        {
            "email": email,
            "password": password,
            "full_name": f"Iota User {suffix}",
            "org_name": f"IotaOrg-{suffix}",
            "industry_type": "information",
            "organization_size": "small",
        },
    )
    return email, password


async def _login(client: AsyncClient, email: str, password: str) -> None:
    await csrf_post(client, "/login", {"email": email, "password": password})


def _make_csv(
    name: str = "Imported Control", domain: str = "LEC - Prevention - Resistance"
) -> bytes:
    """Build a minimal import CSV bytes payload.

    The 4th column carries a sub-function PATH string post-#68. The default
    "LEC - Prevention - Resistance" is a recognized canonical path, so rows
    built with this helper produce Controls with one ControlFunctionAssignment.
    Pre-#91, the default was the legacy enum value "loss_event" — unrecognized
    by the post-#68 importer, producing zero-assignment Controls and a log
    warning. None of the current callers asserted on assignment presence so
    the suite stayed green; the new default forward-compats that.
    """
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["", "name", "description", "domain", ""])  # header
    writer.writerow(["", name, "Imported via test", domain, ""])
    return output.getvalue().encode()


# ---------------------------------------------------------------------------
# List + maintenance gate tests (5 tests)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_controls_list_returns_200(client: AsyncClient) -> None:
    """GET /controls returns 200 — list route remains operational after PR iota.

    NOTE: this test passes BEFORE any control is seeded — the empty-state
    branch in the template bypasses the table render. The list-with-rows
    regression test below covers the loop-body render that was broken
    in main pre-fix.
    """
    email, pwd = await _bootstrap_app(client, uuid.uuid4().hex[:6])
    await _login(client, email, pwd)
    r = await client.get("/controls")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_controls_list_renders_with_seeded_control(client: AsyncClient) -> None:
    """REGRESSION: GET /controls renders the table loop without 500.

    Pre-fix, controls/list.html:21 referenced ``c.function.value`` — an
    attribute removed in PR iota's Control reshape. The empty-state branch
    bypassed the broken loop, so test_controls_list_returns_200 above
    did NOT catch the bug. Any seeded Control made the page 500 with
    ``UndefinedError: 'Control object' has no attribute 'function'``.
    """
    email, pwd = await _bootstrap_app(client, uuid.uuid4().hex[:6])
    await _login(client, email, pwd)

    # Seed via /controls/import (one Control + one ControlFunctionAssignment).
    csv_bytes = _make_csv(name="Regression Firewall")
    token = client.cookies.get("csrf_token", "")
    imp = await client.post(
        "/controls/import",
        files={"file": ("controls.csv", csv_bytes, "text/csv")},
        data={"_csrf": token},
        follow_redirects=False,
    )
    assert imp.status_code == 303

    r = await client.get("/controls")
    assert r.status_code == 200
    assert "Regression Firewall" in r.text
    # Assert the columns the new template emits — sanity-check that the
    # table loop body renders. CSV-imported controls have
    # type=administrative + status=active per services/controls_importer.py.
    # #454 item 3: the Domain column now renders humanized labels
    # ("loss_event" → "Loss Event"); the stored enum value is unchanged.
    assert "Loss Event" in r.text
    assert "administrative" in r.text
    assert "active" in r.text
    # Negative: assert the old stale field references aren't there
    assert "control_strength" not in r.text
    assert "Strength" not in r.text  # old "Strength * Rel * Cov" header gone


@pytest.mark.asyncio
async def test_control_detail_renders_with_seeded_control(client: AsyncClient) -> None:
    """REGRESSION: GET /controls/{id} renders without 500.

    Pre-fix, controls/detail.html:18-20 referenced ``c.function.value``
    and ``c.control_strength`` / ``c.control_reliability`` /
    ``c.control_coverage`` — all removed in PR iota. Any seeded Control
    made the detail page 500.
    """
    email, pwd = await _bootstrap_app(client, uuid.uuid4().hex[:6])
    await _login(client, email, pwd)

    csv_bytes = _make_csv(name="Regression Detail Control")
    token = client.cookies.get("csrf_token", "")
    imp = await client.post(
        "/controls/import",
        files={"file": ("controls.csv", csv_bytes, "text/csv")},
        data={"_csrf": token},
        follow_redirects=False,
    )
    assert imp.status_code == 303

    # Discover the seeded control's id from the list page (HTML href).
    list_resp = await client.get("/controls")
    import re

    m = re.search(
        r'href="/controls/([0-9a-f-]{36})"[^>]*>Regression Detail Control',
        list_resp.text,
    )
    assert m, f"could not find seeded control's id in list: {list_resp.text[:500]}"
    control_id = m.group(1)

    r = await client.get(f"/controls/{control_id}")
    assert r.status_code == 200
    assert "Regression Detail Control" in r.text
    # The new FAIR-CAM section should render with the importer-created assignment.
    assert "FAIR-CAM function assignments" in r.text
    # Negative: stale field references gone.
    assert "control_strength" not in r.text


@pytest.mark.asyncio
async def test_control_new_get_returns_200(client: AsyncClient) -> None:
    """GET /controls/new returns 200 — create route active after PR lambda F9."""
    email, pwd = await _bootstrap_app(client, uuid.uuid4().hex[:6])
    await _login(client, email, pwd)
    r = await client.get("/controls/new")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_control_new_post_returns_422_on_incomplete_form(client: AsyncClient) -> None:
    """POST /controls/new with incomplete payload returns 422 (validation error)."""
    email, pwd = await _bootstrap_app(client, uuid.uuid4().hex[:6])
    await _login(client, email, pwd)
    r = await csrf_post(
        client,
        "/controls/new",
        {"name": "Incomplete"},
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_control_edit_get_returns_404_on_unknown_id(client: AsyncClient) -> None:
    """GET /controls/{id}/edit with unknown UUID returns 404 — gate lifted in PR lambda F9."""
    email, pwd = await _bootstrap_app(client, uuid.uuid4().hex[:6])
    await _login(client, email, pwd)
    r = await client.get(f"/controls/{uuid.uuid4()}/edit")
    # 404: control lookup runs (maintenance gate is gone)
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_control_edit_post_returns_404_on_unknown_id(client: AsyncClient) -> None:
    """POST /controls/{id}/edit with unknown UUID returns 404 — gate lifted in PR lambda F9."""
    email, pwd = await _bootstrap_app(client, uuid.uuid4().hex[:6])
    await _login(client, email, pwd)
    r = await csrf_post(
        client,
        f"/controls/{uuid.uuid4()}/edit",
        {"name": "Blocked"},
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_control_edit_post_persists_changes(client: AsyncClient) -> None:
    """Regression for user-reported #29: 'Saving edited control: no
    feedback + doesn't work.'

    Round-trip an edit: import → GET edit → POST edit with modified name →
    GET detail → assert the new name appears (i.e. the change persisted).
    """
    import re

    email, pwd = await _bootstrap_app(client, uuid.uuid4().hex[:6])
    await _login(client, email, pwd)

    # Seed via import.
    csv_bytes = _make_csv(name="Original Control")
    token = client.cookies.get("csrf_token", "")
    imp = await client.post(
        "/controls/import",
        files={"file": ("c.csv", csv_bytes, "text/csv")},
        data={"_csrf": token},
        follow_redirects=False,
    )
    assert imp.status_code == 303

    # Find the control's id.
    list_resp = await client.get("/controls")
    m = re.search(
        r'href="/controls/([0-9a-f-]{36})"[^>]*>Original Control',
        list_resp.text,
    )
    assert m, f"could not find seeded control id: {list_resp.text[:500]!r}"
    control_id = m.group(1)

    # GET edit form to see what fields are required.
    edit_resp = await client.get(f"/controls/{control_id}/edit")
    assert edit_resp.status_code == 200

    # POST edit with the minimum required payload + a name change.
    # ControlForm requires: name, domain, type, status, version, plus at
    # least one assignment row. We mirror what the importer created.
    payload = {
        "name": "Renamed Control",
        "description": "Edited via test",
        "domain": "loss_event",
        "type": "administrative",
        "status": "active",
        "version": "1.0",
        # One assignment row matching the importer-created assignment.
        "assignments[0][sub_function]": "lec_prev_resistance",
        "assignments[0][capability_value]": "0.7",
        "assignments[0][coverage]": "0.8",
        "assignments[0][reliability]": "0.8",
    }
    r = await csrf_post(
        client,
        f"/controls/{control_id}/edit",
        payload,
        follow_redirects=False,
    )
    assert r.status_code in (204, 303), (
        f"Edit POST should succeed (204+HX-Redirect or 303); got {r.status_code}. "
        f"Body: {r.text[:300]!r}"
    )

    # GET detail and assert the new name persisted.
    detail = await client.get(f"/controls/{control_id}")
    assert detail.status_code == 200
    assert "Renamed Control" in detail.text, (
        "Edit did not persist — control detail still shows the old name. "
        "User-facing complaint: 'doesn't work.'"
    )
    assert "Original Control" not in detail.text


@pytest.mark.asyncio
async def test_control_edit_post_persists_assignment_changes(client: AsyncClient) -> None:
    """Regression for user-reported #29 (variant): editing the assignment
    capability/coverage/reliability values must persist. The user said
    'doesn't work' — possibly meaning the visible numeric fields revert
    after save."""
    import re

    email, pwd = await _bootstrap_app(client, uuid.uuid4().hex[:6])
    await _login(client, email, pwd)

    csv_bytes = _make_csv(name="Tunable Control")
    token = client.cookies.get("csrf_token", "")
    await client.post(
        "/controls/import",
        files={"file": ("c.csv", csv_bytes, "text/csv")},
        data={"_csrf": token},
        follow_redirects=False,
    )
    list_resp = await client.get("/controls")
    m = re.search(r'href="/controls/([0-9a-f-]{36})"[^>]*>Tunable Control', list_resp.text)
    assert m
    control_id = m.group(1)

    payload = {
        "name": "Tunable Control",
        "description": "Importer default",
        "domain": "loss_event",
        "type": "administrative",
        "status": "active",
        "version": "1.0",
        # Importer defaults are 0.7 / 0.8 / 0.8. Change to 0.42 / 0.42 / 0.42.
        "assignments[0][sub_function]": "lec_prev_resistance",
        "assignments[0][capability_value]": "0.42",
        "assignments[0][coverage]": "0.42",
        "assignments[0][reliability]": "0.42",
    }
    r = await csrf_post(
        client,
        f"/controls/{control_id}/edit",
        payload,
        follow_redirects=False,
    )
    assert r.status_code in (204, 303), (
        f"Edit POST should succeed; got {r.status_code}. Body: {r.text[:300]!r}"
    )

    # Re-GET edit form to see if the new values are preserved in the inputs.
    edit2 = await client.get(f"/controls/{control_id}/edit")
    assert edit2.status_code == 200
    body = edit2.text
    # The capability/coverage/reliability inputs should now show 0.42, not 0.7/0.8.
    assert 'value="0.42"' in body or "0.42" in body, (
        "Edited assignment values did not persist — the form shows the old "
        f"importer defaults. User-facing complaint: 'doesn't work.' Body head: "
        f"{body[:500]!r}"
    )


# ---------------------------------------------------------------------------
# Confirm endpoint tests (2 tests)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_confirm_assignment_404_on_random_ids(client: AsyncClient) -> None:
    """Confirm with non-existent IDs returns 404."""
    email, pwd = await _bootstrap_app(client, uuid.uuid4().hex[:6])
    await _login(client, email, pwd)
    r = await csrf_post(
        client,
        f"/controls/{uuid.uuid4()}/assignments/{uuid.uuid4()}/confirm",
        {},
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_confirm_assignment_htmx_header_read(client: AsyncClient) -> None:
    """HTMX POST to confirm with non-existent IDs → 404 (route resolves with HTMX header).

    Verifies the HX-Request header is honored by the route. Full 204 happy-path
    coverage requires same-engine seeding which lives in tests/services/.
    """
    email, pwd = await _bootstrap_app(client, uuid.uuid4().hex[:6])
    await _login(client, email, pwd)
    r = await csrf_post(
        client,
        f"/controls/{uuid.uuid4()}/assignments/{uuid.uuid4()}/confirm",
        {},
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Importer route tests (1 live + 3 skip-stubs)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_import_csv_returns_303(client: AsyncClient) -> None:
    """POST /controls/import with valid CSV → 303 redirect to /controls."""
    email, pwd = await _bootstrap_app(client, uuid.uuid4().hex[:6])
    await _login(client, email, pwd)

    csv_bytes = _make_csv(name="Firewall Import Test")
    token = client.cookies.get("csrf_token", "")

    r = await client.post(
        "/controls/import",
        files={"file": ("controls.csv", csv_bytes, "text/csv")},
        data={"_csrf": token},
        follow_redirects=False,
    )
    assert r.status_code == 303
    # Issue #152: redirect now includes ?imported=N&skipped=K so GET /controls
    # can render the post-import flash via the query-string flash pattern.
    location = r.headers.get("location", "")
    assert location.startswith("/controls"), f"location={location!r}"
    assert "imported=" in location, f"missing imported= query param in {location!r}"
    assert "skipped=" in location, f"missing skipped= query param in {location!r}"


@pytest.mark.asyncio
async def test_import_csv_creates_control_function_assignment() -> None:
    """CSV import creates one ControlFunctionAssignment per imported control.

    Cross-fixture engine isolation: client and db_session use different SQLite
    engines on the same file. DB-assertion paths are covered authoritatively
    in tests/integration/test_controls_import.py with shared AsyncSession.
    """
    pytest.skip(
        "Cross-fixture engine isolation: importer DB assertions covered in "
        "tests/integration/test_controls_import.py"
    )


@pytest.mark.asyncio
async def test_import_csv_assignment_confirmed_at_is_null() -> None:
    """Importer-created assignments have confirmed_by_user_at = NULL.

    Covered authoritatively in tests/integration/test_controls_import.py.
    """
    pytest.skip("Covered in tests/integration/test_controls_import.py (service-level assertion)")


@pytest.mark.asyncio
async def test_import_csv_lec_preventive_maps_to_lec_prev_resistance() -> None:
    """domain=loss_event → sub_function=lec_prev_resistance (OQ2 mapping).

    Covered in tests/integration/test_controls_import.py.
    """
    pytest.skip("OQ2 mapping verified in tests/integration/test_controls_import.py")


# ---------------------------------------------------------------------------
# Issue #123 — multi-assignment wire-shape regression (3 tests)
#
# Before issue #123, the + Add assignment button baked a static
# `?index={{ next_assignment_index }}` URL at server render time. Every click
# fetched the SAME `?index=N`, so each appended row carried duplicate
# `name="assignments[N][...]"` field names that Starlette FormData collapses
# (last value wins) → silent row loss on save.
#
# These tests guard against regression at three layers:
#   A) HTTP POST /controls/new with assignments[0..2] persists all 3.
#   B) HTTP POST /controls/{id}/edit same.
#   C) Template structural assertion: the + Add button does NOT bake a
#      `?index=` URL; it uses hx-vals with a JS expression over data-row-index.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_control_new_post_persists_three_distinct_assignments(
    client: AsyncClient,
) -> None:
    """POST /controls/new with assignments[0..2] (3 distinct sub-functions)
    persists ALL 3 rows. Regression for issue #123 — silent row loss when the
    + Add button reused indices.
    """
    import re

    email, pwd = await _bootstrap_app(client, uuid.uuid4().hex[:6])
    await _login(client, email, pwd)
    payload = {
        "name": "Multi Asgn Control",
        "description": "issue #123 regression",
        "type": "technical",
        "status": "active",
        "version": "1.0",
        "annual_cost": "0",
        # Row 0 — initial blank row, user filled
        "assignments[0][sub_function]": "lec_det_monitoring",
        "assignments[0][capability_value]": "0.76",
        "assignments[0][coverage]": "0.76",
        "assignments[0][reliability]": "0.79",
        # Row 1 — first + Add (post-fix: data-row-index=1)
        "assignments[1][sub_function]": "lec_det_recognition",
        "assignments[1][capability_value]": "0.7",
        "assignments[1][coverage]": "0.8",
        "assignments[1][reliability]": "0.8",
        # Row 2 — second + Add (post-fix: data-row-index=2, NOT collide with 1)
        "assignments[2][sub_function]": "vmc_id_control_monitoring",
        "assignments[2][capability_value]": "0.5",
        "assignments[2][coverage]": "0.8",
        "assignments[2][reliability]": "0.8",
    }
    r = await csrf_post(client, "/controls/new", payload, follow_redirects=False)
    assert r.status_code in (204, 303), (
        f"3-assignment save should succeed; got {r.status_code}. Body head: {r.text[:300]!r}"
    )
    # 204 → HX-Redirect header. 303 → Location header. Either way the new
    # control's id is in the location-style header value.
    location = r.headers.get("HX-Redirect") or r.headers.get("location", "")
    m = re.match(r"/controls/([0-9a-f-]{36})", location)
    assert m, f"could not parse new control id from redirect: {location!r}"
    control_id = m.group(1)

    detail = await client.get(f"/controls/{control_id}")
    assert detail.status_code == 200
    body = detail.text
    # All 3 sub-functions appear in the FAIR-CAM assignments table.
    assert "lec_det_monitoring" in body, "row 0 sub-function missing from detail page"
    assert "lec_det_recognition" in body, "row 1 sub-function missing from detail page"
    assert "vmc_id_control_monitoring" in body, (
        "row 2 sub-function missing from detail page — regression: silent row loss"
    )


@pytest.mark.asyncio
async def test_control_edit_post_persists_three_distinct_assignments(
    client: AsyncClient,
) -> None:
    """POST /controls/{id}/edit with assignments[0..2] (3 distinct sub-functions)
    replaces existing assignments with all 3. Regression for issue #123.
    """
    import re

    email, pwd = await _bootstrap_app(client, uuid.uuid4().hex[:6])
    await _login(client, email, pwd)

    # Seed a single-assignment control via importer.
    csv_bytes = _make_csv(name="Edit-To-Multi Control")
    token = client.cookies.get("csrf_token", "")
    imp = await client.post(
        "/controls/import",
        files={"file": ("c.csv", csv_bytes, "text/csv")},
        data={"_csrf": token},
        follow_redirects=False,
    )
    assert imp.status_code == 303

    list_resp = await client.get("/controls")
    m = re.search(
        r'href="/controls/([0-9a-f-]{36})"[^>]*>Edit-To-Multi Control',
        list_resp.text,
    )
    assert m
    control_id = m.group(1)

    payload = {
        "name": "Edit-To-Multi Control",
        "description": "issue #123 edit regression",
        "type": "administrative",
        "status": "active",
        "version": "1.0",
        "annual_cost": "0",
        "assignments[0][sub_function]": "lec_prev_resistance",
        "assignments[0][capability_value]": "0.7",
        "assignments[0][coverage]": "0.8",
        "assignments[0][reliability]": "0.8",
        "assignments[1][sub_function]": "lec_det_recognition",
        "assignments[1][capability_value]": "0.6",
        "assignments[1][coverage]": "0.7",
        "assignments[1][reliability]": "0.7",
        "assignments[2][sub_function]": "vmc_id_control_monitoring",
        "assignments[2][capability_value]": "0.5",
        "assignments[2][coverage]": "0.9",
        "assignments[2][reliability]": "0.9",
    }
    r = await csrf_post(
        client,
        f"/controls/{control_id}/edit",
        payload,
        follow_redirects=False,
    )
    assert r.status_code in (204, 303), (
        f"3-assignment edit should succeed; got {r.status_code}. Body head: {r.text[:300]!r}"
    )
    detail = await client.get(f"/controls/{control_id}")
    assert detail.status_code == 200
    body = detail.text
    assert "lec_prev_resistance" in body
    assert "lec_det_recognition" in body
    assert "vmc_id_control_monitoring" in body, (
        "row 2 sub-function missing — silent row loss regression"
    )


@pytest.mark.asyncio
async def test_add_assignment_button_uses_dynamic_index(client: AsyncClient) -> None:
    """The + Add assignment button must compute index from the live DOM, not
    server-bake it. Template-level guard for issue #123 — catches regressions
    that don't show up in the HTTP integration tests above (e.g., a refactor
    that re-introduces `?index={{ ... }}`).

    Scope: this guard targets the + Add button block specifically. The
    per-row sub-function ``<select>`` (PR μ.1b #129 T5) legitimately bakes
    ``?index=N`` into its hx-get URL — each select knows its row's index at
    render time and never moves, so server-baking is correct there. The bug
    #123 concern was the + Add button reusing the same baked index for every
    click; that concern does not apply to per-row selects.
    """
    email, pwd = await _bootstrap_app(client, uuid.uuid4().hex[:6])
    await _login(client, email, pwd)
    page = await client.get("/controls/new")
    assert page.status_code == 200
    body = page.text
    # Locate the "+ Add assignment" block by anchoring on the literal button
    # text + a window of surrounding markup. The block is well below the
    # assignment-row partials, and the only hx-* attributes inside it belong
    # to the button itself.
    btn_anchor = "+ Add assignment"
    assert btn_anchor in body, "+ Add assignment button missing from form"
    # Take 800 chars BEFORE the anchor to capture the button's attributes
    # (HTMX attributes precede the button text in the source).
    anchor_idx = body.index(btn_anchor)
    btn_block = body[max(0, anchor_idx - 800) : anchor_idx + len(btn_anchor)]

    # Button hx-get omits query params (dynamic index injected via hx-vals).
    assert 'hx-get="/controls/_assignment_row"' in btn_block, (
        "expected + Add button to hx-get the row partial endpoint without query params; not found"
    )
    # No static-baked index in the button's URL.
    assert "/controls/_assignment_row?index=" not in btn_block, (
        "regression: + Add button has a static `?index=` URL — every click "
        "will reuse the same index, silently dropping rows on save (issue #123)"
    )
    # Dynamic index source visible: hx-vals carries a JS expression referencing
    # data-row-index (the per-row attribute on .assignment-row).
    assert "hx-vals='js:" in btn_block and "data-row-index" in btn_block, (
        "regression: + Add button is no longer wired to a DOM-derived index "
        "via hx-vals='js:...' over data-row-index (issue #123)"
    )
