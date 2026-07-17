"""Tests for the LET formula emitter (verification_workbook_let).

Formula-string + math-equivalence tests for the pure LET emitter that builds the
per-scenario dynamic-array Monte Carlo formula. No openpyxl, no Excel — these
assert the emitted STRINGS carry the same already-reviewed FAIR math as the
explicit-row emitters in verification_workbook.py, re-expressed over array vars.

The emitted strings follow the VALIDATED SPILL RECIPE (gate-passed
``vwb_gate_v8.xlsx``):

  - BARE function names — NO ``_xlfn.``/``_xlfn._xlws.`` prefixes (xlsxwriter,
    configured with ``use_future_functions=True``, adds them; any hand-written
    ``_xlfn.`` short-circuits its auto-prefixing).
  - Every LET-bound name ``_xlpm.``-prefixed at definition AND every use (Excel's
    own LET encoding — without it Mac Excel refuses to open the file).
  - Element-wise boolean clips, NEVER ``MAX(0,arr)``/``MIN(1,arr)`` (those
    AGGREGATE the array to a scalar, collapsing the Monte Carlo).
  - Tail mean (ES) via SUMPRODUCT, NOT AVERAGEIF (range-only over a LET array).
  - 9-stat array via ``CHOOSE({1;2;3;4;5;6;7;8;9}, ...)`` (vertical, semicolons),
    NOT VSTACK.
"""

from __future__ import annotations

from typing import Any

import pytest

from idraa.services.verification_workbook_let import _invcdf, scaled_params, scenario_let_formula


def test_invcdf_pert_symmetric_uses_vose_beta_4_4() -> None:
    expr = _invcdf({"low": 0.0, "mode": 5.0, "high": 10.0}, "u")
    s = expr.replace(" ", "")
    assert "BETA.INV(u,4.0,4.0)" in s  # BARE (no _xlfn.); Vose moment-match, symmetric -> Beta(4,4)
    assert "_xlfn." not in s  # xlsxwriter adds the prefix, not us
    assert s.startswith("0.0+")
    assert "*(10.0-0.0)" in s


def test_invcdf_lognormal_logspace() -> None:
    expr = _invcdf({"distribution": "lognormal", "mean": 1.5, "sigma": 0.4}, "u")
    assert expr.replace(" ", "") == "EXP(NORM.INV(u,1.5,0.4))"  # BARE


def test_invcdf_missing_distribution_defaults_pert() -> None:
    # real wizard shape omits the "distribution" key (issue fixed in #382)
    expr = _invcdf({"low": 1.0, "mode": 3.0, "high": 6.0}, "u")
    s = expr.replace(" ", "")
    assert "BETA.INV(u," in s and "_xlfn." not in s


def test_invcdf_unsupported_distribution_raises() -> None:
    # I-SC-1: unsupported kind must raise so the assembly emits a labeled
    # "unsupported in v1 — app value" cell, never a wrong formula.
    with pytest.raises(ValueError):
        _invcdf({"distribution": "poisson", "lambda": 3.0}, "u")


def test_invcdf_mult_zero_node_collapses_to_constant_zero() -> None:
    # NTH-M1: a perfect-control (mult==0) scaled node must emit the constant 0
    # (scaled_params returns {uniform,0,0}); guards CRN-break parity with the engine.
    expr = _invcdf(scaled_params({"low": 1.0, "mode": 3.0, "high": 6.0}, 0.0), "u")
    assert expr.replace(" ", "") in ("0", "0.0", "0+u*(0-0)", "0.0+u*(0.0-0.0)")


def test_scenario_let_has_crn_and_all_stats() -> None:
    scen = {
        "threat_event_frequency": {"low": 1.0, "mode": 3.0, "high": 6.0},
        "vulnerability": {"distribution": "beta", "alpha": 2.0, "beta": 5.0},
        "primary_loss": {"low": 1000.0, "mode": 5000.0, "high": 20000.0},
        "secondary_loss": {"low": 500.0, "mode": 2000.0, "high": 8000.0},
    }
    mults = {
        "threat_event_frequency": 0.8,
        "vulnerability": 0.7,
        "primary_loss": 1.0,
        "secondary_loss": 0.9,
        "currency_subtractor_total": 1000.0,
    }
    f = scenario_let_formula(scen, mults, n=10000).replace(" ", "")
    assert f.startswith("=LET(")  # BARE (xlsxwriter adds _xlfn.LET)
    assert "_xlfn." not in f and "_xlws." not in f  # writer owns ALL function prefixing
    assert "RANDARRAY(10000,1)" in f  # internal generation at N
    # every LET-bound name is _xlpm.-prefixed (root-cause encoding); no bare def
    assert "_xlpm.u_tef" in f and "_xlpm.res_loss" in f
    # base and residual share the same uniform vars (CRN): _xlpm.u_tef used twice, etc.
    assert f.count("_xlpm.u_tef") >= 2 and f.count("_xlpm.u_vuln") >= 2
    # vuln residual is sample-level [0,1] clip of (vuln*mult) via boolean arithmetic,
    # NOT MIN(1,MAX(0,..)) (those aggregate an array) and NOT param-scaled (N-SC-3 / B-METH-1)
    assert "MIN(" not in f and "MAX(0," not in f  # no array-aggregating clips
    assert "_xlpm.vuln*0.7" in f  # residual vuln scales the SAME vuln draw
    assert ">=1)" in f  # the [0,1]-clip upper-bound term
    # SL residual double-floored with subtractor
    assert "-1000.0" in f
    # tail mean (ES) via SUMPRODUCT (NOT AVERAGEIF — range-only over a LET array)
    assert "SUMPRODUCT(" in f and "AVERAGEIF(" not in f
    # 9-stat array assembled with CHOOSE (vertical, semicolons), NOT VSTACK
    assert "CHOOSE({1;2;3;4;5;6;7;8;9}," in f


