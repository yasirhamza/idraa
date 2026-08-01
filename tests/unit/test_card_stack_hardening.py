"""Static guards for the mobile card-stack hardening (validate-and-harden pass).

The data_table mobile card-stack and its action_menu were future-readiness
scaffolding; this pass validated them on real data and fixed two rough edges:

1. The card `<dl>` used a fixed ``grid-cols-2`` (50/50), wasting half the width
   on short labels and forcing long values to wrap early. Now ``auto_1fr``.
2. The ``action_menu`` dropdown positions FIXED from its trigger's rect
   (owner UAT 2026-08-01: absolute-in-relative was clipped by data_table's
   overflow-x-auto wrapper), flipping above the button when there isn't
   room below and re-anchoring on capture-phase window scroll/resize.
"""

from __future__ import annotations

from pathlib import Path

_MACROS = Path(__file__).resolve().parents[2] / "src" / "idraa" / "templates" / "macros"


def test_card_stack_dl_uses_auto_value_columns() -> None:
    src = (_MACROS / "data_table.html").read_text(encoding="utf-8")
    assert "grid-cols-[auto_1fr]" in src, (
        "card-stack <dl> should size labels to content (auto) and give values "
        "the rest (1fr), not a 50/50 grid-cols-2"
    )


def test_action_menu_flips_up_near_viewport_bottom() -> None:
    src = (_MACROS / "action_menu.html").read_text(encoding="utf-8")
    # flipUp state, the boundary measurement on open, and the conditional anchor.
    assert "flipUp" in src, "action_menu must compute a flip-up decision on open"
    # documentElement.clientHeight, not window.innerHeight (review I5: fixed
    # positioning anchors to the ICB, which excludes classic scrollbars).
    assert "clientHeight" in src and "getBoundingClientRect" in src, (
        "action_menu must measure the button's distance to the viewport bottom on open"
    )
    # Owner UAT 2026-08-01: the menu now anchors via an inline FIXED style
    # computed from the trigger rect — escaping BOTH viewport clipping (the
    # original Arch-13 fix) and overflow-container clipping (data_table's
    # overflow-x-auto wrapper rendered the menu as an empty sliver). flipUp
    # switches the fixed anchor between top: and bottom:.
    assert "position: fixed" in src, "menu must use fixed positioning (escapes overflow clipping)"
    assert "'bottom:'" in src and "'top:'" in src, (
        "action_menu must anchor above (bottom:) when flipping up, below (top:) otherwise"
    )
    # Review I1/I2 pins: scroll does not bubble — the window listener must be
    # capture-phase to see nested scroll containers (else the fixed menu
    # drifts off its trigger when the table wrapper scrolls) — and passive
    # (O(rows) listeners on the window; place() never preventDefaults).
    assert "@scroll.window.capture.passive" in src
    assert "@resize.window.passive" in src
    # The clipped anchoring must not creep back. NOTE (review B2): Tailwind's
    # scanner is a raw-bytes extractor that reads Jinja COMMENTS too — any
    # mention of a class name here or in a template comment keeps the utility
    # alive in tailwind.css, and removing the last mention requires a
    # build-css rebuild+commit or the gate fails stale.
    assert "top-full" not in src and "bottom-full" not in src, (
        "class-based absolute anchoring reintroduces overflow-container clipping"
    )


def test_action_menu_empty_items_renders_nothing() -> None:
    """Owner UAT 2026-08-01 (iPhone): canonical read-only rows pass
    _actions=[] — the macro must emit NO trigger and NO menu, not a ⋯ that
    opens an empty white pill."""
    from idraa.app import templates

    src = "{% from 'macros/action_menu.html' import action_menu %}{{ action_menu(items) }}"
    empty = templates.env.from_string(src).render(items=[])
    assert empty.strip() == "", f"empty items must render nothing, got: {empty[:120]!r}"
    populated = templates.env.from_string(src).render(items=[{"label": "Edit", "href": "/x"}])
    assert "aria-haspopup" in populated and "Edit" in populated
