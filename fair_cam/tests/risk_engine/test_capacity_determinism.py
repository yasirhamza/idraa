"""Determinism pins for PR2 capacity bound (Task 9, gate/finalization task).

Three properties, each pinned once:

1. A CAPPED scenario (a lognormal_mixture loss field carrying `max`), run
   through the FULL engine + control-aware stack TWICE at the same seed,
   produces IDENTICAL samples across the two runs -- no hidden state (a
   module-level cache, an un-seeded fallback, iteration-order-dependent
   float summation) sneaks nondeterminism into the new truncation code path.

2. A scenario with NO capped loss field anywhere (every distribution dict
   lacks the `max` key -- the shape every pre-PR2 row has) is byte-identical
   to a manual replay of the pre-PR2 stream-consumption order, run through
   the FULL `FAIREngine.calculate_risk` (not just a single distribution's
   `.sample()` -- that narrower pin already lives in
   `test_truncation_engine_wiring.py`; this one closes the gap to the
   composed multi-node scenario the product actually runs).

3. Two DIFFERENT scenarios' streams stay INDEPENDENT via
   `SeedSequence.spawn` isolation (`native_control_aware.py`'s `_seed_seq`):
   scenario B's result does not depend on what scenario A's run consumed
   before it, in contrast to a single shared linear RNG stream where A's
   draw count would shift B's state. This is the property that fixes the
   pre-existing "identical-seed" aggregate bug and that PR2's new capped
   mixture sampler (which can consume a DIFFERENT number of draws per
   component depending on `max`) must not silently regress.
"""

from __future__ import annotations

import numpy as np

from fair_cam.risk_engine.fair_core import (
    DistributionType,
    FAIRDistribution,
    FAIREngine,
    FAIRParameters,
)
from fair_cam.risk_engine.native_control_aware import NativeControlAwareRiskCalculator

_N = 20_000

# A capped 3-component mixture (adapter-iteration N>=3 flavor carried through
# to the determinism layer too) -- `max` comfortably clears every component's
# p95 (D19 floor semantics), mirroring the fixture conventions in
# test_mixture_truncation_pin.py without reusing its exact pinned values.
_CAPPED_MIXTURE_COMPONENTS = [
    {"mean": 8.0, "sigma": 0.5, "weight": 0.3},
    {"mean": 10.0, "sigma": 0.8, "weight": 0.5},
    {"mean": 12.0, "sigma": 0.3, "weight": 0.2},
]
_CAPPED_MAX = 5_000_000.0


def _capped_mixture_dist() -> FAIRDistribution:
    return FAIRDistribution(
        DistributionType.LOGNORMAL_MIXTURE,
        {"components": _CAPPED_MIXTURE_COMPONENTS, "max": _CAPPED_MAX},
    )


def _uniform(v: float) -> FAIRDistribution:
    return FAIRDistribution(DistributionType.UNIFORM, {"low": v, "high": v})


def _capped_params() -> FAIRParameters:
    return FAIRParameters(
        threat_event_frequency=_uniform(2.0),
        vulnerability=_uniform(0.5),
        primary_loss=_capped_mixture_dist(),
        secondary_loss=_uniform(0.0),
    )


# ---- 1. same capped scenario + same seed -> identical across runs ----------


def test_capped_scenario_same_seed_identical_across_two_full_engine_runs():
    """Two SEPARATE `FAIREngine` instances at the same seed, same capped
    params, must produce bit-identical risk/loss-magnitude arrays."""
    params = _capped_params()

    engine1 = FAIREngine(iterations=_N, random_seed=4242)
    result1 = engine1.calculate_risk(params)

    engine2 = FAIREngine(iterations=_N, random_seed=4242)
    result2 = engine2.calculate_risk(params)

    np.testing.assert_array_equal(result1["risk_distribution"], result2["risk_distribution"])
    np.testing.assert_array_equal(
        result1["loss_magnitude_distribution"], result2["loss_magnitude_distribution"]
    )