def test_scenario_let_ignores_distribution_fit_metadata_sidecar() -> None:
    # The dominant real wizard shape attaches a "distribution_fit_metadata" dict
    # sidecar to PERT nodes (key-less PERT). The numeric-param guard must skip it
    # exactly as _invcdf / _dict_to_fair_distribution / sample_formula_for do —
    # otherwise a perfectly valid production scenario would fail-loud (no LET).
    scen = {
        "threat_event_frequency": {"low": 1.0, "mode": 3.0, "high": 6.0},
        "vulnerability": {"distribution": "beta", "alpha": 2.0, "beta": 5.0},
        "primary_loss": {
            "low": 1000.0,
            "mode": 5000.0,
            "high": 20000.0,
            "distribution_fit_metadata": {"source": "wizard"},
        },
        "secondary_loss": {"low": 500.0, "mode": 2000.0, "high": 8000.0},
    }
    mults = {
        "threat_event_frequency": 1.0,
        "vulnerability": 1.0,
        "primary_loss": 1.0,
        "secondary_loss": 1.0,
        "currency_subtractor_total": 0.0,
    }
    f = scenario_let_formula(scen, mults, n=10000)  # must NOT raise
    assert f.startswith("=LET(")
    # the sidecar is not interpolated into the formula anywhere.
    assert "distribution_fit_metadata" not in f and "wizard" not in f


def test_sec_n1_non_numeric_param_on_sidecar_bearing_node_still_raises() -> None:
    """Sec-N1 guard: a node that carries the ``distribution_fit_metadata`` sidecar
    must STILL fail-loud if a genuine distribution PARAMETER is non-numeric.

    The ``_NON_PARAM_DIST_KEYS`` allowlist exempts the sidecar from the numeric
    check so the dominant {low, mode, high, distribution_fit_metadata} shape passes
    — but the allowlist must NEVER widen to swallow a real param. A string ``low``
    riding alongside a clean sidecar must raise TypeError (a string must never reach
    a formula); pinning this stops a silent allowlist regression. The companion
    assertion confirms the SAME node with a clean numeric param + sidecar does NOT
    raise (the exemption itself still works).
    """
    # (a) clean sidecar-bearing node -> no raise (exemption works).
    clean_scen = {
        "threat_event_frequency": {"low": 1.0, "mode": 3.0, "high": 6.0},
        "vulnerability": {"distribution": "beta", "alpha": 2.0, "beta": 5.0},
        "primary_loss": {
            "low": 1.0,
            "mode": 3.0,
            "high": 6.0,
            "distribution_fit_metadata": {"source": "wizard"},
        },
        "secondary_loss": {"low": 500.0, "mode": 2000.0, "high": 8000.0},
    }
    mults = {
        "threat_event_frequency": 1.0,
        "vulnerability": 1.0,
        "primary_loss": 1.0,
        "secondary_loss": 1.0,
        "currency_subtractor_total": 0.0,
    }
    scenario_let_formula(clean_scen, mults, n=1000)  # must NOT raise

    # (b) SAME sidecar-bearing node, but "low" is a string -> MUST raise TypeError.
    bad_scen = dict(clean_scen)
    bad_scen["primary_loss"] = {
        "low": "NOT_A_NUMBER",
        "mode": 3.0,
        "high": 6.0,
        "distribution_fit_metadata": {"source": "wizard"},
    }
    with pytest.raises(TypeError):
        scenario_let_formula(bad_scen, mults, n=1000)


