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
    LognormMixture,
    NormalTruncFit,
    NormMixture,
    PertTriple,
    combine_lognorm_trunc,
    combine_norm,
    fit_norm_trunc,
    lognormal_mixture_to_pert_approx,
    normal_mixture_to_pert_approx,
)

from idraa.services.wizard_finalize import (
    FinalizationError,
    PerFieldsetResult,
    _fit_lognorm_native,
    build_scenario_payload,
    fieldset_support,
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
    # issue #27 Task 5 (plan-gate binding amendment): single-SME pooling
    # collapses to a single-component LognormMixture by construction, so the
    # native fit lives at .pooled.components[0] rather than directly on
    # .pooled (which is now the mixture, not the fit).
    assert len(results["pl"].pooled.components) == 1
    pl_fit = results["pl"].pooled.components[0]
    assert pl_fit.sdlog == pytest.approx(expected["sigma"], rel=1e-9)
    assert pl_fit.meanlog == pytest.approx(expected["mean"], rel=1e-9)
    # Well under the sigma<=10 storage guard => this legitimate range saves.
    assert pl_fit.sdlog < 10.0
    # Sanity: the implied median is ~$224k (geometric mean of the anchors),
    # NOT the divergent fit's ~$1.
    assert 100_000 < math.exp(pl_fit.meanlog) < 500_000


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
    sigma} (NOT a PERT triple); vuln stays PERT.

    issue #27 Task 5 (deliberate rewrite): ``PerFieldsetResult.pooled`` is
    now a mixture, so a hand-built single-SME result wraps the fit in a
    single-component ``LognormMixture``/``NormMixture`` rather than passing
    the fit directly.
    """
    ln_fit = LogNormalTruncFit(meanlog=10.0, sdlog=1.2, min_support=0.0, max_support=math.inf)
    pl = PerFieldsetResult(
        pooled=LognormMixture(components=(ln_fit,), weights=(1.0,)),
        pert=PertTriple(low=1.0, mode=2.0, high=3.0),
        mode_clamp_reason=None,
        rows=[{"sme_id": None, "sme_name": "a", "low": 1, "high": 9}],
        clamp_events=[],
    )
    # vuln stays normal/PERT.
    n_fit = NormalTruncFit(mean=0.5, sd=0.1, min_support=0.0, max_support=1.0)
    vuln = PerFieldsetResult(
        pooled=NormMixture(components=(n_fit,), weights=(1.0,)),
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
    # The lognormal sidecar carries per-component log-params (single-element
    # lists for this single-SME case) + schema_version 3 + pooling_method,
    # but DROPS the mode_clamp fields (no PERT collapse on this path).
    pl_meta = payload["pl"]["distribution_fit_metadata"]
    assert pl_meta["schema_version"] == 3
    assert pl_meta["pooling_method"] == "linear_opinion_pool_v1"
    assert pl_meta["component_meanlogs"] == [10.0]
    assert pl_meta["component_sdlogs"] == [1.2]
    assert pl_meta["weights"] == [1.0]
    assert "mode_clamp_reason" not in pl_meta
    assert "mode_boundary_clamped" not in pl_meta

    # vuln unchanged: PERT triple + mode_clamp fields retained.
    assert payload["vuln"]["low"] == 0.3 and payload["vuln"]["mode"] == 0.5
    vuln_meta = payload["vuln"]["distribution_fit_metadata"]
    assert vuln_meta["schema_version"] == 3
    assert vuln_meta["pooling_method"] == "linear_opinion_pool_v1"
    assert vuln_meta["component_means"] == [0.5]
    assert vuln_meta["component_sds"] == [0.1]
    assert vuln_meta["mode_boundary_clamped"] is False
    assert vuln_meta["mode_clamp_reason"] is None
    assert "distribution" not in payload["vuln"]


# Spec-11 PR1: sidecar field check is a SET equality, not a length check, so
# future additions / drops are loud (not silent).
#
# schema_version 3 (issue #27 Task 5, true mixture pooling): the
# NATIVE-lognormal sidecar (CATASTROPHIC pl/sl, #loss-pert-overhaul) DROPS
# the mode_clamp fields (there is no PERT collapse on the native lognormal
# path); the normal/vuln sidecar still carries them (it keeps the PERT
# collapse). The retired scalar pooled_meanlog/pooled_sdlog/pooled_mean/
# pooled_sd keys are replaced 1:1 by component_meanlogs/component_sdlogs/
# component_means/component_sds LISTS, and pooling_method is a new key
# (net +1 key vs the pre-mixture schema_version 2 sidecar) -- see the Task 5
# commit message for the scalar-key reader-grep evidence.
_EXPECTED_FIELDS_LOGNORMAL = {
    "source",
    "schema_version",
    "pooling_method",
    "fitter",
    "q_low_quantile",
    "q_high_quantile",
    "component_meanlogs",
    "component_sdlogs",
    "pooled_min_support",
    "pooled_max_support",
    "n_smes",
    "sme_ids",
    "weights",
    "fitted_at",
}
_EXPECTED_FIELDS_NORMAL = (
    _EXPECTED_FIELDS_LOGNORMAL - {"component_meanlogs", "component_sdlogs"}
) | {
    "component_means",
    "component_sds",
    "mode_boundary_clamped",
    "mode_clamp_reason",
}
# Collapsed-lognormal->PERT hybrid sidecar (tef since Milestone A; capped
# pl/sl since Milestone B #loss-pert-overhaul): the lognormal fit is COLLAPSED
# to a bounded PERT, so the sidecar keeps the pooled log-params for
# provenance AND carries the two mode_clamp fields from the PERT collapse (16
# keys), unlike the native-lognormal path which has no collapse (14 keys).
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

    schema_version 3 (issue #27 Task 5), post-mixture-pooling: the
    collapsed-PERT fieldsets (tef + CAPPED pl/sl — the default) carry the
    16-key hybrid (per-component log params + mode_clamp); catastrophic
    pl/sl keep the 14-key native-lognormal sidecar; the normal fieldset
    (vuln) swaps the per-component log params for component_means/
    component_sds and retains the two mode_clamp fields (16 keys).
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
    """Milestone B + issue #27 Task 5: capped (default) collapses pl/sl to
    PERT with the 16-key hybrid sidecar (same as tef); catastrophic keeps
    native lognormal 14-key. tef/vuln identical in both."""
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


# ---------------------------------------------------------------------------
# issue #27 Task 5: true mixture pooling through wizard_finalize
# ---------------------------------------------------------------------------


def test_multi_sme_catastrophic_stores_mixture_shape() -> None:
    """The headline #27 fix: two divergent catastrophic PL experts ($1k-$10k
    vs $1M-$50M, the #343 worked example) must be pooled as a genuine
    mixture -- each expert's fit survives as its own component, weighted
    equally -- NOT parameter-averaged into a single distribution covering
    neither expert's stated range."""
    state = _state_with(
        {
            "tef": [{"sme_id": "00000000-0000-0000-0000-000000000001", "low": 1.0, "high": 12.0}],
            "vuln": [{"sme_id": "00000000-0000-0000-0000-000000000001", "low": 0.05, "high": 0.5}],
            "pl": [
                {"sme_id": "00000000-0000-0000-0000-000000000001", "low": 1_000, "high": 10_000},
                {
                    "sme_id": "00000000-0000-0000-0000-000000000002",
                    "low": 1_000_000,
                    "high": 50_000_000,
                },
            ],
        }
    )
    state.loss_shape = "catastrophic"
    payload = build_scenario_payload(process_sme_estimates(state), state)

    pl = payload["pl"]
    assert pl["distribution"] == "lognormal_mixture"
    assert len(pl["components"]) == 2

    expected_a = _fit_lognorm_native(1_000.0, 10_000.0, **fieldset_support("pl"))
    expected_b = _fit_lognorm_native(1_000_000.0, 50_000_000.0, **fieldset_support("pl"))
    expected_components = [
        {"mean": expected_a.meanlog, "sigma": expected_a.sdlog, "weight": 0.5},
        {"mean": expected_b.meanlog, "sigma": expected_b.sdlog, "weight": 0.5},
    ]
    print(f"pl components: expected={expected_components}")
    print(f"pl components: actual  ={pl['components']}")
    assert pl["components"] == [
        {
            "mean": pytest.approx(expected_a.meanlog),
            "sigma": pytest.approx(expected_a.sdlog),
            "weight": 0.5,
        },
        {
            "mean": pytest.approx(expected_b.meanlog),
            "sigma": pytest.approx(expected_b.sdlog),
            "weight": 0.5,
        },
    ]

    meta = pl["distribution_fit_metadata"]
    assert meta["component_meanlogs"] == pytest.approx([expected_a.meanlog, expected_b.meanlog])
    assert meta["component_sdlogs"] == pytest.approx([expected_a.sdlog, expected_b.sdlog])
    assert meta["weights"] == pytest.approx([0.5, 0.5])
    assert meta["n_smes"] == 2


