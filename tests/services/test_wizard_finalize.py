"""T5 (wizard step-3 evaluator-style): wizard_finalize pipeline tests.

Covers process_sme_estimates contract per spec section 5.2 + sidecar
shape per Spec-2/Spec-23 PR1/PR2 (set-comparison test per Spec-11 PR1).
"""

from __future__ import annotations

import math
from typing import Any

import pytest
from fair_cam.quantile_pooling import (
    LogNormalTruncFit,
    NormalTruncFit,
    PertTriple,
)

from idraa.services.wizard_finalize import (
    FinalizationError,
    PerFieldsetResult,
    build_scenario_payload,
    process_sme_estimates,
)
from idraa.services.wizard_state import WizardState


def _state_with(estimates: dict[str, list[dict[str, Any]]]) -> WizardState:
    return WizardState(
        tx_id="00000000-0000-0000-0000-000000000000",
        sme_estimates=estimates,
    )


def test_zero_smes_required_fieldset_raises() -> None:
    """MD-6: tef/vuln/pl required. Missing tef -> FinalizationError with the
    field_errors dict pointing at the offender."""
    state = _state_with(
        {
            "tef": [],
            "vuln": [{"sme_id": "00000000-0000-0000-0000-000000000001", "low": 0.1, "high": 0.5}],
            "pl": [{"sme_id": "00000000-0000-0000-0000-000000000002", "low": 1000, "high": 10000}],
        }
    )
    with pytest.raises(FinalizationError) as exc:
        process_sme_estimates(state)
    assert "tef" in exc.value.field_errors


def test_zero_smes_sl_is_optional() -> None:
    """MD-6: sl is the only optional fieldset; 0 estimates -> silently skipped."""
    state = _state_with(
        {
            "tef": [{"sme_id": "00000000-0000-0000-0000-000000000001", "low": 5, "high": 50}],
            "vuln": [{"sme_id": "00000000-0000-0000-0000-000000000001", "low": 0.1, "high": 0.5}],
            "pl": [{"sme_id": "00000000-0000-0000-0000-000000000001", "low": 1000, "high": 100000}],
            "sl": [],
        }
    )
    results = process_sme_estimates(state)
    assert "sl" not in results
    # Required fieldsets all produced results.
    for fs in ("tef", "vuln", "pl"):
        assert fs in results


def test_wide_loss_range_lognormal_fit_converges() -> None:
    """Regression for the wizard-finalize 500 (fix/wizard-finalize-lognormal-fit-convergence).

    A wide-but-legitimate PL range (p5=$1k, p95=$50M — ~4.7 orders of
    magnitude, entirely normal for cyber-loss uncertainty) MUST fit to the
    closed-form untruncated lognormal sigma (~3.29), NOT the divergent
    sigma>10 the truncated scipy fitter produced from its fixed
    ``x0=[0.01, 1.0]`` seed.

    Root cause: the wizard pooling pipeline routed native-lognormal storage
    through ``fit_lognorm_trunc`` (the truncated, optimizer-based fitter),
    exactly what ``_lognormal_native``'s docstring warns against. For large
    anchors Nelder-Mead's initial simplex (~5% of x0) is far too small to
    traverse to the true meanlog within ``maxiter``, so it stalled at a
    garbage ``meanlog~=0, sdlog~=10.76`` (implied median ~=$1). The
    ``sigma<=10`` storage guard then rejected it -> FAIRCAMValidationError
    -> uncaught 500.

    The native closed-form path (matching the form-create + import paths,
    which already use ``lognormal_from_quantiles``) converges exactly.
    """
    from fair_cam.quantile_pooling import lognormal_from_quantiles

    state = _state_with(
        {
            "tef": [{"sme_id": "00000000-0000-0000-0000-000000000001", "low": 0.1, "high": 2.0}],
            "vuln": [{"sme_id": "00000000-0000-0000-0000-000000000001", "low": 0.1, "high": 0.5}],
            "pl": [
                {"sme_id": "00000000-0000-0000-0000-000000000001", "low": 1000, "high": 50_000_000}
            ],
        }
    )
    results = process_sme_estimates(state)
    expected = lognormal_from_quantiles(1000.0, 50_000_000.0)
    assert results["pl"].pooled.sdlog == pytest.approx(expected["sigma"], rel=1e-9)
    assert results["pl"].pooled.meanlog == pytest.approx(expected["mean"], rel=1e-9)
    # Well under the sigma<=10 storage guard => this legitimate range saves.
    assert results["pl"].pooled.sdlog < 10.0
    # Sanity: the implied median is ~$224k (geometric mean of the anchors),
    # NOT the divergent fit's ~$1.
    assert 100_000 < math.exp(results["pl"].pooled.meanlog) < 500_000


