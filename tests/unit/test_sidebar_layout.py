"""F3: sidebar replaces top-bar nav. Section groups, badges, theme toggle slot,
collapse state per plan-gate SC-1."""

from __future__ import annotations

from html.parser import HTMLParser
from types import SimpleNamespace

from idraa.app import templates
from idraa.models.enums import UserRole


class _NestedAnchorDetector(HTMLParser):
    """Flags any <a> opened while another <a> is still open. The stdlib parser
    reports tags as-written (unlike a browser, which silently hoists the inner
    anchor out), so this catches the nested-<a> source bug directly."""

    def __init__(self) -> None:
        super().__init__()
        self._depth = 0
        self.nested = False

    def handle_starttag(self, tag: str, attrs: object) -> None:
        if tag == "a":
            if self._depth > 0:
                self.nested = True
            self._depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._depth > 0:
            self._depth -= 1


def _fake_request(badge_count: int = 0) -> SimpleNamespace:
    return SimpleNamespace(
        state=SimpleNamespace(csrf_token="t", maintenance_badge_count=badge_count),
        url=SimpleNamespace(path="/"),
    )


def _admin() -> SimpleNamespace:
    return SimpleNamespace(email="admin@example.com", role=UserRole.ADMIN)


def _analyst() -> SimpleNamespace:
    return SimpleNamespace(email="a@example.com", role=UserRole.ANALYST)


def test_sidebar_renders_three_groups() -> None:
    tmpl = templates.env.get_template("layouts/_sidebar.html")
    html = tmpl.render(request=_fake_request(), current_user=_admin(), static_version="v1")
    for group in ("WORK", "CONFIGURE", "ADMIN"):
        assert group in html, f"Sidebar must label the {group} group"


def test_sidebar_hides_admin_group_from_non_admin() -> None:
    tmpl = templates.env.get_template("layouts/_sidebar.html")
    html = tmpl.render(request=_fake_request(), current_user=_analyst(), static_version="v1")
    assert "/users" not in html
    assert "ADMIN" not in html


def test_sidebar_shows_maintenance_badge_when_count_positive() -> None:
    tmpl = templates.env.get_template("layouts/_sidebar.html")
    html = tmpl.render(
        request=_fake_request(badge_count=3), current_user=_admin(), static_version="v1"
    )
    assert ">3<" in html, "Maintenance badge value must render"


def test_sidebar_maintenance_badge_not_nested_in_controls_link() -> None:
    """Issue #269: the maintenance badge was an <a href="/controls/maintenance">
    nested inside the <a href="/controls"> row. HTML5 forbids <a> inside <a> —
    the browser hoists the inner anchor out, dropping the badge onto its own
    line (rendered as a label-less '45' row). The badge must be a SIBLING
    anchor, not nested."""
    tmpl = templates.env.get_template("layouts/_sidebar.html")
    html = tmpl.render(
        request=_fake_request(badge_count=7), current_user=_admin(), static_version="v1"
    )
    detector = _NestedAnchorDetector()
    detector.feed(html)
    assert not detector.nested, "Sidebar must not nest <a> inside <a> (issue #269)"
    # The badge still renders and still links to maintenance.
    assert "/controls/maintenance" in html
    assert ">7<" in html


def test_sidebar_includes_theme_toggle_partial() -> None:
    tmpl = templates.env.get_template("layouts/_sidebar.html")
    html = tmpl.render(request=_fake_request(), current_user=_admin(), static_version="v1")
    assert 'data-theme-set="light"' in html
    assert 'data-theme-set="dark"' in html
    assert 'data-theme-set="auto"' in html


def test_sidebar_has_collapse_state_and_toggle() -> None:
    """Plan-gate SC-1: sidebar exposes 240 px expanded vs 64 px icon-only states,
    toggle button, and broadcasts collapse state via data-sidebar-collapsed on <html>."""
    tmpl = templates.env.get_template("layouts/_sidebar.html")
    html = tmpl.render(request=_fake_request(), current_user=_admin(), static_version="v1")
    # Reads pre-paint-resolved attribute (set by base.html bootstrap from F2)
    assert "data-sidebar-collapsed" in html
    assert "w-16" in html and "w-60" in html
    # Toggle button (clickable target) — accept any of these markers
    assert "toggle()" in html or "toggle ()" in html or "@click" in html


def test_sidebar_admin_sees_fx_rates_link() -> None:
    """The rate-admin screen (/fx-rates) needs a nav entry — without it admins
    can't discover it (and the currency picker shows only USD until rates exist)."""
    tmpl = templates.env.get_template("layouts/_sidebar.html")
    html = tmpl.render(request=_fake_request(), current_user=_admin(), static_version="v1")
    assert "/fx-rates" in html
    assert "FX rates" in html


def test_sidebar_hides_fx_rates_from_non_admin() -> None:
    tmpl = templates.env.get_template("layouts/_sidebar.html")
    html = tmpl.render(request=_fake_request(), current_user=_analyst(), static_version="v1")
    assert "/fx-rates" not in html