# Pre-change golden for this EXACT single-SME catastrophic-PL scenario,
# captured from commit 12d8454 (the parent of Task 1, i.e. BEFORE any
# mixture-pooling code landed) via a throwaway `uv sync`'d worktree +
# scratchpad/golden-12d8454/capture_golden.py:
#
#   git worktree add <scratch>/golden-12d8454 12d8454
#   cd <scratch>/golden-12d8454 && uv sync --extra dev
#   SESSION_SECRET=<random> uv run python capture_golden.py
#
# The identity-pin SCOPE (Task 5 binding amendment) is the DISTRIBUTION
# DICT ONLY (distribution/mean/sigma) -- the distribution_fit_metadata
# sidecar intentionally changes for EVERYONE post-mixture (schema_version 3,
# pooling_method, per-component lists), so it is excluded from this pin.
_GOLDEN_SINGLE_SME_CATASTROPHIC_PL = {
    "distribution": "lognormal",
    "mean": 12.31764442118728,
    "sigma": 3.288979063888916,
}


def test_single_sme_catastrophic_byte_identical_to_pre_mixture_golden() -> None:
    """issue #27 Task 5 identity pin: single-SME pooling is the DOMINANT
    production case and must be byte-identical to the pre-mixture-change
    output -- combine_lognorm_trunc's single-fit mixture collapses back to
    the same native {mean, sigma} it always stored."""
    state = _state_with(
        {
            "tef": [{"sme_id": "00000000-0000-0000-0000-000000000001", "low": 5, "high": 50}],
            "vuln": [{"sme_id": "00000000-0000-0000-0000-000000000001", "low": 0.1, "high": 0.5}],
            "pl": [
                {
                    "sme_id": "00000000-0000-0000-0000-000000000001",
                    "low": 1000,
                    "high": 50_000_000,
                }
            ],
        }
    )
    state.loss_shape = "catastrophic"
    payload = build_scenario_payload(process_sme_estimates(state), state)
    actual = {k: v for k, v in payload["pl"].items() if k != "distribution_fit_metadata"}

    print(f"expected (golden, commit 12d8454, pre-mixture): {_GOLDEN_SINGLE_SME_CATASTROPHIC_PL}")
    print(f"actual   (post-mixture, this commit):            {actual}")

    assert actual == _GOLDEN_SINGLE_SME_CATASTROPHIC_PL


