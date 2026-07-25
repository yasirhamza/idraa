"""PR2 Task 8 (D16): per-scenario capacity-cap disclosure.

Unit tests for ``services.run_view_model._build_capacity_cap_note`` and its
helpers (``_field_mean_and_retention``, ``_lognormal_retention``,
``_dist_kind``). These are pure functions over plain dicts mirroring
``RiskAnalysisRun.scenario_inputs_snapshot`` shape -- no DB.

Hand-math anchors (computed independently via a scratch scipy/math script,
NOT by importing the module under test -- see the docstring on each test):

  Case A (single lognormal PL, cap well above p95, no SL):
    mu=ln(1_000_000), sigma=1.7, cap=50_000_000
    R_f = Phi(b - sigma) / Phi(b) = 0.7339899596212376
    cap_effect = 1 - R_f = 0.2660100403787624

  Case B (Case A's lognormal PL + a PERT SL -- both kinds in both sums):
    SL PERT(low=100_000, mode=300_000, high=800_000), E_pert = 350_000.0
    E_ln = exp(mu + sigma**2/2) = 4_241_852.142820434
    R_scen = (E_ln*R_f + E_pert*1.0) / (E_ln + E_pert) = 0.7542657679958906
    cap_effect = 0.24573423200410938

  Case C (lognormal_mixture, 2 components, UNEQUAL sigma, shared cap):
    w=(0.4, 0.6); (mu1=ln(1_000), s1=0.5); (mu2=ln(1_000_000_000), s2=0.5)
    cap=5_000_000_000
    m1=1133.148453066826, m2=1133148453.0668256
    r1=1.0 (Phi(b1) saturates), r2=0.9973665667726783
    E_f = w1*m1 + w2*m2 = 679889525.0994766
    R_mix = (w1*m1*r1 + w2*m2*r2) / E_f = 0.9973665685282993
    cap_effect = 0.002633431471700698

  Case D (ndtr underflow guard): mu=ln(1e9), sigma=0.3, cap=1.0 ->
    b = -69.07755278982137 -> ndtr(b) == 0.0 exactly (underflow).

  Case E (anti-hardcode, same field, two caps):
    mu=ln(1_000_000), sigma=1.7; cap_lo=20_000_000 -> R=0.5461043864128475
    cap_hi=200_000_000 -> R=0.92255216171454 (R_lo < R_hi, i.e. cap_effect
    shrinks as the cap widens).
"""

from __future__ import annotations

import math
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

from idraa.services.reporting_currency import ReportingCurrency
from idraa.services.run_view_model import (
    _build_capacity_cap_note,
    _dist_kind,
    _field_mean_and_retention,
    _lognormal_retention,
    build_display_results,
)

_USD = ReportingCurrency("USD", Decimal("1"), is_pinned=True, provenance=None)

_MU_A = math.log(1_000_000.0)
_SIGMA_A = 1.7
_CAP_A = 50_000_000.0


def _snapshot(pl: dict[str, Any] | None, sl: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "scenarios": [
            {
                "scenario_id": "s1",
                "scenario_name": "Test scenario",
                "threat_event_frequency": {
                    "distribution": "pert",
                    "low": 0.1,
                    "mode": 0.5,
                    "high": 2.0,
                },
                "vulnerability": {"distribution": "pert", "low": 0.2, "mode": 0.4, "high": 0.6},
                "primary_loss": pl,
                "secondary_loss": sl,
                "effect": None,
            }
        ]
    }


# ---- _dist_kind ----------------------------------------------------------


def test_dist_kind_case_insensitive_and_absent_defaults_to_pert() -> None:
    assert _dist_kind({"distribution": "PERT"}) == "pert"
    assert _dist_kind({"distribution": "pert"}) == "pert"
    assert _dist_kind({"distribution": "Lognormal"}) == "lognormal"
    assert _dist_kind({}) == "pert"  # absent key -> PERT, mirrors run_executor


# ---- _lognormal_retention --------------------------------------------------


def test_lognormal_retention_matches_hand_math_case_a() -> None:
    r = _lognormal_retention(_MU_A, _SIGMA_A, _CAP_A)
    assert r == pytest.approx(0.7339899596212376, rel=1e-9)