def test_capped_scenario_same_seed_identical_across_two_control_aware_runs():
    """Same property one layer up: `NativeControlAwareRiskCalculator`
    (the actual production call site, base_risk + residual_risk via CRN)
    reproduces bit-identically across two independent calculator instances
    seeded alike, including the persisted `spawn_key`."""
    params = _capped_params()

    calc1 = NativeControlAwareRiskCalculator(controls=[], n_simulations=_N, random_seed=2026)
    result1 = calc1.calculate_control_enhanced_risk(params, [], "capped-scenario")

    calc2 = NativeControlAwareRiskCalculator(controls=[], n_simulations=_N, random_seed=2026)
    result2 = calc2.calculate_control_enhanced_risk(params, [], "capped-scenario")

    # Two separate assertions (not a chained `==`) -- a chained
    # `a.spawn_key == b.spawn_key == (0,)` trips gitleaks' generic-api-key
    # heuristic on the "<name>_key ==" shape; this is functionally identical.
    assert result1.spawn_key == (0,)
    assert result2.spawn_key == (0,)
    np.testing.assert_array_equal(
        result1.base_risk.simulation_results, result2.base_risk.simulation_results
    )
    np.testing.assert_array_equal(
        result1.residual_risk.simulation_results, result2.residual_risk.simulation_results
    )


# ---- 2. NO capped loss -> byte-identical to the pre-PR2 path (full engine) --


def test_uncapped_scenario_full_engine_byte_identical_to_pre_pr2_replay():
    """A full 4-node scenario where NO distribution carries `max` (the shape
    every pre-PR2 row has) must be byte-identical, at the composed
    `FAIREngine.calculate_risk` level, to a manual replay using the exact
    pre-PR2 rng-consumption order `_generate_samples` documents (tef,
    vulnerability, primary_loss, secondary_loss -- dict-literal evaluation
    order) and the exact pre-PR2 per-node sampling calls (`rng.uniform`,
    `rng.choice` + `rng.lognormal` gather). This is the FULL-scenario sibling
    of `test_truncation_engine_wiring.py`'s per-distribution byte-identity
    pins -- it closes the gap between "one distribution's .sample() is
    unchanged" and "the actual composed scenario the product runs is
    unchanged"."""
    seed = 17
    n = _N
    tef_val, vuln_val = 2.0, 0.5

    pl_components = [
        {"mean": 8.0, "sigma": 0.5, "weight": 0.3},
        {"mean": 10.0, "sigma": 0.8, "weight": 0.5},
        {"mean": 12.0, "sigma": 0.3, "weight": 0.2},
    ]
    sl_meanlog, sl_sigma = 7.0, 0.6

    params = FAIRParameters(
        threat_event_frequency=_uniform(tef_val),
        vulnerability=_uniform(vuln_val),
        primary_loss=FAIRDistribution(
            DistributionType.LOGNORMAL_MIXTURE, {"components": pl_components}
        ),
        secondary_loss=FAIRDistribution(
            DistributionType.LOGNORMAL, {"mean": sl_meanlog, "sigma": sl_sigma}
        ),
    )

    engine = FAIREngine(iterations=n, random_seed=seed)
    result = engine.calculate_risk(params)

    # Manual pre-PR2 replay: same rng, same consumption order.
    rng = np.random.default_rng(seed)
    tef = np.maximum(rng.uniform(tef_val, tef_val, n), 0)
    vulnerability = np.clip(rng.uniform(vuln_val, vuln_val, n) * 1.0, 0, 1)

    mean_arr = np.array([c["mean"] for c in pl_components])
    sigma_arr = np.array([c["sigma"] for c in pl_components])
    weight_arr = np.array([c["weight"] for c in pl_components])
    idx = rng.choice(3, size=n, p=weight_arr)
    primary_loss = np.maximum(rng.lognormal(mean_arr[idx], sigma_arr[idx], size=n), 0)

    secondary_loss = np.maximum(0.0, np.maximum(rng.lognormal(sl_meanlog, sl_sigma, n), 0.0) - 0.0)

    lef = tef * vulnerability
    loss_magnitude = primary_loss + secondary_loss
    risk = lef * loss_magnitude

    np.testing.assert_array_equal(result["risk_distribution"], risk)
    np.testing.assert_array_equal(result["loss_magnitude_distribution"], loss_magnitude)
    np.testing.assert_array_equal(result["lef_distribution"], lef)