def _valid_scen_and_mults() -> tuple[dict[str, Any], dict[str, Any]]:
    """A minimal valid (scen, mults) pair for mutating in fail-loud tests."""
    scen = {
        "threat_event_frequency": {"low": 1.0, "mode": 3.0, "high": 6.0},
        "vulnerability": {"distribution": "beta", "alpha": 2.0, "beta": 5.0},
        "primary_loss": {"low": 1000.0, "mode": 5000.0, "high": 20000.0},
        "secondary_loss": {"low": 500.0, "mode": 2000.0, "high": 8000.0},
    }
    mults = {
        "threat_event_frequency": 0.8,
        "vulnerability": 0.7,
        "primary_loss": 1.0,
        "secondary_loss": 0.9,
        "currency_subtractor_total": 1000.0,
    }
    return scen, mults


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -0.5])
def test_scenario_let_rejects_non_finite_or_negative_vuln_mult(bad: float) -> None:
    """Methodology parity (fair_core.py:412-424): the sample-level vulnerability
    multiplier never passes through scaled_params, so it gets its own finite +
    non-negative guard — otherwise nan/inf/negative would emit a #NUM!/NaN
    residual column silently (PR #306/#307 non-finite-reaches-distribution class).
    """
    scen, mults = _valid_scen_and_mults()
    mults["vulnerability"] = bad
    with pytest.raises(ValueError):
        scenario_let_formula(scen, mults, n=10000)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -1000.0])
def test_scenario_let_rejects_non_finite_or_negative_subtractor(bad: float) -> None:
    """Methodology parity (fair_core.py:412-424): the sample-level currency
    subtractor never passes through scaled_params, so it gets its own finite +
    non-negative guard — otherwise nan/inf/negative would emit a #NUM!/NaN
    residual column silently (PR #306/#307 non-finite-reaches-distribution class).
    """
    scen, mults = _valid_scen_and_mults()
    mults["currency_subtractor_total"] = bad
    with pytest.raises(ValueError):
        scenario_let_formula(scen, mults, n=10000)


def test_scenario_let_no_xlfn_and_all_let_names_xlpm_prefixed() -> None:
    """Regression: the emitted string carries ZERO writer-prefix literals and
    every LET-bound name (defs + uses) is ``_xlpm.``-prefixed.

    The two root-cause bugs of the superseded encoding were (a) hand-written
    ``_xlfn.`` short-circuiting xlsxwriter's auto-prefixing, and (b) un-prefixed
    LET params making Mac Excel refuse to open the file. This pins both away.
    """
    import re

    scen = {
        "threat_event_frequency": {"low": 1.0, "mode": 3.0, "high": 6.0},
        "vulnerability": {"distribution": "beta", "alpha": 2.0, "beta": 5.0},
        "primary_loss": {"low": 1000.0, "mode": 5000.0, "high": 20000.0},
        "secondary_loss": {"low": 500.0, "mode": 2000.0, "high": 8000.0},
    }
    mults = {
        "threat_event_frequency": 0.8,
        "vulnerability": 0.7,
        "primary_loss": 1.0,
        "secondary_loss": 0.9,
        "currency_subtractor_total": 1000.0,
    }
    f = scenario_let_formula(scen, mults, n=10000)

    # (a) NO writer-prefix literal anywhere.
    assert "_xlfn." not in f and "_xlws." not in f

    # (b) Every LET-bound name appears ONLY with the _xlpm. prefix. The 15 names
    # are the four uniform draws + every intermediate value. Scan for any bare
    # occurrence (a token boundary not preceded by "_xlpm.").
    let_names = (
        "u_tef",
        "u_vuln",
        "u_pl",
        "u_sl",
        "tef",
        "vuln",
        "pl",
        "sl",
        "base_loss",
        "tef_r",
        "sl_raw",
        "pl_r",
        "vuln_r",
        "sl_r",
        "res_loss",
    )
    for name in let_names:
        # match the name as a whole token (Excel name chars: letters/digits/_/.)
        for m in re.finditer(rf"(?<![A-Za-z0-9_.]){re.escape(name)}(?![A-Za-z0-9_])", f):
            start = m.start()
            preceding = f[max(0, start - 6) : start]
            assert preceding.endswith("_xlpm."), (
                f"bare LET name {name!r} at index {start} not _xlpm.-prefixed "
                f"(context: ...{f[max(0, start - 12) : start + len(name) + 4]}...)"
            )


# --- scaled_params (param-level residual scaling; MOVED here from --------------
# verification_workbook.py in the spill redesign, ported with its tests in Task 7) -
import math  # noqa: E402


def test_scaled_params_pert_scales_low_mode_high() -> None:
    # _scale_distribution fair_core.py:303-308 PERT/triangular scale low/mode/high
    out = scaled_params({"distribution": "pert", "low": 1.0, "mode": 2.0, "high": 4.0}, 0.5)
    assert out == {"distribution": "pert", "low": 0.5, "mode": 1.0, "high": 2.0}


