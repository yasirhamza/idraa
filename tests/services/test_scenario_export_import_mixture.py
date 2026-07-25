"""JSON export/import round-trip for lognormal_mixture (issue #27 Task 7).

Plan-gate BINDING amendment (docs/superpowers/plans/2026-07-19-mixture-pooling.md
Task 7): the importable mixture shape is EXACTLY ``{"distribution",
"components"}`` (components exactly ``{"mean", "sigma", "weight"}``) — the
anti-blob gate is never loosened to admit ``distribution_fit_metadata``. This
module's round-trip test therefore uses a MINIMAL metadata-free mixture. The
metadata-carrying case (which does NOT re-import — a pre-existing
scalar-lognormal asymmetry) is covered in
tests/unit/test_scenario_export_serializer.py
(``test_json_export_metadata_carrying_mixture_emits_verbatim_but_fails_reimport``),
next to ``scenario_export``'s module docstring that documents it.
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.config import get_settings
from idraa.models.scenario import Scenario
from idraa.services.loss_capacity import capacity_max_for_org
from idraa.services.scenario_export import scenario_to_json_obj
from idraa.services.scenario_import import apply_validated_preview, validate_upload

# Minimal, metadata-free, hand-authored mixture. 3 components (not 2) so the
# storage round-trip also exercises the N>=3 adapter-iteration contract
# (CLAUDE.md data-contract rule) — no component silently dropped by a
# hypothetical [0]/[-1]/[first] optimization anywhere on the import/export path.
#
# PR2 D13/D18/D19 (Task 4b) require an explicit "max" on a lognormal_mixture
# loss imported into a revenue-set org (D18), and that max must clear every
# component's log-space p95 (D19). The largest component (mean=15.77,
# sigma=1.19) has ln(p95) = 15.77 + 1.645*1.19 ~= 17.727 -> p95 ~= $5.0e7, so
# 1_000_000_000.0 clears the floor with wide margin. This is an explicit,
# hand-authored max (Option A from the fix brief), NOT a value minted by the
# import path -- Task 4b's mint-or-preserve logic preserves an explicit max
# untouched, so the roundtrip stays trivially byte-identical.
_MINIMAL_MIXTURE: dict[str, Any] = {
    "distribution": "lognormal_mixture",
    "components": [
        {"mean": 8.06, "sigma": 0.70, "weight": 0.2},
        {"mean": 12.0, "sigma": 0.9, "weight": 0.3},
        {"mean": 15.77, "sigma": 1.19, "weight": 0.5},
    ],
    "max": 1_000_000_000.0,
}


def _scenario_obj(name: str) -> dict[str, Any]:
    """Minimal JSON scenario object carrying a lognormal_mixture primary_loss."""
    return {
        "name": name,
        "threat_category": "ransomware",
        "threat_event_frequency": {"distribution": "PERT", "low": 0.1, "mode": 0.5, "high": 2},
        "vulnerability": {"distribution": "PERT", "low": 0.2, "mode": 0.35, "high": 0.6},
        "primary_loss": _MINIMAL_MIXTURE,
    }


@pytest.mark.asyncio
async def test_minimal_mixture_json_roundtrip_creates_and_reimports_identically(
    db_session: AsyncSession,
    organization: Any,
    admin_user: Any,
) -> None:
    """Hand-authored minimal mixture JSON -> import creates a scenario ->
    export JSON -> re-import -> the stored dict is byte-identical."""
    # PR2 D18: the catastrophic (lognormal_mixture) import path requires the
    # importing org's own revenue-derived capacity, threaded in by the
    # CALLER (mirrors routes/scenario_import.py's
    # `capacity_max_for_org(organization.annual_revenue, ...)` -- this test
    # calls the service layer directly, bypassing the route, so it must
    # compute + pass the same value itself). Set locally on the
    # function-scoped `organization` fixture instance -- no shared/session-
    # scoped fixture is mutated, so this does not ripple into other tests.
    organization.annual_revenue = Decimal("1000000000")  # $1B; >= _MINIMAL_MIXTURE["max"]
    await db_session.commit()
    capacity_max = capacity_max_for_org(organization.annual_revenue, get_settings().capacity_k)

    data = json.dumps([_scenario_obj("MixtureRT-A")]).encode()
    token, preview, errors = await validate_upload(
        db_session,
        org_id=organization.id,
        user_id=admin_user.id,
        data=data,
        filename="s.json",
        content_type="application/json",
        capacity_max=capacity_max,
    )
    assert errors == []
    assert [p["action"] for p in preview] == ["create"]

    imported, skipped, apply_errors = await apply_validated_preview(
        db_session,
        token=token,
        org_id=organization.id,
        user=admin_user,
        capacity_max=capacity_max,
    )
    assert imported == 1
    assert skipped == 0
    assert apply_errors == []

    scenario_a = (
        await db_session.execute(select(Scenario).where(Scenario.name == "MixtureRT-A"))
    ).scalar_one()
    # Stored dict is exactly the hand-authored minimal shape -- no sidecar,
    # no dropped/reordered components.
    assert scenario_a.primary_loss == _MINIMAL_MIXTURE
    assert len(scenario_a.primary_loss["components"]) == 3

    # Export -> the stored dict comes back out of scenario_to_json_obj verbatim.
    exported = scenario_to_json_obj(scenario_a)
    assert exported["primary_loss"] == _MINIMAL_MIXTURE

    # Re-import the exported object (renamed to dodge the dup-name skip) --
    # creates a SECOND scenario whose stored dict must be identical to the
    # first: the round-trip is lossless for a minimal metadata-free mixture.
    exported["name"] = "MixtureRT-B"
    data2 = json.dumps([exported]).encode()
    token2, preview2, errors2 = await validate_upload(
        db_session,
        org_id=organization.id,
        user_id=admin_user.id,
        data=data2,
        filename="s2.json",
        content_type="application/json",
        capacity_max=capacity_max,
    )
    assert errors2 == []
    assert [p["action"] for p in preview2] == ["create"]

    imported2, skipped2, apply_errors2 = await apply_validated_preview(
        db_session,
        token=token2,
        org_id=organization.id,
        user=admin_user,
        capacity_max=capacity_max,
    )
    assert imported2 == 1
    assert skipped2 == 0
    assert apply_errors2 == []

    scenario_b = (
        await db_session.execute(select(Scenario).where(Scenario.name == "MixtureRT-B"))
    ).scalar_one()
    assert scenario_b.primary_loss == scenario_a.primary_loss == _MINIMAL_MIXTURE
