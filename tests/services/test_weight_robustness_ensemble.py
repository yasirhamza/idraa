"""Tests for run_weight_ensemble and rank-stability metrics (issue #419, Task 3).

Covers: ranges + stability for constant-ranking value function, seed
reproducibility, budget-degradation fallback to band-endpoint envelope,
modal≠canonical stability fixture (Meth-I1/I4), and σ-sensitivity (Meth-I2).
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
import pytest
from fair_cam.models.composition_topology import KAPPA_META_RELIABILITY, BooleanGroup
from fair_cam.risk_engine.group_composition import finalize_composition, precompose_parts
from fair_cam.tests.risk_engine._helpers import make_control

from idraa.services.weight_robustness import (
    CORRELATION_GROUPS,
    band_endpoint_draws,
    canonical_param_values,
    run_weight_ensemble,
    sample_ensemble_draw,
)


def _batched(
    fn: Callable[[object], dict[str, float]],
) -> Callable[[list[object]], list[dict[str, float]]]:
    """Adapt a per-draw value fn to run_weight_ensemble's BATCHED contract
    (#419/#439): call the per-draw fn once per draw, in draw order. The runner
    now hands the value fn ALL K draws at once; production vectorizes across
    them, but for these unit fakes a per-draw list-comprehension is equivalent
    (and preserves any per-call side effects like counters/recorders in order)."""
    return lambda draws: [fn(d) for d in draws]


# Arch-I6: the runner takes rng= (not seed=); per_control_value_fn receives the
# node_mapping object the sampler returns. A trivial sampler/value-fn keeps the
# closed-form machinery out of the unit test.
def _const_value(cids: list[str]) -> Callable[[object], dict[str, float]]:
    return lambda nm: {
        c: (len(cids) - i) * 100.0 for i, c in enumerate(cids)
    }  # canonical descending


def test_ensemble_reports_ranges_and_stability():
    cids = ["a", "b", "c"]
    res = run_weight_ensemble(
        per_control_value_fn=_batched(_const_value(cids)),
        control_ids=cids,
        rng=np.random.default_rng(7),
        draws=64,
        eval_cost_per_draw=8,
        # widened contract: sampler returns an EnsembleDraw (mapping, kappa); the
        # canonical-None sentinel is legal here since the value-fn ignores the payload.
        sampler=lambda r: (None, KAPPA_META_RELIABILITY),
    )
    assert res["state"] == "ok"
    assert res["per_control"]["a"]["stability_class"] == "stable"  # constant ranking
    assert res["kendall_tau_p50"] == 1.0
    assert res["per_control"]["a"]["reduction_p50"] == 300.0
    assert res["indistinguishable_pairs"] == []


def test_ensemble_seed_reproducible():
    cids = ["a", "b"]
    counter = {"n": 0}

    def f(nm):
        counter["n"] += 1
        return {"a": counter["n"] % 2, "b": 1 - counter["n"] % 2}  # alternating, deterministic

    r1 = run_weight_ensemble(
        per_control_value_fn=_batched(f),
        control_ids=cids,
        rng=np.random.default_rng(3),
        draws=32,
        eval_cost_per_draw=4,
        sampler=lambda r: (None, KAPPA_META_RELIABILITY),
    )
    counter["n"] = 0
    r2 = run_weight_ensemble(
        per_control_value_fn=_batched(f),
        control_ids=cids,
        rng=np.random.default_rng(3),
        draws=32,
        eval_cost_per_draw=4,
        sampler=lambda r: (None, KAPPA_META_RELIABILITY),
    )
    assert r1 == r2


def test_ensemble_degrades_to_fallback_under_budget(caplog):
    # k = 5000//1000 = 5 < min_draws(32) -> insufficient_budget band-endpoint fallback,
    # NO confident verdict on noise (Arch-B1/Meth-I2). band_endpoint_draws is real,
    # so the value-fn must accept the endpoint EnsembleDraws (not raw node_mappings).
    cids = ["a"]
    res = run_weight_ensemble(
        per_control_value_fn=_batched(lambda nm: {"a": 1.0}),
        control_ids=cids,
        rng=np.random.default_rng(1),
        draws=1000,
        eval_cost_per_draw=1000,
        eval_budget=5000,
        min_draws=32,
    )
    assert res["draws_used"] == 5 and res["state"] == "insufficient_budget"
    assert res["per_control"]["a"]["stability_class"] == "not_assessed"
    assert any(
        "degrad" in r.message.lower() or "fallback" in r.message.lower() for r in caplog.records
    )


def test_stability_vs_canonical_not_modal():
    """Meth-I4: stability_class is computed against CANONICAL rank (±1), not the
    modal (most-frequent) per-draw rank.

    Fixture: 3 controls, canonical order ["a", "b", "c"] (a is canonical rank 0).
    Value function: a wins only when prevention.tef weight is very high (w > 0.95),
    which occurs in ~6% of draws at σ=1.0 (logit gap ≈ 1.56 std devs above canonical).
    In the remaining ~94% of draws, a=50 falls below b=90 and c=85, placing a at rank 2.

    Modal rank of a across draws = 2 (94% of the time).
    Canonical rank of a = 0.

    stability_class is "unstable" because it is computed vs CANONICAL rank 0:
      held_fraction = P(draw-rank ∈ {0, 1}) ≈ 6%  <<  0.90 threshold.

    If stability were (incorrectly) computed vs modal rank 2, the held fraction
    would be P(draw-rank ∈ {1, 2}) ≈ 94%, yielding a false "stable" verdict.
    This test pins the correct canonical-reference behaviour (Meth-I4).
    """

    def _modal_displaced(draw):
        """a has canonical value 300 but drops to last in ~94% of draws."""
        mapping, _kappa = draw  # widened contract: unpack the EnsembleDraw
        if mapping is None:
            return {"a": 300.0, "b": 90.0, "c": 85.0}
        w = mapping[BooleanGroup.LEC_PREVENTION].weights["threat_event_frequency"]
        if w > 0.95:  # ~6% at σ=1.0; only here does a rank first
            return {"a": 300.0, "b": 90.0, "c": 85.0}
        return {"a": 50.0, "b": 90.0, "c": 85.0}  # a last (rank 2)

    res = run_weight_ensemble(
        per_control_value_fn=_batched(_modal_displaced),
        control_ids=["a", "b", "c"],  # canonical: a > b > c
        rng=np.random.default_rng(42),
        draws=1024,
        eval_cost_per_draw=8,
        sampler=lambda r: sample_ensemble_draw(r, sigma=1.0),
    )

    ctrl_a = res["per_control"]["a"]
    # stability is "unstable": held_fraction ≈ 6% << 90% threshold (canonical rank 0)
    assert ctrl_a["stability_class"] == "unstable", (
        f"stability_class should be 'unstable' (vs canonical rank 0); got {ctrl_a!r}"
    )
    # median rank is 2 (modal rank), confirming a is displaced in most draws
    assert ctrl_a["rank_p50"] == 2, f"rank_p50 should be 2 (modal rank); got {ctrl_a['rank_p50']!r}"
    # sanity: b and c are stable (always within ±1 of their canonical ranks 1 and 2)
    assert res["per_control"]["b"]["stability_class"] == "stable"
    assert res["per_control"]["c"]["stability_class"] == "stable"


def test_sigma_sensitivity_near_tie_monotone():
    """Meth-I2 σ-sensitivity: wider σ never reduces the indistinguishable-pair set
    for a constructed near-tie pair.

    Property: for a near-tie pair (nt_a, nt_b) where nt_a's reduction scales with
    the perturbed prevention.tef weight:

      indistinguishable_pairs(σ=0.6) ⊆ indistinguishable_pairs(σ=1.0)
                                      ⊆ indistinguishable_pairs(σ=1.5)

    Specifically, the near-tie pair is:
      - Distinguishable at σ=0.6  (theoretical flip rate ≈ 3.4%,  K=1024 expected ~35 flips  <<  102 threshold)
      - Indistinguishable at σ=1.0 (theoretical flip rate ≈ 13.6%, K=1024 expected ~139 flips >>  102 threshold)
      - Indistinguishable at σ=1.5 (theoretical flip rate ≈ 23.2%, K=1024 expected ~238 flips >>  102 threshold)

    Derivation (K=1024, indistinguishable threshold = 10% × K = 102 flips):
      canonical values: nt_a = 700, nt_b = 500, c = 100.
      nt_a value at draw w: 700 × w / 0.8 = 875w.
      Flip (nt_b overtakes nt_a) when 875w < 500  →  w < 4/7 ≈ 0.5714.
      logit(0.5714) ≈ 0.2877;  logit(0.8) ≈ 1.3863;  Δ ≈ 1.099.
      P(flip | σ) = Φ(−Δ/σ):
        σ=0.6: Φ(−1.83) ≈ 3.4% → expected  35 flips, margin 4.7 std devs below threshold
        σ=1.0: Φ(−1.10) ≈ 13.6% → expected 139 flips, margin 3.4 std devs above threshold
        σ=1.5: Φ(−0.73) ≈ 23.2% → expected 238 flips, well above threshold

    This pins σ's effect on the indistinguishable-pair verdict as a reviewed property;
    a change to the default σ or canonical weights that breaks this fixture is visible
    immediately.
    """
    nt_a_canonical = 700.0  # canonical nt_a value (descending: nt_a > nt_b > c)
    nt_b_value = 500.0  # nt_b constant challenger
    w_canon = 0.8  # canonical prevention.tef weight (logit ≈ 1.386)

    def _near_tie_value(draw):
        """nt_a scales with perturbed tef weight; nt_b is constant."""
        mapping, _kappa = draw  # widened contract: unpack the EnsembleDraw
        if mapping is None:
            return {"nt_a": nt_a_canonical, "nt_b": nt_b_value, "c": 100.0}
        w = mapping[BooleanGroup.LEC_PREVENTION].weights["threat_event_frequency"]
        return {"nt_a": nt_a_canonical * w / w_canon, "nt_b": nt_b_value, "c": 100.0}

    cids = ["nt_a", "nt_b", "c"]  # canonical descending order
    k = 1024
    seed = 17

    results = {}
    for sigma in (0.6, 1.0, 1.5):
        results[sigma] = run_weight_ensemble(
            per_control_value_fn=_batched(_near_tie_value),
            control_ids=cids,
            rng=np.random.default_rng(seed),
            draws=k,
            eval_cost_per_draw=8,
            # custom sampler captures sigma so each run uses the target perturbation width;
            # the sigma= kwarg on run_weight_ensemble only affects the deterministic-envelope
            # fallback, not the ensemble draws.
            sampler=lambda r, s=sigma: sample_ensemble_draw(r, sigma=s),
        )

    nt_pair = ["nt_a", "nt_b"]

    # Narrow σ: near-tie pair is distinguishable (flip rate ≈ 3.4% << 10% threshold)
    assert nt_pair not in results[0.6]["indistinguishable_pairs"], (
        f"σ=0.6: near-tie pair should be distinguishable (theoretical ~3.4% flip rate); "
        f"got indistinguishable_pairs={results[0.6]['indistinguishable_pairs']}"
    )
    # Wider σ: near-tie pair becomes indistinguishable
    assert nt_pair in results[1.0]["indistinguishable_pairs"], (
        f"σ=1.0: near-tie pair should be indistinguishable (theoretical ~13.6% flip rate); "
        f"got indistinguishable_pairs={results[1.0]['indistinguishable_pairs']}"
    )
    assert nt_pair in results[1.5]["indistinguishable_pairs"], (
        f"σ=1.5: near-tie pair should be indistinguishable (theoretical ~23.2% flip rate); "
        f"got indistinguishable_pairs={results[1.5]['indistinguishable_pairs']}"
    )
    # Monotonicity ⊆: for this 3-control fixture (only the near-tie pair can ever cross
    # the 10% threshold — the other pairs are dominated by c=100 which is far below both),
    # the indistinguishable set at wider σ must be a superset of the narrower σ set.
    pairs_06 = {tuple(p) for p in results[0.6]["indistinguishable_pairs"]}
    pairs_10 = {tuple(p) for p in results[1.0]["indistinguishable_pairs"]}
    pairs_15 = {tuple(p) for p in results[1.5]["indistinguishable_pairs"]}
    assert pairs_06.issubset(pairs_10), (
        f"monotonicity: σ=0.6 indistinguishable set must ⊆ σ=1.0; "
        f"got σ=0.6={pairs_06}, σ=1.0={pairs_10}"
    )
    assert pairs_10.issubset(pairs_15), (
        f"monotonicity: σ=1.0 indistinguishable set must ⊆ σ=1.5; "
        f"got σ=1.0={pairs_10}, σ=1.5={pairs_15}"
    )


def test_sampled_draws_never_reintroduce_retired_meta_targets():
    """T3-Meth-5 tripwire (Slice 2 #439 D1): direct meta (VMC/DSC) node targets
    were retired — meta value flows exclusively via the kappa reliability
    coupling now. ``sample_ensemble_draw`` only perturbs WEIGHTS on existing
    (group, node) targets (via ``_apply_param_values`` writing into a deepcopy
    of ``GROUP_NODE_MAPPING``); it must never manufacture a non-empty
    ``targets`` tuple for a retired group. Guards "retired targets can't
    re-enter via ensemble overrides" — placed here (not in fair_cam/tests/)
    because fair_cam must not import from idraa (layering rule); this test
    exercises the v3-owned sampler against fair_cam's BooleanGroup topology.
    """
    retired_meta_groups = (
        BooleanGroup.VMC_VARIANCE_PREVENTION,
        BooleanGroup.VMC_IDENTIFICATION,
        BooleanGroup.VMC_CORRECTION,
        BooleanGroup.VMC_IDENTIFICATION_CORRECTION_PAIR,
        BooleanGroup.DSC_PREVENTION,
        BooleanGroup.DSC_IDENTIFICATION_CORRECTION_PAIR,
    )
    nm, _kappa = sample_ensemble_draw(np.random.default_rng(0))
    for group in retired_meta_groups:
        assert nm[group].targets == (), (
            f"{group} must have empty targets — a non-empty tuple here would mean "
            f"a retired direct meta node target re-entered via the ensemble sampler"
        )


# ---------------------------------------------------------------------------
# Slice 2 (#439) Task 4: meta.kappa canonical param + per-draw kappa
# ---------------------------------------------------------------------------


def test_kappa_in_canonical_param_values() -> None:
    vals = canonical_param_values()
    assert vals["meta.kappa"] == 0.5
    assert "vmc.vuln" not in vals  # retired with the direct channel (Slice 2 D1)


def test_correlation_groups_cover_kappa_and_guard_passes() -> None:
    all_keys = {k for keys in CORRELATION_GROUPS.values() for k in keys}
    assert "meta.kappa" in all_keys
    assert "vmc.vuln" not in all_keys


def test_correlation_group_order_pins_prevention_magnitude_draws() -> None:
    """'meta' replaced 'vmc' IN PLACE (third position) so the shared-z draw
    sequence for prevention/magnitude is unchanged across the slice
    (seeded-reproducibility guard)."""
    assert list(CORRELATION_GROUPS.keys()) == ["prevention", "magnitude", "meta"]


def test_sample_ensemble_draw_perturbs_kappa() -> None:
    rng = np.random.default_rng(7)
    draws = {sample_ensemble_draw(rng, sigma=0.6)[1] for _ in range(8)}
    assert len(draws) > 1  # actually perturbed
    assert all(0.0 < k < 1.0 for k in draws)  # logit-normal stays in (0,1)


def test_sigma_zero_reproduces_canonical_kappa() -> None:
    rng = np.random.default_rng(7)
    _, k = sample_ensemble_draw(rng, sigma=0.0)
    assert k == pytest.approx(0.5, abs=1e-12)


def test_band_endpoint_draws_bracket_kappa() -> None:
    eps = band_endpoint_draws(sigma=0.6)
    assert eps["low"][1] < eps["base"][1] < eps["high"][1]
    assert eps["base"][1] == pytest.approx(0.5, abs=1e-12)


def test_per_draw_kappa_changes_composition_via_shared_parts() -> None:
    """Cache correctness: the SAME ComposedParts finalized under two kappas
    yields different LEC effectiveness — a kappa-blind cache would not.

    Requires e_meta > 0 (a meta control present) AND an LEC opeff with r0 < 1
    (so the r_eff uplift ``r0 + (1-r0)*kappa*e_meta`` actually moves with kappa).
    """
    meta = make_control(
        control_id="meta",
        assignments=[("vmc_prev_reduce_change_freq", "percent_reduction", 0.5)],
    )
    lec = make_control(
        control_id="lec",
        assignments=[("lec_prev_resistance", "probability", 0.8)],
        reliability=0.7,  # r0 < 1 so the kappa uplift moves LEC_PREVENTION opeff
    )
    parts = precompose_parts([meta, lec])
    a = finalize_composition(parts, kappa=0.1)
    b = finalize_composition(parts, kappa=0.9)
    assert (
        a.group_effectiveness[BooleanGroup.LEC_PREVENTION]
        != b.group_effectiveness[BooleanGroup.LEC_PREVENTION]
    )


def test_insufficient_budget_envelope_carries_kappa() -> None:
    """The degraded path must reflect kappa uncertainty (plan-gate converged
    BLOCKER): drive run_weight_ensemble with a budget too small for min_draws so
    _deterministic_envelope fires, with a value-fn that RECORDS the kappa of every
    draw it receives; assert three distinct kappas (low/base/high) were seen and
    that they bracket KAPPA_META_RELIABILITY. Reuses the insufficient-budget setup
    from test_ensemble_degrades_to_fallback_under_budget (k = 5000//1000 = 5 < 32).
    """
    seen: list[float] = []

    def _recording_value_fn(draw: tuple[object, float]) -> dict[str, float]:
        _mapping, kappa = draw
        seen.append(kappa)
        return {"c1": 1000.0 * kappa}  # kappa-sensitive so ranges move too

    res = run_weight_ensemble(
        per_control_value_fn=_batched(_recording_value_fn),
        control_ids=["c1"],
        rng=np.random.default_rng(1),
        draws=1000,
        eval_cost_per_draw=1000,
        eval_budget=5000,
        min_draws=32,
        sigma=0.6,  # PINNED band width so low/base/high bracket is non-degenerate
    )
    assert res["state"] == "insufficient_budget"
    assert len(set(seen)) == 3  # low/base/high must be distinct, not collapsed
    assert sorted(set(seen))[0] < KAPPA_META_RELIABILITY < sorted(set(seen))[-1]


def test_cache_credited_two_rate_degrade_hand_math(caplog):
    """#432 item 1 — two-rate (cache-credited) K-degrade, hand-math anchored.

    E (realized full-compose cost) = 1000 evals, ratio r = 0.45:
      first_draw_cost = ceil(E x (1 + r)) = 1450
      eval_cost_per_draw (subsequent) = ceil(E x r) = 450
    Budget B = 10_000, requested K = 256:
      total(256) = 1450 + 255 x 450 = 116_200 > B  -> degrade
      K = 1 + (10_000 - 1450) // 450 = 1 + 19 = 20
    The legacy linear model on the same inputs affords only B // E = 10 draws —
    the credit doubles K here at identical spend, which is the entire point.
    """
    res = run_weight_ensemble(
        per_control_value_fn=_batched(lambda nm: {"a": 1.0}),
        control_ids=["a"],
        rng=np.random.default_rng(1),
        draws=256,
        eval_cost_per_draw=450,
        first_draw_cost=1450,
        eval_budget=10_000,
        min_draws=8,
    )
    assert res["draws_used"] == 20
    assert res["degraded"] is True
    assert any("first draw" in r.message for r in caplog.records)


def test_cache_credited_affordable_no_degrade():
    """total(5) = 1450 + 4 x 450 = 3250 <= 10_000 -> all requested draws run."""
    res = run_weight_ensemble(
        per_control_value_fn=_batched(lambda nm: {"a": 1.0}),
        control_ids=["a"],
        rng=np.random.default_rng(1),
        draws=5,
        eval_cost_per_draw=450,
        first_draw_cost=1450,
        eval_budget=10_000,
        min_draws=4,
    )
    assert res["draws_used"] == 5
    assert res["degraded"] is False


def test_cache_credited_first_draw_unaffordable_falls_back():
    """first_draw_cost 1450 > budget 1000 -> k = 0 < min_draws -> the
    insufficient_budget band-endpoint envelope, exactly like the linear path."""
    res = run_weight_ensemble(
        per_control_value_fn=_batched(lambda nm: {"a": 1.0}),
        control_ids=["a"],
        rng=np.random.default_rng(1),
        draws=256,
        eval_cost_per_draw=450,
        first_draw_cost=1450,
        eval_budget=1000,
        min_draws=8,
    )
    assert res["draws_used"] == 0
    assert res["state"] == "insufficient_budget"
    assert res["rank_stability_available"] is False