# ---- 3. two scenarios' streams stay independent (SeedSequence.spawn) -------


def test_two_scenarios_streams_independent_via_seed_sequence_spawn():
    """Scenario B's stream must be unaffected by how many draws scenario A's
    run consumed -- the property `SeedSequence.spawn` gives (each scenario
    gets its OWN independently-seeded child stream), in contrast to a single
    shared linear RNG where A's draw count would shift B's starting state.

    Pinned by varying scenario A's shape between the two calculators (a
    2-component vs. a 4-component capped mixture -- different draw
    consumption per the mixture branch's rng.choice + rng.random(size)
    pattern) while scenario B is held byte-identical; scenario B's result
    must not move."""
    seed_root = 777

    b_params = _capped_params()

    a_small = FAIRParameters(
        threat_event_frequency=_uniform(1.0),
        vulnerability=_uniform(1.0),
        primary_loss=FAIRDistribution(
            DistributionType.LOGNORMAL_MIXTURE,
            {
                "components": [
                    {"mean": 9.0, "sigma": 0.4, "weight": 0.5},
                    {"mean": 11.0, "sigma": 0.5, "weight": 0.5},
                ]
            },
        ),
        secondary_loss=_uniform(0.0),
    )
    a_large = FAIRParameters(
        threat_event_frequency=_uniform(1.0),
        vulnerability=_uniform(1.0),
        primary_loss=FAIRDistribution(
            DistributionType.LOGNORMAL_MIXTURE,
            {
                "components": [
                    {"mean": 7.0, "sigma": 0.3, "weight": 0.25},
                    {"mean": 9.0, "sigma": 0.4, "weight": 0.25},
                    {"mean": 11.0, "sigma": 0.5, "weight": 0.25},
                    {"mean": 13.0, "sigma": 0.6, "weight": 0.25},
                ],
                "max": 8_000_000.0,
            },
        ),
        secondary_loss=FAIRDistribution(DistributionType.LOGNORMAL, {"mean": 6.0, "sigma": 0.4}),
    )

    calc1 = NativeControlAwareRiskCalculator(controls=[], n_simulations=_N, random_seed=seed_root)
    result_a1 = calc1.calculate_control_enhanced_risk(a_small, [], "A")
    result_b1 = calc1.calculate_control_enhanced_risk(b_params, [], "B")

    calc2 = NativeControlAwareRiskCalculator(controls=[], n_simulations=_N, random_seed=seed_root)
    result_a2 = calc2.calculate_control_enhanced_risk(a_large, [], "A")
    result_b2 = calc2.calculate_control_enhanced_risk(b_params, [], "B")

    # Sanity: A's own draw pattern really did differ between the two runs
    # (2-component vs 4-component mixture, and a different max) -- otherwise
    # this test would vacuously pass no matter what.
    assert not np.array_equal(
        result_a1.base_risk.simulation_results, result_a2.base_risk.simulation_results
    )

    # The property under test: B is spawned from the SAME root SeedSequence
    # at the SAME spawn index (1) in both runs, independent of what A did
    # first -- so B's result must be bit-identical across the two runs.
    # (Two separate assertions, not a chained `==` -- see the sibling test
    # above for why.)
    assert result_b1.spawn_key == (1,)
    assert result_b2.spawn_key == (1,)
    np.testing.assert_array_equal(
        result_b1.base_risk.simulation_results, result_b2.base_risk.simulation_results
    )
    np.testing.assert_array_equal(
        result_b1.residual_risk.simulation_results, result_b2.residual_risk.simulation_results
    )

    # And cross-checked directly against a fresh root SeedSequence spawned
    # ONLY for B (spawn(2)[1]), skipping A's spawn entirely -- proves B's
    # stream is what spawn-index 1 off this root always yields, not an
    # artifact of running A first.
    root = np.random.SeedSequence(seed_root)
    b_only_seed = root.spawn(2)[1]
    b_only_engine_base = FAIREngine(iterations=_N, random_seed=b_only_seed)
    b_only_result = b_only_engine_base.calculate_risk(b_params)
    np.testing.assert_array_equal(
        result_b1.base_risk.simulation_results, b_only_result["risk_distribution"]
    )
