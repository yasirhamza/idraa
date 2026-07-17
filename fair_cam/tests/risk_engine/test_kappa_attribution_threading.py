"""Slice 2 (#439) — κ threaded through the attribution value function.

``subset_reduction_closed_form`` (the Shapley v(S)) now composes with a
``kappa`` argument (default :data:`KAPPA_META_RELIABILITY`), so a meta (VMC/DSC)
control that co-occurs with a Loss-Event control CREDITS its coupling into v(S).
The two standalone seams — ``build_control_adjustment`` (the displayed
per-control attribution cell) and ``entry_scores`` (catalog scoring) — instead
compose with ``kappa=0.0`` and are therefore self-coupling-free (Slice 2 D5): a
single control carrying BOTH a meta and an LEC channel does NOT uplift its own
reliability there.

Helper style mirrors ``test_meta_reliability_coupling.py`` (Task 2): ``_ctrl``
builds a ``Control`` directly so per-assignment (capability, coverage,
reliability) tuples can be set; ``_make_params`` reuses the
``make_fair_parameters`` FAIRParameters factory from ``_helpers`` (same fixture
style as ``test_effect_type_wiring.py``).
"""

from __future__ import annotations

import pytest

from fair_cam.controls.effectiveness import ControlEffectivenessCalculator
from fair_cam.models.control import (
    Control,
    ControlDomain,
    ControlType,
    FairCamControlFunctionAssignment,
)
from fair_cam.models.sub_function import FairCamSubFunction
from fair_cam.risk_engine.control_attribution import (
    build_control_adjustment,
    subset_reduction_closed_form,
)
from fair_cam.risk_engine.fair_core import FAIRParameters
from fair_cam.tests.risk_engine._helpers import make_fair_parameters