def test_lognormal_pipeline_uses_native_closed_form_fitter() -> None:
    """Pin the wizard's lognormal fit to the closed-form native fitter.

    Guards against a future "consolidation" silently re-wiring the lognormal
    pipeline back to the truncated scipy fitter (``fit_lognorm_trunc``), which
    diverges for wide anchors and re-introduces the finalize-500. The native
    storage path MUST stay on the closed form (see _fit_lognorm_native).
    """
    from idraa.services.wizard_finalize import (
        _LOGNORMAL_PIPELINE,
        _fit_lognorm_native,
    )

    assert _LOGNORMAL_PIPELINE.fitter is _fit_lognorm_native


def test_dedup_uses_latest_per_sme() -> None:
    """Multiple submits from the same SME -> only the last one fits."""
    state = _state_with(
        {
            "tef": [
                {"sme_id": "00000000-0000-0000-0000-000000000001", "low": 5, "high": 50},
                {"sme_id": "00000000-0000-0000-0000-000000000001", "low": 10, "high": 100},
            ],
            "vuln": [{"sme_id": "00000000-0000-0000-0000-000000000001", "low": 0.1, "high": 0.5}],
            "pl": [{"sme_id": "00000000-0000-0000-0000-000000000001", "low": 1000, "high": 100000}],
        }
    )
    results = process_sme_estimates(state)
    assert len(results["tef"].rows) == 1
    assert results["tef"].rows[0]["high"] == 100


def _minimal_state() -> WizardState:
    return WizardState(tx_id="00000000-0000-0000-0000-000000000000")


def test_build_payload_stores_native_lognormal() -> None:
    """Epic B #326 D6: lognormal fieldsets store native {distribution, mean,
    sigma} (NOT a PERT triple); vuln stays PERT."""
    ln_fit = LogNormalTruncFit(meanlog=10.0, sdlog=1.2, min_support=0.0, max_support=math.inf)
    pl = PerFieldsetResult(
        pooled=ln_fit,
        pert=PertTriple(low=1.0, mode=2.0, high=3.0),
        mode_clamp_reason=None,
        rows=[{"sme_id": None, "sme_name": "a", "low": 1, "high": 9}],
        clamp_events=[],
    )
    # vuln stays normal/PERT.
    n_fit = NormalTruncFit(mean=0.5, sd=0.1, min_support=0.0, max_support=1.0)
    vuln = PerFieldsetResult(
        pooled=n_fit,
        pert=PertTriple(low=0.3, mode=0.5, high=0.7),
        mode_clamp_reason=None,
        rows=[{"sme_id": None, "sme_name": "a", "low": 0.3, "high": 0.7}],
        clamp_events=[],
    )
    payload = build_scenario_payload({"pl": pl, "vuln": vuln}, state=_minimal_state())

    assert payload["pl"]["distribution"] == "lognormal"
    assert payload["pl"]["mean"] == 10.0
    assert payload["pl"]["sigma"] == 1.2
    assert "low" not in payload["pl"]  # NO PERT triple on a lognormal node
    # The lognormal sidecar carries pooled log-params + the new schema_version 2,
    # but DROPS the mode_clamp fields (no PERT collapse on this path).
    pl_meta = payload["pl"]["distribution_fit_metadata"]
    assert pl_meta["schema_version"] == 2
    assert pl_meta["pooled_meanlog"] == 10.0
    assert pl_meta["pooled_sdlog"] == 1.2
    assert "mode_clamp_reason" not in pl_meta
    assert "mode_boundary_clamped" not in pl_meta

    # vuln unchanged: PERT triple + mode_clamp fields retained.
    assert payload["vuln"]["low"] == 0.3 and payload["vuln"]["mode"] == 0.5
    vuln_meta = payload["vuln"]["distribution_fit_metadata"]
    assert vuln_meta["schema_version"] == 2
    assert vuln_meta["mode_boundary_clamped"] is False
    assert vuln_meta["mode_clamp_reason"] is None
    assert "distribution" not in payload["vuln"]


