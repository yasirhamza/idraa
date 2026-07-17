"""SHA-256 hash composition over reproducibility-relevant scenario inputs.

PR π: simplified to hash only scenario distributions + control_ids +
mc_iterations. The calibration runtime (industry/revenue_tier/IRIS year/
overlay/override pinning) is gone -- scenarios store their distributions
directly and that is what gets hashed.

Reproducibility model post-PR-π: each RiskAnalysisRun.inputs_hash is
captured at run creation against then-current scenario distributions.
Future analyst edits to the scenario row don't retroactively affect old
hashes (they're stored as immutable strings on the run row).

Pure function -- no session, no side effects.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any, Protocol


class ScenarioLike(Protocol):
    threat_event_frequency: dict[str, Any]
    vulnerability: dict[str, Any]
    primary_loss: dict[str, Any]
    secondary_loss: dict[str, Any] | None


def build_inputs_hash(
    scenario: ScenarioLike,
    control_ids: list[uuid.UUID],
    mc_iterations: int,
    random_seed: int = 42,
) -> str:
    """Return SHA-256 hex of the canonical JSON payload."""
    payload = {
        "threat_event_frequency": scenario.threat_event_frequency,
        "vulnerability": scenario.vulnerability,
        "primary_loss": scenario.primary_loss,
        "secondary_loss": scenario.secondary_loss,
        "control_ids": sorted(str(cid) for cid in control_ids),
        "mc_iterations": mc_iterations,
        "random_seed": random_seed,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_aggregate_inputs_hash(
    scenarios: list[ScenarioLike],
    control_ids: list[uuid.UUID],
    mc_iterations: int,
    random_seed: int = 42,
) -> str:
    """Stable hash for AGGREGATE runs. Order-independent across scenario_ids
    and control_ids; same scenarios in different submission orders -> same hash.
    """
    per_scenario_hashes = sorted(
        build_inputs_hash(scenario, control_ids, mc_iterations, random_seed=random_seed)
        for scenario in scenarios
    )
    payload = json.dumps(
        {
            "kind": "aggregate",
            "per_scenario_hashes": per_scenario_hashes,
            "random_seed": random_seed,
        },
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
