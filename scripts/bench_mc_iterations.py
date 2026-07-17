"""Subprocess-isolated Monte-Carlo iteration benchmark harness (Task 8, Arch-I2).

Measures wall-clock, peak RSS, and stored (compressed) bytes for the
production engine path at a given (n_simulations, n_scenarios) size, so the
numbers can authorize (or block) raising `Settings.mc_iterations_max` beyond
100k (Task 11).

Subprocess isolation is the whole point: `resource.getrusage(RUSAGE_SELF)
.ru_maxrss` is a process-LIFETIME high-water mark that never decreases, so
measuring multiple (n, m) sizes in one process would conflate their peaks and
the envelope gate would read a bogus number. `measure_in_subprocess` runs
`measure_once` in a fresh `multiprocessing` (spawn context) child and reports
that child's terminal `ru_maxrss` only.

`measure_once` mirrors the real dispatch path in
`idraa.services.run_executor._execute_run_body`:
  - FAIRParameters are built directly (in-memory, representative lognormal /
    PERT params) -- this is the same shape `_scenario_to_fair_parameters`
    produces from a Scenario row, just without needing a DB-backed Scenario.
  - `NativeControlAwareRiskCalculator.calculate_control_enhanced_risk` (m==1)
    or `.calculate_aggregate_enhanced_risk` (m>=2) -- same calls
    `_execute_run_body` makes for RunType.SINGLE / RunType.AGGREGATE.
  - `_build_results_payload` / `_build_aggregate_results_payload`,
    `split_simulation_payload`, `encode_sample_arrays_streaming` -- the same
    payload-shaping + sample-codec pipeline the executor uses before
    persisting `RunSamples`.
"""

from __future__ import annotations

import argparse
import multiprocessing
import queue
import sys
import time
from multiprocessing import Queue
from types import ModuleType
from typing import Any

from fair_cam.risk_engine.fair_core import DistributionType, FAIRDistribution, FAIRParameters
from fair_cam.risk_engine.native_control_aware import NativeControlAwareRiskCalculator

from idraa.services.run_executor import _build_aggregate_results_payload, _build_results_payload
from idraa.services.sample_codec import encode_sample_arrays_streaming
from idraa.services.simulation_payload import split_simulation_payload

# `resource` is POSIX-only, but CLAUDE.md requires the project to import cleanly
# on Windows-native sessions (and the CI matrix runs windows-latest). Peak-RSS is
# unmeasurable without it — the child worker returns 0.0 then. The explicit
# `ModuleType | None` annotation keeps the else-branch reachable to mypy (which
# would otherwise narrow `resource` to the module type on POSIX).
resource: ModuleType | None
try:
    import resource
except ImportError:  # pragma: no cover - POSIX-only; Windows-native sessions / CI matrix
    resource = None

# Default subprocess wall-clock budget (seconds). Generous — a memory-constrained
# confirmatory run (Fly/Docker) that OOM-kills the child must surface a
# diagnostic RuntimeError, not hang the parent forever (T8 review Finding 2).
_DEFAULT_SUBPROCESS_TIMEOUT_S = 600.0


# Representative lognormal/PERT params, same shape
# `run_executor._scenario_to_fair_parameters` builds from a Scenario row's
# threat_event_frequency/vulnerability/primary_loss/secondary_loss JSON
# columns (Epic B native lognormal authoring, #326) -- values chosen to be
# in a realistic cyber-loss range, not to hit any particular scenario.
def _representative_fair_params() -> FAIRParameters:
    return FAIRParameters(
        threat_event_frequency=FAIRDistribution(
            DistributionType.PERT, {"low": 2.0, "mode": 6.0, "high": 12.0}
        ),
        vulnerability=FAIRDistribution(
            DistributionType.PERT, {"low": 0.05, "mode": 0.15, "high": 0.35}
        ),
        primary_loss=FAIRDistribution(DistributionType.LOGNORMAL, {"mean": 11.5, "sigma": 0.8}),
        secondary_loss=FAIRDistribution(DistributionType.LOGNORMAL, {"mean": 10.0, "sigma": 1.0}),
    )


