"""Design-language Phase 3 acceptance tests (issue #59): graphite palette,
brand-contrast, DaisyUI bridge, sonar-arcs logomark, color-class retirement,
hamburger clearance."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.asyncio

APP_CSS_PATH = Path(__file__).resolve().parents[2] / "src" / "idraa" / "static" / "css" / "app.css"
TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "src" / "idraa" / "templates"


async def test_graphite_brand_tokens() -> None:
    """P3: graphite palette — light #37464F / dark #B8C6CC — with the brass
    logo accent and the brand-contrast foreground in BOTH theme scopes.

    NOTE: rpartition, not partition — app.css line 3's header COMMENT
    mentions the [data-theme="dark"] selector; the real block is the last
    occurrence (plan-gate Q-1)."""
    css = APP_CSS_PATH.read_text(encoding="utf-8")
    root, _, dark = css.rpartition('[data-theme="dark"]')
    assert re.search(r"--color-brand:\s+#37464F", root)
    assert re.search(r"--color-brand:\s+#B8C6CC", dark)
    assert re.search(r"--color-logo-accent:\s+#C89141", root)
    assert re.search(r"--color-logo-accent:\s+#C89141", dark)
    assert re.search(r"--color-brand-contrast:\s+#FFFFFF", root)
    assert re.search(r"--color-brand-contrast:\s+#0A0A0B", dark)
    assert "#0F4C81" not in css


async def test_brand_contrast_routing() -> None:
    """Arch-1: no white-on-brand hardcodes survive — btn-primary and the
    active chart tab route their foreground through --color-brand-contrast
    (dark brand #B8C6CC is a LIGHT fill; white text would be ~1.7:1)."""
    css = APP_CSS_PATH.read_text(encoding="utf-8")
    assert "text-brand-contrast" in css
    assert "--tw-ring-color: var(--color-brand)" in css
    # Split on the rule's opening brace, not the bare selector — the selector
    # text also appears in an explanatory COMMENT above the rule (Q-11).
    for rule in (".btn-primary", ".tabs-boxed .tab-active"):
        block = css.split(rule + " {", 1)[1].split("}", 1)[0]
        assert "var(--color-brand-contrast)" in block, rule
        assert "#fff" not in block, rule


async def test_daisyui_bridge_vars() -> None:
    """Arch-3: DaisyUI component internals (--b1/--b2/--b3/--bc/--p/--pc)
    are re-grounded to token-equivalent OKLCH triplets in both scopes so
    alerts/stats/modals/tabs match token surfaces."""
    css = APP_CSS_PATH.read_text(encoding="utf-8")
    root, _, dark = css.rpartition('[data-theme="dark"]')
    for var in ("--b1:", "--b2:", "--b3:", "--bc:", "--p:", "--pc:"):
        assert var in root, f"{var} missing in :root"
        assert var in dark, f"{var} missing in dark scope"
    assert "21.0331% 0.005860 285.885153" in root  # light --bc = ink-1 #18181B
    assert "21.0331% 0.005860 285.885153" in dark  # dark --b1 = surface-1 #18181B


async def test_sonar_arcs_mark_and_favicon(client) -> None:
    """P3: the logomark is the sonar-arcs mark — two bilateral arcs over the
    brass dot — in the login page SVG and in the favicon (which carries a
    dark-scheme media query so the mark survives dark browser chrome)."""
    r = await client.get("/login")
    assert r.status_code == 200
    assert "M9.5 19 A 9 9 0 0 1 22.5 19" in r.text
    assert "M5 14.5 A 15.5 15.5 0 0 1 27 14.5" in r.text
    assert "var(--color-logo-accent)" in r.text

    fav = (await client.get("/static/favicon.svg")).text
    assert "M9.5 19 A 9 9 0 0 1 22.5 19" in fav
    assert "#C89141" in fav
    assert "prefers-color-scheme: dark" in fav
    assert "#B8C6CC" in fav


_DAISY_COLOR_CLASS_RE = (
    r"(?:bg|text|border|ring|from|to|divide)-"
    r"(?:base-|primary\b|secondary\b|accent\b|error\b|success\b|warning\b|info\b)"
)


async def test_no_daisyui_color_classes() -> None:
    """P3: DaisyUI color utilities (base-* AND the semantic families) are
    retired from templates — fills/text/borders route through the app.css
    tokens. Component classes (btn, alert, badge...) are exempt: their
    internals are re-grounded by the Task-1 bridge. Guard against
    re-introduction (Arch-2)."""
    import re

    offenders: list[str] = []
    for path in sorted(TEMPLATES_DIR.rglob("*.html")):
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(_DAISY_COLOR_CLASS_RE, line):
                offenders.append(f"{path.relative_to(TEMPLATES_DIR)}:{i}")
    assert not offenders, f"DaisyUI color classes found: {offenders[:20]}"


async def test_no_legacy_brand_hex() -> None:
    """Arch-7: the retired brand navy may not reappear anywhere first-party
    (templates, CSS, services, JS) — vendored assets excluded."""
    src = Path(__file__).resolve().parents[2] / "src" / "idraa"
    offenders: list[str] = []
    for path in sorted(src.rglob("*")):
        if not path.is_file() or "static/vendor" in str(path).replace("\\", "/"):
            continue
        if path.suffix not in {".py", ".html", ".css", ".js", ".svg"}:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "0F4C81" in text.upper().replace("#", ""):
            offenders.append(str(path.relative_to(src)))
    assert not offenders, f"legacy brand hex #0F4C81/FF0F4C81 found in: {offenders}"


async def test_rebuilt_sheet_has_hover_surface() -> None:
    """Arch-4/Q-10r: the JIT build must generate the swap destinations —
    hover:bg-surface-2 (5 hover:bg-base-200 swaps, incl. 2 Alpine :class
    strings) and the checked-library-card color-mix tint (which must be a
    LIVE rule, unlike the dead bg-primary/5 it replaces)."""
    sheet_text = (APP_CSS_PATH.parent / "tailwind.css").read_text(encoding="utf-8")
    assert "hover\\:bg-surface-2" in sheet_text
    # tailwind.css is JIT-built from _tailwind_entry.css (app.css serves
    # separately), so today it contains NO color-mix — this is a strict
    # positive guard that the tint utility generated.
    assert "color-mix" in sheet_text
