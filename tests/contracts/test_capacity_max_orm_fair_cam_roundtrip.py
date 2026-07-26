# tests/contracts/test_capacity_max_orm_fair_cam_roundtrip.py
"""Round-trip contract (PR2 capacity bound, Task 9): the per-distribution
`max` support bound survives ORM -> fair_cam DTO -> scaled residual, with an
N >= 3 mixture (CLAUDE.md data-contract-enforcement: adapter iteration
contract -- catches a future `[0]`/`[-1]`/`[first]` optimization that
silently drops components or the shared cap).

Structurally mirrors the precedent
tests/contracts/test_scenario_distribution_fit_metadata_roundtrip.py (build a
real ORM row, persist, force a reload from the DB to exercise the JSON
TypeDecorator round trip, then walk it through the production adapters) --
but for `max`, not the distribution_fit_metadata sidecar. The field-sync
framework (tests/contracts/test_orm_sme_columns_subset_of_dto_fields.py and
siblings) compares ORM column NAMES to DTO field NAMES; it cannot see a JSON
sub-key nested inside a column's dict value, so it is the wrong tool for this
property -- a dedicated round-trip test is required instead (stated here so
nobody re-tries routing this through the field-sync harness).

Chain exercised, matching the production call sequence:
  1. ORM: a persisted `Scenario.primary_loss` JSON column holding a
     `lognormal_mixture` dict with a shared top-level `max` and 3 components.
  2. -> fair_cam DTO: `run_executor._scenario_to_fair_parameters` (the real
     production read adapter, which internally calls
     `_dict_to_fair_distribution` -- Task 3) builds a `FAIRParameters` whose
     `primary_loss` is a `fair_cam` `FAIRDistribution` -- fair_cam's own
     dataclasses ARE the DTOs v3 imports (CLAUDE.md fair_cam-dependency
     section), so "DTO" and "fair_cam" are the same object here, not two
     separate hops.
  3. -> scaled residual: `FAIRParameters.apply_node_multipliers` -- the
     engine path `fair_cam.risk_engine.native_control_aware` actually calls
     to build a control-adjusted RESIDUAL `FAIRParameters` (NOT
     `FAIRParameters.scaled()`, which `fair_core.py`'s own docstring says is
     test-only/scheduled for removal). `apply_node_multipliers` scales
     `primary_loss` via `_scale_distribution`'s LOGNORMAL_MIXTURE branch,
     which multiplies the shared `max` by the same multiplier that shifts
     every component's meanlog (Task 3's scale-equivariance rationale).
"""

from __future__ import annotations

import math
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.enums import EntityStatus, ScenarioSource, ThreatCategory
from idraa.models.scenario import Scenario
from idraa.services.run_executor import _scenario_to_fair_parameters

# N=3 (not 2): the adapter-iteration contract requires N >= 3 so a
# hypothetical [0]/[-1]/[first] regression in the components rebuild is
# visible (a 2-component fixture cannot distinguish "dropped the last
# component" from "dropped everything past the first").
_MEANLOGS: tuple[float, ...] = (8.0, 10.0, 12.0)
_SIGMAS: tuple[float, ...] = (0.5, 0.6, 0.4)
_WEIGHTS: tuple[float, ...] = (0.2, 0.3, 0.5)

# Comfortably above every component's p95 (D19 floor semantics -- Task 3b):
# exp(mu_i + 1.645*sigma_i) tops out at ~314,325 for the largest-meanlog
# component (mu=12.0, sigma=0.4); 5,000,000 clears it >15x over, well clear
# of both the underflow footgun (max far BELOW the median) and the ndtr(b)
# saturation-to-1.0 regime this test does not need to probe (that is Task 2's
# job, pinned in fair_cam/tests/risk_engine/test_mixture_truncation_pin.py).
_MAX = 5_000_000.0

# An arbitrary control-driven residual scale-down. This test does not care
# about FAIR-CAM composition semantics (that is Task 2/3's job) -- only that
# `max` and every one of the N components survive plumbing through the
# adapter and out the other side of the scaling call.
_MULTIPLIER = 0.4


