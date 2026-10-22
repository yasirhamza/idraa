"""#268 — the control form's "Correcting Misaligned Decisions" link select and its routes.

The derived ``dsc_corr_misaligned`` row stores only a link to another row of the SAME
control. It is authored through a top-level form field
(``corrects_misaligned_decisions_from``) — never through the submitted assignments — which
the routes parse separately and hand to ``create_control`` / ``update_control``.

Pinned here: the select renders (new + edit, edit pre-filled, derived row hidden from the
assignment editor), the unchanged-save round trip, the 422 refusals (create AND edit — a
refused edit re-renders the whole page and changes nothing), the reflected-breakout
escaping, the IntegrityError branches, RBAC/CSRF, the CSV export, and the resync consent
page / resync IntegrityError redirect.
"""

from __future__ import annotations

import json
import re
import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from bs4 import BeautifulSoup
from bs4.element import Tag
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.audit_log import AuditLog
from idraa.models.control import Control
from idraa.models.control_function_assignment import ControlFunctionAssignment
from idraa.models.enums import DSC_CORR_LINK_SOURCES, FairCamSubFunction
from idraa.services import controls as controls_svc
from idraa.services.sub_function_options import build_sub_function_groups
from tests.conftest import csrf_post
from tests.services.test_control_resync import _adopted_with_derived, _entry_version

LINK = "corrects_misaligned_decisions_from"
PREV = "lec_prev_resistance"
ET = "lec_resp_event_termination"
RES = "lec_resp_resilience"
IMPL = "vmc_corr_implementation"
DSC = "dsc_corr_misaligned"

# Capability per sub-function in the units the form expects (days for the ELAPSED_TIME ones).
_CAP = {PREV: "0.75", ET: "30", RES: "0.7", IMPL: "3"}

