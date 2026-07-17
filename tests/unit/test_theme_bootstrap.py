"""F1+F2: theme tokens, dark mode, OS-match bootstrap.

F1 covers:
- Build-time `tailwind.config.js` with token theme + `darkMode: ["selector", '[data-theme="dark"]']`.
  (Task 4: the Play CDN + inline `<script>window.tailwind.config = {...}</script>` in base.html
  were removed; config now lives in a static, purge-built `tailwind.config.js` file.)
- Brand colour wired through `extend.colors.brand` referencing `var(--color-brand)`.
- borderRadius differentiated (input 4 px, card 6 px, table 0) per spec §1.
- spacing constrained (under `theme:`, NOT `extend:`) to {0,1,2,3,4,6,8,12,16,24,px}.
- CSS token vars (`--color-surface-1`, `--color-ink-1`, `--color-brand`, `--color-status-critical`) in app.css.
- No `window.daisyuiConfig` runtime push (DaisyUI CDN ignores it per plan-gate Arch-1).
"""

from __future__ import annotations

import re
from types import SimpleNamespace

from idraa.app import templates


def _render_base() -> str:
    """Render base.html as a placeholder block — produces the surrounding chrome we test."""
    tmpl = templates.env.from_string(
        '{% extends "base.html" %}{% block content %}placeholder{% endblock %}'
    )
    return tmpl.render(
        request=SimpleNamespace(
            state=SimpleNamespace(csrf_token="t", maintenance_badge_count=0),
            url=SimpleNamespace(path="/"),
        ),
        current_user=None,
        static_version="v1",
    )


def _read_tailwind_config() -> str:
    """Task 4: config moved out of base.html's inline `<script>` into a build-time
    `tailwind.config.js` file (Play CDN removed; CSS is now a static purged build)."""
    with open("tailwind.config.js") as fh:
        return fh.read()


def test_tailwind_config_has_dark_selector() -> None:
    config = _read_tailwind_config()
    assert "darkMode" in config
    # darkMode must use the data-theme selector form
    assert re.search(
        r"darkMode\s*:\s*\[\s*['\"]selector['\"]\s*,\s*['\"]\[data-theme=['\"]?dark['\"]?\]['\"]\s*\]",
        config,
    )


def test_tailwind_config_has_brand_color() -> None:
    """Plan-gate Arch-1: brand colour drives off Tailwind extend.colors.brand.
    DaisyUI custom themes are NOT used."""
    config = _read_tailwind_config()
    assert "extend" in config
    assert "brand" in config
    assert "var(--color-brand)" in config


def test_base_does_not_ship_daisyui_runtime_config() -> None:
    """Plan-gate Arch-1: DaisyUI 4 on Play CDN ignores window.daisyuiConfig.
    The plan deliberately does not ship it to avoid creating the illusion of an effect."""
    html = _render_base()
    assert "daisyuiConfig" not in html, (
        "DaisyUI runtime config has no effect on the CDN — do not ship it"
    )


def test_tailwind_config_has_radii_constraints() -> None:
    """Plan-gate SC-4: radii differentiated by surface (input 4px / card 6px / table 0)."""
    config = _read_tailwind_config()
    assert 'input: "4px"' in config
    assert 'card: "6px"' in config
    assert 'table: "0"' in config


def test_base_does_not_override_tailwind_spacing_scale() -> None:
    """Regression guard (2026-05-23): a previous version of F1 set
    `theme.spacing = {…}` at the top level — replacing Tailwind's default
    spacing scale. That nuked every `h-{9,10,11,…}`, `w-{16,60,…}`,
    `max-h-64`, `gap-y-3` etc. used in macros and pages, leaving tables
    collapsed to content height and click targets misaligned. SC-9's drift-
    prevention intent is reinstated as a future lint check rather than a
    runtime override; never override Tailwind's spacing map again.
    """
    html = _render_base()
    # The full top-level override block must NOT reappear.
    # Permit `extend.spacing` (additive, harmless) — only forbid the top-level form.
    # Anchor: the original buggy literal "'12': '3rem'" only appeared under the
    # top-level `spacing:` block; if it shows up again the override is back.
    forbidden_combos = (
        ("'1':  '0.25rem'", "'6':  '1.5rem'"),
        ('"1": "0.25rem"', '"6": "1.5rem"'),
    )
    for combo in forbidden_combos:
        present = all(literal in html for literal in combo)
        assert not present, (
            "Tailwind spacing override regressed — see "
            "test_base_does_not_override_tailwind_spacing_scale docstring."
        )


def test_app_css_declares_token_vars() -> None:
    with open("src/idraa/static/css/app.css") as fh:
        css = fh.read()
    for var in ("--color-surface-1", "--color-ink-1", "--color-brand", "--color-status-critical"):
        assert var in css, f"Missing token var {var} in app.css"


def test_app_css_declares_dark_theme_overrides() -> None:
    """Plan-gate Arch-1: dark palette via CSS vars (effective), not via DaisyUI runtime
    config (ineffective on CDN)."""
    with open("src/idraa/static/css/app.css") as fh:
        css = fh.read()
    assert '[data-theme="dark"]' in css
    assert "#0A0A0B" in css, "Dark surface-0 must be in CSS"
    assert "#FAFAFA" in css, "Dark ink-1 (near-white) must be in CSS"
    assert "#3B82F6" in css, "Dark brand color must be in CSS"


def test_base_includes_pre_paint_theme_bootstrap() -> None:
    """The bootstrap script runs BEFORE Tailwind/DaisyUI load so the data-theme attr
    is set before first paint — no FOUC."""
    html = _render_base()
    assert (
        "localStorage.getItem('idraa.theme')" in html
        or 'localStorage.getItem("idraa.theme")' in html
    )
    assert "prefers-color-scheme" in html
    assert (
        "documentElement.setAttribute('data-theme'" in html
        or 'documentElement.setAttribute("data-theme"' in html
    )


def test_base_pre_paint_resolves_sidebar_collapse_too() -> None:
    """Plan-gate Arch-14: sidebar collapse state also resolved pre-paint to avoid
    240→64 px layout snap when Alpine init() fires after DOMContentLoaded."""
    html = _render_base()
    assert (
        "localStorage.getItem('idraa.sidebar')" in html
        or 'localStorage.getItem("idraa.sidebar")' in html
    )
    assert "data-sidebar-collapsed" in html


def test_base_includes_color_scheme_meta() -> None:
    """<meta name='color-scheme' content='light dark'> lets browser chrome match."""
    html = _render_base()
    assert 'name="color-scheme"' in html
    assert "light dark" in html


def test_theme_toggle_partial_exists_with_tri_state() -> None:
    """Plan-gate spec §1: tri-state preference (light/dark/auto)."""
    tmpl = templates.env.get_template("layouts/_theme_toggle.html")
    rendered = tmpl.render()
    for v in ("light", "dark", "auto"):
        assert f'data-theme-set="{v}"' in rendered, f"Toggle must offer {v}"