def test_multi_sme_capped_tef_vuln_pert_matches_direct_mixture_collapse() -> None:
    """Multi-SME capped/tef/vuln PERT triples must equal calling
    combine_lognorm_trunc/combine_norm + lognormal_mixture_to_pert_approx/
    normal_mixture_to_pert_approx DIRECTLY on the same per-SME fits --
    process_sme_estimates must not silently diverge from the T2 math it
    wraps."""
    rows: dict[str, list[dict[str, Any]]] = {
        "tef": [
            {"sme_id": "00000000-0000-0000-0000-000000000001", "low": 1.0, "high": 12.0},
            {"sme_id": "00000000-0000-0000-0000-000000000002", "low": 2.0, "high": 20.0},
        ],
        "vuln": [
            {"sme_id": "00000000-0000-0000-0000-000000000001", "low": 0.05, "high": 0.4},
            {"sme_id": "00000000-0000-0000-0000-000000000002", "low": 0.1, "high": 0.6},
        ],
        "pl": [
            {"sme_id": "00000000-0000-0000-0000-000000000001", "low": 10_000, "high": 200_000},
            {"sme_id": "00000000-0000-0000-0000-000000000002", "low": 20_000, "high": 500_000},
        ],
    }
    state = _state_with(dict(rows))  # capped default -- both tef and pl collapse to PERT
    results = process_sme_estimates(state)

    for fs in ("tef", "pl"):
        fits = [_fit_lognorm_native(r["low"], r["high"], **fieldset_support(fs)) for r in rows[fs]]
        mix = combine_lognorm_trunc(fits)
        expected_pert, expected_reason = lognormal_mixture_to_pert_approx(mix)
        actual_pert = results[fs].pert
        print(f"{fs}: expected PERT={expected_pert} actual PERT={actual_pert}")
        assert actual_pert == expected_pert
        assert results[fs].mode_clamp_reason == expected_reason

    vuln_fits = [
        fit_norm_trunc(r["low"], r["high"], **fieldset_support("vuln")) for r in rows["vuln"]
    ]
    vuln_mix = combine_norm(vuln_fits)
    expected_pert, expected_reason = normal_mixture_to_pert_approx(vuln_mix)
    actual_pert = results["vuln"].pert
    print(f"vuln: expected PERT={expected_pert} actual PERT={actual_pert}")
    assert actual_pert == expected_pert
    assert results["vuln"].mode_clamp_reason == expected_reason


