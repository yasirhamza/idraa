"""#130 Task 6 — engine≡diagnostic equivalence + monotonicity + boundary guarantees.

This module completes the spec §8 "correctness guarantees" that are not already
pinned by the prior tasks' pulled-forward tests. Specifically:

  * Engine ≡ diagnostic at the shared-group-effectiveness layer (spec §8 anti-drift,
    plan Task 6 Step 1). NOTE: this group-effectiveness equality is SUPERSEDED for
    routing-correctness by `test_node_level_equivalence.py` (the table-driven
    node-level contract): the shared-layer equality alone would NOT have caught the
    original Response mis-route — both engine and diagnostic read `compose_groups`
    BY CONSTRUCTION, so they agree on group effectiveness even when the engine's
    group→node collapse is wrong (spec §14 NEW-3). It is kept as the explicit
    statement of the D2 shared-source contract, marked superseded.
  * Monotonicity (spec §8): adding or improving any control never raises residual
    ALE (`or_compose` ↑, weak-AND ↑, `1 − E·w` ↓ all compose monotonically). Covers
    the Detection→Response gate (present AND absent) and a multi-group control set.
    A deterministic parameter grid stands in for a `hypothesis` property test
    (`hypothesis` is not a project dependency; CLAUDE.md forbids installing one for
    a single test). Monotonicity is asserted on the DETERMINISTIC adjusted node
    multipliers / parameters (no Monte-Carlo sampling noise), then confirmed end to
    end on residual ALE for the headline gate + multi-group cases.
  * Boundary (plan-gate I-spec-1): all-perfect Response (+Detection gate open) →
    maximal magnitude reduction. The no-controls→identity half lives in
    `test_response_magnitude_reroute.py::test_no_controls_is_identity`.

The combined Event-Termination + Loss-Reduction "subtractor stays out of
`risk_reduction_value`" guarantee (R3 N-arch-A) is pinned in
`test_per_control_attribution_reconciliation.py::test_event_termination_plus_loss_reduction_excludes_subtractor`
— not duplicated here.
"""

from __future__ import annotations

import itertools

import pytest

from fair_cam.models.composition_topology import (
    GROUP_NODE_MAPPING,
    BooleanGroup,
)
from fair_cam.risk_engine.control_aware import (
    _NODE_KEYS,
    _group_comp_to_node_multipliers,
)
from fair_cam.risk_engine.group_composition import build_group_effectiveness_reports, compose_groups
from fair_cam.risk_engine.native_control_aware import NativeControlAwareRiskCalculator
from fair_cam.tests.risk_engine._helpers import (
    make_control,
    make_fair_parameters,
)


# --------------------------------------------------------------------------- #
# Reusable control fixtures spanning all engine-exercised groups.
# --------------------------------------------------------------------------- #
def _prevention(strength: float, cid: str = "prev") -> object:
    return make_control(
        control_id=cid,
        assignments=[("lec_prev_resistance", "probability", strength)],
    )


def _detection(cid: str = "det") -> object:
    # Detection is strict-AND across all three sub-functions; supply all three so
    # the gate can open.
    return make_control(
        control_id=cid,
        assignments=[
            ("lec_det_visibility", "probability", 0.9),
            ("lec_det_monitoring", "elapsed_time", 1.0),
            ("lec_det_recognition", "probability", 0.9),
        ],
    )


def _response(strength: float, cid: str = "resp") -> object:
    return make_control(
        control_id=cid,
        assignments=[("lec_resp_resilience", "probability", strength)],
    )


def _vmc(cid: str = "vmc") -> object:
    return make_control(
        control_id=cid,
        assignments=[
            ("vmc_prev_reduce_change_freq", "percent_reduction", 0.5),
            ("vmc_prev_reduce_variance_prob", "percent_reduction", 0.5),
        ],
    )


# --------------------------------------------------------------------------- #
# 1. Engine ≡ diagnostic — shared group-effectiveness layer (D2).
# --------------------------------------------------------------------------- #
def test_engine_uses_same_group_effectiveness_as_diagnostic() -> None:
    """The per-group effectivenesses the engine consumes equal the diagnostic's
    `GroupEffectivenessReport` values (spec §8 anti-drift, D2).

    SUPERSEDED by `test_node_level_equivalence.py` for routing correctness — see
    module docstring — but retained as the explicit shared-source statement.
    """
    controls = [_prevention(0.7), _detection(), _response(0.6), _vmc()]

    reports = build_group_effectiveness_reports(controls)  # diagnostic
    comp = compose_groups(controls)  # what the engine consumes

    for group, rpt in reports.items():
        if rpt.group_effectiveness is None:
            assert comp.group_effectiveness.get(group) is None
        else:
            assert comp.group_effectiveness[group] == pytest.approx(rpt.group_effectiveness)