def test_scaled_params_triangular_scales_low_mode_high() -> None:
    out = scaled_params({"distribution": "triangular", "low": 2.0, "mode": 4.0, "high": 8.0}, 2.0)
    assert out == {"distribution": "triangular", "low": 4.0, "mode": 8.0, "high": 16.0}


def test_scaled_params_uniform_scales_low_high() -> None:
    out = scaled_params({"distribution": "uniform", "low": 3.0, "high": 9.0}, 2.0)
    assert out == {"distribution": "uniform", "low": 6.0, "high": 18.0}


def test_scaled_params_lognormal_log_shift_sign() -> None:
    # SC-N2 SIGN test, hand-math pinned. _scale_distribution fair_core.py:319-325:
    #   lognormal real-space scale by k -> log-space mean += ln(k); sigma unchanged.
    # mult=0.5 (<1) must LOWER the log-space mean: ln(0.5) < 0.
    # Hand-math: 1.0 + ln(0.5) = 0.3068528194400547 -> round(_FMT=10) = 0.3068528194
    expected_mean = 1.0 + math.log(0.5)
    out = scaled_params({"distribution": "lognormal", "mean": 1.0, "sigma": 0.5}, 0.5)
    assert out["sigma"] == 0.5  # unchanged
    assert out["mean"] == round(expected_mean, 10) == 0.3068528194
    assert out["mean"] < 1.0  # reduction LOWERS the log-space mean


def test_scaled_params_mult_zero_collapses_to_constant_zero() -> None:
    # _node fair_core.py:274-275: mult == 0 -> degenerate UNIFORM {low:0, high:0}.
    # Mirrors the residual-collapse (perfect control) for tef/pl/sl param nodes.
    out = scaled_params({"distribution": "lognormal", "mean": 1.0, "sigma": 0.5}, 0.0)
    assert out == {"distribution": "uniform", "low": 0.0, "high": 0.0}
    # also collapses a PERT node
    out2 = scaled_params({"distribution": "pert", "low": 1.0, "mode": 2.0, "high": 4.0}, 0.0)
    assert out2 == {"distribution": "uniform", "low": 0.0, "high": 0.0}


def test_scaled_params_rejects_negative_mult() -> None:
    with pytest.raises(ValueError):
        scaled_params({"distribution": "uniform", "low": 1.0, "high": 2.0}, -0.5)


def test_scenario_let_null_secondary_loss_emits_engine_parity_zero() -> None:
    """Workbook-500 fix: a null secondary_loss (25 of 93 library entries; valid
    FAIR — no secondary loss) must emit, sampling SL as the engine does:
    run_executor._ZERO_SECONDARY_LOSS == degenerate constant-0 UNIFORM. The
    formula must be IDENTICAL to passing that zero-uniform explicitly."""
    base = {
        "threat_event_frequency": {"low": 1.0, "mode": 3.0, "high": 6.0},
        "vulnerability": {"low": 0.1, "mode": 0.3, "high": 0.5},
        "primary_loss": {"low": 1000.0, "mode": 5000.0, "high": 20000.0},
    }
    mults = {
        "threat_event_frequency": 0.8,
        "vulnerability": 0.7,
        "primary_loss": 1.0,
        "secondary_loss": 0.9,
        "currency_subtractor_total": 0.0,
    }
    f_null = scenario_let_formula({**base, "secondary_loss": None}, mults, n=500)
    f_zero = scenario_let_formula(
        {**base, "secondary_loss": {"distribution": "uniform", "low": 0.0, "high": 0.0}},
        mults,
        n=500,
    )
    assert f_null == f_zero
    assert f_null.startswith("=LET(")


def test_scenario_let_missing_secondary_loss_key_also_emits() -> None:
    """A snapshot MISSING the secondary_loss key entirely (defensive: same
    engine `if sl_payload` falsy treatment) emits identically to None."""
    base = {
        "threat_event_frequency": {"low": 1.0, "mode": 3.0, "high": 6.0},
        "vulnerability": {"low": 0.1, "mode": 0.3, "high": 0.5},
        "primary_loss": {"low": 1000.0, "mode": 5000.0, "high": 20000.0},
    }
    mults = {
        "threat_event_frequency": 1.0,
        "vulnerability": 1.0,
        "primary_loss": 1.0,
        "secondary_loss": 1.0,
        "currency_subtractor_total": 0.0,
    }
    f_missing = scenario_let_formula(dict(base), mults, n=500)
    f_null = scenario_let_formula({**base, "secondary_loss": None}, mults, n=500)
    assert f_missing == f_null