def _ctrl(cid: str, assigns: list[tuple[str, float | None, float, float]]) -> Control:
    """(sub_function_value, capability, coverage, reliability) -> Control.

    Copied from ``test_meta_reliability_coupling.py``: LOSS_EVENT is a valid
    placeholder ControlDomain (composition routes by sub-function via
    ``sub_function_to_group``, never by ControlDomain — the brief drafted
    ``ControlDomain.TECHNICAL``, but TECHNICAL is a ControlType).
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


@pytest.fixture
def calc() -> ControlEffectivenessCalculator:
    """The same ControlEffectivenessCalculator the build_control_adjustment tests use."""
    return ControlEffectivenessCalculator()


def test_subset_reduction_grows_with_meta_partner() -> None:
    """v({lec, meta}) > v({lec}) — the marginal contribution Shapley credits."""
    lec = _ctrl("mfa", [("lec_prev_resistance", 0.9, 0.8, 0.7)])
    meta = _ctrl("sat", [("dsc_prev_communication", None, 0.8, 0.8)])
    rp = _make_params()  # same FAIRParameters fixture style as test_effect_type_wiring.py
    v_lec = subset_reduction_closed_form(rp, [lec])
    v_both = subset_reduction_closed_form(rp, [lec, meta])
    assert v_both > v_lec > 0.0


def test_meta_alone_subset_reduction_is_zero() -> None:
    meta = _ctrl("sat", [("dsc_prev_communication", None, 0.8, 0.8)])
    rp = _make_params()
    assert subset_reduction_closed_form(rp, [meta]) == pytest.approx(0.0)


def test_kappa_zero_reproduces_uncoupled_reduction() -> None:
    lec = _ctrl("mfa", [("lec_prev_resistance", 0.9, 0.8, 0.7)])
    meta = _ctrl("sat", [("dsc_prev_communication", None, 0.8, 0.8)])
    rp = _make_params()
    assert subset_reduction_closed_form(rp, [lec, meta], kappa=0.0) == pytest.approx(
        subset_reduction_closed_form(rp, [lec])
    )


def test_hybrid_self_coupling_is_the_documented_mvp_behavior() -> None:
    """Spec D2 (plan-gate Meth-I4): a control carrying BOTH meta and LEC
    channels self-credits at default kappa in v(S) — scenario-level E_meta
    includes its own meta channels. Deliberately different from the kappa=0
    standalone seams (entry_scores / build_control_adjustment). Pinned so
    the simplification is visible, not accidental."""
    hybrid = _ctrl(
        "uac",
        [
            ("lec_prev_resistance", 0.9, 0.8, 0.7),
            ("dsc_prev_defined_expectations", None, 0.8, 0.8),
        ],
    )
    pure = _ctrl("uac2", [("lec_prev_resistance", 0.9, 0.8, 0.7)])
    rp = _make_params()
    assert subset_reduction_closed_form(rp, [hybrid]) > subset_reduction_closed_form(rp, [pure])


def test_453_weak_dsc_partner_never_lowers_subset_value() -> None:
    """#453 regression (exact prod shape, run ce3d0294): a STRONG-DSC control
    plus an LEC control, then adding a WEAK-DSC control — v(S) must NOT drop.

    Under the old mean-of-present, the weak DSC member pulled DSC_PREVENTION's
    mean down, cutting E_meta and hence v(S∪{weak}) below v(S) — a genuinely
    negative marginal that produced negative exact Shapley. The
    best-coherent-subset mean is the monotone envelope, so the weak partner's
    marginal is ≥ 0 (here exactly 0: it is fully dominated)."""
    lec = _ctrl("mfa", [("lec_prev_resistance", 0.9, 0.8, 0.7)])
    strong_dsc = _ctrl("thm", [("dsc_prev_sa_reporting", None, 0.8, 0.8)])  # 0.32
    weak_dsc = _ctrl("sat", [("dsc_prev_sa_analysis", None, 0.7, 0.7)])  # 0.245
    rp = _make_params()
    v_without_weak = subset_reduction_closed_form(rp, [lec, strong_dsc])
    v_with_weak = subset_reduction_closed_form(rp, [lec, strong_dsc, weak_dsc])
    assert v_with_weak >= v_without_weak  # monotone: NO negative marginal
    assert v_with_weak == pytest.approx(v_without_weak)  # weak member dominated


# F453-Meth-3: the trio-exact-Shapley regression (importing
# idraa.services.shapley over this fair_cam v(S)) relocated to
# tests/services/test_shapley_453_regression.py — fair_cam keeps only the pure
# v(S) monotonicity regression above; a v3-service import does not belong in a
# fair_cam test module (import-direction discipline).


def test_build_control_adjustment_never_self_credits(
    calc: ControlEffectivenessCalculator,
) -> None:
    """A control carrying BOTH a meta and an LEC channel gets the SAME
    standalone adjustment as the LEC channel alone (kappa=0.0 seam)."""
    hybrid = _ctrl(
        "uac",
        [
            ("lec_prev_resistance", 0.9, 0.8, 0.7),
            ("dsc_prev_defined_expectations", None, 0.8, 0.8),
        ],
    )
    pure = _ctrl("uac2", [("lec_prev_resistance", 0.9, 0.8, 0.7)])
    adj_h = build_control_adjustment(hybrid, calc, 1.0, 0.5, 1e6, 5e5)
    adj_p = build_control_adjustment(pure, calc, 1.0, 0.5, 1e6, 5e5)
    # NB the brief drafted ``.tef_multiplier``; the real ControlAdjustment field
    # is ``threat_event_frequency_multiplier`` (risk_enhanced.py).
    assert adj_h.threat_event_frequency_multiplier == pytest.approx(
        adj_p.threat_event_frequency_multiplier
    )
    assert adj_h.vulnerability_multiplier == pytest.approx(adj_p.vulnerability_multiplier)
    # T3-Meth-4: extend the never-self-credits pin to the remaining node
    # multipliers + the currency subtractor accumulation, not just TEF/Vuln.
    assert adj_h.primary_loss_multiplier == pytest.approx(adj_p.primary_loss_multiplier)
    assert adj_h.secondary_loss_multiplier == pytest.approx(adj_p.secondary_loss_multiplier)
    assert adj_h.loss_reduction_per_event == pytest.approx(adj_p.loss_reduction_per_event)
