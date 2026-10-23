"""Library entry calibration — pure functions, no DB I/O.

Issue #103 (PR gamma-2) introduced org revenue-tier ratio-scaling of a library
entry's PL/SL at pre-fill time. That scaling was **removed (2026-07-07)**:
after the Epic D envelope x share recalibration, each entry's loss is anchored to
the IRIS Figure A3 **sector envelope** — a sector aggregate with no single
revenue tier — so scaling it by a per-entry ``calibration_anchor.revenue_tier``
(which no longer matched the loss basis; 64/93 entries were off by >3x) produced
incoherent magnitudes. The sector envelope IS the calibration; org size
influences risk via controls at Monte-Carlo time, not a loss pre-multiplier.

``library_calibrated_pre_fill`` now passes TEF/vuln/PL/SL through unchanged
(override fall-through preserved) and returns ``None`` calibration metadata, so
no "calibrated for your org" banner is shown.

The ``calibration_anchor`` industry/revenue_tier provenance shape is still
stored on every entry and is validated at load time by
``LibraryEntrySeed._validate_calibration_anchor`` in
``services/seed_library_loader.py``. ``revenue_tier`` is now vestigial for
calibration — kept as provenance metadata; removing it from the schema is a
separate data migration. (The never-instantiated ``CalibrationAnchor`` Pydantic
model that once shadowed that validator was removed in #242.)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from idraa.models.scenario_library import (
        ScenarioLibraryEntry,
        ScenarioLibraryOverride,
    )


def library_calibrated_pre_fill(
    entry: ScenarioLibraryEntry,
    override: ScenarioLibraryOverride | None,
) -> tuple[dict[str, dict[str, Any] | None], None]:
    """Return (form_dict, None) — entry-absolute pre-fill, no org scaling.

    form_dict keys: 'tef', 'vuln', 'pl', 'sl' — each a distribution dict (or None
    for sl). Each field is the override value when the override sets it, else the
    entry's own value. **No revenue-tier scaling is applied** (removed
    2026-07-07 — the sector envelope IS the calibration; see module docstring).
    The second tuple element is always ``None`` (no calibration banner).
    """
    if override is not None:
        override_tef = override.threat_event_frequency
        override_vuln = override.vulnerability
        override_pl = override.primary_loss
        override_sl = override.secondary_loss
    else:
        override_tef = None
        override_vuln = None
        override_pl = None
        override_sl = None

    return (
        {
            "tef": override_tef if override_tef is not None else entry.threat_event_frequency,
            "vuln": override_vuln if override_vuln is not None else entry.vulnerability,
            "pl": override_pl if override_pl is not None else entry.primary_loss,
            "sl": override_sl if override_sl is not None else entry.secondary_loss,
        },
        None,
    )
