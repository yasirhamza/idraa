# tests/contracts/test_scenario_distribution_fit_metadata_roundtrip.py
"""Sidecar roundtrip: ScenarioForm -> DB -> reload preserves all
distribution_fit_metadata keys (spec §9.2 / §7.3 Spec-2 / Spec-23 PR1/PR2).

schema_version 2, post-Milestone-B (#loss-pert-overhaul): the native-lognormal
node (CATASTROPHIC pl/sl) stores native {distribution, mean, sigma} (13
sidecar keys — mode_clamp fields dropped); the vuln/PERT node keeps the 15-key
sidecar; collapsed-lognormal->PERT nodes (tef #tef-pert-revert + CAPPED pl/sl,
the default) keep the log params AND carry the two mode_clamp fields (15 keys).

The sidecar payload lives INSIDE the JSON column (threat_event_frequency /
vulnerability / etc.) so this test exercises the JSON serializer +
deserializer paths around build_scenario_payload's nested dict.

Parametrized over four cases (lognorm_native -> tef collapsed to PERT;
lognorm_native_loss -> catastrophic pl, 13-key; lognorm_collapsed_loss ->
capped pl, 15-key; norm_trunc -> vuln). Per Spec-11 PR1 the assertion uses
key-set equality so future additions / drops are loud.
"""

from __future__ import annotations

import math
from typing import Any

