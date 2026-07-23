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

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

import idraa.services.security_settings as ss
from idraa.app import templates
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
