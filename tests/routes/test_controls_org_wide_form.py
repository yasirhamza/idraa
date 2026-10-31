"""#299: the control form renders and posts org_wide_meta; ineligible submissions
(create AND edit) re-render with the refused-save error (no row written / no
change); list/detail show the badge."""

from __future__ import annotations

import json
import re
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.control import Control
from tests.conftest import csrf_post

# Fragments of the service's fixed refusal text (services/controls.py
# ``_check_org_wide_eligible``): the first names the eligible families, the second
# the derived-link rule, so a rewording of either fails here.
MSG = "Variance Management or Decision Support"
LINK_RULE = "counts only when its source is a Variance Management function"


def _data(*sfs: str, org_wide: bool, name: str = "Org Wide Test") -> dict[str, str]:
    d = {"name": name, "type": "administrative"}
    for i, sf in enumerate(sfs):
        d[f"assignments[{i}][sub_function]"] = sf
        d[f"assignments[{i}][capability_value]"] = "0.5"
        d[f"assignments[{i}][coverage]"] = "0.8"
        d[f"assignments[{i}][reliability]"] = "0.8"
    if org_wide:
        d["org_wide_meta"] = "true"  # form_field toggle posts value="true"
    return d


async def _one(db: AsyncSession, name: str) -> Control | None:
    return (await db.execute(select(Control).where(Control.name == name))).scalar_one_or_none()


@pytest.mark.asyncio
async def test_new_form_renders_toggle_and_meta_slugs(
    authed_admin: tuple[AsyncClient, uuid.UUID],
) -> None:
    client, _ = authed_admin
    body = (await client.get("/controls/new")).text
    assert 'name="org_wide_meta"' in body
    assert "vmc_prev_reduce_variance_prob" in body  # Alpine meta-slug list for the disabled state
    assert "score-neutral" in body  # Treatment Selection is label-only


def _hint_text(body: str) -> str:
    """The toggle's explanatory paragraphs (``text-ink-3``), tags stripped."""
    start = body.index("Loss-Event functions still need")
    end = body.index("</div>", start)
    return re.sub(r"<[^>]+>", " ", body[start:end])


@pytest.mark.asyncio
async def test_toggle_hint_states_the_pair_rule_and_scopes_score_neutral_to_treatment_selection(
    authed_admin: tuple[AsyncClient, uuid.UUID],
) -> None:
    """The hint must describe how the VMC/DSC pairs compose (Variance Management
    identification and Implementation score only as a pair; Decision Support's Identify
    misalignment pairs with a Correcting Misaligned Decisions link instead, never with
    Implementation; both across the scenario's active controls, inherited ones included),
    say the uplift is a reliability coupling of the Loss-Event functions and not a
    standalone reduction, carry the Correcting Misaligned Decisions eligibility rule, and
    call ONLY Treatment Selection score-neutral, with the register reference attached to
    that clause alone."""
    client, _ = authed_admin
    body = (await client.get("/controls/new")).text
    hint = " ".join(_hint_text(body).split())
    # the retired, wrong claims
    assert "contribute 0 until" not in body
    assert "until an Implementation function is added" not in body
    assert "Identification-only" not in body
    # "score-neutral" only ever next to Treatment Selection, with the register reference
    assert body.count("score-neutral") == 1
    assert "Treatment Selection is score-neutral (register C8)" in hint
    assert hint.count("register C8") == 1
    # the pair rule is scoped: an unscoped "Identification and Implementation" pair would be
    # false for Decision Support, whose identification pairs with the correcting link
    assert "Identification and Implementation functions score only as a pair" not in hint
    assert (
        "Variance Management identification (Threat intelligence, Control monitoring) and "
        "Implementation score only as a pair" in hint
    )
    assert (
        "Decision Support's Identify misalignment pairs only with a Correcting Misaligned "
        "Decisions link" in hint
    )
    # scenario-wide and inherited-inclusive
    assert "both halves are present across its active controls, inherited ones included" in hint
    assert "alongside another control that supplies the other" in hint
    # reliability coupling, not a direct reduction
    assert (
        "raising the reliability of the Loss-Event functions on the scenario's other controls"
        in hint
    )
    assert "not as a standalone reduction" in hint
    # client-side statement of the derived-link eligibility rule
    assert (
        "A Correcting Misaligned Decisions link counts only when its source is a "
        "Variance Management function" in hint
    )