import pytest
from fair_cam.quantile_pooling import (
    LogNormalTruncFit,
    NormalTruncFit,
    PertTriple,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.enums import (
    AssetClass,
    EntityStatus,
    ScenarioSource,
    ScenarioType,
    ThreatActorType,
    ThreatCategory,
)
from idraa.models.scenario import Scenario
from idraa.services.wizard_finalize import PerFieldsetResult, build_scenario_payload
from idraa.services.wizard_state import WizardState

# schema_version 2: the NATIVE lognormal node (pl/sl) DROPS the mode_clamp
# fields (no PERT collapse on this path); vuln (PERT) keeps them; tef
# (#tef-pert-revert) collapses its lognormal fit to PERT so it keeps the log
# params AND regains the two mode_clamp fields (15 keys).
_LOGNORM_EXPECTED_KEYS = {
    "source",
    "schema_version",
    "fitter",
    "q_low_quantile",
    "q_high_quantile",
    "pooled_meanlog",
    "pooled_sdlog",
    "pooled_min_support",
    "pooled_max_support",
    "n_smes",
    "sme_ids",
    "weights",
    "fitted_at",
}
_TEF_PERT_EXPECTED_KEYS = _LOGNORM_EXPECTED_KEYS | {
    "mode_boundary_clamped",
    "mode_clamp_reason",
}

_NORMAL_EXPECTED_KEYS = {
    "source",
    "schema_version",
    "fitter",
    "q_low_quantile",
    "q_high_quantile",
    "pooled_mean",
    "pooled_sd",
    "pooled_min_support",
    "pooled_max_support",
    "n_smes",
    "sme_ids",
    "weights",
    "fitted_at",
    "mode_boundary_clamped",
    "mode_clamp_reason",
}


def _make_result(fitter: str, sme_id: str) -> tuple[str, PerFieldsetResult, set[str], str]:
    """Build a (fieldset, result, expected_key_set, column_name) bundle."""
    rows = [{"sme_id": sme_id, "low": 1.0, "high": 2.0}]
    if fitter == "lognorm_native":
        return (
            "tef",
            PerFieldsetResult(
                pooled=LogNormalTruncFit(
                    meanlog=0.5,
                    sdlog=0.5,
                    min_support=0.0,
                    max_support=math.inf,
                ),
                pert=PertTriple(low=1.0, mode=1.5, high=2.0),
                mode_clamp_reason=None,
                rows=rows,
                clamp_events=[],
                collapsed=True,
            ),
            _TEF_PERT_EXPECTED_KEYS,
            "threat_event_frequency",
        )
    if fitter == "lognorm_native_loss":
        # CATASTROPHIC pl/sl native-lognormal path (collapser=None,
        # collapsed=False): 13-key sidecar, native {distribution, mean, sigma}
        # node, NO PERT collapse. Guards the native-lognormal DB roundtrip.
        return (
            "pl",
            PerFieldsetResult(
                pooled=LogNormalTruncFit(
                    meanlog=11.0,
                    sdlog=0.9,
                    min_support=0.0,
                    max_support=math.inf,
                ),
                pert=PertTriple(low=1000.0, mode=20000.0, high=100000.0),
                mode_clamp_reason=None,
                rows=rows,
                clamp_events=[],
                collapsed=False,
            ),
            _LOGNORM_EXPECTED_KEYS,
            "primary_loss",
        )
    if fitter == "lognorm_collapsed_loss":
        # CAPPED pl/sl (Milestone B #loss-pert-overhaul, the default):
        # lognormal fit collapsed to PERT — 15-key hybrid sidecar, PERT node.
        return (
            "pl",
            PerFieldsetResult(
                pooled=LogNormalTruncFit(
                    meanlog=11.0,
                    sdlog=0.9,
                    min_support=0.0,
                    max_support=math.inf,
                ),
                pert=PertTriple(low=13650.0, mode=26580.0, high=263000.0),
                mode_clamp_reason=None,
                rows=rows,
                clamp_events=[],
                collapsed=True,
            ),
            _TEF_PERT_EXPECTED_KEYS,
            "primary_loss",
        )
    # norm_trunc (vuln)
    return (
        "vuln",
        PerFieldsetResult(
            pooled=NormalTruncFit(mean=0.3, sd=0.1, min_support=0.0, max_support=1.0),
            pert=PertTriple(low=0.1, mode=0.3, high=0.5),
            mode_clamp_reason=None,
            rows=rows,
            clamp_events=[],
        ),
        _NORMAL_EXPECTED_KEYS,
        "vulnerability",
    )


@pytest.mark.parametrize(
    "fitter", ["lognorm_native", "lognorm_native_loss", "lognorm_collapsed_loss", "norm_trunc"]
)
@pytest.mark.asyncio
async def test_distribution_fit_metadata_15_fields_round_trip_through_db(
    db_session: AsyncSession,
    seed_organization: Any,
    seed_user: Any,
    fitter: str,
) -> None:
    """Full sidecar survives ScenarioForm -> DB -> reload roundtrip.

    Build a fake-but-shape-correct PerFieldsetResult, run it through
    build_scenario_payload, persist as part of a Scenario row, reload by
    PK, and assert the JSON column's distribution_fit_metadata contains
    EXACTLY the expected key-set (15 for the tef node collapsed to PERT, 15
    for the vuln/PERT node; the native-lognormal pl/sl node is 13).
    """
    # Build a minimal WizardState (only basic_fields() are read by
    # build_scenario_payload; the rest is fieldset-specific).
    state = WizardState(tx_id="00000000-0000-0000-0000-000000000000")

    fieldset, result, expected_keys, column = _make_result(
        fitter, sme_id="11111111-1111-1111-1111-111111111111"
    )
    payload = build_scenario_payload({fieldset: result}, state)

    # The payload[fieldset] sub-dict is what would be slotted into the
    # corresponding Scenario column via _PAYLOAD_TO_FORM. Persist a Scenario
    # row with this exact JSON under the right column to exercise the
    # SQLAlchemy JSON TypeDecorator + SQLite/Postgres roundtrip.
    org_id = seed_organization.id
    base_dist = {"low": 1.0, "mode": 2.0, "high": 3.0}  # placeholder for the OTHER columns
    kwargs = {
        "organization_id": org_id,
        "name": f"sidecar-roundtrip-{fitter}",
        "scenario_type": ScenarioType.CUSTOM,
        "threat_category": ThreatCategory.RANSOMWARE,
        "threat_actor_type": ThreatActorType.CYBERCRIMINALS,
        "asset_class": AssetClass.SYSTEMS,
        "attack_vector": "email",
        "threat_event_frequency": base_dist,
        "vulnerability": {"low": 0.1, "mode": 0.2, "high": 0.3},
        "primary_loss": base_dist,
        "source": ScenarioSource.EXPERT_JUDGMENT,
        "status": EntityStatus.ACTIVE,
        "version": "1.0",
        "created_by": seed_user.id,
    }
    kwargs[column] = payload[fieldset]
    scenario = Scenario(**kwargs)
    db_session.add(scenario)
    await db_session.flush()
    scenario_id = scenario.id

    db_session.expunge_all()  # force a real reload from DB.
    reloaded = (
        await db_session.execute(select(Scenario).where(Scenario.id == scenario_id))
    ).scalar_one()
    sidecar = getattr(reloaded, column)["distribution_fit_metadata"]
    assert set(sidecar.keys()) == expected_keys, (
        f"distribution_fit_metadata key drift on {fitter}: "
        f"missing={expected_keys - set(sidecar.keys())!r} "
        f"unexpected={set(sidecar.keys()) - expected_keys!r}"
    )
    # Spot-check that schema_version + fitter survived intact (catches
    # JSON-encoder normalisation bugs). The stored fitter is the FITTER name
    # (lognorm_native for tef+pl, norm_trunc for vuln), not the parametrize
    # case label (which distinguishes the tef-collapsed-PERT case from the
    # native-lognormal pl case that share the same fitter).
    expected_fitter = "norm_trunc" if fitter == "norm_trunc" else "lognorm_native"
    assert sidecar["schema_version"] == 2
    assert sidecar["fitter"] == expected_fitter
    assert sidecar["n_smes"] == 1
    assert isinstance(sidecar["sme_ids"], list)
    assert len(sidecar["sme_ids"]) == 1
