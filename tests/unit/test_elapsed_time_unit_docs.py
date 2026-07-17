"""Issue #204: the ELAPSED_TIME capability_value time-unit must be documented.

The CSV importer + form did not state the TIME UNIT for an ELAPSED_TIME
capability_value, so operators assumed hours while the form widget + engine
interpret the value as DAYS (576x off for a "24-hour SLA" entered as 24).

Canonical unit = DAYS, established by reading the consumers:
  - fair_cam.calibration.elapsed_time_taus.TAU_BY_SUB_FUNCTION — τ values
    are in days (IBM CODB MTTI mean 194d, MTTC 64d; DBIR KEV median 55d).
  - fair_cam.composition.compute_assignment_opeff_two_branch — ELAPSED_TIME
    branch computes exp(-t/τ); t (capability_value) must share τ's unit (days).
  - src/idraa/templates/macros/unit_aware_inputs.html — the form widget
    renders a "days" badge + "(days)" label suffix.

These doc-string + template-blurb assertions are the regression guard so a
future edit cannot silently drop the unit again. Doc-only — no math touch.
"""

from __future__ import annotations

from pathlib import Path

from idraa.services import controls_importer

_TEMPLATES_ROOT = Path("src/idraa/templates")


def test_importer_docstring_names_elapsed_time_unit_as_days() -> None:
    """controls_importer module docstring must name the ELAPSED_TIME unit (days)."""
    doc = controls_importer.__doc__ or ""
    assert "ELAPSED_TIME" in doc, "docstring should mention ELAPSED_TIME"
    # The bug: the docstring described ELAPSED_TIME bounds but never the unit.
    assert "days" in doc.lower(), (
        "controls_importer docstring must state ELAPSED_TIME capability_value "
        "is interpreted in DAYS (issue #204) — operators otherwise assume hours"
    )


def test_import_html_blurb_names_elapsed_time_unit_as_days() -> None:
    """import.html upload blurb must state ELAPSED_TIME is interpreted as days."""
    html = (_TEMPLATES_ROOT / "controls" / "import.html").read_text(encoding="utf-8")
    assert "ELAPSED_TIME" in html
    assert "days" in html.lower(), (
        "import.html upload blurb must state ELAPSED_TIME values are interpreted "
        "in DAYS (issue #204), matching the form widget + engine τ unit"
    )


def test_unit_aware_inputs_macro_uses_days_chip() -> None:
    """The form widget's days chip is the UI source of truth the docs must match."""
    macro = (_TEMPLATES_ROOT / "macros" / "unit_aware_inputs.html").read_text(encoding="utf-8")
    # elapsed_time branch renders a "days" badge — this is what operators see.
    assert "elapsed_time" in macro
    assert "days" in macro, "unit_aware_inputs ELAPSED_TIME widget must show a days unit"
