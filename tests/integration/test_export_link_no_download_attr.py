"""Task 7: the ``download`` HTML attribute is redundant AND harmful on
step-up-gated export links.

``page_header``, ``data_table``, and ``action_menu`` all used to emit
``hx-boost="false" download`` for ``download=True`` items. ``hx-boost="false"``
is correct — it opts the anchor out of the global ``<body hx-boost="true">``
so the click performs a real browser navigation instead of an HTMX AJAX swap.

But the ``download`` attribute tells the browser: whatever the *final*
resource is after following redirects, save it to disk — don't render/navigate
it. When a step-up-gated export is stale, the server 303s to
``/auth/step-up``; with ``download`` present the browser saves that redirect's
HTML page as a file instead of navigating the user to the step-up form. The
server already sends ``Content-Disposition: attachment`` on every real
export/report response (see the Task 7 audit in
``.superpowers/sdd/task-7-report.md``), so the attribute buys nothing on the
success path and actively breaks the step-up redirect path.

Fix: keep emitting ``hx-boost="false"`` (still needed) but drop the bare
``download`` token. This file has two layers:

  - macro-level (direct Jinja render, no HTTP/DB): the three macros emit
    ``hx-boost="false"`` without a ``download`` attribute for a
    ``download=True`` item.
  - route-level (real app, real DB): a step-up-gated export renders its link
    without ``download``, and hitting the export while stale actually
    navigates (303 to /auth/step-up) rather than downloading a 200 attachment.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

import idraa.services.security_settings as ss
from idraa.app import templates
from idraa.models.risk_analysis_run import RiskAnalysisRun
from idraa.models.security_settings import SecuritySettings
from tests.integration.test_step_up_flow import _make_stale  # reuse the real stale-session helper

# Anchors can span multiple lines (attrs on their own line) — DOTALL.
_ANCHOR_RE = re.compile(r"<a\b.*?>", re.IGNORECASE | re.DOTALL)

# A bare HTML boolean `download` attribute: whitespace before/after (or `>`),
# never followed by `=` (which would make it `download="..."`, not the same
# footgun — none of our call sites use that form, but this keeps the check
# precise rather than a blunt substring match).
_BARE_DOWNLOAD_ATTR_RE = re.compile(r"(?<![\w-])download(?![\w=-])")


def _anchor_to(html: str, href_substr: str) -> str:
    matches = [str(tag) for tag in _ANCHOR_RE.findall(html) if href_substr in tag]
    assert matches, f"expected an anchor referencing {href_substr!r} in:\n{html}"
    return matches[0]


async def _apply(db: AsyncSession, org_id: uuid.UUID, **kw: object) -> None:
    db.add(SecuritySettings(organization_id=org_id, step_up_window_seconds=600, **kw))
    await db.commit()
    await ss.load_security_settings(db, org_id)


# ---------------------------------------------------------------------------
# Macro-level: render each macro directly, no HTTP/DB involved.
# ---------------------------------------------------------------------------


def test_page_header_download_link_no_download_attr() -> None:
    src = (
        "{% from 'macros/page_header.html' import page_header %}"
        "{{ page_header(title='X', actions=[{'label': 'Export CSV', "
        "'href': '/controls/export.csv', 'style': 'brand', 'download': True}]) }}"
    )
    html = templates.env.from_string(src).render()
    tag = _anchor_to(html, "/controls/export.csv")
    assert 'hx-boost="false"' in tag, f"boost opt-out missing: {tag!r}"
    assert not _BARE_DOWNLOAD_ATTR_RE.search(tag), f"bare download attr still emitted: {tag!r}"


def test_page_header_non_brand_download_link_no_download_attr() -> None:
    """The macro has two branches (brand-style vs outline/primary/ghost) — cover both."""
    src = (
        "{% from 'macros/page_header.html' import page_header %}"
        "{{ page_header(title='X', actions=[{'label': 'CSV template', "
        "'href': '/overlays/template.csv', 'style': 'outline', 'download': True}]) }}"
    )
    html = templates.env.from_string(src).render()
    tag = _anchor_to(html, "/overlays/template.csv")
    assert 'hx-boost="false"' in tag, f"boost opt-out missing: {tag!r}"
    assert not _BARE_DOWNLOAD_ATTR_RE.search(tag), f"bare download attr still emitted: {tag!r}"


def test_data_table_exportable_link_no_download_attr() -> None:
    src = (
        "{% from 'macros/data_table.html' import data_table %}"
        "{{ data_table(rows=[{'name': 'x'}], columns=[{'key': 'name', 'label': 'Name'}], "
        "exportable=True, export_url='/controls/export.csv') }}"
    )
    html = templates.env.from_string(src).render()
    tag = _anchor_to(html, "/controls/export.csv")
    assert 'hx-boost="false"' in tag, f"boost opt-out missing: {tag!r}"
    assert not _BARE_DOWNLOAD_ATTR_RE.search(tag), f"bare download attr still emitted: {tag!r}"


def test_action_menu_download_item_no_download_attr() -> None:
    src = (
        "{% from 'macros/action_menu.html' import action_menu %}"
        "{{ action_menu([{'label': 'Download PDF', 'href': '/reports/run/x', "
        "'download': True}]) }}"
    )
    html = templates.env.from_string(src).render()
    tag = _anchor_to(html, "/reports/run/x")
    assert 'hx-boost="false"' in tag, f"boost opt-out missing: {tag!r}"
    assert not _BARE_DOWNLOAD_ATTR_RE.search(tag), f"bare download attr still emitted: {tag!r}"


def test_action_menu_busy_spinner_still_wired_without_download_attr() -> None:
    """Step 3 check: the tap-debounce spinner (@click/busy, 'Generating…') is
    coupled to `item.download`, not to the HTML `download` attribute itself —
    removing the attribute must not silently drop the debounce guard that
    exists to prevent stacked-download DB-pool exhaustion (prod outage
    2026-06-15)."""
    src = (
        "{% from 'macros/action_menu.html' import action_menu %}"
        "{{ action_menu([{'label': 'Download PDF', 'href': '/reports/run/x', "
        "'download': True}]) }}"
    )
    html = templates.env.from_string(src).render()
    tag = _anchor_to(html, "/reports/run/x")
    assert "busy ? $event.preventDefault()" in tag
    assert 'x-show="busy"' in html
    assert "Generating" in html


def test_empty_state_cta_download_link_no_download_attr() -> None:
    """Scope-expansion follow-up (coordinator-approved, same PR): the same
    ``download`` token in ``macros/empty_state.html``'s ``cta`` rendering —
    reachable from ``overlays/list.html``'s empty-state CTA to
    ``/overlays/template.csv``."""
    src = (
        "{% from 'macros/empty_state.html' import empty_state %}"
        "{{ empty_state('⨂', 'No overlays yet', 'Create one or import.', "
        "cta=[{'label': 'CSV template', 'href': '/overlays/template.csv', "
        "'style': 'outline', 'download': True}]) }}"
    )
    html = templates.env.from_string(src).render()
    tag = _anchor_to(html, "/overlays/template.csv")
    assert 'hx-boost="false"' in tag, f"boost opt-out missing: {tag!r}"
    assert not _BARE_DOWNLOAD_ATTR_RE.search(tag), f"bare download attr still emitted: {tag!r}"


# ---------------------------------------------------------------------------
# Route-level: real app + DB. Exercise a live export link and the step-up
# redirect path it must survive.
# ---------------------------------------------------------------------------


async def test_controls_export_link_renders_without_download_attr(
    authed_admin: tuple[AsyncClient, uuid.UUID],
) -> None:
    client, _ = authed_admin
    r = await client.get("/controls")
    assert r.status_code == 200
    tag = _anchor_to(r.text, "/controls/export.csv")
    assert 'hx-boost="false"' in tag, f"boost opt-out missing: {tag!r}"
    assert not _BARE_DOWNLOAD_ATTR_RE.search(tag), f"bare download attr still emitted: {tag!r}"


async def test_stale_session_export_navigates_to_step_up_not_a_200_attachment(
    authed_admin: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """The actual bug this task fixes: with EXPORTS step-up on and a stale
    session, GET /controls/export.csv must 303 to /auth/step-up (a page the
    browser can navigate to) — never a 200 with Content-Disposition:
    attachment (which, combined with the now-removed `download` attribute,
    used to get saved to disk as an HTML file instead)."""
    client, org_id = authed_admin
    await _apply(db_session, org_id, step_up_exports=True)
    await _make_stale(db_session, client)
    r = await client.get("/controls/export.csv", follow_redirects=False)
    assert r.status_code == 303
    assert "/auth/step-up" in r.headers["location"]
    assert "Content-Disposition" not in r.headers


async def test_overlays_page_empty_state_cta_no_download_attr(
    authed_admin: tuple[AsyncClient, uuid.UUID],
) -> None:
    """Real call site for the empty_state fix: a fresh org has zero overlays,
    so GET /overlays renders macros/empty_state.html's ``cta`` list, which
    includes a second (non-page_header) anchor to /overlays/template.csv.
    Assert on EVERY anchor to that href — page_header's own action for the
    same href is also present on this page and must stay clean too."""
    client, _ = authed_admin
    r = await client.get("/overlays")
    assert r.status_code == 200
    assert "No overlays yet" in r.text, "expected the empty_state branch to render"
    anchors = [tag for tag in _ANCHOR_RE.findall(r.text) if "/overlays/template.csv" in tag]
    assert len(anchors) >= 2, (
        f"expected both the page_header action and the empty-state CTA anchor, got {anchors!r}"
    )
    for tag in anchors:
        assert 'hx-boost="false"' in tag, f"boost opt-out missing: {tag!r}"
        assert not _BARE_DOWNLOAD_ATTR_RE.search(tag), f"bare download attr still emitted: {tag!r}"


@pytest_asyncio.fixture
async def analyst_org_aggregate_run_with_controls(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
    seed_user: Any,
    seed_scenario_factory: Callable[..., Awaitable[Any]],
    seed_control_factory: Callable[..., Awaitable[Any]],
    wire_executor_to_test_db: None,
) -> RiskAnalysisRun:
    """A COMPLETED AGGREGATE run WITH mitigating controls (attribution matrix
    populated), needed so control_ledger.html's ``{% else %}`` branch (which
    renders the raw "Export matrix CSV" link) is reached — the bare
    ``matrix.controls`` check is falsy without an assigned control.

    Copied from tests/integration/test_run_detail_components.py's fixture of
    the same name (that module's own comment explains why: fixtures there are
    file-local, not shared via conftest — a cross-module import trips ruff
    F811 on the parameter shadowing the import). This is the cheapest correct
    way to reach the export link: control_ledger.html is a template macro
    driven by the real display-result builder (currency, weight_robustness,
    Shapley matrix), not something worth hand-faking with a synthetic
    SimpleNamespace.
    """
    from fastapi import BackgroundTasks

    from idraa.models.scenario_control import ScenarioControl
    from idraa.services.runs import RunService

    _, org_id = authed_analyst
    s1 = await seed_scenario_factory(
        name="dl-ctrl-s1", organization_id=org_id, created_by=seed_user.id
    )
    s2 = await seed_scenario_factory(
        name="dl-ctrl-s2", organization_id=org_id, created_by=seed_user.id
    )
    ctrl_a = await seed_control_factory(
        name="DL Control Alpha", organization_id=org_id, created_by=seed_user.id
    )
    db_session.add_all(
        [
            ScenarioControl(scenario_id=s1.id, control_id=ctrl_a.id),
            ScenarioControl(scenario_id=s2.id, control_id=ctrl_a.id),
        ]
    )
    await db_session.commit()

    bg = BackgroundTasks()
    service = RunService(db_session)
    run = await service.create_and_dispatch(
        organization_id=org_id,
        scenario_ids=[s1.id, s2.id],
        mc_iterations_override=200,
        created_by=seed_user.id,
        background_tasks=bg,
    )
    await db_session.refresh(run)
    return run


async def test_control_matrix_export_link_no_download_attr(
    analyst_org_aggregate_run_with_controls: RiskAnalysisRun,
    authed_analyst: tuple[AsyncClient, uuid.UUID],
) -> None:
    """Scope-expansion follow-up (coordinator-approved, same PR):
    runs/components/control_ledger.html's raw (non-macro) "Export matrix CSV"
    link had the identical hardcoded ``hx-boost="false" download`` — not
    itself a macro, so it's exercised directly rather than via a macro-level
    render."""
    client, _ = authed_analyst
    run = analyst_org_aggregate_run_with_controls
    r = await client.get(f"/runs/{run.id}")
    assert r.status_code == 200
    href = f"/runs/{run.id}/control-matrix.csv"
    # The SAME href also appears in an action_menu item on this page
    # (runs/detail.html:56) — already covered by the action_menu macro test.
    # Scope specifically to the raw anchor inside control_ledger.html's
    # <section id="control-ledger"> so a regression there isn't masked by
    # `findall` matching the (already-fixed) action_menu instance first.
    assert 'id="control-ledger"' in r.text, "expected the control ledger section to render"
    ledger_section = r.text.split('id="control-ledger"', 1)[1].split("</section>", 1)[0]
    tag = _anchor_to(ledger_section, href)
    assert 'hx-boost="false"' in tag, f"boost opt-out missing: {tag!r}"
    assert not _BARE_DOWNLOAD_ATTR_RE.search(tag), f"bare download attr still emitted: {tag!r}"