def test_multi_sme_metadata_pins_schema_version_pooling_method_real_weights() -> None:
    """issue #27 Task 5 metadata pins: schema_version bumps 2->3,
    pooling_method is stamped, and the sidecar's weights are the REAL
    normalized linear-opinion-pool weights -- NOT the pre-mixture hardcoded
    ``[1.0] * n_smes`` (which for n=3 summed to 3, not a normalized 1)."""
    state = _state_with(
        {
            "tef": [{"sme_id": "00000000-0000-0000-0000-000000000001", "low": 1.0, "high": 12.0}],
            "vuln": [{"sme_id": "00000000-0000-0000-0000-000000000001", "low": 0.05, "high": 0.5}],
            "pl": [
                {"sme_id": "00000000-0000-0000-0000-000000000001", "low": 1_000, "high": 10_000},
                {"sme_id": "00000000-0000-0000-0000-000000000002", "low": 2_000, "high": 20_000},
                {"sme_id": "00000000-0000-0000-0000-000000000003", "low": 3_000, "high": 30_000},
            ],
        }
    )
    payload = build_scenario_payload(process_sme_estimates(state), state)
    meta = payload["pl"]["distribution_fit_metadata"]

    expected_weights = [1 / 3, 1 / 3, 1 / 3]
    print(f"pl weights: expected={expected_weights} actual={meta['weights']}")
    assert meta["schema_version"] == 3
    assert meta["pooling_method"] == "linear_opinion_pool_v1"
    assert meta["weights"] == pytest.approx(expected_weights)
    assert sum(meta["weights"]) == pytest.approx(1.0)
    # NOT the pre-mixture hardcoded [1.0] * n_smes (would sum to 3, not 1).
    assert meta["weights"] != [1.0, 1.0, 1.0]