def test_lognormal_retention_underflow_guard_fails_soft_case_d() -> None:
    """A cap absurdly below the field's own core (b ~ -69, deep underflow
    territory per run_executor._validated_capacity_bound's documented
    footgun) must fail SOFT to R_f=1.0 (cap treated as non-binding), never
    raise and never divide by zero. D19 blocks this at store time for new
    rows, but a pre-D19 legacy snapshot or a raw-SQL row is not guaranteed
    to satisfy it -- this is exactly the "legacy/edge snapshot" guard the
    brief requires."""
    r = _lognormal_retention(math.log(1_000_000_000.0), 0.3, 1.0)
    assert r == 1.0


def test_lognormal_retention_non_positive_sigma_fails_soft() -> None:
    assert _lognormal_retention(10.0, 0.0, 1_000_000.0) == 1.0
    assert _lognormal_retention(10.0, -1.0, 1_000_000.0) == 1.0


def test_lognormal_retention_non_finite_or_non_positive_cap_fails_soft() -> None:
    assert _lognormal_retention(10.0, 1.0, float("inf")) == 1.0
    assert _lognormal_retention(10.0, 1.0, float("nan")) == 1.0
    assert _lognormal_retention(10.0, 1.0, 0.0) == 1.0
    assert _lognormal_retention(10.0, 1.0, -5.0) == 1.0


def test_lognormal_retention_monotonic_in_cap_case_e() -> None:
    """Anti-hardcode property at the single-field level: a wider cap always
    retains MORE mean (R grows towards 1 as cap -> infinity)."""
    r_lo = _lognormal_retention(_MU_A, _SIGMA_A, 20_000_000.0)
    r_hi = _lognormal_retention(_MU_A, _SIGMA_A, 200_000_000.0)
    assert r_lo == pytest.approx(0.5461043864128475, rel=1e-9)
    assert r_hi == pytest.approx(0.92255216171454, rel=1e-9)
    assert r_lo < r_hi


# ---- _field_mean_and_retention ---------------------------------------------


def test_field_mean_and_retention_pert_ignores_cap_key_entirely() -> None:
    e_f, r_f = _field_mean_and_retention(
        {"distribution": "pert", "low": 100_000.0, "mode": 300_000.0, "high": 800_000.0}
    )
    assert e_f == pytest.approx(350_000.0)
    assert r_f == 1.0


def test_field_mean_and_retention_pert_uppercase_and_absent_key_agree() -> None:
    """The prod backup stores 'PERT' UPPERCASE on 31/40 loss dicts
    (B-CAP-BASIS); a naive `== "pert"` comparison would silently drop these
    fields from both weighted sums (issue #90 Task 6.5 defect class)."""
    upper = _field_mean_and_retention(
        {"distribution": "PERT", "low": 100_000.0, "mode": 300_000.0, "high": 800_000.0}
    )
    lower = _field_mean_and_retention(
        {"distribution": "pert", "low": 100_000.0, "mode": 300_000.0, "high": 800_000.0}
    )
    absent = _field_mean_and_retention({"low": 100_000.0, "mode": 300_000.0, "high": 800_000.0})
    assert upper == lower == absent
    assert upper[0] == pytest.approx(350_000.0)
    assert upper[1] == 1.0


def test_field_mean_and_retention_lognormal_no_cap_key_is_non_binding() -> None:
    e_f, r_f = _field_mean_and_retention(
        {"distribution": "lognormal", "mean": _MU_A, "sigma": _SIGMA_A}
    )
    assert e_f == pytest.approx(math.exp(_MU_A + _SIGMA_A**2 / 2.0))
    assert r_f == 1.0


def test_field_mean_and_retention_lognormal_capped_matches_case_a() -> None:
    e_f, r_f = _field_mean_and_retention(
        {"distribution": "lognormal", "mean": _MU_A, "sigma": _SIGMA_A, "max": _CAP_A}
    )
    assert e_f == pytest.approx(4_241_852.142820434, rel=1e-9)
    assert r_f == pytest.approx(0.7339899596212376, rel=1e-9)


