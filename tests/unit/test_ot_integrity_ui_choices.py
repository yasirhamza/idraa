"""ot_integrity must appear in every operator-facing threat-category surface.

The content-extension added the ``ot_integrity`` effect to the enum + DB CHECK,
but the operator-facing dropdowns enumerate threat categories from THREE
independent hardcoded lists (no single label-map source of truth). A new effect
value is invisible to users until added to all three. These tests pin that every
surface offers ``ot_integrity`` alongside the other two OT effects, so the 3 new
integrity entries are both filterable (library sidebar) and selectable (wizard +
simple-form), matching the existing OT label style ("OT availability").
"""

from __future__ import annotations

from pathlib import Path

from idraa.routes.scenario_form_helpers import THREAT_CATEGORY_CHOICES


def _tpl(*parts: str) -> str:
    root = Path(__file__).resolve().parents[2] / "src" / "idraa" / "templates"
    return root.joinpath(*parts).read_text(encoding="utf-8")


def test_form_helper_choices_include_ot_integrity():
    """The simple-form/route THREAT_CATEGORY_CHOICES single list includes the
    ot_integrity value with a human-readable OT-style label."""
    values = {v for v, _ in THREAT_CATEGORY_CHOICES}
    assert {"ot_safety_tampering", "ot_availability", "ot_integrity"} <= values
    label = dict(THREAT_CATEGORY_CHOICES)["ot_integrity"]
    assert "OT" in label and "integrity" in label.lower()


def test_wizard_step2_template_offers_ot_integrity():
    """scenarios/wizard/step_2_basic.html threat-category select wires ot_integrity."""
    text = _tpl("scenarios", "wizard", "step_2_basic.html")
    assert "ot_safety_tampering" in text and "ot_availability" in text
    assert '"ot_integrity"' in text