# --------------------------------------------------------------------------- #
# 2. Monotonicity — deterministic node/parameter level (no MC noise).
# --------------------------------------------------------------------------- #
def _node_multipliers(controls: list) -> dict[str, float]:
    return _group_comp_to_node_multipliers(compose_groups(controls))


def test_all_node_multipliers_bounded_above_by_identity() -> None:
    """Every group's contribution can only REDUCE a node (multiplier ≤ 1.0); the
    engine never amplifies risk. Pin across a control set spanning every group."""
    controls = [_prevention(0.7), _detection(), _response(0.6), _vmc()]
    mults = _node_multipliers(controls)
    for node in _NODE_KEYS:
        assert 0.0 <= mults[node] <= 1.0, f"{node} multiplier out of [0,1]: {mults[node]}"


# (label, baseline control set, control to ADD). Adding any control must not
# raise any node multiplier (monotone non-increasing) — covers the gate present
# AND absent, and multi-group accumulation.
_ADD_CASES = [
    ("add_prevention_to_empty", [], _prevention(0.7, "p2")),
    ("add_response_no_detection_gate_closed", [], _response(0.6, "r2")),
    ("add_response_with_detection_gate_open", [_detection()], _response(0.6, "r2")),
    ("add_detection_opens_response_gate", [_response(0.6)], _detection("d2")),
    ("add_vmc_to_prevention", [_prevention(0.7)], _vmc("v2")),
    ("add_second_prevention", [_prevention(0.5)], _prevention(0.6, "p2")),
]


@pytest.mark.parametrize("label,base_controls,extra", _ADD_CASES, ids=lambda c: c)
def test_adding_a_control_never_raises_any_node_multiplier(label, base_controls, extra) -> None:
    """Monotonicity at the deterministic node level: adding a control to any set
    leaves every node multiplier ≤ its prior value (a lower multiplier = more
    reduction = lower ALE). Catches a gate that ACCIDENTALLY raises a multiplier
    (e.g. opening the Detection→Response gate must only lower magnitude, never
    raise frequency)."""
    before = _node_multipliers(base_controls)
    after = _node_multipliers([*base_controls, extra])
    for node in _NODE_KEYS:
        assert after[node] <= before[node] + 1e-12, (
            f"{label}: adding a control RAISED {node} multiplier "
            f"({before[node]} -> {after[node]}) — monotonicity violation"
        )


# Improving a single Response control's strength (gate open) must not raise the
# magnitude multipliers (stronger control = more reduction).
@pytest.mark.parametrize("weaker,stronger", [(0.3, 0.6), (0.6, 0.9), (0.1, 0.99)])
def test_improving_response_strength_never_raises_magnitude_multiplier(weaker, stronger) -> None:
    det = _detection()
    weak_mults = _node_multipliers([det, _response(weaker)])
    strong_mults = _node_multipliers([det, _response(stronger)])
    for node in ("secondary_loss", "primary_loss"):
        assert strong_mults[node] <= weak_mults[node] + 1e-12, (
            f"improving Response {weaker}->{stronger} raised {node} multiplier"
        )


def test_monotonicity_over_full_control_powerset_grid() -> None:
    """Deterministic stand-in for a hypothesis property test: over a grid of
    control subsets, every superset's node multipliers are ≤ each subset's (adding
    controls only ever reduces). Sweeps the gate (Detection in/out), Prevention,
    VMC, and Response together."""
    pool = {
        "prev": _prevention(0.7, "prev"),
        "det": _detection("det"),
        "resp": _response(0.6, "resp"),
        "vmc": _vmc("vmc"),
    }
    keys = list(pool)
    # Compare every subset against every subset obtained by adding ONE more
    # control; assert node multipliers are non-increasing on the added control.
    all_subsets = [
        frozenset(c) for r in range(len(keys) + 1) for c in itertools.combinations(keys, r)
    ]
    for subset in all_subsets:
        sub_mults = _node_multipliers([pool[k] for k in subset])
        for extra in keys:
            if extra in subset:
                continue
            super_mults = _node_multipliers([pool[k] for k in (subset | {extra})])
            for node in _NODE_KEYS:
                assert super_mults[node] <= sub_mults[node] + 1e-12, (
                    f"adding {extra!r} to {set(subset)} raised {node} multiplier "
                    f"({sub_mults[node]} -> {super_mults[node]})"
                )


