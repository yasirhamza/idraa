"""Regression guard: control-library card capability badges must grow to fit
wrapped text.

The FAIR-CAM reference-default capability labels are long (e.g.
"Dsc Prev Defined Expectations · ref. 70%") and wrap to two lines on a narrow
catalog card. A stock DaisyUI ``.badge`` is fixed-height, so the wrapped second
line overflows the pill and overlaps the badge below it (an unreadable
strikethrough-looking smear). The fix lets the pill grow to its content with
``h-auto`` (+ ``whitespace-normal`` / ``leading-tight``). This test pins those
classes on the capability badge so the fix can't be silently dropped — a pure
source assertion (no app boot needed).
"""

from __future__ import annotations

import re
from pathlib import Path

_CARD = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "idraa"
    / "templates"
    / "controls"
    / "library"
    / "_entry_card.html"
)


def test_capability_badge_grows_to_fit_wrapped_text() -> None:
    source = _CARD.read_text()
    # The capability pill is identified by its h-auto growth class (distinct
    # from the framework badge-secondary pills — NIST/CIS/ISO — which are
    # short and never wrap). Both pill families use badge-secondary now that
    # FA-7 retired badge-accent (brass stays logo-dot-only, never text-on-light).
    matches = re.findall(r'class="(badge[^"]*h-auto[^"]*)"', source)
    assert matches, "expected an h-auto capability pill in the card"
    for cls in matches:
        assert "badge-secondary" in cls, (
            "capability badge should use badge-secondary (badge-accent retired "
            f"per FA-7); got: {cls!r}"
        )
        assert "whitespace-normal" in cls, (
            f"capability badge must allow its text to wrap cleanly; got: {cls!r}"
        )


def test_already_adopted_badge_stays_one_line() -> None:
    """The "Already adopted" status badge sits in the card-title flex next to a
    potentially long title. It's a short label, so it must stay on ONE line
    (whitespace-nowrap) — otherwise it wraps and overflows the fixed-height
    DaisyUI badge, overlapping the title (same class of bug as the capability
    pills, opposite fix: nowrap for short labels, grow for long ones)."""
    source = _CARD.read_text()
    matches = re.findall(r'class="(badge[^"]*badge-success[^"]*)"', source)
    assert matches, "expected the badge-success 'Already adopted' pill in the card"
    for cls in matches:
        assert "whitespace-nowrap" in cls, (
            f"'Already adopted' badge must stay one line (whitespace-nowrap); got: {cls!r}"
        )
