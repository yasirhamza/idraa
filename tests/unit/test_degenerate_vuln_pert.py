"""Engine + create-path verification of the converter's neutral vulnerability
encoding (epic #34 P1b, Task 3).

The qualitative-register converter (Task 5) synthesizes vulnerability as a
fixed, non-derived PERT so LEF is driven purely by TEF (frequency band) —
FAIR's Loss Event Frequency = TEF x Vulnerability, and a converted register
row has no vulnerability signal to plug in. The plan pre-authorizes the
degenerate point-mass ``{"low": 1.0, "mode": 1.0, "high": 1.0}`` as the
primary encoding, with a documented fallback of ``{"low": 0.99, "mode": 1.0,
"high": 1.0}`` if the degenerate triple misbehaves anywhere on the real path.

Per the plan-gate BINDING amendment (Task 3), this check MUST drive the
RUN-EXECUTOR mapper (``_scenario_to_fair_parameters`` / the ``_dict_to_fair_
distribution`` lowercasing at run_executor.py ~L128) into the actual fair_cam
FAIREngine — NOT fair_cam's validator fed a raw uppercase dict (fair_cam's
DistributionType only recognizes lowercase "pert"; the mapper's ``.lower()``
call is exactly what makes the wizard's uppercase "PERT" JSON key value
work). It must ALSO prove the full ``ScenarioService.create()`` path (which
routes through ``validate_fair_distributions`` before any DB write) accepts
the same encoding, since Task 5's converter persists scenarios through that
exact service method (never raw ORM writes, per the plan's architecture note).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import numpy as np
import pytest
from fair_cam.risk_engine.fair_core import FAIREngine
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.enums import ThreatCategory
from idraa.models.scenario import Scenario
from idraa.schemas.scenario import ScenarioForm
from idraa.services.run_executor import _scenario_to_fair_parameters
from idraa.services.scenarios import ScenarioService

pytestmark = pytest.mark.asyncio

# The primary encoding the converter is expected to ship (spec §3 / plan
# Global Constraints). If part (a) below ever regresses, the pre-authorized
# fallback is {"low": 0.99, "mode": 1.0, "high": 1.0} — see module docstring.
NEUTRAL_VULN_PERT: dict[str, Any] = {
    "distribution": "PERT",
    "low": 1.0,
    "mode": 1.0,
    "high": 1.0,
}

# A representative, non-degenerate TEF so the LEF-vs-TEF comparison is
# meaningful (a degenerate TEF would make the assertion vacuous). Analytical
# PERT mean (Vose gamma=4): (low + 4*mode + high) / 6 = (0.5 + 8 + 5) / 6 = 2.25.
_TEF_PERT: dict[str, Any] = {"distribution": "PERT", "low": 0.5, "mode": 2.0, "high": 5.0}
_TEF_ANALYTICAL_MEAN = (0.5 + 4.0 * 2.0 + 5.0) / 6.0

_PL_PERT: dict[str, Any] = {
    "distribution": "PERT",
    "low": 10_000.0,
    "mode": 100_000.0,
    "high": 1_000_000.0,
}

_ITERATIONS = 5_000
_SEED = 20260718


# ---- (a) engine path via the run-executor mapper -----------------------------


def _build_scenario(*, vulnerability: dict[str, Any]) -> Scenario:
    """A bare (unpersisted) Scenario row — _scenario_to_fair_parameters only
    reads the four JSON distribution columns, so no DB session is needed."""
    return Scenario(
        name="degenerate-vuln-pert-probe",
        threat_category=ThreatCategory.RANSOMWARE.value,
        threat_event_frequency=_TEF_PERT,
        vulnerability=vulnerability,
        primary_loss=_PL_PERT,
        secondary_loss=None,
    )


def test_neutral_vuln_pert_through_executor_mapper_into_engine() -> None:
    """Drives Scenario -> _scenario_to_fair_parameters (the run-executor
    mapper, which internally calls _dict_to_fair_distribution and lowercases
    "PERT") -> FAIREngine.calculate_risk, exactly the path execute_run takes.
    """
    scenario = _build_scenario(vulnerability=NEUTRAL_VULN_PERT)

    fair_params = _scenario_to_fair_parameters(scenario)

    engine = FAIREngine(iterations=_ITERATIONS, random_seed=_SEED)
    result = engine.calculate_risk(fair_params)  # must not raise

    lef = result["lef_distribution"]
    assert lef.shape == (_ITERATIONS,)
    assert np.all(np.isfinite(lef)), "vulnerability-derived LEF samples must all be finite"
    assert np.all(np.isfinite(result["risk_distribution"]))

    # Neutral vulnerability (point-mass at 1.0) means LEF == TEF sample-wise
    # (lef = tef * vuln = tef * 1.0), so LEF's Monte Carlo mean should track
    # TEF's analytical PERT mean within ordinary sampling error.
    pct_diff = abs(result["lef_mean"] - _TEF_ANALYTICAL_MEAN) / _TEF_ANALYTICAL_MEAN
    assert pct_diff < 0.02, (
        f"LEF mean {result['lef_mean']} vs TEF analytical mean "
        f"{_TEF_ANALYTICAL_MEAN} differ by {pct_diff:.4%}, expected <2%"
    )


def test_neutral_vuln_pert_samples_are_point_mass_at_one() -> None:
    """Regression pin: the degenerate PERT must short-circuit to a constant
    (fair_cam's `low == high` branch), never attempt the Vose Beta-PERT
    alpha/beta computation (which would divide by (mean - low) == 0)."""
    scenario = _build_scenario(vulnerability=NEUTRAL_VULN_PERT)
    fair_params = _scenario_to_fair_parameters(scenario)

    raw_vuln_samples = fair_params.vulnerability.sample(2_000, rng=np.random.default_rng(_SEED))
    assert np.all(raw_vuln_samples == 1.0)


# ---- (b) full ScenarioService.create() path -----------------------------------

SeedOrgUser = Callable[..., Awaitable[Any]]


def _scenario_form(*, vulnerability: dict[str, Any]) -> ScenarioForm:
    return ScenarioForm(
        name="degenerate-vuln-pert-create-probe",
        threat_category=ThreatCategory.RANSOMWARE.value,
        threat_event_frequency=_TEF_PERT,
        vulnerability=vulnerability,
        primary_loss=_PL_PERT,
    )


async def test_create_path_accepts_neutral_vuln_pert(
    db_session: AsyncSession,
    seed_org_user: SeedOrgUser,
) -> None:
    """ScenarioService.create() -> validate_fair_distributions must accept the
    converter's neutral vulnerability encoding (never rejected as out-of-range
    or malformed) since Task 5 persists converted scenarios through this exact
    service method."""
    org, user = await seed_org_user(db_session)
    service = ScenarioService(db_session)

    scenario = await service.create(
        organization_id=org.id,
        form=_scenario_form(vulnerability=NEUTRAL_VULN_PERT),
        current_user=user,
    )

    assert scenario.id is not None
    assert scenario.vulnerability == NEUTRAL_VULN_PERT
