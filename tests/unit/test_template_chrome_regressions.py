"""Static guards for two mobile-chrome regressions fixed in the mobile tranche.

1. Empty-state icons must be literal Unicode glyphs, never HTML entities. The
   ``empty_state`` macro renders ``{{ icon }}`` autoescaped, so an entity like
   ``"&#x26E8;"`` shows as the literal text ``&#x26E8;`` instead of the glyph
   (its own default ``'◇'`` is already a literal char). A new list page that
   copy-pastes an entity icon would silently reintroduce the bug.

2. The sticky ``page_header`` must reserve left space on small screens for the
   fixed hamburger (``☰``, ``fixed top-4 left-4`` in ``layouts/_sidebar.html``)
   so the breadcrumb/title don't render underneath it.
"""

from __future__ import annotations

import re
from pathlib import Path

_TEMPLATES = Path(__file__).resolve().parents[2] / "src" / "idraa" / "templates"


def test_no_html_entity_empty_state_icons() -> None:
    offenders: list[str] = []
    for html in _TEMPLATES.rglob("*.html"):
        for line_no, line in enumerate(html.read_text(encoding="utf-8").splitlines(), 1):
            # The empty-state icon is declared as `"icon": "<value>"`.
            m = re.search(r'"icon"\s*:\s*"([^"]*)"', line)
            if m and "&#" in m.group(1):
                offenders.append(f"{html.relative_to(_TEMPLATES)}:{line_no}: {m.group(1)!r}")
    assert not offenders, (
        "empty-state icons must be literal Unicode glyphs, not HTML entities "
        "(empty_state renders {{ icon }} autoescaped → entities show as text):\n  "
        + "\n  ".join(offenders)
    )


def test_page_header_reserves_space_for_mobile_hamburger() -> None:
    header = (_TEMPLATES / "macros" / "page_header.html").read_text(encoding="utf-8")
    # pl-16 (mobile) + md:px-6 (reset once the hamburger is md:hidden).
    assert "pl-16" in header and "md:px-6" in header, (
        "page_header must pad its left edge on <md so the breadcrumb/title clear "
        "the fixed hamburger; md:px-6 restores symmetric padding on desktop"
    )