# Spec-11 PR1: sidecar field check is a SET equality, not a length check, so
# future additions / drops are loud (not silent).
#
# schema_version 2 (Epic B #326 D6): the NATIVE-lognormal sidecar (now the
# CATASTROPHIC pl/sl path, #loss-pert-overhaul) DROPS the mode_clamp fields
# (there is no PERT collapse on the native lognormal path); the normal/vuln
# sidecar still carries them (it keeps the PERT collapse).
_EXPECTED_FIELDS_LOGNORMAL = {
    "source",
    "schema_version",
    "fitter",
    "q_low_quantile",
    "q_high_quantile",
    "pooled_meanlog",
    "pooled_sdlog",
    "pooled_min_support",
    "pooled_max_support",
    "n_smes",
    "sme_ids",
    "weights",
    "fitted_at",
}
_EXPECTED_FIELDS_NORMAL = (_EXPECTED_FIELDS_LOGNORMAL - {"pooled_meanlog", "pooled_sdlog"}) | {
    "pooled_mean",
    "pooled_sd",
    "mode_boundary_clamped",
    "mode_clamp_reason",
}
# Collapsed-lognormal->PERT hybrid sidecar (tef since Milestone A; capped
# pl/sl since Milestone B #loss-pert-overhaul): the lognormal fit is COLLAPSED
# to a bounded PERT, so the sidecar keeps the lognormal pooled log-params for
# provenance AND carries the two mode_clamp fields from the PERT collapse (15
# keys), unlike the native-lognormal path which has no collapse (13 keys).
_EXPECTED_FIELDS_TEF_PERT = _EXPECTED_FIELDS_LOGNORMAL | {
    "mode_boundary_clamped",
    "mode_clamp_reason",
}

_FOUR_FIELDSET_ROWS = {
    "tef": [{"sme_id": "00000000-0000-0000-0000-000000000001", "low": 5, "high": 50}],
    "vuln": [{"sme_id": "00000000-0000-0000-0000-000000000001", "low": 0.1, "high": 0.5}],
    "pl": [{"sme_id": "00000000-0000-0000-0000-000000000001", "low": 1000, "high": 100000}],
    "sl": [{"sme_id": "00000000-0000-0000-0000-000000000001", "low": 1000, "high": 50000}],
}


def test_build_payload_sidecar_keys_match_spec() -> None:
    """Spec-11 PR1: assert exact field names, not just count.

    schema_version 2, post-Milestone-B: the collapsed-PERT fieldsets (tef +
    CAPPED pl/sl — the default) carry the 15-key hybrid (log params +
    mode_clamp); catastrophic pl/sl keep the 13-key native-lognormal sidecar;
    the normal fieldset (vuln) swaps the pooled log-params for
    pooled_mean/pooled_sd and retains the two mode_clamp fields (15 keys).
    """
    state = _state_with(dict(_FOUR_FIELDSET_ROWS))  # loss_shape defaults "capped"
    payload = build_scenario_payload(process_sme_estimates(state), state)
    for fs in ("tef", "pl", "sl"):
        meta = payload[fs]["distribution_fit_metadata"]
        assert set(meta.keys()) == _EXPECTED_FIELDS_TEF_PERT, (
            f"{fs} sidecar keys mismatch: extra="
            f"{set(meta.keys()) - _EXPECTED_FIELDS_TEF_PERT}, missing="
            f"{_EXPECTED_FIELDS_TEF_PERT - set(meta.keys())}"
        )
    vuln_meta = payload["vuln"]["distribution_fit_metadata"]
    assert set(vuln_meta.keys()) == _EXPECTED_FIELDS_NORMAL, (
        f"vuln sidecar keys mismatch: extra="
        f"{set(vuln_meta.keys()) - _EXPECTED_FIELDS_NORMAL}, missing="
        f"{_EXPECTED_FIELDS_NORMAL - set(vuln_meta.keys())}"
    )

    # Catastrophic: pl/sl keep the 13-key native-lognormal sidecar.
    cat_state = _state_with(dict(_FOUR_FIELDSET_ROWS))
    cat_state.loss_shape = "catastrophic"
    payload = build_scenario_payload(process_sme_estimates(cat_state), cat_state)
    for fs in ("pl", "sl"):
        meta = payload[fs]["distribution_fit_metadata"]
        assert set(meta.keys()) == _EXPECTED_FIELDS_LOGNORMAL, (
            f"{fs} (catastrophic) sidecar keys mismatch: extra="
            f"{set(meta.keys()) - _EXPECTED_FIELDS_LOGNORMAL}, missing="
            f"{_EXPECTED_FIELDS_LOGNORMAL - set(meta.keys())}"
        )