def _mixture_dist(max_value: float) -> dict[str, Any]:
    return {
        "distribution": "lognormal_mixture",
        "components": [
            {"mean": m, "sigma": s, "weight": w}
            for m, s, w in zip(_MEANLOGS, _SIGMAS, _WEIGHTS, strict=True)
        ],
        "max": max_value,
    }


@pytest.mark.asyncio
async def test_capacity_max_survives_orm_to_fair_cam_dto_to_scaled_residual(
    db_session: AsyncSession,
    seed_organization: Any,
    seed_user: Any,
) -> None:
    scenario = Scenario(
        organization_id=seed_organization.id,
        name="capacity-max-roundtrip",
        threat_category=ThreatCategory.RANSOMWARE,
        threat_event_frequency={"distribution": "PERT", "low": 0.5, "mode": 1.0, "high": 2.0},
        vulnerability={"distribution": "PERT", "low": 0.2, "mode": 0.4, "high": 0.6},
        primary_loss=_mixture_dist(_MAX),
        secondary_loss=None,
        source=ScenarioSource.EXPERT_JUDGMENT,
        status=EntityStatus.ACTIVE,
        version="1.0",
        created_by=seed_user.id,
    )
    db_session.add(scenario)
    await db_session.flush()
    scenario_id = scenario.id

    db_session.expunge_all()  # force a real reload -- exercises the JSON roundtrip
    reloaded = (
        await db_session.execute(select(Scenario).where(Scenario.id == scenario_id))
    ).scalar_one()

    # ---- Step 1/2: ORM -> fair_cam DTO (the real production adapter) ----
    fair_params = _scenario_to_fair_parameters(reloaded)
    pl_params = fair_params.primary_loss.parameters

    assert pl_params["max"] == _MAX
    assert len(pl_params["components"]) == 3, (
        "adapter-iteration contract: all N=3 mixture components must survive "
        f"the ORM->DTO hop; got {len(pl_params['components'])}"
    )
    for i, (m, s, w) in enumerate(zip(_MEANLOGS, _SIGMAS, _WEIGHTS, strict=True)):
        assert pl_params["components"][i]["mean"] == pytest.approx(m)
        assert pl_params["components"][i]["sigma"] == pytest.approx(s)
        assert pl_params["components"][i]["weight"] == pytest.approx(w)

    # ---- Step 3: fair_cam DTO -> scaled residual ----
    # apply_node_multipliers is the ENGINE path (native_control_aware.py) --
    # NOT FAIRParameters.scaled(), which fair_core.py's own docstring marks
    # test-only / scheduled for removal (#328).
    node_multipliers = {
        "threat_event_frequency": 1.0,
        "vulnerability": 1.0,
        "primary_loss": _MULTIPLIER,
        "secondary_loss": 1.0,
    }
    adjusted, _vuln_mult = fair_params.apply_node_multipliers(node_multipliers)
    adj_pl = adjusted.primary_loss.parameters

    # The shared cap survives -- scaled by the SAME multiplier that shifts
    # every component's meanlog (scale-equivariance, fair_core.py's
    # _scale_distribution LOGNORMAL_MIXTURE branch), not silently dropped.
    assert adj_pl["max"] == pytest.approx(_MAX * _MULTIPLIER)
    assert len(adj_pl["components"]) == 3, (
        "adapter-iteration contract: all N=3 mixture components must survive "
        f"the scaling hop too; got {len(adj_pl['components'])}"
    )
    for i, (m, s) in enumerate(zip(_MEANLOGS, _SIGMAS, strict=True)):
        assert adj_pl["components"][i]["sigma"] == pytest.approx(s)
        assert adj_pl["components"][i]["mean"] == pytest.approx(m + math.log(_MULTIPLIER))

    # b-invariance cross-check (the REASON max scales, per fair_core.py's own
    # derivation comment): the truncation boundary in standardized space is
    # unchanged by uniform scaling, per component.
    for i, (m, s) in enumerate(zip(_MEANLOGS, _SIGMAS, strict=True)):
        b_before = (math.log(_MAX) - m) / s
        b_after = (math.log(adj_pl["max"]) - adj_pl["components"][i]["mean"]) / adj_pl[
            "components"
        ][i]["sigma"]
        assert b_after == pytest.approx(b_before, rel=1e-9, abs=1e-12)