_UNKNOWN_SOURCE_MESSAGE = "Unknown Correcting Misaligned Decisions source"
_HELP_TEXT = (
    "Also credit this control's correction or response process with Correcting Misaligned "
    "Decisions (FAIR-CAM §5.3). It takes that process's effectiveness."
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _payload(
    name: str,
    subs: list[str],
    link: str | None = None,
    **overrides: str,
) -> dict[str, str]:
    data = {"name": name, "description": "d", "type": "technical", "annual_cost": "1000"}
    for i, sf in enumerate(subs):
        data[f"assignments[{i}][sub_function]"] = sf
        data[f"assignments[{i}][capability_value]"] = _CAP[sf]
        data[f"assignments[{i}][coverage]"] = "0.8"
        data[f"assignments[{i}][reliability]"] = "0.8"
    if link is not None:
        data[LINK] = link
    data.update(overrides)
    return data


async def _create_linked(
    client: AsyncClient, name: str, subs: list[str] | None = None, link: str | None = ET
) -> uuid.UUID:
    """Create a control through the real route; return its id."""
    subs = subs if subs is not None else [PREV, ET]
    r = await csrf_post(client, "/controls/new", _payload(name, subs, link), follow_redirects=False)
    assert r.status_code == 303, r.text[:600]
    return uuid.UUID(r.headers["location"].rsplit("/", 1)[1])


async def _rows(db: AsyncSession, control_id: uuid.UUID) -> dict[str, ControlFunctionAssignment]:
    result = await db.execute(
        select(ControlFunctionAssignment)
        .where(ControlFunctionAssignment.control_id == control_id)
        .execution_options(populate_existing=True)
    )
    return {r.sub_function.value: r for r in result.scalars().all()}


async def _control(db: AsyncSession, control_id: uuid.UUID) -> Control:
    result = await db.execute(
        select(Control).where(Control.id == control_id).execution_options(populate_existing=True)
    )
    return result.scalar_one()


async def _audit_count(db: AsyncSession, org_id: uuid.UUID) -> int:
    result = await db.execute(
        select(func.count()).select_from(AuditLog).where(AuditLog.organization_id == org_id)
    )
    return int(result.scalar_one())


async def _confirm_audits(db: AsyncSession, entity_id: uuid.UUID) -> list[AuditLog]:
    result = await db.execute(
        select(AuditLog).where(
            AuditLog.entity_id == entity_id,
            AuditLog.action == "control_function_assignment.confirm",
        )
    )
    return list(result.scalars().all())


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def _link_select(html: str) -> Tag:
    select_el = _soup(html).find("select", attrs={"name": LINK})
    assert isinstance(select_el, Tag), "the Correcting Misaligned Decisions select is missing"
    return select_el


def _selected_values(select_el: Tag) -> list[str]:
    return [str(o.get("value", "")) for o in select_el.find_all("option") if o.has_attr("selected")]


def _form_payload(html: str) -> dict[str, str]:
    """What a browser would submit for the rendered #control-form, unchanged."""
    form = _soup(html).find("form", id="control-form")
    assert isinstance(form, Tag)
    data: dict[str, str] = {}
    for el in form.find_all(["input", "select", "textarea"]):
        name = el.get("name")
        if not name or el.has_attr("disabled"):
            continue
        if el.name == "input":
            kind = str(el.get("type", "text"))
            if kind in {"submit", "button", "image", "reset", "file"}:
                continue
            if kind in {"checkbox", "radio"} and not el.has_attr("checked"):
                continue
            data[str(name)] = str(el.get("value", ""))
        elif el.name == "select":
            chosen = el.find("option", selected=True) or el.find("option")
            data[str(name)] = str(chosen.get("value", "")) if isinstance(chosen, Tag) else ""
        else:
            data[str(name)] = el.get_text()
    return data


# ---------------------------------------------------------------------------
# the select renders
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_new_form_renders_the_link_select_with_exactly_the_eligible_sources(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
) -> None:
    client, _ = authed_analyst
    r = await client.get("/controls/new")
    assert r.status_code == 200
    select_el = _link_select(r.text)

    options = [
        (str(o.get("value", "")), o.get_text(strip=True)) for o in select_el.find_all("option")
    ]
    assert options[0][0] == "" and "none" in options[0][1]
    # Parity with DSC_CORR_LINK_SOURCES: the template lists the slugs itself (its render
    # points are anchored), so the set is pinned here against the engine-side mirror.
    assert {v for v, _ in options[1:]} == {sf.value for sf in DSC_CORR_LINK_SOURCES}
    assert len(options) == 1 + len(DSC_CORR_LINK_SOURCES)
    # Labels come from sub_function_options, never from the slug.
    labels = {o.value: o.label for g in build_sub_function_groups() for o in g.options}
    for value, text in options[1:]:
        assert text == labels[value]
    # Nothing is pre-selected on a new control; the help text is the specified sentence.
    assert _selected_values(select_el) in ([], [""])
    assert "Decision Support" in r.text
    assert _HELP_TEXT in r.text.replace("&#39;", "'")
    # The derived-only sub-function is never a picker choice.
    assert DSC not in {o.get("value") for o in _soup(r.text).find_all("option")}


@pytest.mark.asyncio
async def test_edit_form_prefills_the_link_and_hides_the_derived_row_from_the_editor(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
) -> None:
    client, _ = authed_analyst
    cid = await _create_linked(client, "Linked edit render", [PREV, ET, RES], link=RES)

    r = await client.get(f"/controls/{cid}/edit")
    assert r.status_code == 200
    assert _selected_values(_link_select(r.text)) == [RES]
    # Only the three real rows are editable; the derived row is not an assignment row.
    rows = _soup(r.text).select("#assignments .assignment-row")
    assert len(rows) == 3
    row_values = {
        str(s.get("value", "")) or str(o.get("value", ""))
        for row in rows
        for s in row.find_all("select")
        for o in s.find_all("option", selected=True)
    }
    assert row_values == {PREV, ET, RES}
    assert not any(
        o.get("value") == DSC and o.has_attr("selected") for o in _soup(r.text).find_all("option")
    )


@pytest.mark.asyncio
async def test_edit_form_of_an_unlinked_control_selects_none(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
) -> None:
    client, _ = authed_analyst
    cid = await _create_linked(client, "Unlinked edit render", [PREV, ET], link=None)
    r = await client.get(f"/controls/{cid}/edit")
    assert r.status_code == 200
    assert _selected_values(_link_select(r.text)) in ([], [""])


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_new_with_a_link_creates_the_derived_row(
    authed_analyst: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    client, _ = authed_analyst
    cid = await _create_linked(client, "Linked create", [PREV, ET], link=ET)
    rows = await _rows(db_session, cid)
    assert set(rows) == {PREV, ET, DSC}
    derived = rows[DSC]
    assert derived.derived_from_assignment_id == rows[ET].id
    assert (derived.capability_value, derived.coverage, derived.reliability) == (None, 0.0, 0.0)
    assert derived.confirmed_by_user_at is not None  # a form save confirms it


@pytest.mark.asyncio
@pytest.mark.parametrize("link", [None, ""], ids=["field-absent", "field-empty"])
async def test_post_new_without_a_link_creates_no_derived_row(
    authed_analyst: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession, link: str | None
) -> None:
    client, _ = authed_analyst
    cid = await _create_linked(client, f"Unlinked create {link!r}", [PREV, ET], link=link)
    assert set(await _rows(db_session, cid)) == {PREV, ET}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("subs", "link", "message"),
    [
        ([PREV, ET], PREV, "is not an eligible source"),
        ([PREV, ET], RES, "no longer has"),
        ([PREV, ET], DSC, "is not an eligible source"),
        ([PREV, ET], "no_such_function", _UNKNOWN_SOURCE_MESSAGE),
    ],
    ids=["ineligible", "absent-source", "derived-slug-itself", "unknown-slug"],
)
async def test_post_new_refuses_a_bad_link_with_a_422_and_creates_nothing(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
    subs: list[str],
    link: str,
    message: str,
) -> None:
    client, org_id = authed_analyst
    name = f"Refused create {link}"
    r = await csrf_post(client, "/controls/new", _payload(name, subs, link), follow_redirects=False)
    assert r.status_code == 422
    assert message in r.text
    # Python's stock enum error must never reach the page.
    assert "is not a valid" not in r.text
    assert "FairCamSubFunction" not in r.text
    found = (await db_session.execute(select(Control).where(Control.name == name))).first()
    assert found is None
    assert await _audit_count(db_session, org_id) == 0


@pytest.mark.asyncio
async def test_a_refused_create_keeps_the_submitted_link_selection(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
) -> None:
    client, _ = authed_analyst
    # RES is eligible but the submission does not carry it -> refused; the select keeps it.
    r = await csrf_post(
        client, "/controls/new", _payload("Keep selection", [PREV, ET], RES), follow_redirects=False
    )
    assert r.status_code == 422
    assert _selected_values(_link_select(r.text)) == [RES]


@pytest.mark.asyncio
async def test_link_posted_as_a_file_part_is_a_422_not_a_500(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
) -> None:
    client, _ = authed_analyst
    await client.get("/setup")
    token = client.cookies.get("csrf_token")
    r = await client.post(
        "/controls/new",
        data={**_payload("File link", [PREV, ET]), "_csrf": token},
        files={LINK: ("link.txt", b"lec_resp_event_termination", "text/plain")},
    )
    assert r.status_code == 422
    assert _UNKNOWN_SOURCE_MESSAGE in r.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    ['"><script>alert(1)</script>', "'};alert(1)//", "</script><img src=x onerror=alert(1)>"],
    ids=["tag-breakout", "js-breakout", "script-close"],
)
async def test_a_reflected_breakout_link_value_is_escaped(
    authed_analyst: tuple[AsyncClient, uuid.UUID], payload: str
) -> None:
    client, _ = authed_analyst
    r = await csrf_post(
        client, "/controls/new", _payload("Breakout", [PREV, ET], payload), follow_redirects=False
    )
    assert r.status_code == 422
    assert _UNKNOWN_SOURCE_MESSAGE in r.text
    # The raw payload appears nowhere in the page ...
    assert payload not in r.text
    assert "alert(1)</script>" not in r.text
    # ... it only survives as a JSON string inside the SINGLE-QUOTED x-data attribute.
    fieldset = _link_select(r.text).find_parent("fieldset")
    assert isinstance(fieldset, Tag)
    x_data = str(fieldset.get("x-data", ""))
    assert x_data, "the link fieldset must carry its Alpine state in x-data"
    assert re.search(r"<fieldset[^>]*\sx-data='", r.text), "x-data must be single-quoted"
    # The state literal round-trips through JSON to the exact submitted string and carries no
    # raw HTML / attribute metacharacter (tojson escapes < > & ' as backslash-u sequences).
    literal = re.search(r"selected:\s*(\"(?:[^\"\\]|\\.)*\")", x_data)
    assert literal is not None
    assert json.loads(literal.group(1)) == payload
    assert not set("<>&'") & set(literal.group(1))
    # The select itself never selects a value outside the trusted option list.
    assert _selected_values(_link_select(r.text)) in ([], [""])