def test_build_payload_per_node_distribution_shape() -> None:
    """Node distribution shapes post-Milestone-B: capped (default) pl collapses
    to a bounded PERT triple (like tef); catastrophic pl stays native lognormal
    {distribution, mean, sigma}; the bounded vuln node keeps its bare PERT
    triple in both states.
    """
    rows = {
        "tef": [{"sme_id": "00000000-0000-0000-0000-000000000001", "low": 5, "high": 50}],
        "vuln": [{"sme_id": "00000000-0000-0000-0000-000000000001", "low": 0.1, "high": 0.5}],
        "pl": [{"sme_id": "00000000-0000-0000-0000-000000000001", "low": 1000, "high": 100000}],
    }
    state = _state_with(dict(rows))  # capped default
    payload = build_scenario_payload(process_sme_estimates(state), state)

    # Capped pl: collapsed PERT triple with explicit distribution key; NO
    # native lognormal params. Mode may clamp to low for wide anchors, so
    # assert low <= mode (the wizard analytic mode is interior here but the
    # contract is the clamped-inclusive ordering).
    assert payload["pl"]["distribution"] == "PERT"
    for k in ("low", "mode", "high"):
        assert isinstance(payload["pl"][k], float)
    assert payload["pl"]["low"] <= payload["pl"]["mode"] < payload["pl"]["high"]
    for k in ("mean", "sigma"):
        assert k not in payload["pl"]

    # tef: collapsed to a bounded PERT triple with an explicit distribution key;
    # NO native lognormal params.
    assert payload["tef"]["distribution"] == "PERT"
    for k in ("low", "mode", "high"):
        assert isinstance(payload["tef"][k], float)
    assert payload["tef"]["low"] < payload["tef"]["mode"] < payload["tef"]["high"]
    for k in ("mean", "sigma"):
        assert k not in payload["tef"]

    # vuln: bare PERT triple, no native distribution key.
    assert "distribution" not in payload["vuln"]
    for k in ("low", "mode", "high"):
        assert k in payload["vuln"]
        assert isinstance(payload["vuln"][k], float)
    assert payload["vuln"]["low"] <= payload["vuln"]["high"]

    # Catastrophic pl: native lognormal params, NO PERT triple.
    cat_state = _state_with(dict(rows))
    cat_state.loss_shape = "catastrophic"
    payload = build_scenario_payload(process_sme_estimates(cat_state), cat_state)
    assert payload["pl"]["distribution"] == "lognormal"
    assert isinstance(payload["pl"]["mean"], float)
    assert isinstance(payload["pl"]["sigma"], float)
    assert payload["pl"]["sigma"] > 0.0
    for k in ("low", "mode", "high"):
        assert k not in payload["pl"]


def test_build_payload_loss_shape_dispatch() -> None:
    """Milestone B: capped (default) collapses pl/sl to PERT with the 15-key
    hybrid sidecar (same as tef); catastrophic keeps native lognormal 13-key.
    tef/vuln identical in both."""
    capped_state = _state_with(dict(_FOUR_FIELDSET_ROWS))  # loss_shape defaults "capped"
    payload = build_scenario_payload(process_sme_estimates(capped_state), capped_state)
    for fs in ("pl", "sl"):
        assert payload[fs]["distribution"] == "PERT"
        assert payload[fs]["low"] <= payload[fs]["mode"] < payload[fs]["high"]
        meta = payload[fs]["distribution_fit_metadata"]
        assert set(meta.keys()) == _EXPECTED_FIELDS_TEF_PERT, fs
        assert "mean" not in payload[fs] and "sigma" not in payload[fs]

    cat_state = _state_with(dict(_FOUR_FIELDSET_ROWS))
    cat_state.loss_shape = "catastrophic"
    payload = build_scenario_payload(process_sme_estimates(cat_state), cat_state)
    for fs in ("pl", "sl"):
        assert payload[fs]["distribution"] == "lognormal"
        assert set(payload[fs]["distribution_fit_metadata"].keys()) == _EXPECTED_FIELDS_LOGNORMAL

    # tef/vuln invariant across shapes.
    assert payload["tef"]["distribution"] == "PERT"
    assert "distribution" not in payload["vuln"]