def test_field_mean_and_retention_mixture_kernel_matches_hand_math_case_c() -> None:
    """The mixture kernel (R_f = sum_i w_i*m_i*R_i / sum_i w_i*m_i) is NEW
    code with no in-repo precedent -- the single-field formula does not
    apply. Uses UNEQUAL component sigma so the test cannot pass under a
    scalar-sigma-blind implementation (every b_i would be invariant to
    which sigma is hoisted if sigma were equal across components)."""
    field = {
        "distribution": "lognormal_mixture",
        "components": [
            {"weight": 0.4, "mean": math.log(1_000.0), "sigma": 0.5},
            {"weight": 0.6, "mean": math.log(1_000_000_000.0), "sigma": 0.5},
        ],
        "max": 5_000_000_000.0,
    }
    e_f, r_f = _field_mean_and_retention(field)
    assert e_f == pytest.approx(679889525.0994766, rel=1e-9)  # w1*m1 + w2*m2
    assert r_f == pytest.approx(0.9973665685282993, rel=1e-9)


def test_field_mean_and_retention_mixture_no_cap_is_non_binding() -> None:
    field = {
        "distribution": "LOGNORMAL_MIXTURE",
        "components": [
            {"weight": 0.4, "mean": math.log(1_000.0), "sigma": 0.5},
            {"weight": 0.6, "mean": math.log(1_000_000_000.0), "sigma": 0.5},
        ],
    }
    e_f, r_f = _field_mean_and_retention(field)
    assert r_f == 1.0
    assert e_f == pytest.approx(679889525.0994766, rel=1e-9)  # w1*m1 + w2*m2


def test_field_mean_and_retention_none_field_contributes_nothing() -> None:
    assert _field_mean_and_retention(None) == (0.0, 1.0)
    assert _field_mean_and_retention({}) == (0.0, 1.0)


def test_field_mean_and_retention_unknown_kind_excluded_not_crashed() -> None:
    """An unsupported kind (e.g. a shape that should never reach PL/SL, but
    a disclosure surface must not 500 on it) contributes to neither sum."""
    e_f, r_f = _field_mean_and_retention({"distribution": "uniform", "low": 0.0, "high": 1.0})
    assert (e_f, r_f) == (0.0, 1.0)


def test_field_mean_and_retention_malformed_lognormal_missing_keys_excluded() -> None:
    e_f, r_f = _field_mean_and_retention({"distribution": "lognormal"})  # no mean/sigma
    assert (e_f, r_f) == (0.0, 1.0)


# ---- _build_capacity_cap_note ----------------------------------------------


def test_none_when_snapshot_attribute_absent() -> None:
    run = SimpleNamespace()  # no scenario_inputs_snapshot attribute at all
    assert _build_capacity_cap_note(run, _USD) is None


def test_none_when_snapshot_is_null() -> None:
    run = SimpleNamespace(scenario_inputs_snapshot=None)
    assert _build_capacity_cap_note(run, _USD) is None


def test_none_when_snapshot_has_no_scenarios() -> None:
    run = SimpleNamespace(scenario_inputs_snapshot={"scenarios": []})
    assert _build_capacity_cap_note(run, _USD) is None


def test_none_when_nothing_is_capped() -> None:
    """PERT-only scenario, no `max` anywhere -- nothing to disclose."""
    snap = _snapshot(
        pl={"distribution": "pert", "low": 100_000.0, "mode": 300_000.0, "high": 800_000.0},
        sl={"distribution": "pert", "low": 10_000.0, "mode": 30_000.0, "high": 80_000.0},
    )
    run = SimpleNamespace(scenario_inputs_snapshot=snap)
    assert _build_capacity_cap_note(run, _USD) is None


def test_single_lognormal_field_matches_hand_math_case_a() -> None:
    snap = _snapshot(
        pl={"distribution": "lognormal", "mean": _MU_A, "sigma": _SIGMA_A, "max": _CAP_A},
        sl=None,
    )
    run = SimpleNamespace(scenario_inputs_snapshot=snap)
    note = _build_capacity_cap_note(run, _USD)
    assert note is not None
    assert note["cap_effect_frac"] == pytest.approx(0.2660100403787624, rel=1e-9)
    assert note["pl_max"] == pytest.approx(_CAP_A)
    assert note["sl_max"] is None


