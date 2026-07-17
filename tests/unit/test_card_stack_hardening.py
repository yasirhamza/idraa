"""Static guards for the mobile card-stack hardening (validate-and-harden pass).

The data_table mobile card-stack and its action_menu were future-readiness
scaffolding; this pass validated them on real data and fixed two rough edges:

1. The card `<dl>` used a fixed ``grid-cols-2`` (50/50), wasting half the width
   on short labels and forcing long values to wrap early. Now ``auto_1fr``.
2. The ``action_menu`` dropdown always opened downward (``mt-1``), clipping
   below the viewport on the last card / last table row. It now flips above the
   button (``bottom-full``) when there isn't room below (``flipUp``).
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
    assert "flipUp" in src, "action_menu must track a flipUp state"
    assert "innerHeight" in src and "getBoundingClientRect" in src, (
        "action_menu must measure the button's distance to the viewport bottom on open"
    )
    assert "bottom-full" in src and "top-full" in src, (
        "action_menu must anchor above (bottom-full) when flipping up, below (top-full) otherwise"
    )