def measure_once(n: int, m: int) -> dict[str, float]:
    """Run ONE (n_simulations, n_scenarios) engine pass in-process and time it.

    Mirrors `run_executor._execute_run_body`'s SINGLE (m==1) / AGGREGATE
    (m>=2) branches: build FAIRParameters, call the calculator, build the
    results payload, split summary/arrays, encode the sample-array blob.
    `stored_bytes` is `len(blob)` -- the same bytes `RunSamples.arrays_codec`
    would persist.
    """
    t0 = time.perf_counter()
    calculator = NativeControlAwareRiskCalculator(controls=[], n_simulations=n, random_seed=42)

    if m == 1:
        enhanced = calculator.calculate_control_enhanced_risk(
            _representative_fair_params(), [], "bench-scenario"
        )
        results_payload = _build_results_payload(enhanced)
    else:
        per_scenario_inputs = [
            (f"bench-scenario-{i}", f"Bench Scenario {i}", _representative_fair_params())
            for i in range(m)
        ]
        aggregate = calculator.calculate_aggregate_enhanced_risk(per_scenario_inputs, [])
        results_payload = _build_aggregate_results_payload(aggregate)

    _summary, arrays = split_simulation_payload(results_payload, copy=False)
    blob = encode_sample_arrays_streaming(arrays)
    wall_s = time.perf_counter() - t0

    return {"wall_s": wall_s, "stored_bytes": float(len(blob))}


def _subprocess_worker(n: int, m: int, q: Queue[dict[str, Any]]) -> None:
    """Child-process entry point: run `measure_once`, then read THIS
    process's terminal `ru_maxrss` (its lifetime peak, since it only ever ran
    this one (n, m) size) and normalize to MB.

    macOS `ru_maxrss` is bytes; Linux reports KB (man getrusage(2)). On
    Windows (`resource is None`), peak-RSS is unmeasurable and returns 0.0.
    """
    try:
        result = measure_once(n, m)
        if resource is not None:
            ru_maxrss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            divisor = 1024.0 * 1024.0 if sys.platform == "darwin" else 1024.0
            result["peak_rss_mb"] = ru_maxrss / divisor
        else:
            # POSIX-only `resource` unavailable (Windows): peak-RSS unmeasurable.
            result["peak_rss_mb"] = 0.0
        q.put({"ok": True, **result})
    except Exception as exc:
        q.put({"ok": False, "error": f"{type(exc).__name__}: {exc}"})


def measure_in_subprocess(
    n: int, m: int, timeout_s: float = _DEFAULT_SUBPROCESS_TIMEOUT_S
) -> dict[str, float]:
    """Run `measure_once(n, m)` in a fresh spawned child process and return
    its metrics, including that child's terminal `ru_maxrss` (normalized to
    MB). Fresh-process-per-size is required -- see module docstring.

    A `timeout_s` budget guards against a child that hangs or is OOM-killed
    (SIGKILL leaves no result on the queue): on timeout, or a dead child that
    produced no result, terminate any surviving child and raise a RuntimeError
    naming the (n, m) and likely cause — so a memory-constrained confirmatory
    run surfaces a diagnostic instead of blocking the parent forever.
    """
    ctx = multiprocessing.get_context("spawn")
    q: Queue[dict[str, Any]] = ctx.Queue()
    p = ctx.Process(target=_subprocess_worker, args=(n, m, q))
    p.start()
    try:
        result = q.get(timeout=timeout_s)
    except queue.Empty:
        # No result within budget. A live child = hang; a dead child with no
        # result = crash / OOM-kill (SIGKILL cannot flush the queue).
        if p.is_alive():
            p.terminate()
            p.join(timeout=10.0)
            raise RuntimeError(
                f"measure_in_subprocess(n={n}, m={m}) timed out after {timeout_s}s "
                f"(child still alive — likely too slow for this budget)"
            ) from None
        raise RuntimeError(
            f"measure_in_subprocess(n={n}, m={m}) child died with no result "
            f"(exitcode={p.exitcode} — likely OOM-kill/SIGKILL or crash)"
        ) from None
    finally:
        if p.is_alive():
            p.terminate()
        p.join(timeout=10.0)

    if not result.get("ok", False):
        raise RuntimeError(
            f"measure_in_subprocess(n={n}, m={m}) child failed: {result.get('error')}"
        )
    del result["ok"]
    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark MC wall-clock / peak RSS / stored bytes across iteration counts."
    )
    parser.add_argument(
        "--sizes", type=str, default="10000,100000", help="Comma-separated n_simulations values."
    )
    parser.add_argument(
        "--scenarios", type=str, default="1,10", help="Comma-separated n_scenarios (m) values."
    )
    args = parser.parse_args()

    sizes = [int(s) for s in args.sizes.split(",") if s.strip()]
    scenario_counts = [int(s) for s in args.scenarios.split(",") if s.strip()]

    header = f"{'n':>10}  {'m':>4}  {'wall_s':>10}  {'peak_rss_mb':>12}  {'stored_bytes':>14}"
    print(header)
    print("-" * len(header))
    for n in sizes:
        for m in scenario_counts:
            metrics = measure_in_subprocess(n=n, m=m)
            print(
                f"{n:>10}  {m:>4}  {metrics['wall_s']:>10.2f}  "
                f"{metrics['peak_rss_mb']:>12.1f}  {int(metrics['stored_bytes']):>14}"
            )


if __name__ == "__main__":
    main()
