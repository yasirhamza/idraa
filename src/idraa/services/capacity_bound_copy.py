"""Shared PR2 D18/D19 operator-facing copy (owner-signed 2026-07-25).

Single source for the pinned remedy strings so every catastrophic-loss
authoring surface renders IDENTICAL operator-facing copy instead of
hand-typed near-duplicates that drift out of sync — the wizard (Task 4a,
``routes/scenarios.py``) defined these first; the scenario importer (Task
4b, ``services/scenario_import.py`` + ``routes/scenario_import.py``)
extracted them here to reuse verbatim rather than re-type them. See
``docs/superpowers/specs/2026-07-25-capacity-bound-design.md`` §"D18/D19
operator-facing copy" for the pinned text — do not reword either string.
"""

from __future__ import annotations

from idraa.services.fair_cam_validation import FAIRCAMValidationError

# D18 (owner-signed 2026-07-25): annual_revenue is a PRECONDITION for the
# catastrophic authoring path on the wizard and (Task 4b) CSV/JSON import;
# neither surface gains a `max` input of its own (that's the expert form's
# D17 job, Task 4c). Two remedies: set annual revenue, or use the expert
# form with an explicit cap.
D18_REVENUE_MESSAGE = (
    "Modeling catastrophic loss needs your organization's annual revenue: it "
    "sets the per-scenario loss cap. Set it in Organization settings, or "
    "build this scenario in the expert form with an explicit cap."
)

# D19: the Task-3b validator (services/fair_cam_validation.py
# ``_validate_capacity_floor``) raises the FACTUAL "max > p95" floor-conflict
# string inside a FAIRCAMValidationError -- this marker (a verbatim substring
# of that function's own error text) distinguishes it from every OTHER
# FAIRCAMValidationError (non-finite params, PERT ordering, etc.), which get
# generic error-message treatment instead.
D19_FLOOR_MARKER = "capacity floor"


def wrap_d19_floor_message(exc: FAIRCAMValidationError) -> str:
    """D19 operator-facing copy: wrap the validator's FACTUAL p95-vs-cap
    string with the three remedies the design pins (the validator produces
    the fact; producer surfaces -- the wizard and the importer -- add the
    remedies; see ``_validate_capacity_floor``'s docstring). Amounts in
    ``exc`` are the raw stored numbers, which every producer surface treats
    as USD (the wizard authors in USD only per P2; the importer stores raw
    distribution numbers as-is regardless of ``entry_currency``, which is
    pure provenance metadata -- see ``services/scenario_import.py``'s
    module docstring, Invariant 3).
    """
    return (
        f"{exc} (amounts in USD). To resolve this: lower the loss estimates so "
        "the scenario's 95th percentile sits below the cap; correct your "
        "organization's annual revenue if it's understated; or build this "
        "scenario in the expert form with an explicit max cap."
    )
