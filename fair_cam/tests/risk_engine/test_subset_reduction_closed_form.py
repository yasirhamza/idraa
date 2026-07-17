"""v(S) closed-form evaluator: reconcile with the engine's single-control math,
verify sub-additivity (OR-composition), currency handling, and v([]) == 0."""

import pytest

from fair_cam.controls.effectiveness import ControlEffectivenessCalculator
from fair_cam.risk_engine.control_attribution import (
    build_control_adjustment,
    representative_value,
    subset_reduction_closed_form,
)
from fair_cam.risk_engine.fair_core import DistributionType, FAIRDistribution, FAIRParameters
from fair_cam.tests.risk_engine._helpers import make_control, make_fair_parameters


def _fp():
    return make_fair_parameters(tef=2.0, vuln=0.5, primary=1_000_000, secondary=400_000)


def test_empty_subset_zero():
    assert subset_reduction_closed_form(_fp(), []) == pytest.approx(0.0)


def test_single_control_matches_build_control_adjustment():
    """v({c}) for a non-currency control == the standalone cell's risk_reduction_value,
    on the SAME representative_value basis (spec §4 basis continuity)."""
    fp = _fp()
    c = make_control(control_id="c1", assignments=[("lec_prev_resistance", "probability", 0.6)])
    b_tef = representative_value(fp.threat_event_frequency)
    b_vuln = representative_value(fp.vulnerability)
    b_pl = representative_value(fp.primary_loss)
    b_sl = representative_value(fp.secondary_loss)
    adj = build_control_adjustment(c, ControlEffectivenessCalculator(), b_tef, b_vuln, b_pl, b_sl)
    assert subset_reduction_closed_form(fp, [c]) == pytest.approx(adj.risk_reduction_value)


def test_split_path_equals_wrapper():
    """#419 perf refactor: the explicit split the weight-robustness ensemble uses
    (scenario_base_ale once + compose_groups cached + reduction_from_composition per
    draw) is EXACTLY equal to subset_reduction_closed_form (which now delegates to
    it). Exact ==, not approx — identical arithmetic, reorganised so the
    weight-invariant compose_groups can be cached across draws."""
    from fair_cam.risk_engine.control_attribution import (
        reduction_from_composition,
        scenario_base_ale,
    )
    from fair_cam.risk_engine.group_composition import compose_groups

    fp = _fp()
    a = make_control(control_id="a", assignments=[("lec_prev_resistance", "probability", 0.5)])
    b = make_control(control_id="b", assignments=[("lec_prev_resistance", "probability", 0.4)])
    for ctrls in ([], [a], [b], [a, b]):
        direct = subset_reduction_closed_form(fp, ctrls, None)
        split = reduction_from_composition(scenario_base_ale(fp), compose_groups(ctrls), None)
        assert split == direct  # exact equality — same floats, reorganised


def test_two_controls_same_group_are_subadditive():
    """OR-composition: v({a,b}) < v({a}) + v({b}) when both hit the same node."""
    fp = _fp()
    a = make_control(control_id="a", assignments=[("lec_prev_resistance", "probability", 0.5)])
    b = make_control(control_id="b", assignments=[("lec_prev_resistance", "probability", 0.5)])
    va = subset_reduction_closed_form(fp, [a])
    vb = subset_reduction_closed_form(fp, [b])
    vab = subset_reduction_closed_form(fp, [a, b])
    assert vab < va + vb  # overlap removed
    assert vab > max(va, vb)  # but adding b still helps


def test_currency_control_subtracts_annualized_loss():
    """A CURRENCY (insurance) control reduces ALE by point_LEF * subtractor (no clamp).

    Note (B-Meth-I1): this is the POINT-LEF basis (representative TEF x Vuln),
    DELIBERATELY different from the old cell's MC-mean-LEF annualization —
    required so all cells share one point basis and Shapley credits sum coherently.
    """
    fp = _fp()
    c = make_control(
        control_id="ins", assignments=[("lec_resp_loss_reduction", "currency", 50_000.0)]
    )
    point_lef = representative_value(fp.threat_event_frequency) * representative_value(
        fp.vulnerability
    )
    assert subset_reduction_closed_form(fp, [c]) == pytest.approx(point_lef * 50_000.0)


