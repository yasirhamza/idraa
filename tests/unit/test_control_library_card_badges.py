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
    # The capability pill is the badge-accent span (distinct from the pink
    # framework badge-secondary pills, which are short and never wrap).
    matches = re.findall(r'class="(badge[^"]*badge-accent[^"]*)"', source)
    assert matches, "expected a badge-accent capability pill in the card"
    for cls in matches:
        assert "h-auto" in cls, (
            "capability badge must use h-auto so long labels that wrap don't "
            f"overflow a fixed-height pill and overlap the next one; got: {cls!r}"
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
