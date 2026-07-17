"""Leave-one-out ("if removed") attribution — hand-math anchored unit tests.

The map-legend distinction (2026-07-03 methodology adjudication): the Shapley
figure is a FAIR-SHARE allocation (Σφ = v(N)); LOO_i = v(N) − v(N∖{i}) is the
DROP-COST counterfactual. The three cases below pin exactly the configurations
where the two DIVERGE — the divergence is the feature's point, so each case
carries its full hand derivation and asserts both statistics side by side.

All expected values are derived analytically in the docstrings from the node
algebra: with magnitude untouched, v(S) = A·(1 − mtef(S)·mvuln(S)) where
A = original_ale from scenario_base_ale, mtef = 1 − E·0.8, mvuln = 1 − E·0.9
(LEC_PREVENTION weights); magnitude-side v(S) = tef·vuln·(pl·(1−mpl) +
sl·(1−msl)) with mpl = 1 − E_pair·0.2, msl = 1 − E_pair·0.5 (LEC_RESPONSE
weights, pair-substituted effectiveness on stealth effects).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fair_cam.models.control import (
    Control,
    ControlDomain,
    ControlType,
    FairCamControlFunctionAssignment,
)
from fair_cam.models.sub_function import FairCamSubFunction
from fair_cam.risk_engine.control_attribution import (
    scenario_base_ale,
    subset_reduction_closed_form,
)
from fair_cam.tests.risk_engine._helpers import make_fair_parameters

from idraa.services.run_executor import (
    _compute_loo_by_scenario,
    _inject_loo,
)
from idraa.services.shapley import shapley_values


def _ctrl(cid: str, assigns: list[tuple[str, float | None, float, float]]) -> Control:
    """(sub_function_value, capability, coverage, reliability) -> Control."""
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


def _calculator(controls: dict[str, Control]) -> SimpleNamespace:
    """Duck-typed stand-in for the executor's calculator: only
    ``control_registry.get_control`` is consumed by the attribution passes."""
    return SimpleNamespace(control_registry=SimpleNamespace(get_control=controls.get))


_RP = make_fair_parameters(tef=2.0, vuln=0.5, primary=1_000_000.0, secondary=500_000.0)


def _run_loo(
    controls: dict[str, Control],
    *,
    availability: dict[str, bool] | None = None,
    budget: int | None = None,
) -> tuple[dict[str, float], list[tuple[str, str]]]:
    kwargs: dict = {"per_scenario_availability": availability}
    if budget is not None:
        kwargs["total_eval_budget"] = budget
    by, skipped = _compute_loo_by_scenario(
        _calculator(controls),
        [("s1", "scenario one", _RP)],
        None,
        list(controls),
        **kwargs,
    )
    return by.get("s1", {}), skipped


def _shapley(controls: dict[str, Control]) -> dict[str, float]:
    def _v(subset: frozenset[str]) -> float:
        return subset_reduction_closed_form(_RP, [controls[c] for c in subset])

    return shapley_values(list(controls), _v)


# --------------------------------------------------------------------------- #
# Case A — redundant pair: LOO ≈ 0-ish each, fair share splits evenly.
# --------------------------------------------------------------------------- #


def test_redundant_pair_loo_far_below_fair_share() -> None:
    """Two identical resistance controls (cap 0.9, cov 1.0, rel 1.0), same
    sub-function → OR overlap.

    Hand math (per unit of A = original_ale):
      single: E = 0.9 → mtef = 1−0.72 = 0.28, mvuln = 1−0.81 = 0.19
              v({i})/A = 1 − 0.28·0.19 = 1 − 0.0532 = 0.9468
      pair:   E = or(0.9, 0.9) = 0.99 → mtef = 0.208, mvuln = 0.109
              v(N)/A = 1 − 0.022672 = 0.977328
      LOO_i/A = 0.977328 − 0.9468 = 0.030528 (each — near-redundant)
      Shapley (symmetry + efficiency): φ_i = v(N)/2 = 0.488664·A each.
      Σ LOO = 0.061056·A ≠ v(N) — LOO is NOT an allocation (pinned).
    """
    controls = {
        "r1": _ctrl("r1", [("lec_prev_resistance", 0.9, 1.0, 1.0)]),
        "r2": _ctrl("r2", [("lec_prev_resistance", 0.9, 1.0, 1.0)]),
    }
    a = scenario_base_ale(_RP)[4]  # original_ale
    loo, skipped = _run_loo(controls)
    assert skipped == []

    v_full = subset_reduction_closed_form(_RP, list(controls.values()))
    assert v_full == pytest.approx(0.977328 * a, rel=1e-12)
    for cid in controls:
        assert loo[cid] == pytest.approx(0.030528 * a, rel=1e-12)

    phi = _shapley(controls)
    for cid in controls:
        assert phi[cid] == pytest.approx(v_full / 2, rel=1e-12)
        # The map-legend divergence: fair share is ~16x the true drop cost here.
        assert loo[cid] < 0.07 * phi[cid]
    # Non-allocation pin: Σ LOO ≠ v(N) by construction on overlapping controls.
    assert sum(loo.values()) == pytest.approx(0.061056 * a, rel=1e-12)
    assert sum(loo.values()) != pytest.approx(v_full, rel=1e-6)


# --------------------------------------------------------------------------- #
# Case B — gating pair (stealth effect): LOO of EACH member = the FULL value.
# --------------------------------------------------------------------------- #


def test_gating_pair_loo_is_full_value_for_both() -> None:
    """Detection trio on one control + response on the other (resilience 0.8 →
    weak-AND single = 0.8); stealth (availability False) → magnitude credit
    rides the detection∧response pair.

    Hand math (LOO-Meth-1): monitoring capability is deliberately None, which
    fair_cam anchors to 0.5 (`compute_assignment_part` maps NULL capability →
    0.5 · coverage) — that safe-anchor is itself part of what this test pins.
      det AND = 0.7 · 0.5 · 0.7 = 0.245        [LEC_DETECTION is GroupType.AND]
      E_pair  = 0.245 · 0.8 = 0.196
      mpl = 1 − 0.2·0.196 = 0.9608 ; msl = 1 − 0.5·0.196 = 0.902
      v(N) = tef·vuln·(pl·0.2 + sl·0.5)·0.196   [prevention untouched]
      v({D}) = 0 (D8 substitution zeroes standalone detection on stealth)
      v({R}) = 0 (response without detection is gated on stealth effects)
      LOO_D = LOO_R = v(N) — BOTH are individually necessary.
      Shapley: φ_D = φ_R = v(N)/2 — fair share halves what removal costs.
    """
    controls = {
        "det": _ctrl(
            "det",
            [
                ("lec_det_visibility", 0.7, 1.0, 1.0),
                ("lec_det_monitoring", None, 1.0, 1.0),  # NULL → 0.5 anchor
                ("lec_det_recognition", 0.7, 1.0, 1.0),
            ],
        ),
        "resp": _ctrl("resp", [("lec_resp_resilience", 0.8, 1.0, 1.0)]),
    }
    # AND of (0.7, 0.5, 0.7) = 0.245; pair = 0.245 · 0.8 = 0.196
    base = scenario_base_ale(_RP)
    base_tef, base_vuln, base_pl, base_sl, _a = base
    e_pair = 0.7 * 0.5 * 0.7 * 0.8
    expected_v_full = base_tef * base_vuln * (base_pl * 0.2 * e_pair + base_sl * 0.5 * e_pair)

    loo, skipped = _run_loo(controls)
    assert skipped == []
    v_full = subset_reduction_closed_form(_RP, list(controls.values()))
    assert v_full == pytest.approx(expected_v_full, rel=1e-12)
    assert subset_reduction_closed_form(_RP, [controls["det"]]) == 0.0
    assert subset_reduction_closed_form(_RP, [controls["resp"]]) == 0.0

    # Both members carry the FULL drop cost.
    assert loo["det"] == pytest.approx(v_full, rel=1e-12)
    assert loo["resp"] == pytest.approx(v_full, rel=1e-12)
    # Fair share halves it — the exact misreading the legend corrects.
    phi = _shapley(controls)
    assert phi["det"] == pytest.approx(v_full / 2, rel=1e-12)
    assert phi["resp"] == pytest.approx(v_full / 2, rel=1e-12)


# --------------------------------------------------------------------------- #
# Case C — meta + LEC: the coupling gives the meta control a positive LOO.
# --------------------------------------------------------------------------- #


def test_meta_control_loo_positive_via_coupling() -> None:
    """Meta (dsc_prev_communication 1.0/1.0/1.0 → E_meta = 1.0) + LEC
    (resistance cap 1.0, cov 1.0, r0 0.5).

    Hand math (per unit of A):
      v({L}): r_eff = r0 = 0.5 (no meta partner) → E = 0.5
              → mtef = 0.6, mvuln = 0.55 → v/A = 1 − 0.33 = 0.67
      v({M}) = 0 (nothing to uplift)
      v(N):  r_eff = 0.5 + 0.5·κ·E_meta = 0.5 + 0.25 = 0.75 (κ = 0.5)
              → mtef = 0.4, mvuln = 0.325 → v/A = 1 − 0.13 = 0.87
      LOO_M/A = 0.87 − 0.67 = 0.20 ; LOO_L/A = 0.87
      Shapley: φ_M = (0 + 0.20)/2 = 0.10·A ; φ_L = (0.67 + 0.87)/2 = 0.77·A
      (efficiency: 0.10 + 0.77 = 0.87 ✓ — and φ_M ≠ LOO_M, another legend case)
    """
    controls = {
        "meta": _ctrl("meta", [("dsc_prev_communication", 1.0, 1.0, 1.0)]),
        "lec": _ctrl("lec", [("lec_prev_resistance", 1.0, 1.0, 0.5)]),
    }
    a = scenario_base_ale(_RP)[4]
    loo, skipped = _run_loo(controls)
    assert skipped == []
    assert loo["meta"] == pytest.approx(0.20 * a, rel=1e-12)
    assert loo["lec"] == pytest.approx(0.87 * a, rel=1e-12)

    phi = _shapley(controls)
    assert phi["meta"] == pytest.approx(0.10 * a, rel=1e-12)
    assert phi["lec"] == pytest.approx(0.77 * a, rel=1e-12)
    assert phi["meta"] + phi["lec"] == pytest.approx(0.87 * a, rel=1e-12)


# --------------------------------------------------------------------------- #
# Mechanics: linearity (over-cap coverage), budget, availability, injection.
# --------------------------------------------------------------------------- #


def test_loo_linear_cost_covers_many_controls_within_budget() -> None:
    """14 controls under a 100-eval budget: LOO costs only n+1 = 15 evals and
    must return all 14, where exhaustive enumeration (2^14 = 16384) could not.
    (LOO-Meth-6: n=14 is NOT over_cap — MAX_ATTRIBUTION_CONTROLS is 64 and the
    Shapley pass would Maleki-sample it; what this pins is LOO's linear budget
    accounting, the property that also makes truly over-cap scenarios, n > 64,
    reachable for LOO when Shapley skips them.)"""
    controls = {
        f"c{i}": _ctrl(f"c{i}", [("lec_prev_resistance", 0.05 + 0.01 * i, 1.0, 1.0)])
        for i in range(14)
    }
    loo, skipped = _run_loo(controls, budget=100)
    assert skipped == []
    assert len(loo) == 14
    assert all(v >= 0.0 for v in loo.values())


def test_loo_budget_skip() -> None:
    """A 2-control scenario costs 3 evals; a budget of 2 must skip it whole."""
    controls = {
        "r1": _ctrl("r1", [("lec_prev_resistance", 0.9, 1.0, 1.0)]),
        "r2": _ctrl("r2", [("lec_prev_resistance", 0.9, 1.0, 1.0)]),
    }
    loo_map, skipped = _run_loo(controls, budget=2)
    assert loo_map == {}
    assert skipped == [("s1", "over_budget")]


def test_loo_availability_key_validation() -> None:
    """Mis-keyed availability map fails loudly (same rule as the Shapley pass)."""
    controls = {"r1": _ctrl("r1", [("lec_prev_resistance", 0.9, 1.0, 1.0)])}
    with pytest.raises(ValueError, match="per_scenario_availability"):
        _compute_loo_by_scenario(
            _calculator(controls),
            [("s1", "scenario one", _RP)],
            None,
            list(controls),
            per_scenario_availability={"WRONG": True},
        )


def test_inject_loo_absent_scenario_gets_no_key() -> None:
    """absent≠0.0 convention: a scenario missing from the LOO map renders the
    unavailable state, never a misleading $0."""
    payload = [
        {"scenario_id": "s1", "control_adjustments": [{"control_id": "c1"}]},
        {"scenario_id": "s2", "control_adjustments": [{"control_id": "c1"}]},
    ]
    _inject_loo(payload, {"s1": {"c1": 123.45}})
    assert payload[0]["control_adjustments"][0]["if_removed_value"] == 123.45
    assert "if_removed_value" not in payload[1]["control_adjustments"][0]