def test_mixed_pert_and_lognormal_both_kinds_in_both_sums_case_b() -> None:
    """The load-bearing composition property: PERT SL must NOT be dropped
    from the weighted average just because it carries no `max` -- dropping
    it would silently reweight the scenario to the lognormal field's OWN
    retention (0.2660...), which is a DIFFERENT (wrong) number from the
    correctly-composed 0.24573423200410938."""
    snap = _snapshot(
        pl={"distribution": "lognormal", "mean": _MU_A, "sigma": _SIGMA_A, "max": _CAP_A},
        sl={"distribution": "pert", "low": 100_000.0, "mode": 300_000.0, "high": 800_000.0},
    )
    run = SimpleNamespace(scenario_inputs_snapshot=snap)
    note = _build_capacity_cap_note(run, _USD)
    assert note is not None
    assert note["cap_effect_frac"] == pytest.approx(0.24573423200410938, rel=1e-9)
    # Sanity: this must differ from the single-field (PL-only) figure --
    # proves the PERT SL was actually folded into the composition.
    assert note["cap_effect_frac"] != pytest.approx(0.2660100403787624, rel=1e-9)


def test_pert_uppercase_and_absent_distribution_key_do_not_change_composition() -> None:
    """The exact same Case B composition, but with the PERT SL's kind
    spelled 'PERT' (uppercase) and, separately, with the key entirely
    absent. Both must produce the SAME result as lowercase 'pert' -- a
    case-sensitive or KeyError-prone implementation either drops the field
    (reweighting to the lognormal-only figure) or crashes with a 500."""
    base_pl = {"distribution": "lognormal", "mean": _MU_A, "sigma": _SIGMA_A, "max": _CAP_A}
    expected = 0.24573423200410938

    upper = _build_capacity_cap_note(
        SimpleNamespace(
            scenario_inputs_snapshot=_snapshot(
                pl=base_pl,
                sl={"distribution": "PERT", "low": 100_000.0, "mode": 300_000.0, "high": 800_000.0},
            )
        ),
        _USD,
    )
    absent_key = _build_capacity_cap_note(
        SimpleNamespace(
            scenario_inputs_snapshot=_snapshot(
                pl=base_pl,
                sl={"low": 100_000.0, "mode": 300_000.0, "high": 800_000.0},
            )
        ),
        _USD,
    )
    assert upper is not None and absent_key is not None
    assert upper["cap_effect_frac"] == pytest.approx(expected, rel=1e-9)
    assert absent_key["cap_effect_frac"] == pytest.approx(expected, rel=1e-9)


def test_mixture_field_case_c() -> None:
    pl = {
        "distribution": "lognormal_mixture",
        "components": [
            {"weight": 0.4, "mean": math.log(1_000.0), "sigma": 0.5},
            {"weight": 0.6, "mean": math.log(1_000_000_000.0), "sigma": 0.5},
        ],
        "max": 5_000_000_000.0,
    }
    run = SimpleNamespace(scenario_inputs_snapshot=_snapshot(pl=pl, sl=None))
    note = _build_capacity_cap_note(run, _USD)
    assert note is not None
    assert note["cap_effect_frac"] == pytest.approx(0.002633431471700698, rel=1e-9)


def test_ndtr_underflow_never_500s_case_d() -> None:
    """A legacy/edge snapshot with a cap far below the field's own core
    must render a (degenerate but finite) note, never raise."""
    pl = {"distribution": "lognormal", "mean": math.log(1_000_000_000.0), "sigma": 0.3, "max": 1.0}
    run = SimpleNamespace(scenario_inputs_snapshot=_snapshot(pl=pl, sl=None))
    note = _build_capacity_cap_note(run, _USD)
    assert note is not None
    assert note["cap_effect_frac"] == 0.0  # fail-soft: cap treated as non-binding


