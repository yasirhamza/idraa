"""#130 Task 8 — perf guard: `compose_groups` runs ONCE per scenario.

`compose_groups` composes all active controls per Boolean group. It is a
deterministic, control-only computation (independent of any Monte-Carlo draw),
so it MUST be invoked exactly ONCE per `calculate_control_enhanced_risk` call —
NOT inside the per-iteration sampling loop. Calling it N times (once per MC
iteration) would multiply engine runtime by N for zero benefit (the result is
identical every iteration). This guard pins the call site outside the sampling
loop so a future refactor cannot accidentally hoist it in.

EPIC #324: re-pointed from the (removed) pyfair ``ControlAwareRiskCalculator``
to ``NativeControlAwareRiskCalculator``. The native calculator composes via
``native_control_aware.compose_groups`` for the engine ALE path (ONCE) and via
``control_attribution.compose_groups`` for the closed-form per-control D9
attribution (once per control). The call budget is unchanged:
``1 (engine ALE path) + n_controls (D9 attribution)``, never per-iteration.
"""

from __future__ import annotations

from unittest.mock import patch

from fair_cam.risk_engine import control_attribution, native_control_aware
from fair_cam.risk_engine.native_control_aware import NativeControlAwareRiskCalculator
from fair_cam.tests.risk_engine._helpers import make_control, make_fair_parameters


def _controls() -> list:
    return [
        make_control(
            control_id="prev",
            assignments=[("lec_prev_resistance", "probability", 0.7)],
        ),
        make_control(
            control_id="det",
            assignments=[
                ("lec_det_visibility", "probability", 0.9),
                ("lec_det_monitoring", "elapsed_time", 1.0),
                ("lec_det_recognition", "probability", 0.9),
            ],
        ),
        make_control(
            control_id="resp",
            assignments=[("lec_resp_resilience", "probability", 0.6)],
        ),
    ]


def _count_compose_calls(n_simulations: int) -> int:
    controls = _controls()
    calc = NativeControlAwareRiskCalculator(
        controls=controls, n_simulations=n_simulations, random_seed=7
    )
    params = make_fair_parameters(
        tef=10.0,
        vuln=0.4,
        primary=1_000_000,
        secondary=500_000,
    )
    ids = [c.control_id for c in controls]
    # Patch the name as bound in BOTH modules that invoke `compose_groups`
    # (imported `from ... import compose_groups`), wrapping the real
    # implementation so behaviour is intact. The engine ALE path composes via
    # `native_control_aware.compose_groups`; the per-control D9 attribution
    # composes via `control_attribution.compose_groups`. Counting across both
    # bindings keeps the budget assertion (1 engine + n_controls D9) intact.
    real = native_control_aware.compose_groups
    with (
        patch.object(native_control_aware, "compose_groups", side_effect=real) as spy_engine,
        patch.object(control_attribution, "compose_groups", side_effect=real) as spy_attr,
    ):
        calc.calculate_control_enhanced_risk(params, ids, scenario_name="perf-guard")
    return spy_engine.call_count + spy_attr.call_count


def test_compose_groups_call_count_independent_of_iteration_count() -> None:
    """`compose_groups` is a deterministic control-only computation, so its call
    count for ONE scenario must NOT scale with the Monte-Carlo iteration count —
    proving it is hoisted OUT of the sampling loop. (Per-control D9 attribution
    calls are bounded by control count, not iterations; the engine ALE path
    itself composes exactly once.)"""
    low = _count_compose_calls(n_simulations=500)
    high = _count_compose_calls(n_simulations=50_000)
    assert low == high, (
        f"compose_groups call count changed with iteration count "
        f"({low} @500 vs {high} @50k) — it has leaked into the MC sampling loop"
    )


def test_compose_groups_call_count_is_engine_once_plus_per_control() -> None:
    """The call budget for one scenario is exactly `1 (engine ALE path) +
    n_controls (closed-form D9 per-control attribution)` — never per-iteration.
    Pins the call site so a refactor cannot hoist composition into the loop."""
    n_controls = len(_controls())
    calls = _count_compose_calls(n_simulations=1000)
    assert calls == 1 + n_controls, (
        f"expected 1 engine-path call + {n_controls} per-control D9 calls = "
        f"{1 + n_controls}; got {calls}"
    )
