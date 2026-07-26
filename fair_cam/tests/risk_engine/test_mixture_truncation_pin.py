"""The LOGNORMAL_MIXTURE capped-draw pin (PR2 capacity bound, Task 2).

Implements EXACTLY the construction agreed in
``docs/superpowers/plans/PR2-REV3-CARRYOVER.md`` section "Pin construction"
(and its SIXTH/SEVENTH defeat-mode addenda). Two fixtures, three layers:

- **Fixture 1** (mu_light=ln(1e3), mu_heavy=ln(1e9), sigma=0.5 both,
  weights=(0.4, 0.6), M=1e9 = the heavy component's median): carries
  Layers A (semantics) and C (single-component identity). Its own D19
  floor is ABOVE M=1e9 (Layer-A needs M at the heavy median for the
  component-recovery-by-magnitude trick to work), so this fixture is a
  SAMPLER-level pin and must never be routed through the Task 3b
  validator.
- **Fixture 2** (mu=(ln(1e8), ln(1e9)), sigma=(0.5, 0.9) UNEQUAL,
  weights=(0.4, 0.6), M=5e9 PINNED): carries Layer B (stream replay)
  ONLY. Fixture 1 cannot carry Layer B's discriminating power for the
  SIXTH/SEVENTH defeat modes below because Phi(b_light) is float-exactly
  1.0 there (so `u * Phi(b_light) == u` byte-for-byte, masking selective
  truncation) and because equal sigma makes every b_i invariant to which
  sigma is used (masking scalar-sigma hoisting). M=5e9 sits inside the
  D19-floor-to-saturation admissible window
  ($4,394,563,744, $6,319,340,851) for these parameters -- ARMED
  (0 < Phi(b_i) < 1 strictly for every component), which the test asserts
  as its own precondition before trusting the stream-equality assertion
  built on top of it.

Seven tabled defeat modes, all killed here (see the self-review section of
this task's report for detail):
  1. ignoring `max` entirely                       -> Layer A (draws < M)
  2. conditioned-mixture semantics (reweighting)     -> Layer A (heavy
                                                         fraction ~=0.6,
                                                         not 3/7)
  3. desynced/extra rng.choice calls                 -> Layer B
  4. degenerate/zero output                          -> Layer A (magnitude
                                                         classification)
  5. routing 1-component through rng.choice           -> Layer C
  6. selective per-component truncation               -> fixture 2 Layer B
  7. scalar-sigma hoisting (blind on equal-sigma)     -> fixture 2 Layer B
     (unequal sigma)
"""

from __future__ import annotations

import math

import numpy as np
from scipy.special import ndtr, ndtri

from fair_cam.risk_engine.fair_core import DistributionType, FAIRDistribution

# ---- Fixture 1: Layers A and C ----

_F1_MU_LIGHT = math.log(1e3)
_F1_MU_HEAVY = math.log(1e9)
_F1_SIGMA = 0.5
_F1_WEIGHTS = (0.4, 0.6)
_F1_M = 1e9  # heavy component's median exactly: Phi(b_heavy) = 0.5


def _f1_components() -> list[dict[str, float]]:
    return [
        {"mean": _F1_MU_LIGHT, "sigma": _F1_SIGMA, "weight": _F1_WEIGHTS[0]},
        {"mean": _F1_MU_HEAVY, "sigma": _F1_SIGMA, "weight": _F1_WEIGHTS[1]},
    ]


def _f1_dist(*, capped: bool) -> FAIRDistribution:
    params: dict[str, object] = {"components": _f1_components()}
    if capped:
        params["max"] = _F1_M
    return FAIRDistribution(DistributionType.LOGNORMAL_MIXTURE, params)