def test_anti_hardcode_cap_effect_changes_with_the_cap_case_e() -> None:
    """The disclosed figure must be DERIVED from the computed ratio, not a
    fixed string -- pins that it changes (and moves the correct direction)
    across two different fixture caps on the same field."""
    note_tight = _build_capacity_cap_note(
        SimpleNamespace(
            scenario_inputs_snapshot=_snapshot(
                pl={
                    "distribution": "lognormal",
                    "mean": _MU_A,
                    "sigma": _SIGMA_A,
                    "max": 20_000_000.0,
                }
            )
        ),
        _USD,
    )
    note_loose = _build_capacity_cap_note(
        SimpleNamespace(
            scenario_inputs_snapshot=_snapshot(
                pl={
                    "distribution": "lognormal",
                    "mean": _MU_A,
                    "sigma": _SIGMA_A,
                    "max": 200_000_000.0,
                }
            )
        ),
        _USD,
    )
    assert note_tight is not None and note_loose is not None
    assert note_tight["cap_effect_frac"] != note_loose["cap_effect_frac"]
    # Tighter cap -> bigger disclosed effect; looser cap -> smaller.
    assert note_tight["cap_effect_frac"] > note_loose["cap_effect_frac"]


def test_null_secondary_loss_does_not_crash() -> None:
    """secondary_loss is a legitimate None on scenarios with no SL --
    must compute over PL alone, not raise."""
    snap = _snapshot(
        pl={"distribution": "lognormal", "mean": _MU_A, "sigma": _SIGMA_A, "max": _CAP_A},
        sl=None,
    )
    run = SimpleNamespace(scenario_inputs_snapshot=snap)
    note = _build_capacity_cap_note(run, _USD)
    assert note is not None
    assert note["sl_max"] is None


def test_currency_conversion_of_cap_values() -> None:
    eur = ReportingCurrency(
        "EUR", Decimal("0.92"), is_pinned=True, provenance="Converted from USD at 1 USD = 0.92 EUR"
    )
    snap = _snapshot(
        pl={"distribution": "lognormal", "mean": _MU_A, "sigma": _SIGMA_A, "max": _CAP_A},
        sl={"distribution": "pert", "low": 100_000.0, "mode": 300_000.0, "high": 800_000.0},
    )
    run = SimpleNamespace(scenario_inputs_snapshot=snap)
    note = _build_capacity_cap_note(run, eur)
    assert note is not None
    assert note["pl_max"] == pytest.approx(_CAP_A * 0.92)
    assert note["sl_max"] is None  # PERT never carries `max`


# ---- build_display_results (top-level wiring) ------------------------------


def test_build_display_results_includes_capacity_cap_note_key() -> None:
    """End-to-end through the public builder (not just the private helper),
    proving the call site is actually wired into the returned view-model."""
    run = SimpleNamespace(
        simulation_results={
            "base_risk": {"annualized_loss_expectancy": 1_000_000.0},
            "residual_risk": {"annualized_loss_expectancy": 800_000.0},
            "confidence_intervals": {"lower_bound": 0.0, "upper_bound": 0.0},
            "control_adjustments": [],
            "loss_exceedance_curve": [],
        },
        controls_snapshot=[],
        scenario_inputs_snapshot=_snapshot(
            pl={"distribution": "lognormal", "mean": _MU_A, "sigma": _SIGMA_A, "max": _CAP_A}
        ),
    )
    vm = build_display_results(run)
    assert vm is not None
    assert "capacity_cap_note" in vm
    assert vm["capacity_cap_note"]["cap_effect_frac"] == pytest.approx(0.2660100403787624, rel=1e-9)


def test_build_display_results_capacity_cap_note_none_on_legacy_run() -> None:
    """A run predating the T2/#351 snapshot column has no attribute set on
    the ORM object at all in some legacy-fixture styles; build_display_results
    must not raise and must render capacity_cap_note=None."""
    run = SimpleNamespace(
        simulation_results={
            "base_risk": {"annualized_loss_expectancy": 1_000_000.0},
            "residual_risk": {"annualized_loss_expectancy": 800_000.0},
            "confidence_intervals": {"lower_bound": 0.0, "upper_bound": 0.0},
            "control_adjustments": [],
            "loss_exceedance_curve": [],
        },
        controls_snapshot=[],
        # scenario_inputs_snapshot intentionally absent
    )
    vm = build_display_results(run)
    assert vm is not None
    assert vm["capacity_cap_note"] is None