def test_currency_floor_binds_clamps_to_secondary_cap():
    """When the subtractor exceeds secondary loss, v(S) clamps (max(0,.)) — the
    nonlinearity that gates Owen and that Shapley must stay efficient over (B-Spec-I1)."""
    fp = make_fair_parameters(tef=2.0, vuln=0.5, primary=1_000_000, secondary=100_000)
    c = make_control(
        control_id="big_ins", assignments=[("lec_resp_loss_reduction", "currency", 250_000.0)]
    )
    b_tef = representative_value(fp.threat_event_frequency)
    b_vuln = representative_value(fp.vulnerability)
    b_pl = representative_value(fp.primary_loss)
    b_sl = representative_value(fp.secondary_loss)
    original = b_tef * b_vuln * (b_pl + b_sl)
    expected = original - b_tef * b_vuln * (b_pl + 0.0)  # secondary floors at 0
    assert subset_reduction_closed_form(fp, [c]) == pytest.approx(expected)


def test_basis_is_representative_value_not_distribution_mean():
    """Wrong-statistic tripwire (review finding I1, IBM-MTTI mean/median precedent).

    On an ASYMMETRIC lognormal secondary-loss distribution, v(S) must be computed
    from ``representative_value`` (the MEDIAN = exp(mu)) not from the distribution
    mean (= exp(mu + sigma^2/2)).

    With mu=ln(400_000) and sigma=1.0:
      - median (representative_value) = exp(mu) = 400_000
      - mean = exp(mu + 0.5) ≈ 659_489

    All 5 existing tests use symmetric PERTs (mean==mode) or point-mass UNIFORM vuln
    so a regression swapping ``representative_value`` for ``.mean()`` / ``exp(mu+sigma^2/2)``
    would keep them green.  This test fails LOUDLY by a factor of ~exp(0.5)≈1.65 if the
    basis statistic drifts to the lognormal mean.

    Expected value derivation (hand math — NOT derived by calling the code under test):
      LEC_PREVENTION weights (GROUP_NODE_MAPPING):
        threat_event_frequency weight = 0.8, vulnerability weight = 0.9
      With E=0.6:
        tef_mult  = 1 - 0.6 * 0.8 = 0.52
        vuln_mult = 1 - 0.6 * 0.9 = 0.46
      Base scalars (from representative_value):
        b_tef  = 2.0   (symmetric PERT mode)
        b_vuln = 0.5   (point-mass UNIFORM midpoint)
        b_pl   = 1_000_000  (symmetric PERT mode)
        b_sl   = 400_000    (LOGNORMAL median = exp(mu), NOT exp(mu+sigma^2/2)≈659_489)
      original_ale = 2.0 * 0.5 * (1_000_000 + 400_000) = 1_400_000
      adjusted_ale = 2.0 * 0.52 * 0.5 * 0.46 * (1_000_000 + 400_000) = 334_880
      v({r})       = 1_400_000 - 334_880 = 1_065_120

    If the implementation used the lognormal MEAN (~659_489) instead of the median
    (400_000) for b_sl, original_ale would be ~2_059_489, adjusted_ale ~494_677, and
    v({r}) ~1_262_539 — a delta of ~197_419 from the correct value, far outside approx().
    """
    import math

    mu = math.log(400_000.0)
    sigma = 1.0  # median = 400_000; mean = 400_000 * exp(0.5) ≈ 659_489

    base = make_fair_parameters(tef=2.0, vuln=0.5, primary=1_000_000, secondary=400_000)
    fp = FAIRParameters(
        threat_event_frequency=base.threat_event_frequency,
        vulnerability=base.vulnerability,
        primary_loss=base.primary_loss,
        secondary_loss=FAIRDistribution(DistributionType.LOGNORMAL, {"mean": mu, "sigma": sigma}),
    )

    # Control: LEC_PREVENTION (OR group) — tef weight=0.8, vuln weight=0.9
    r = make_control(
        control_id="r",
        assignments=[("lec_prev_resistance", "probability", 0.6)],
    )

    # Hand-derived expected value (see docstring):
    b_tef = 2.0
    b_vuln = 0.5
    b_pl = 1_000_000.0
    b_sl_median = math.exp(mu)  # = 400_000.0 exactly

    tef_mult = 1 - 0.6 * 0.8  # = 0.52
    vuln_mult = 1 - 0.6 * 0.9  # = 0.46

    original_ale = b_tef * b_vuln * (b_pl + b_sl_median)
    adjusted_ale = b_tef * tef_mult * b_vuln * vuln_mult * (b_pl + b_sl_median)
    expected_v = original_ale - adjusted_ale  # = 1_064_224.0

    assert subset_reduction_closed_form(fp, [r]) == pytest.approx(expected_v)