@pytest.mark.asyncio
async def test_an_injected_derived_from_assignment_id_is_ignored(
    authed_analyst: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    client, _ = authed_analyst
    data = _payload("Injected id", [PREV, ET])
    data["assignments[0][derived_from_assignment_id]"] = str(uuid.uuid4())
    r = await csrf_post(client, "/controls/new", data, follow_redirects=False)
    assert r.status_code == 303
    cid = uuid.UUID(r.headers["location"].rsplit("/", 1)[1])
    rows = await _rows(db_session, cid)
    assert set(rows) == {PREV, ET}
    assert all(row.derived_from_assignment_id is None for row in rows.values())


@pytest.mark.asyncio
async def test_a_submitted_dsc_corr_misaligned_row_is_refused_even_with_an_injected_id(
    authed_analyst: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    client, org_id = authed_analyst
    data = _payload("Smuggled derived", [ET])
    data.update(
        {
            "assignments[1][sub_function]": DSC,
            "assignments[1][capability_value]": "",
            "assignments[1][coverage]": "0.8",
            "assignments[1][reliability]": "0.8",
            "assignments[1][derived_from_assignment_id]": str(uuid.uuid4()),
        }
    )
    r = await csrf_post(client, "/controls/new", data, follow_redirects=False)
    assert r.status_code == 422
    assert (
        await db_session.execute(select(Control).where(Control.name == "Smuggled derived"))
    ).first() is None
    assert await _audit_count(db_session, org_id) == 0


# ---------------------------------------------------------------------------
# edit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unchanged_edit_round_trip_keeps_and_confirms_the_derived_row_once(
    authed_analyst: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    client, _ = authed_analyst
    cid = await _create_linked(client, "Round trip", [PREV, ET], link=ET)
    before = (await _rows(db_session, cid))[DSC]
    derived_id = before.id
    # A duplicate / resync leaves the derived row unconfirmed; simulate that state.
    before.confirmed_by_user_at = None
    before.measured_by = None
    before.measured_at = None
    await db_session.commit()

    page = await client.get(f"/controls/{cid}/edit")
    assert page.status_code == 200
    payload = _form_payload(page.text)
    assert payload[LINK] == ET
    assert "assignments[2][sub_function]" not in payload  # the derived row is not an editor row

    r = await csrf_post(client, f"/controls/{cid}/edit", payload, follow_redirects=False)
    assert r.status_code == 303, r.text[:600]

    after = (await _rows(db_session, cid))[DSC]
    assert after.id == derived_id  # same row, not delete + recreate
    assert after.derived_from_assignment_id == (await _rows(db_session, cid))[ET].id
    assert after.confirmed_by_user_at is not None
    assert len(await _confirm_audits(db_session, derived_id)) == 1


@pytest.mark.asyncio
async def test_edit_can_repoint_and_then_clear_the_link(
    authed_analyst: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    client, _ = authed_analyst
    cid = await _create_linked(client, "Repoint", [PREV, ET, RES], link=ET)
    derived_id = (await _rows(db_session, cid))[DSC].id

    r = await csrf_post(
        client,
        f"/controls/{cid}/edit",
        _payload("Repoint", [PREV, ET, RES], link=RES),
        follow_redirects=False,
    )
    assert r.status_code == 303
    rows = await _rows(db_session, cid)
    assert rows[DSC].id == derived_id
    assert rows[DSC].derived_from_assignment_id == rows[RES].id

    r = await csrf_post(
        client,
        f"/controls/{cid}/edit",
        _payload("Repoint", [PREV, ET, RES], link=""),
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert DSC not in await _rows(db_session, cid)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("link", "message"),
    [
        (RES, "no longer has"),  # eligible, but the submission does not carry it
        (PREV, "is not an eligible source"),
        ("no_such_function", _UNKNOWN_SOURCE_MESSAGE),
    ],
    ids=["absent-source", "ineligible", "unknown-slug"],
)
async def test_a_refused_edit_rerenders_the_full_page_and_changes_nothing(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
    link: str,
    message: str,
) -> None:
    client, org_id = authed_analyst
    cid = await _create_linked(client, "Refused edit", [PREV, ET], link=ET)
    rows_before = await _rows(db_session, cid)
    derived_id = rows_before[DSC].id
    caps_before = {sf: row.capability_value for sf, row in rows_before.items()}
    audits_before = await _audit_count(db_session, org_id)

    # Changed name AND changed capability values: none of it may land.
    data = _payload("Renamed by a refused edit", [PREV, ET], link=link)
    data["assignments[0][capability_value]"] = "0.11"
    data["assignments[1][capability_value]"] = "99"
    r = await csrf_post(client, f"/controls/{cid}/edit", data, follow_redirects=False)

    assert r.status_code == 422
    assert "<html" in r.text.lower() and "Edit Control" in r.text  # the whole template, no 500
    assert message in r.text
    if link == RES:
        assert _selected_values(_link_select(r.text)) == [RES]  # keeps the submitted selection

    control = await _control(db_session, cid)
    assert control.name == "Refused edit"
    rows_after = await _rows(db_session, cid)
    assert set(rows_after) == {PREV, ET, DSC}
    assert {sf: row.capability_value for sf, row in rows_after.items()} == caps_before
    assert rows_after[DSC].id == derived_id
    assert rows_after[DSC].derived_from_assignment_id == rows_after[ET].id
    assert await _audit_count(db_session, org_id) == audits_before


@pytest.mark.asyncio
async def test_a_smuggled_derived_row_on_edit_is_refused_and_changes_nothing(
    authed_analyst: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    client, org_id = authed_analyst
    cid = await _create_linked(client, "Smuggle on edit", [PREV, ET], link=ET)
    rows_before = await _rows(db_session, cid)
    audits_before = await _audit_count(db_session, org_id)

    data = _payload("Smuggle on edit", [PREV, ET], link=ET)
    data.update(
        {
            "assignments[2][sub_function]": DSC,
            "assignments[2][capability_value]": "",
            "assignments[2][coverage]": "0.8",
            "assignments[2][reliability]": "0.8",
            "assignments[2][derived_from_assignment_id]": str(rows_before[ET].id),
        }
    )
    r = await csrf_post(client, f"/controls/{cid}/edit", data, follow_redirects=False)
    assert r.status_code == 422
    rows_after = await _rows(db_session, cid)
    assert rows_after[DSC].id == rows_before[DSC].id
    assert await _audit_count(db_session, org_id) == audits_before


# ---------------------------------------------------------------------------
# IntegrityError branches (the session is rolled back, so expired ORM state must not be read)
# ---------------------------------------------------------------------------

_ORIG = "UNIQUE constraint failed: secret_table.secret_column"


async def _boom(*_a: Any, **_k: Any) -> None:
    raise IntegrityError("INSERT INTO secret_table", {}, Exception(_ORIG))


@pytest.mark.asyncio
async def test_new_post_integrity_error_is_a_409_that_does_not_leak_the_db_error(
    authed_analyst: tuple[AsyncClient, uuid.UUID], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = authed_analyst
    monkeypatch.setattr(controls_svc, "create_control", _boom)
    r = await csrf_post(
        client, "/controls/new", _payload("Integrity new", [PREV, ET], ET), follow_redirects=False
    )
    assert r.status_code == 409
    assert "already exists" in r.text
    assert "secret_table" not in r.text and "secret_column" not in r.text
    assert _selected_values(_link_select(r.text)) == [ET]  # keeps the submitted selection


@pytest.mark.asyncio
async def test_edit_post_integrity_error_is_a_409_with_the_full_page_and_no_leak(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _ = authed_analyst
    cid = await _create_linked(client, "Integrity edit", [PREV, ET], link=ET)
    monkeypatch.setattr(controls_svc, "update_control", _boom)
    r = await csrf_post(
        client,
        f"/controls/{cid}/edit",
        _payload("Integrity edit", [PREV, ET], link=ET),
        follow_redirects=False,
    )
    assert r.status_code == 409
    assert "Edit Control" in r.text and "already exists" in r.text
    assert "secret_table" not in r.text and "secret_column" not in r.text
    assert (await _rows(db_session, cid))[DSC].derived_from_assignment_id is not None


# ---------------------------------------------------------------------------
# RBAC / CSRF / export
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_viewer_cannot_open_or_submit_the_form(
    authed_viewer: tuple[AsyncClient, uuid.UUID],
) -> None:
    client, _ = authed_viewer
    assert (await client.get("/controls/new")).status_code == 403
    assert (await client.get(f"/controls/{uuid.uuid4()}/edit")).status_code == 403
    r = await csrf_post(client, "/controls/new", _payload("Viewer", [PREV, ET], ET))
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_a_post_without_a_csrf_token_is_rejected(
    authed_analyst: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    client, _ = authed_analyst
    r = await client.post("/controls/new", data=_payload("No csrf", [PREV, ET], ET))
    assert r.status_code == 403
    assert (
        await db_session.execute(select(Control).where(Control.name == "No csrf"))
    ).first() is None


@pytest.mark.asyncio
async def test_the_controls_csv_export_lists_the_derived_sub_function(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
) -> None:
    client, _ = authed_analyst
    await _create_linked(client, "Exported linked", [PREV, ET], link=ET)
    r = await client.get("/controls/export.csv")
    assert r.status_code == 200
    line = next(ln for ln in r.text.splitlines() if "Exported linked" in ln)
    slugs = line.rsplit(",", 1)[-1].strip().strip('"').split("|")
    assert sorted(slugs) == sorted([PREV, ET, DSC])


# ---------------------------------------------------------------------------
# resync consent page + IntegrityError redirect
# ---------------------------------------------------------------------------

_SOURCE_ID = uuid.UUID("00000000-0000-4000-8000-000000000001")
_DERIVED_ID = uuid.UUID("ffffffff-ffff-4fff-bfff-fffffffffffe")


async def _stale_linked_control(
    db: AsyncSession, org_id: uuid.UUID, *, v2_funcs: list[FairCamSubFunction]
) -> uuid.UUID:
    """An adopted control carrying a derived row whose library entry has a newer version."""

    async def seed(_db: Any) -> tuple[Any, Any]:
        return SimpleNamespace(id=org_id), SimpleNamespace(id=None)

    _, _, control, entry = await _adopted_with_derived(
        db,
        seed,
        source_id=_SOURCE_ID,
        derived_id=_DERIVED_ID,
        v1_funcs=[
            FairCamSubFunction.VMC_CORR_IMPLEMENTATION,
            FairCamSubFunction.LEC_PREV_RESISTANCE,
        ],
    )
    await _entry_version(db, funcs=v2_funcs, version=2, entry_id=entry.id)
    await db.commit()  # cross-engine SQLite: commit so the route's session sees it
    return control.id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("v2_funcs", "expected", "unexpected"),
    [
        (
            [FairCamSubFunction.VMC_CORR_IMPLEMENTATION, FairCamSubFunction.LEC_RESP_RESILIENCE],
            "Correcting Misaligned Decisions link is kept",
            "Correcting Misaligned Decisions link will be removed",
        ),
        (
            [FairCamSubFunction.LEC_PREV_RESISTANCE, FairCamSubFunction.LEC_RESP_RESILIENCE],
            "Correcting Misaligned Decisions link will be removed",
            "Correcting Misaligned Decisions link is kept",
        ),
    ],
    ids=["relinked", "dropped"],
)
async def test_the_resync_page_reports_the_derived_rows_fate(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
    v2_funcs: list[FairCamSubFunction],
    expected: str,
    unexpected: str,
) -> None:
    client, org_id = authed_analyst
    cid = await _stale_linked_control(db_session, org_id, v2_funcs=v2_funcs)
    r = await client.get(f"/controls/{cid}/resync")
    assert r.status_code == 200
    text = " ".join(_soup(r.text).get_text().split())  # collapse template line breaks
    assert expected in text
    assert unexpected not in text
    # The sentinel row is not a diff row, and the copy no longer claims ALL assignments go.
    assert DSC not in text
    assert "ALL function assignments" not in text


@pytest.mark.asyncio
async def test_a_control_without_a_derived_row_shows_no_fate_on_the_resync_page(
    authed_analyst: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    client, org_id = authed_analyst
    cid = await _stale_linked_control(
        db_session, org_id, v2_funcs=[FairCamSubFunction.VMC_CORR_IMPLEMENTATION]
    )
    derived = (await _rows(db_session, cid))[DSC]
    await db_session.delete(derived)
    await db_session.commit()
    r = await client.get(f"/controls/{cid}/resync")
    assert r.status_code == 200
    assert "Correcting Misaligned Decisions link" not in " ".join(r.text.split())


@pytest.mark.asyncio
async def test_a_resync_integrity_error_redirects_with_a_fixed_alert_and_changes_nothing(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from idraa.routes import controls as controls_routes

    client, org_id = authed_analyst
    cid = await _stale_linked_control(
        db_session,
        org_id,
        v2_funcs=[
            FairCamSubFunction.VMC_CORR_IMPLEMENTATION,
            FairCamSubFunction.LEC_RESP_RESILIENCE,
        ],
    )
    rows_before = await _rows(db_session, cid)
    audits_before = await _audit_count(db_session, org_id)

    monkeypatch.setattr(controls_routes, "apply_resync", _boom)
    r = await csrf_post(client, f"/controls/{cid}/resync", {}, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == f"/controls/{cid}?resync_error=1"

    page = await client.get(r.headers["location"])
    assert page.status_code == 200
    assert "Resync could not be applied; no changes were saved" in page.text
    assert "secret_table" not in page.text and "secret_column" not in page.text
    # And without the parameter the alert is absent.
    clean = await client.get(f"/controls/{cid}")
    assert "Resync could not be applied" not in clean.text

    rows_after = await _rows(db_session, cid)
    assert {sf: row.id for sf, row in rows_after.items()} == {
        sf: row.id for sf, row in rows_before.items()
    }
    assert rows_after[DSC].id == _DERIVED_ID
    assert await _audit_count(db_session, org_id) == audits_before
