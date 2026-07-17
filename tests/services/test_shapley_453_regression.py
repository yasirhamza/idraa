"""#453 — exact Shapley over a {LEC, strong-DSC, weak-DSC} trio yields NO
negative per-control value.

F453-Meth-3 (methodology re-review): relocated from
``fair_cam/tests/risk_engine/test_kappa_attribution_threading.py``, which had
a function-local ``from idraa.services.shapley import shapley_values``
workaround — a v3-service import does not belong in a fair_cam test module
(import-direction discipline: fair_cam is a library consumed by v3, never the
reverse). fair_cam keeps only the pure ``v(S)`` monotonicity regression
(``test_453_weak_dsc_partner_never_lowers_subset_value``); this module is the
v3-service-level regression that exercises the same fix through the real
Shapley service the executor uses.

Preserved verbatim from the original test body minus the deferred import
(now a normal module-level import, matching the convention in
``tests/services/test_weight_robustness_ensemble.py``).
"""

from __future__ import annotations

import pytest
from fair_cam.models.control import (
    Control,
    ControlDomain,
    ControlType,
    FairCamControlFunctionAssignment,
)
from fair_cam.models.sub_function import FairCamSubFunction
from fair_cam.risk_engine.control_attribution import subset_reduction_closed_form
from fair_cam.risk_engine.fair_core import FAIRParameters
from fair_cam.tests.risk_engine._helpers import make_fair_parameters

from idraa.services.shapley import shapley_values


def _ctrl(cid: str, assigns: list[tuple[str, float | None, float, float]]) -> Control:
    """(sub_function_value, capability, coverage, reliability) -> Control.

    Copied from ``test_meta_reliability_coupling.py`` /
    ``test_kappa_attribution_threading.py``: LOSS_EVENT is a valid placeholder
    ControlDomain (composition routes by sub-function via
    ``sub_function_to_group``, never by ControlDomain).
    """
    return Control(
        control_id=cid,
        name=cid,
        domain=ControlDomain.LOSS_EVENT,
        control_type=ControlType.PREVENTIVE,
        assignments=[
            FairCamControlFunctionAssignment(
                sub_function=FairCamSubFunction(sf),
                capability_value=cap,
                coverage=cov,
                reliability=rel,
            )
            for sf, cap, cov, rel in assigns
        ],
    )


def _make_params() -> FAIRParameters:
    """Positive-ALE FAIRParameters (same factory as test_effect_type_wiring.py)."""
    return make_fair_parameters(tef=10.0, vuln=0.4, primary=1_000_000, secondary=500_000)


def test_453_trio_shapley_has_no_negative_values() -> None:
    """#453: exact Shapley over {LEC, strong-DSC, weak-DSC} yields NO negative
    per-control value. Pre-fix, the weak DSC control scored a negative Shapley
    (its below-average member lowered E_meta on the coalitions it joined).

    Uses the v3 Shapley service with fair_cam's closed-form v(S) as the value
    function — the same wiring the executor uses."""
    lec = _ctrl("mfa", [("lec_prev_resistance", 0.9, 0.8, 0.7)])
    strong_dsc = _ctrl("thm", [("dsc_prev_sa_reporting", None, 0.8, 0.8)])
    weak_dsc = _ctrl("sat", [("dsc_prev_sa_analysis", None, 0.7, 0.7)])
    by_id = {c.control_id: c for c in (lec, strong_dsc, weak_dsc)}
    rp = _make_params()

    def value_fn(s: frozenset[str]) -> float:
        return subset_reduction_closed_form(rp, [by_id[cid] for cid in s])

    phi = shapley_values(list(by_id), value_fn)
    assert all(v >= -1e-9 for v in phi.values()), phi
    # Efficiency still holds (Σφ == v(N)); the fix is monotonicity, not a clamp.
    assert sum(phi.values()) == pytest.approx(value_fn(frozenset(by_id)))