def test_fixture1_layer_a_semantics_and_no_degenerate_output():
    """Layer A: implementation-independent semantics derived from the
    design, not the algorithm.

    - all draws strictly below M (support [0, M)) -- kills mode 1
      (ignoring `max`): with weight 0.6 on the heavy component and
      Phi(b_heavy)=0.5 exactly, an implementation that ignores `max`
      would put ~30,000 of 100,000 draws >= M (heavy draws violate M
      w.p. 0.5 each); "zero violations" is therefore a POWERFUL
      assertion here, not a coin-flip pass.
    - realized heavy fraction ~= weight (0.6), NOT the conditioned-mixture
      value 3/7 ~= 0.4286 -- kills mode 2 (conditioning the whole mixture
      on X<=M instead of truncating each component independently). The
      two hypotheses are ~110 sigma apart at n=100,000 (carryover figure).
    - every draw is unambiguously classifiable as light or heavy by
      magnitude alone, and the classes' own medians land near their
      respective component medians -- kills mode 4 (degenerate/zero
      output, which would collapse both buckets to 0 and fail this
      trivially).
    """
    n = 100_000
    dist = _f1_dist(capped=True)
    samples = dist.sample(size=n, rng=np.random.default_rng(2027))

    print(f"support: min={samples.min()!r} max={samples.max()!r} M={_F1_M!r}")
    assert samples.min() >= 0.0
    assert samples.max() < _F1_M  # strictly -- kills mode 1 (see docstring)

    threshold = 1e6
    heavy_mask = samples >= threshold
    light_mask = samples < threshold
    assert int(heavy_mask.sum() + light_mask.sum()) == n  # every draw classified

    heavy_fraction = float(heavy_mask.mean())
    conditioned_fraction = (_F1_WEIGHTS[1] * 0.5) / (_F1_WEIGHTS[0] * 1.0 + _F1_WEIGHTS[1] * 0.5)
    binomial_sigma = math.sqrt(n * _F1_WEIGHTS[1] * (1 - _F1_WEIGHTS[1])) / n
    print(
        f"realized heavy fraction: expected(per-component semantics)={_F1_WEIGHTS[1]} "
        f"actual={heavy_fraction:.6f} "
        f"vs conditioned-mixture alternative={conditioned_fraction:.6f} "
        f"(binomial sigma={binomial_sigma:.6f}, separation="
        f"{abs(_F1_WEIGHTS[1] - conditioned_fraction) / binomial_sigma:.1f} sigma)"
    )
    assert abs(heavy_fraction - _F1_WEIGHTS[1]) < 5 * binomial_sigma
    assert abs(heavy_fraction - conditioned_fraction) > 20 * binomial_sigma

    # Not degenerate: each bucket's median lands near its own component's
    # real-space median, not at 0 or some collapsed constant.
    light_median = float(np.median(samples[light_mask]))
    heavy_median = float(np.median(samples[heavy_mask]))
    print(
        f"light bucket median: expected~=exp(mu_light)={math.exp(_F1_MU_LIGHT):,.1f} "
        f"actual={light_median:,.1f}"
    )
    print(
        f"heavy bucket median: expected~=exp(mu_heavy) truncated below={math.exp(_F1_MU_HEAVY):,.1f} "
        f"actual={heavy_median:,.1f}"
    )
    assert 100.0 < light_median < 1e5
    assert 1e7 < heavy_median < _F1_M


def test_fixture1_layer_b_stream_replay():
    """Layer B: exact array_equal against a replay of the pinned contract
    -- idx = rng.choice(n, size, p=w); ONE u = rng.random(size); then
    exp(mu[idx] + sigma[idx]*ndtri(u*Phi(b[idx]))). Catches per-component
    loops, extra rng calls, reordering (mode 3)."""
    n = 50_000
    seed = 555
    dist = _f1_dist(capped=True)
    engine_samples = dist.sample(size=n, rng=np.random.default_rng(seed))

    rng = np.random.default_rng(seed)
    means = np.array([_F1_MU_LIGHT, _F1_MU_HEAVY])
    sigmas = np.array([_F1_SIGMA, _F1_SIGMA])
    weights = np.array(_F1_WEIGHTS)
    idx = rng.choice(2, size=n, p=weights)
    u = rng.random(n)
    b = (math.log(_F1_M) - means[idx]) / sigmas[idx]
    expected = np.exp(means[idx] + sigmas[idx] * ndtri(u * ndtr(b)))

    np.testing.assert_array_equal(engine_samples, expected)


def test_fixture1_layer_c_capped_single_component_identical_to_capped_plain_lognormal():
    """Layer C: a capped 1-component mixture must be byte-identical to a
    capped plain lognormal at the same seed -- the choice-bypass amendment
    (pinned pre-PR2 for the uncapped case) must survive the capped path.
    Kills mode 5 (routing a 1-component mixture through rng.choice, which
    would desync single-SME pooling's stream)."""
    n = 10_000
    seed = 321
    single_component_mixture = FAIRDistribution(
        DistributionType.LOGNORMAL_MIXTURE,
        {
            "components": [{"mean": _F1_MU_HEAVY, "sigma": _F1_SIGMA, "weight": 1.0}],
            "max": _F1_M,
        },
    )
    capped_plain_lognormal = FAIRDistribution(
        DistributionType.LOGNORMAL,
        {"mean": _F1_MU_HEAVY, "sigma": _F1_SIGMA, "max": _F1_M},
    )

    mix_samples = single_component_mixture.sample(size=n, rng=np.random.default_rng(seed))
    plain_samples = capped_plain_lognormal.sample(size=n, rng=np.random.default_rng(seed))

    np.testing.assert_array_equal(mix_samples, plain_samples)


