"""F5-F6: chrome macros — page_header, breadcrumb, status_pill, empty_state, action_menu."""

from __future__ import annotations

import pytest

from idraa.app import templates


def _r(macro_path: str, macro_name: str, **kwargs) -> str:
    """Render a macro with kwargs. Helper for terse macro-test cases."""
    src = f"{{% from '{macro_path}' import {macro_name} %}}{{{{ {macro_name}(**kw) }}}}"
    return templates.env.from_string(src).render(kw=kwargs)


def test_breadcrumb_renders_items_with_separators() -> None:
    html = _r(
        "macros/breadcrumb.html",
        "breadcrumb",
        items=[("Home", "/"), ("Controls", "/controls"), ("Edit", None)],
    )
    assert 'href="/"' in html
    assert 'href="/controls"' in html
    assert "Edit" in html
    # The current page (last item) has no href — rendered as aria-current="page"
    assert 'aria-current="page"' in html


def test_breadcrumb_renders_home_only_for_root() -> None:
    html = _r("macros/breadcrumb.html", "breadcrumb", items=[("Home", None)])
    assert "Home" in html
    assert 'aria-current="page"' in html


def test_page_header_renders_title_breadcrumb_and_actions() -> None:
    html = _r(
        "macros/page_header.html",
        "page_header",
        title="Controls",
        breadcrumb=[("Home", "/"), ("Controls", None)],
        actions=[
            {"label": "Export CSV", "href": "/controls.csv", "style": "outline"},
            {"label": "+ New", "href": "/controls/new", "style": "primary"},
        ],
    )
    assert ">Controls<" in html
    assert "Export CSV" in html
    assert 'href="/controls/new"' in html
    # Sticky positioning
    assert "sticky" in html


def test_page_header_supports_meta_subtitle() -> None:
    html = _r(
        "macros/page_header.html",
        "page_header",
        title="Dashboard",
        breadcrumb=[("Home", None)],
        actions=[],
        meta="Last refreshed 2 minutes ago",
    )
    assert "Last refreshed 2 minutes ago" in html


def test_page_header_action_with_requires_md_hides_on_phone() -> None:
    """Plan-gate spec §2: authoring actions hidden on <md."""
    html = _r(
        "macros/page_header.html",
        "page_header",
        title="Controls",
        breadcrumb=[("Home", None)],
        actions=[{"label": "+ New", "href": "/x/new", "style": "primary", "requires_md": True}],
    )
    assert "hidden md:inline-flex" in html


def test_page_header_action_supports_htmx_attrs() -> None:
    html = _r(
        "macros/page_header.html",
        "page_header",
        title="Runs",
        breadcrumb=[("Home", None)],
        actions=[
            {
                "label": "Refresh",
                "href": "#",
                "hx": {"get": "/runs", "target": "#runs", "swap": "innerHTML"},
            }
        ],
    )
    assert 'hx-get="/runs"' in html
    assert 'hx-target="#runs"' in html
    assert 'hx-swap="innerHTML"' in html


@pytest.mark.parametrize(
    "kind,value,expected_class",
    [
        ("risk", "low", "status-success"),
        ("risk", "moderate", "status-warning"),
        ("risk", "high", "status-critical"),
        ("risk", "critical", "status-critical"),
        ("run", "queued", "status-info"),
        ("run", "running", "status-info"),
        ("run", "completed", "status-success"),
        ("run", "failed", "status-critical"),
        ("run", "cancelled", "ink-3"),
        ("control", "active", "status-success"),
        ("control", "maintenance", "status-warning"),
        ("control", "inactive", "ink-3"),
    ],
)
def test_status_pill_maps_value_to_class(kind: str, value: str, expected_class: str) -> None:
    html = _r("macros/status_pill.html", "status_pill", value=value, kind=kind)
    assert expected_class in html, f"{kind}/{value} must map to {expected_class}"
    # Dot + text — never colour alone (a11y)
    assert "●" in html or "○" in html
    assert value.lower() in html.lower() or value.title() in html


def test_empty_state_renders_title_body_and_optional_cta() -> None:
    html = _r(
        "macros/empty_state.html",
        "empty_state",
        icon="⛨",
        title="No controls yet",
        body="Create one, or import the FAIR-CAM library CSV.",
        cta={"label": "+ New control", "href": "/controls/new", "style": "primary"},
    )
    assert "No controls yet" in html
    assert "Create one" in html
    assert 'href="/controls/new"' in html


def test_empty_state_works_without_cta() -> None:
    html = _r(
        "macros/empty_state.html",
        "empty_state",
        icon="◇",
        title="Nothing here",
        body="The list is empty.",
    )
    assert "Nothing here" in html
    assert "btn" not in html  # no CTA = no button


def test_empty_state_supports_multiple_ctas() -> None:
    html = _r(
        "macros/empty_state.html",
        "empty_state",
        icon="⛨",
        title="No data",
        body="Try again or refresh.",
        cta=[
            {"label": "Refresh", "href": "/x"},
            {"label": "+ New", "href": "/x/new", "style": "primary"},
        ],
    )
    assert "Refresh" in html and "+ New" in html
    assert 'href="/x/new"' in html


def test_action_menu_renders_link_and_form_items() -> None:
    html = _r(
        "macros/action_menu.html",
        "action_menu",
        items=[
            {"label": "Edit", "href": "/x/edit"},
            {"label": "Duplicate", "method": "post", "action": "/x/duplicate"},
            {"label": "Delete", "method": "post", "action": "/x/delete", "danger": True},
        ],
    )
    assert 'href="/x/edit"' in html
    assert 'action="/x/duplicate"' in html
    assert "Delete" in html
    assert "status-critical" in html, "danger items mark themselves"
    # Inline x-data for HTMX swap-race avoidance (CLAUDE.md UI rendering conventions).
    # Substring (not a brace-bound literal) so the x-data can carry extra state —
    # e.g. the `flipUp` boundary-flip flag — without this proxy assertion breaking.
    assert "x-data=" in html
    assert "open: false" in html or "'open':" in html or '"open":' in html


def test_status_pill_confirmed_maps_to_success() -> None:
    """Fix I-1: `confirmed` status (used on confirmed assignment rows) maps to
    status-success, not the dim grey fallback."""
    html = _r("macros/status_pill.html", "status_pill", value="confirmed", kind="control")
    assert "status-success" in html
    assert "●" in html


@pytest.mark.parametrize("status", ["draft", "active", "deprecated", "deleted"])
def test_status_pill_entity_kind_renders_non_neutral(status: str) -> None:
    """Issue #265: EntityStatus (draft/active/deprecated/deleted) rendered via
    the ``entity`` pill-kind must NOT fall through to the neutral grey default.

    The neutral fallback is ('ink-2', '○'); every entity status must map to a
    distinct semantic colour."""
    html = _r("macros/status_pill.html", "status_pill", value=status, kind="entity")
    assert "text-ink-2" not in html, f"{status} fell through to neutral default"
    assert status in html.lower()


def test_action_menu_renders_csrf_for_form_items() -> None:
    """Form-submission items must include csrf_field() output. Render with a fake
    request that provides the csrf_token state attribute."""
    from types import SimpleNamespace

    src = (
        "{% from 'macros/action_menu.html' import action_menu %}"
        "{{ action_menu([{'label':'Delete','method':'post','action':'/x/delete'}]) }}"
    )
    fake_request = SimpleNamespace(state=SimpleNamespace(csrf_token="test-csrf"))
    html = templates.env.from_string(src).render(request=fake_request)
    assert 'name="_csrf"' in html
    assert "test-csrf" in html
