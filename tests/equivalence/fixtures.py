"""Shared-surface equivalence fixtures: ONLY what pyfair can represent
(point-mass vulnerability as PERT{v,v,v}, PERT/normal loss, multiplicative
controls, currency subtractor in the NO-CLAMP regime c<low). Native-only
extensions (lognormal, distributional vuln, clamp-regime subtractor) are
validated by analytic property tests, NOT here. See spec §5.1.

Point-mass vuln uses PERT{v,v,v} (NOT degenerate UNIFORM) so the pyfair-side
``_vuln_to_float`` returns mode==v (mean==mode for a point mass), matching the
native engine. SL is a non-degenerate PERT so the pyfair-side ``_dist_to_dict``
does not widen it to {0,0,1}; no zero-point-mass SL on the shared surface.

Control fixtures resolve their control ids against an in-test ``ControlRegistry``
(see ``build_control_registry`` / the ``control_registry_fixture`` pytest fixture
in the harness). The controls are built with real ``fair_cam`` sub-functions:

  * ``ctrl_prevention``        — LEC Prevention (TEF×0.8 + Vuln×0.9 multiplier).
  * ``ctrl_det_response``      — Detection-gated Response (magnitude multiplier on
                                 primary/secondary loss; gated on Detection per D8).
  * ``ctrl_loss_subtractor``   — LEC Response Loss-Reduction CURRENCY subtractor in
                                 the NO-CLAMP regime (c < SL.low so max(0, SL−c) never
                                 floors — param-translation ≡ sample-level there). The
                                 CLAMP regime (c > high) is NOT tested vs pyfair; it is
                                 validated by the §5.3 analytic property test.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from fair_cam.models.control import ControlRegistry
from fair_cam.risk_engine.fair_core import (
    DistributionType,
    FAIRDistribution,
    FAIRParameters,
)
from fair_cam.tests.risk_engine._helpers import make_control

N_ITER = 100_000
SEEDS = (1, 2, 3, 4, 5, 6, 7, 8, 9, 10)
# Tolerances pinned from measured Monte-Carlo standard error (see Task-7 report).
# Mean/median converge fastest; tail metrics (p99 / es95) carry the most MC noise
# at n=100k so they get the widest band. Do NOT loosen to force green — a real
# divergence is the gate's whole point.
REL_TOL = {
    "mean": 0.01,
    "median": 0.01,
    "p90": 0.02,
    "p95": 0.03,
    "p99": 0.05,
    "var95": 0.03,
    "es95": 0.05,
}


@dataclass
class Fixture:
    name: str
    params: FAIRParameters
    control_ids: list = field(default_factory=list)
    subtractor_controls: bool = False  # marks the no-clamp subtractor fixture
    # The metrics on which native-vs-pyfair PARITY is a meaningful claim for this
    # fixture. Defaults to the full REL_TOL set. The NORMAL-loss fixture restricts
    # this to mean/median because the pyfair oracle never samples a true normal —
    # the run_executor bridge (`_dist_to_dict`, run_executor.py:279-283) approximates
    # NORMAL as a PERT bounded at mode ± 2σ. That truncated PERT preserves the mean
    # (symmetric, mode==mean) and is close on the median, but its UPPER TAIL is
    # systematically THINNER than a true normal, so p90/p95/p99/es95 diverge by a
    # deterministic +2..+5% (measured ~90+ standard errors, NOT MC noise). The
    # native engine samples a true normal via numpy `rng.normal` and is the MORE
    # faithful sampler here; forcing it to match pyfair's truncated tail would make
    # it LESS correct. The true-normal tail is independently trustworthy (stdlib
    # sampler, no custom math), so tail parity vs this lossy oracle is not asserted.
    parity_metrics: tuple = ()  # () -> all REL_TOL keys (see harness)


def _pert(low, mode, high):
    return FAIRDistribution(DistributionType.PERT, {"low": low, "mode": mode, "high": high})


def _pm(v):  # point-mass via PERT so _vuln_to_float returns v
    return FAIRDistribution(DistributionType.PERT, {"low": v, "mode": v, "high": v})


# --------------------------------------------------------------------------- #
# In-test control registry (shared by the pyfair oracle + the native calc).
# --------------------------------------------------------------------------- #

# LEC Prevention — multiplicative on TEF (w=0.8) + Vulnerability (w=0.9).
_PREVENTION_ASSIGNMENTS = [
    ("lec_prev_avoidance", "probability", 0.9),
    ("lec_prev_deterrence", "probability", 0.9),
    ("lec_prev_resistance", "probability", 0.9),
]

# Detection-gated Response — magnitude multiplier on primary/secondary loss.
# Detection must be PRESENT for the Response magnitude benefit to apply (D8).
_DETECTION_ASSIGNMENTS = [
    ("lec_det_visibility", "probability", 0.9),
    ("lec_det_monitoring", "elapsed_time", 5.0),
    ("lec_det_recognition", "probability", 0.9),
]
_RESPONSE_ASSIGNMENTS = [
    ("lec_resp_event_termination", "elapsed_time", 5.0),
    ("lec_resp_resilience", "probability", 0.8),
]

# Loss-Reduction CURRENCY subtractor. capability_value is dollars; the engine
# subtracts max(0, SL - c). c=300 stays BELOW the subtractor-fixture SL.low (500),
# so flooring never bites (no-clamp regime).
_LOSS_SUBTRACTOR_DOLLARS = 300.0
_SUBTRACTOR_ASSIGNMENTS = [
    ("lec_resp_loss_reduction", "currency", _LOSS_SUBTRACTOR_DOLLARS),
]

CTRL_PREVENTION_ID = "ctrl_prevention"
CTRL_DET_RESPONSE_ID = "ctrl_det_response"
CTRL_LOSS_SUBTRACTOR_ID = "ctrl_loss_subtractor"


def build_control_registry() -> ControlRegistry:
    """Build the in-test registry shared by the pyfair oracle and native calc."""
    reg = ControlRegistry()
    reg.register_control(
        make_control(control_id=CTRL_PREVENTION_ID, assignments=_PREVENTION_ASSIGNMENTS)
    )
    reg.register_control(
        make_control(
            control_id=CTRL_DET_RESPONSE_ID,
            assignments=_DETECTION_ASSIGNMENTS + _RESPONSE_ASSIGNMENTS,
        )
    )
    reg.register_control(
        make_control(control_id=CTRL_LOSS_SUBTRACTOR_ID, assignments=_SUBTRACTOR_ASSIGNMENTS)
    )
    return reg


def shared_surface_fixtures() -> list:
    return [
        Fixture(
            "no_control_pert",
            FAIRParameters(
                threat_event_frequency=_pert(1, 3, 6),
                vulnerability=_pm(0.4),
                primary_loss=_pert(1_000, 10_000, 50_000),
                secondary_loss=_pert(500, 2_000, 8_000),
            ),
        ),
        Fixture(
            "normal_loss",
            FAIRParameters(
                threat_event_frequency=_pert(2, 4, 8),
                vulnerability=_pm(0.5),
                primary_loss=FAIRDistribution(
                    DistributionType.NORMAL, {"mean": 20_000, "std": 3_000}
                ),
                secondary_loss=_pert(100, 400, 1_000),
            ),
            # pyfair only PERT-approximates NORMAL (lossy bridge); parity is a valid
            # claim only on the bridge-preserved mean/median. Tails diverge by design.
            parity_metrics=("mean", "median"),
        ),
        Fixture(
            "prevention_control",
            FAIRParameters(
                threat_event_frequency=_pert(1, 3, 6),
                vulnerability=_pm(0.4),
                primary_loss=_pert(1_000, 10_000, 50_000),
                secondary_loss=_pert(500, 2_000, 8_000),
            ),
            control_ids=[CTRL_PREVENTION_ID],
        ),
        Fixture(
            "detection_gated_response",
            FAIRParameters(
                threat_event_frequency=_pert(2, 4, 8),
                vulnerability=_pm(0.5),
                primary_loss=_pert(2_000, 12_000, 60_000),
                secondary_loss=_pert(500, 2_000, 8_000),
            ),
            control_ids=[CTRL_DET_RESPONSE_ID],
        ),
        Fixture(
            "loss_subtractor_no_clamp",
            FAIRParameters(
                threat_event_frequency=_pert(1, 3, 6),
                vulnerability=_pm(0.4),
                primary_loss=_pert(1_000, 10_000, 50_000),
                # SL.low (500) > subtractor (300) -> NO-CLAMP regime.
                secondary_loss=_pert(500, 2_000, 8_000),
            ),
            control_ids=[CTRL_LOSS_SUBTRACTOR_ID],
            subtractor_controls=True,
        ),
    ]


def aggregate_fixture_pair() -> list:
    """Two independent non-degenerate scenarios for the AGGREGATE parity check."""
    return [
        (
            "s1",
            "S1",
            FAIRParameters(
                threat_event_frequency=_pert(1, 2, 5),
                vulnerability=_pm(0.3),
                primary_loss=_pert(1_000, 8_000, 40_000),
                secondary_loss=_pert(200, 1_000, 5_000),
            ),
        ),
        (
            "s2",
            "S2",
            FAIRParameters(
                threat_event_frequency=_pert(2, 3, 7),
                vulnerability=_pm(0.5),
                primary_loss=_pert(2_000, 12_000, 60_000),
                secondary_loss=_pert(300, 1_500, 6_000),
            ),
        ),
    ]