# ---- Fixture 2: Layer B only, unequal sigma, M pinned inside the
# D19-floor-to-saturation window ----

_F2_MU = (math.log(1e8), math.log(1e9))
_F2_SIGMA = (0.5, 0.9)  # UNEQUAL -- load-bearing, see the SEVENTH defeat mode
_F2_WEIGHTS = (0.4, 0.6)
_F2_M = 5e9  # PINNED -- do not vary (see module docstring)


def _f2_b_and_phi() -> tuple[np.ndarray, np.ndarray]:
    means = np.array(_F2_MU)
    sigmas = np.array(_F2_SIGMA)
    b = (math.log(_F2_M) - means) / sigmas
    return b, ndtr(b)


def test_fixture2_asserts_its_own_preconditions_armed_and_in_window():
    """The fixture must assert its own preconditions, not rely on prose: a
    future parameter edit that moves M out of the admissible window must
    fail LOUD here, not silently de-fang the stream-equality pin below.
    Requires 0.0 < Phi(b_i) < 1.0 STRICTLY for EVERY component -- at or
    below the D19 floor b_i<=0 hides the bug (validator would reject this
    M anyway); at or above saturation (b>~8.29) Phi(b) is float-exactly
    1.0 and selective truncation becomes byte-identical to correct."""
    b, phi = _f2_b_and_phi()
    for i in range(len(_F2_MU)):
        print(f"component[{i}]: b={b[i]!r} Phi(b)={phi[i]!r}")
        assert 0.0 < phi[i] < 1.0, (
            f"fixture 2 precondition violated for component {i}: "
            f"Phi(b)={phi[i]!r} is not strictly in (0, 1) -- M={_F2_M!r} has drifted "
            f"out of the admissible window; this pin needs the fixture repaired, "
            f"not the assertion loosened"
        )


def test_fixture2_layer_b_stream_replay_unequal_sigma():
    """Layer B with UNEQUAL sigma and M inside the D19-floor-to-saturation
    window. Kills:

    - mode 6 (selective per-component truncation): fixture 1 cannot see
      this because Phi(b_light) is float-exactly 1.0 there, so
      `u * Phi(b_light) == u` byte-for-byte and an implementation that
      skips truncating the light component is byte-identical to correct
      on fixture 1. Here Phi(b_1) != 1.0 (armed by the precondition test
      above), so `u * Phi(b_1)` byte-distinguishes from `u`.
    - mode 7 (scalar-sigma hoisting): with equal sigma (fixture 1), b_i is
      invariant to which sigma is used, so hoisting a single scalar sigma
      is byte-identical to correct on BOTH equal-sigma fixtures. Unequal
      sigma here makes the two components' b_i differ depending on which
      sigma is (mis)used, so a hoisting bug changes the stream output.
    """
    # Precondition, asserted again here (not just relied on from the
    # dedicated precondition test above) so this test alone is a complete
    # pin if run in isolation.
    b_check, phi_check = _f2_b_and_phi()
    assert np.all((phi_check > 0.0) & (phi_check < 1.0))

    n = 50_000
    seed = 909
    components = [
        {"mean": _F2_MU[0], "sigma": _F2_SIGMA[0], "weight": _F2_WEIGHTS[0]},
        {"mean": _F2_MU[1], "sigma": _F2_SIGMA[1], "weight": _F2_WEIGHTS[1]},
    ]
    dist = FAIRDistribution(
        DistributionType.LOGNORMAL_MIXTURE, {"components": components, "max": _F2_M}
    )
    engine_samples = dist.sample(size=n, rng=np.random.default_rng(seed))

    rng = np.random.default_rng(seed)
    means = np.array(_F2_MU)
    sigmas = np.array(_F2_SIGMA)
    weight_arr = np.array(_F2_WEIGHTS)
    idx = rng.choice(2, size=n, p=weight_arr)
    # Both components must be realized for Layer B to exercise both b_i.
    assert set(np.unique(idx).tolist()) == {0, 1}
    u = rng.random(n)
    b = (math.log(_F2_M) - means[idx]) / sigmas[idx]
    expected = np.exp(means[idx] + sigmas[idx] * ndtri(u * ndtr(b)))

    np.testing.assert_array_equal(engine_samples, expected)