@pytest.mark.asyncio
async def test_eligibility_scan_reads_only_sub_function_selects(
    authed_admin: tuple[AsyncClient, uuid.UUID],
) -> None:
    """The Alpine eligibility scan filters on the ``[sub_function]`` name suffix (the
    selector the Correcting Misaligned Decisions block uses), so a future per-row select
    cannot make the toggle look eligible."""
    client, _ = authed_admin
    body = (await client.get("/controls/new")).text
    assert 'filter(s => s.name.endsWith("[sub_function]")).some(' in body


@pytest.mark.asyncio
async def test_create_org_wide_meta_control_persists_and_badges(
    authed_admin: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    client, _ = authed_admin
    r = await csrf_post(
        client, "/controls/new", data=_data("vmc_prev_reduce_variance_prob", org_wide=True)
    )
    assert r.status_code in (200, 303)
    c = await _one(db_session, "Org Wide Test")
    assert c is not None and c.org_wide_meta is True
    assert "Org-wide" in (await client.get("/controls")).text
    assert "Org-wide" in (await client.get(f"/controls/{c.id}")).text


@pytest.mark.asyncio
async def test_unflagged_control_shows_no_org_wide_badge(
    authed_admin: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    """Discrimination control for the badge assertions above: a control created
    WITHOUT the toggle must not render the badge on the list or the detail page."""
    client, _ = authed_admin
    r = await csrf_post(
        client,
        "/controls/new",
        data=_data("vmc_prev_reduce_variance_prob", org_wide=False, name="Plain"),
    )
    assert r.status_code in (200, 303)
    c = await _one(db_session, "Plain")
    assert c is not None and c.org_wide_meta is False
    assert "Org-wide" not in (await client.get("/controls")).text
    assert "Org-wide" not in (await client.get(f"/controls/{c.id}")).text


INHERITED_TIP = 'title="Inherited by every scenario (VMC/DSC functions only)"'
NOT_INHERITED_TIP = "Flagged organization-wide; not inherited until the control"


@pytest.mark.asyncio
async def test_badge_tooltip_follows_the_effective_predicate(
    authed_admin: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    """Displays use the effective status (flag set AND status Active AND stage Active). A
    flagged control that is not yet Active keeps its badge but never claims inheritance on
    the list or the detail page; an effective one says it is inherited."""
    client, _ = authed_admin
    planned = _data("vmc_prev_reduce_variance_prob", org_wide=True, name="Flagged Planned")
    planned["implementation_stage"] = "planned"
    r = await csrf_post(client, "/controls/new", data=planned)
    assert r.status_code in (200, 303)
    active = _data("vmc_prev_reduce_variance_prob", org_wide=True, name="Flagged Active")
    r = await csrf_post(client, "/controls/new", data=active)
    assert r.status_code in (200, 303)
    c_planned = await _one(db_session, "Flagged Planned")
    c_active = await _one(db_session, "Flagged Active")
    assert c_planned is not None and c_planned.org_wide_meta is True
    assert c_active is not None and c_active.org_wide_meta is True

    detail = (await client.get(f"/controls/{c_planned.id}")).text
    assert "Org-wide" in detail  # the badge stays for a flagged control
    assert NOT_INHERITED_TIP in detail
    assert "Inherited by every scenario" not in detail

    detail = (await client.get(f"/controls/{c_active.id}")).text
    assert INHERITED_TIP in detail
    assert NOT_INHERITED_TIP not in detail

    listing = (await client.get("/controls")).text
    # The list renders each row's name cell once per layout (table + mobile card), so compare
    # counts instead of pinning 1: one effective control and one planned control must match.
    inherited = listing.count(INHERITED_TIP)
    not_inherited = listing.count(NOT_INHERITED_TIP)
    assert inherited >= 1
    assert inherited == not_inherited
    assert listing.count("Org-wide</span>") == inherited + not_inherited  # no unlabeled badge


@pytest.mark.asyncio
async def test_badge_tooltip_for_inactive_status_is_not_inherited(
    authed_admin: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    """The other half of the effective predicate: stage Active but status DRAFT/DEPRECATED."""
    client, _ = authed_admin
    r = await csrf_post(
        client,
        "/controls/new",
        data={
            **_data("vmc_prev_reduce_variance_prob", org_wide=True, name="Flagged Draft"),
            "status": "draft",
        },
    )
    assert r.status_code in (200, 303)
    c = await _one(db_session, "Flagged Draft")
    assert c is not None and c.org_wide_meta is True
    detail = (await client.get(f"/controls/{c.id}")).text
    assert NOT_INHERITED_TIP in detail
    assert "Inherited by every scenario" not in detail


@pytest.mark.asyncio
async def test_create_org_wide_with_lec_only_rerenders_error_and_writes_nothing(
    authed_admin: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    client, _ = authed_admin
    r = await csrf_post(client, "/controls/new", data=_data("lec_prev_resistance", org_wide=True))
    assert r.status_code == 422
    assert MSG in r.text
    assert LINK_RULE in r.text
    assert await _one(db_session, "Org Wide Test") is None


@pytest.mark.asyncio
async def test_422_for_ineligible_flag_escapes_user_authored_text(
    authed_admin: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    """The refused-save re-render echoes the submitted name; it must reach the page only
    through autoescaping, never as live markup."""
    client, _ = authed_admin
    payload = "<script>alert(1)</script>"
    r = await csrf_post(
        client,
        "/controls/new",
        data=_data("lec_prev_resistance", org_wide=True, name=payload),
    )
    assert r.status_code == 422
    assert payload not in r.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in r.text
    assert await _one(db_session, payload) is None


@pytest.mark.asyncio
async def test_edit_removing_last_meta_row_rerenders_error_and_keeps_flag(
    authed_admin: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    client, _ = authed_admin
    r = await csrf_post(
        client,
        "/controls/new",
        data=_data("lec_prev_resistance", "vmc_corr_implementation", org_wide=True, name="Hybrid"),
    )
    assert r.status_code in (200, 303)
    c = await _one(db_session, "Hybrid")
    assert c is not None
    r = await csrf_post(
        client,
        f"/controls/{c.id}/edit",
        data=_data("lec_prev_resistance", org_wide=True, name="Hybrid"),
    )
    assert r.status_code == 422
    assert MSG in r.text
    await db_session.refresh(c)
    assert c.org_wide_meta is True
    assert {a.sub_function.value for a in c.assignments} == {
        "lec_prev_resistance",
        "vmc_corr_implementation",
    }


def _toggle_tag(body: str) -> str:
    start = body.index('name="org_wide_meta"')
    return body[body.rindex("<input", 0, start) : body.index(">", start) + 1]


def _has_checked_attr(tag: str) -> bool:
    """The BARE ``checked`` attribute. A substring test (``"checked" in tag``) is vacuous
    when the tag carries any other token containing the word; this regex requires
    whitespace before and whitespace or ``>`` after, so ``:disabled=...`` /
    ``x-model=...`` bindings can never satisfy it."""
    return re.search(r"\schecked(?=[\s>])", tag) is not None


@pytest.mark.asyncio
async def test_edit_form_toggle_of_org_wide_control_is_checked_and_never_unconditionally_disabled(
    authed_admin: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    """Browser path of spec §3.1: a disabled form control is NOT submitted, and an absent
    field coerces to False — so the toggle of an org-wide control must render checked and
    must only ever be disabled while UNCHECKED and ineligible. Then the real browser path
    (analyst removes the last meta row, saves) still posts org_wide_meta=true and hits the
    422 above."""
    client, _ = authed_admin
    r = await csrf_post(
        client,
        "/controls/new",
        data=_data("lec_prev_resistance", "vmc_corr_implementation", org_wide=True, name="Hybrid2"),
    )
    assert r.status_code in (200, 303)
    c = await _one(db_session, "Hybrid2")
    assert c is not None
    tag = _toggle_tag((await client.get(f"/controls/{c.id}/edit")).text)
    assert _has_checked_attr(tag)
    assert " disabled" not in tag  # no server-rendered disabled attribute
    assert ':disabled="!eligible && !isOrgWide"' in tag  # the only disabled binding
    assert ':disabled="!eligible"' not in tag
    assert 'x-ref="orgWide"' in tag and 'x-model="isOrgWide"' in tag  # reactive state + handle
    # the 422 re-render of the browser path keeps the toggle enabled and checked too
    r = await csrf_post(
        client,
        f"/controls/{c.id}/edit",
        data=_data("lec_prev_resistance", org_wide=True, name="Hybrid2"),
    )
    assert r.status_code == 422
    tag = _toggle_tag(r.text)
    assert _has_checked_attr(tag) and " disabled" not in tag
    assert ':disabled="!eligible && !isOrgWide"' in tag
    # discrimination control: a NEW (unchecked) form renders the same tag WITHOUT the bare attribute
    new_tag = _toggle_tag((await client.get("/controls/new")).text)
    assert not _has_checked_attr(new_tag)
    assert ':disabled="!eligible && !isOrgWide"' in new_tag


@pytest.mark.asyncio
async def test_unrelated_422_echoes_an_explicit_uncheck(
    authed_admin: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    """An analyst unticks the toggle of an org-wide control and the save is refused for an
    UNRELATED reason. The re-render must show the toggle UNCHECKED, never fall back to the
    stored True. This holds because ``_parse_control_form_dict`` always writes
    ``org_wide_meta`` into ``form_dict`` (False when the toggle is absent), and the 422
    paths re-render with that ``form_dict`` as ``form_data``, so ``cur_org_wide`` takes the
    submitted value."""
    client, _ = authed_admin
    r = await csrf_post(
        client,
        "/controls/new",
        data=_data("vmc_corr_implementation", org_wide=True, name="Uncheck422"),
    )
    assert r.status_code in (200, 303)
    c = await _one(db_session, "Uncheck422")
    assert c is not None and c.org_wide_meta is True
    bad = _data("vmc_corr_implementation", org_wide=False, name="Uncheck422")  # toggle unticked
    bad["assignments[0][reliability]"] = "1.5"  # out of [0, 1] → ValidationError → 422
    r = await csrf_post(client, f"/controls/{c.id}/edit", data=bad)
    assert r.status_code == 422
    assert not _has_checked_attr(_toggle_tag(r.text))
    await db_session.refresh(c)
    assert c.org_wide_meta is True  # nothing saved on the refused edit


@pytest.mark.asyncio
async def test_unrelated_422_keeps_a_checked_toggle_checked(
    authed_admin: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    """Counterpart of the explicit-uncheck echo: when the submitted toggle is CHECKED and
    the save is refused for an unrelated reason, the re-render keeps it checked (and the
    same `_global`-free validation path is used, so the submitted state is what is echoed)."""
    client, _ = authed_admin
    bad = _data("vmc_corr_implementation", org_wide=True, name="Keep422")
    bad["assignments[0][reliability]"] = "1.5"
    r = await csrf_post(client, "/controls/new", data=bad)
    assert r.status_code == 422
    tag = _toggle_tag(r.text)
    assert _has_checked_attr(tag) and " disabled" not in tag
    assert await _one(db_session, "Keep422") is None


@pytest.mark.asyncio
async def test_meta_slugs_in_page_match_fair_cam_export(
    authed_admin: tuple[AsyncClient, uuid.UUID],
) -> None:
    """The Alpine slug list is derived from fair_cam's ``meta_sub_functions()`` — not a
    hand-written copy: the rendered list contains every exported slug and no LEC slug."""
    from fair_cam.risk_engine.group_composition import meta_sub_functions

    client, _ = authed_admin
    body = (await client.get("/controls/new")).text
    m = re.search(r"metaSlugs:\s*(\[[^\]]*\])", body)
    assert m is not None
    rendered = json.loads(m.group(1))
    assert sorted(rendered) == sorted(sf.value for sf in meta_sub_functions())
    assert "lec_prev_resistance" not in rendered