# --------------------------------------------------------------------------- #
# 3. Monotonicity — end-to-end residual ALE (headline gate + multi-group).
# --------------------------------------------------------------------------- #
def _residual_ale(controls: list) -> float:
    # Epic #324: re-pointed from the (removed) pyfair ControlAwareRiskCalculator
    # to the native calculator. The native path shares one spawned seed between
    # base and residual (common random numbers), which only SHARPENS the
    # control-value delta — the monotonicity invariant asserted by the callers
    # (a control never RAISES residual ALE) is unchanged.
    calc = NativeControlAwareRiskCalculator(controls=controls, n_simulations=2000, random_seed=2024)
    enhanced = calc.calculate_control_enhanced_risk(
        make_fair_parameters(tef=10.0, vuln=0.4, primary=1_000_000, secondary=500_000),
        [c.control_id for c in controls],
    )
    return enhanced.residual_risk.annualized_loss_expectancy


def test_adding_response_with_detection_never_increases_ale() -> None:
    """End-to-end: a Response control (with the Detection gate open) never raises
    residual ALE versus the Detection-only baseline. Asserted on the deterministic
    expected-loss check first, then confirmed on the simulated ALE with a tolerance
    band for Monte-Carlo jitter."""
    det = _detection()
    # Deterministic: Response magnitude multipliers ≤ 1.0 (gate open).
    mults_with_resp = _node_multipliers([det, _response(0.8)])
    assert mults_with_resp["secondary_loss"] <= 1.0
    assert mults_with_resp["primary_loss"] <= 1.0

    ale_base = _residual_ale([det])
    ale_with_resp = _residual_ale([det, _response(0.8)])
    # 2% tolerance band for sampling jitter at n=2000.
    assert ale_with_resp <= ale_base * 1.02


def test_adding_any_single_control_never_increases_ale_from_empty() -> None:
    """Adding ONE control to the empty set never raises residual ALE (relative to
    the no-controls baseline), for a Prevention, a gate-closed Response, a
    Detection+Response pair, and a VMC control."""
    base_ale = _residual_ale([])
    for controls in (
        [_prevention(0.7)],
        [_response(0.6)],  # gate closed -> magnitude unchanged, ALE ≈ base
        [_detection(), _response(0.6)],
        [_vmc()],
    ):
        assert _residual_ale(controls) <= base_ale * 1.02


# --------------------------------------------------------------------------- #
# 4. Boundary — all-perfect Response (gate open) -> maximal magnitude reduction.
# --------------------------------------------------------------------------- #
def test_all_perfect_response_max_magnitude_reduction() -> None:
    """All-perfect Response (Event Termination + Resilience at opeff 1.0) with the
    Detection gate fully open (Detection opeff 1.0) drives the magnitude multiplier
    to its MAXIMAL reduction `1 − 1·w` (secondary 0.5, primary 0.2) — the floor of
    the `1 − E·w` form. No higher reduction is reachable through composition (I-spec-1)."""
    # elapsed_time opeff is 1.0 exactly at capability 0.0 (instant = perfect);
    # probability sub-functions are perfect at 1.0.
    perfect_detection = make_control(
        control_id="det",
        assignments=[
            ("lec_det_visibility", "probability", 1.0),
            ("lec_det_monitoring", "elapsed_time", 0.0),
            ("lec_det_recognition", "probability", 1.0),
        ],
    )
    perfect_response = make_control(
        control_id="resp",
        assignments=[
            ("lec_resp_event_termination", "elapsed_time", 0.0),
            ("lec_resp_resilience", "probability", 1.0),
        ],
    )
    controls = [perfect_detection, perfect_response]
    comp = compose_groups(controls)
    pair_eff = comp.group_effectiveness[BooleanGroup.LEC_DETECTION_RESPONSE_PAIR]
    assert pair_eff == pytest.approx(1.0)  # both arms perfect -> AND -> 1.0

    # #328: asserted on the LIVE node multipliers (the retired param-dict
    # application layer is gone; the native engine consumes these directly).
    m = _group_comp_to_node_multipliers(comp)
    w = GROUP_NODE_MAPPING[BooleanGroup.LEC_RESPONSE].weights
    # Maximal reduction = 1 - 1*w (no control set can push below this through the
    # 1 - E·w form, since E ∈ [0,1]).
    assert m["secondary_loss"] == pytest.approx(1.0 - w["secondary_loss"])
    assert m["primary_loss"] == pytest.approx(1.0 - w["primary_loss"])
