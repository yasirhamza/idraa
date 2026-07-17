"""Tests for standalone-scoring predicate, 3-way triage, and staleness (#437 T4/T8).

B1: topology-derived scores_standalone (lec_prev_* + vmc_prev_* score; detection,
    gated-response, multi-member-AND leaves do NOT).
B2: pair-aware entry_scores via the actual engine closed-form (a 1-of-2 VMC pair
    = $0; a full 2-id+2-corr pair scores > $0).

T7: residual_meta_entries — slugs whose assignment SET does not score (#437 T7 → #439).
T7-M3: residual_partition seed-data contract — partition assignment across real entries.

T8: flag_runs_stale_for_control — sets is_stale=True on COMPLETED runs that reference
    a control; run stays COMPLETED; covers SINGLE + AGGREGATE run types; org-scoped;
    idempotent.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from fair_cam.risk_engine.control_attribution import reduction_from_composition, scenario_base_ale
from fair_cam.risk_engine.group_composition import compose_groups
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.risk_analysis_run import RiskAnalysisRun, RunStatus, RunType
from idraa.services.control_library import flag_runs_stale_for_control
from idraa.services.control_library_residual import residual_meta_entries, residual_partition
from idraa.services.control_library_scoring import (
    _REPRESENTATIVE_RP,
    _seed_entry_to_control,
    classify_entry,
    entry_scores,
    scores_standalone,
)


def test_lec_prevention_scores_meta_prevention_does_not() -> None:
    """Slice 2 (#439) D1 triage: lec_prev_* still scores standalone, but the
    VMC/DSC "meta" families no longer do. Task 1 retired the direct FAIR-node
    targets on the VMC/DSC groups (§2.2 p.5 "Indirectly Affect Risk"), so
    ``scores_standalone`` — which is topology-derived (reads
    ``GROUP_NODE_MAPPING[g].targets``) — now correctly returns False for
    ``vmc_prev_reduce_variance_prob``: a variance-management control affects
    risk ONLY indirectly, via the κ meta→reliability coupling on a co-present
    LEC control, never standalone. (Pre-slice this asserted True.)"""
    assert scores_standalone("lec_prev_resistance") is True
    assert scores_standalone("vmc_prev_reduce_variance_prob") is False
    # Final-Spec-N3: pin the OTHER Variance-Prevention member too, so the
    # whole VMC_VARIANCE_PREVENTION group's non-standalone status is guarded.
    assert scores_standalone("vmc_prev_reduce_change_freq") is False


def test_detection_gated_response_and_meta_do_not_score() -> None:
    assert scores_standalone("lec_det_monitoring") is False
    assert scores_standalone("lec_resp_event_termination") is False  # detection-gated
    assert scores_standalone("lec_resp_resilience") is False  # detection-gated
    assert scores_standalone("vmc_id_control_monitoring") is False
    assert scores_standalone("dsc_prev_communication") is False


def test_currency_response_scores() -> None:
    assert scores_standalone("lec_resp_loss_reduction") is True  # currency subtractor


def _e(*subs: str) -> dict[str, Any]:
    """Minimal entry; _seed_entry_to_control defaults missing cap/cov/rel to 0.8."""
    return {"assignments": [{"sub_function": s} for s in subs]}


def test_triage_three_way() -> None:
    assert classify_entry(_e("vmc_id_control_monitoring")) == "under-authored"
    assert classify_entry(_e("lec_prev_resistance", "lec_det_monitoring")) == "scoring"


def test_pair_aware_entry_scoring() -> None:
    """Slice 2 (#439) D1 triage: VMC-only entries no longer score standalone.

    Pre-slice a FULL VMC id∧corr pair scored via its direct VMC node targets.
    Task 1 retired those targets (§2.2 p.5 "Indirectly Affect Risk"): a
    VMC-only entry — partial OR complete pair — now yields v(S)=$0 because the
    meta strength E_meta has no co-present Loss-Event control to uplift, and
    ``entry_scores`` composes STANDALONE with κ=0 anyway (self-coupling-free,
    Slice 2 D5). So the FULL VMC pair, previously the canonical "scores via the
    pair" case, is now genuinely-meta → non-scoring-residual (≥2 assignments).
    The partial-pair engine behaviour (still $0) is unchanged.
    """
    # Partial VMC pair: 1-of-2 id + 1-of-2 corr -> v(S)=$0 -> NOT scoring (unchanged).
    assert entry_scores(_e("vmc_id_control_monitoring", "vmc_corr_implementation")) is False
    assert (
        classify_entry(_e("vmc_id_control_monitoring", "vmc_corr_implementation"))
        == "non-scoring-residual"
    )
    # FULL VMC pair (2 id + 2 corr): post-slice VMC has no direct node targets,
    # so even a complete pair scores $0 standalone -> non-scoring-residual.
    full_vmc_pair = _e(
        "vmc_id_threat_intelligence",
        "vmc_id_control_monitoring",
        "vmc_corr_treatment_selection",
        "vmc_corr_implementation",
    )
    assert entry_scores(full_vmc_pair) is False
    assert classify_entry(full_vmc_pair) == "non-scoring-residual"
    # DBR-shaped: gated response + lone id + dsc -> no pair completes -> residual
    assert (
        entry_scores(
            _e("lec_resp_resilience", "vmc_id_control_monitoring", "dsc_prev_defined_expectations")
        )
        is False
    )


def test_null_currency_capability_is_under_authored() -> None:
    """Regression: M-1/M-2 — cyber-insurance-shaped entry (currency, no capability_default).

    Before the fix, _seed_entry_to_control coerced None → 0.8 for CURRENCY units,
    injecting a fake ~$0.51 subtractor so entry_scores returned True ("scoring").
    The engine only adds to the currency subtractor `if a.capability_value is not None`
    (group_composition.py:101-102), so None must be preserved → v(S)=0 → False.
    """
    cyber_insurance = {"assignments": [{"sub_function": "lec_resp_loss_reduction"}]}
    assert entry_scores(cyber_insurance) is False, (
        "lec_resp_loss_reduction with null capability must NOT score (no subtractor)"
    )
    assert classify_entry(cyber_insurance) == "under-authored", (
        "cyber-insurance with null capability must classify as under-authored, not scoring"
    )


def test_zero_assignment_entry_is_under_authored() -> None:
    """Regression: zero-assignment entries must not raise (Control requires ≥1 assignment).

    entry_scores must return False before building the Control; classify_entry must
    return "under-authored" (≤1 assignment bucket).
    """
    empty_entry: dict[str, Any] = {"assignments": []}
    assert entry_scores(empty_entry) is False, "zero-assignment entry must not score"
    assert classify_entry(empty_entry) == "under-authored", (
        "zero-assignment entry must classify as under-authored"
    )


def test_entry_scores_is_self_coupling_free() -> None:
    """Catalog scoring composes the entry standalone with kappa=0: an entry
    whose only channels are meta must NOT score (Slice 2 D5). The hybrid
    half of the seam (hybrid entry == LEC-alone) is pinned separately by
    test_build_control_adjustment_never_self_credits and the Task 7 library
    sweep."""
    meta_only = {
        "assignments": [
            {
                "sub_function": "dsc_prev_communication",
                "capability_default": None,
                "coverage_default": 0.8,
                "reliability_default": 0.8,
            },
        ],
    }
    assert entry_scores(meta_only) is False


# ---------------------------------------------------------------------------
# T7: residual_meta_entries tests (#437 T7 → #439 scope list)
# ---------------------------------------------------------------------------


def test_enriched_cspm_not_residual() -> None:
    # CSPM gains lec_prev_avoidance/resistance (scores) -> off the residual list
    entries = [
        {
            "slug": "cspm",
            "assignments": [
                {"sub_function": "lec_prev_resistance"},
                {"sub_function": "vmc_id_control_monitoring"},
            ],
        }
    ]
    assert residual_meta_entries(entries) == []


def test_pure_monitor_is_residual() -> None:
    entries = [
        {"slug": "posture_dash", "assignments": [{"sub_function": "vmc_id_control_monitoring"}]}
    ]
    assert residual_meta_entries(entries) == ["posture_dash"]


def test_detection_only_is_residual() -> None:
    # B1 regression: detection does NOT score standalone -> detection-only entry is residual, not "covered"
    entries = [
        {
            "slug": "ids",
            "assignments": [
                {"sub_function": "lec_det_monitoring"},
                {"sub_function": "lec_det_visibility"},
            ],
        }
    ]
    assert residual_meta_entries(entries) == ["ids"]


# ---------------------------------------------------------------------------
# T7-M3: residual_partition seed-data contract (#437 T7 M-3)
# ---------------------------------------------------------------------------


def _load_seed_entries() -> list[dict[str, Any]]:
    seed_path = Path(__file__).parents[2] / "data" / "seed_control_library_entries.json"
    data = json.loads(seed_path.read_text(encoding="utf-8"))
    return data.get("entries", [])  # type: ignore[return-value]


def test_cspm_not_in_residual_meta_entries() -> None:
    """cloud-security-posture-management has scoring assignments -> NOT in residual union."""
    entries = _load_seed_entries()
    residuals = residual_meta_entries(entries)
    assert "cloud-security-posture-management" not in residuals, (
        "cloud-security-posture-management scores via lec_prev_* -> must not appear in residual_meta_entries"
    )


def test_cyber_insurance_in_under_authored_partition() -> None:
    """cyber-insurance has 1 assignment (lec_resp_loss_reduction, no capability_default)
    -> under-authored partition, NOT genuinely_meta."""
    entries = _load_seed_entries()
    partition = residual_partition(entries)
    assert "cyber-insurance" in partition["under_authored"], (
        "cyber-insurance (1 assignment, no capability_value) must be in under_authored"
    )
    assert "cyber-insurance" not in partition["genuinely_meta"], (
        "cyber-insurance must NOT be in genuinely_meta (it only has <=1 assignment)"
    )


def test_mobile_device_management_scores_after_t1_recuration() -> None:
    """mobile-device-management gains lec_prev_resistance in the rollout tranche-1
    re-curation (device hardening, grounded by the genuine PR.AC-3) -> it now SCORES and
    must NOT appear in the residual partition (was genuinely_meta pre-T1). The initially
    proposed secondary lec_prev_avoidance channel (borderline CIS 3.12 grounding) was
    dropped as dispensable in the methodology fix; resistance alone carries the score."""
    entries = _load_seed_entries()
    residuals = residual_meta_entries(entries)
    assert "mobile-device-management" not in residuals, (
        "mobile-device-management scores via lec_prev_* after T1 -> must not be residual"
    )


def test_deception_technology_in_genuinely_meta_partition() -> None:
    """deception-technology has 3 detection assignments (>=2) and does not score
    (detection-only, no response/prevention channel) -> genuinely_meta partition.
    It is deliberately NOT re-curated in T1: rubric §6.6 blesses deception ->
    avoidance/deterrence, but the P2a crosswalk has no grounding code, so grafting
    would be assign-to-score (flagged for methodology review as a crosswalk gap)."""
    entries = _load_seed_entries()
    partition = residual_partition(entries)
    assert "deception-technology" in partition["genuinely_meta"], (
        "deception-technology (>=2 assignments, no scoring channel) must be in genuinely_meta"
    )
    assert "deception-technology" not in partition["under_authored"], (
        "deception-technology must NOT be in under_authored (it has >=2 assignments)"
    )


# ---------------------------------------------------------------------------
# Task 7 (#439 Slice 2, plan-gate Meth-N3): one-shot catalog sweep pinning
# "only CM changes behavior" as code, not prose.
# ---------------------------------------------------------------------------


def _entry_reduction(entry: dict[str, Any], base: float) -> float:
    """Same closed-form path as ``entry_scores`` (kappa=0.0, standalone), but
    returns the raw reduction VALUE instead of the >1e-9 boolean threshold."""
    if not entry.get("assignments"):
        return 0.0
    ctrl = _seed_entry_to_control(entry)
    return reduction_from_composition(base, compose_groups([ctrl], kappa=0.0), None)


def test_no_seed_entry_scores_through_meta_channels_post_d1() -> None:
    """Meta (VMC/DSC) channels must contribute NOTHING to an entry's standalone
    catalog score. ``entry_scores`` composes every entry standalone at kappa=0
    (self-coupling-free, D5 / ``test_entry_scores_is_self_coupling_free``
    above), so stripping an entry's meta assignments and keeping only its
    LEC_* assignments must NEVER change its outcome. If it did, that entry
    would be scoring "through" a meta channel in the catalog -- which D1
    forbids (VMC/DSC families lost their direct FAIR-node targets; meta value
    flows exclusively via the kappa reliability coupling on a co-present LEC
    control inside a live run's Shapley value function, never inside the
    standalone catalog composition).

    Iterates EVERY seed catalog entry, composes it TWICE (all channels vs
    LEC-only channels), and asserts the raw REDUCTION VALUES are equal
    (T7-Meth-N1), not just the ``entry_scores()`` boolean -- a boolean-only
    comparison has a hybrid-entry blind spot: a meta channel could leak a
    sub-threshold nonzero contribution (below the 1e-9 boolean gate) into a
    hybrid entry's reduction without ever flipping the boolean, which the
    prior boolean-equality check could not see. Comparing the underlying
    ``reduction_from_composition`` output per entry enforces the spec's "only
    CM changes behavior" regression-surface claim exactly, as code.
    """
    entries = _load_seed_entries()
    base = scenario_base_ale(_REPRESENTATIVE_RP)
    differing: list[str] = []
    for entry in entries:
        assignments = entry.get("assignments", [])
        lec_only_assignments = [a for a in assignments if a["sub_function"].startswith("lec_")]
        lec_only_entry = {**entry, "assignments": lec_only_assignments}

        all_channels_reduction = _entry_reduction(entry, base)
        lec_only_reduction = _entry_reduction(lec_only_entry, base)

        if all_channels_reduction != lec_only_reduction:
            differing.append(
                f"{entry.get('slug', '<unknown>')!r}: all_channels={all_channels_reduction!r} "
                f"lec_only={lec_only_reduction!r}"
            )

    # D1 regression-surface claim: no seed entry's reduction VALUE differs
    # between "all channels" and "LEC-only channels" composition.
    assert differing == []


# ---------------------------------------------------------------------------
# T8: flag_runs_stale_for_control (#437 T8)
#
# Staleness mechanism: is_stale boolean column — a COMPLETED run is flagged
# is_stale=True when a library entry it used is re-curated; the run STAYS
# COMPLETED so all COMPLETED-gated consumers (reports/PDF/dashboard) remain
# unaffected. Both SINGLE and AGGREGATE run types store their controls in
# control_ids_used: list[str] of hyphenated-UUID strings; the helper covers
# both in a single filter pass. The helper is org-scoped (I-1 finding).
# ---------------------------------------------------------------------------


def _make_completed_run(org_id: UUID, control_ids: list[uuid.UUID], **kw: Any) -> RiskAnalysisRun:
    """Build a COMPLETED RiskAnalysisRun for testing. scenario_id is left NULL
    (nullable column) so the test requires no Scenario FK row."""
    return RiskAnalysisRun(
        organization_id=org_id,
        scenario_id=None,
        run_type=kw.pop("run_type", RunType.SINGLE),
        status=RunStatus.COMPLETED,
        mc_iterations=100,
        inputs_hash=kw.pop("inputs_hash", uuid.uuid4().hex * 2),
        controls_snapshot=[],
        control_ids_used=[str(cid) for cid in control_ids],
        **kw,
    )


@pytest.mark.asyncio
async def test_staleness_single_run_is_flagged(db: AsyncSession, org_id: UUID) -> None:
    """SINGLE run whose control_ids_used contains the target control is flagged
    is_stale=True; status STAYS COMPLETED."""
    control_id = uuid.uuid4()
    run = _make_completed_run(org_id, [control_id])
    db.add(run)
    await db.flush()

    count = await flag_runs_stale_for_control(db, org_id, control_id)

    assert count == 1
    await db.refresh(run)
    assert run.is_stale is True
    assert run.status == RunStatus.COMPLETED  # must stay COMPLETED


@pytest.mark.asyncio
async def test_staleness_aggregate_run_is_flagged(db: AsyncSession, org_id: UUID) -> None:
    """AGGREGATE run (scenario_id=NULL, aggregate_scenario_ids set) is flagged
    when control_ids_used contains the target control; status stays COMPLETED."""
    control_id = uuid.uuid4()
    other_control_id = uuid.uuid4()
    scen_id_a = str(uuid.uuid4())
    scen_id_b = str(uuid.uuid4())
    run = _make_completed_run(
        org_id,
        [control_id, other_control_id],
        run_type=RunType.AGGREGATE,
        aggregate_scenario_ids=[scen_id_a, scen_id_b],
    )
    db.add(run)
    await db.flush()

    count = await flag_runs_stale_for_control(db, org_id, control_id)

    assert count == 1
    await db.refresh(run)
    assert run.is_stale is True
    assert run.status == RunStatus.COMPLETED


@pytest.mark.asyncio
async def test_staleness_unrelated_run_not_flagged(db: AsyncSession, org_id: UUID) -> None:
    """Run that does NOT include the target control_id remains COMPLETED + not stale."""
    target_control_id = uuid.uuid4()
    other_control_id = uuid.uuid4()
    run = _make_completed_run(org_id, [other_control_id])
    db.add(run)
    await db.flush()

    count = await flag_runs_stale_for_control(db, org_id, target_control_id)

    assert count == 0
    await db.refresh(run)
    assert run.is_stale is False
    assert run.status == RunStatus.COMPLETED


@pytest.mark.asyncio
async def test_staleness_non_completed_runs_not_flagged(db: AsyncSession, org_id: UUID) -> None:
    """QUEUED / FAILED runs are not touched even when they reference the control."""
    control_id = uuid.uuid4()

    queued_run = RiskAnalysisRun(
        organization_id=org_id,
        scenario_id=None,
        run_type=RunType.SINGLE,
        status=RunStatus.QUEUED,
        mc_iterations=100,
        inputs_hash=uuid.uuid4().hex * 2,
        controls_snapshot=[],
        control_ids_used=[str(control_id)],
    )
    failed_run = RiskAnalysisRun(
        organization_id=org_id,
        scenario_id=None,
        run_type=RunType.SINGLE,
        status=RunStatus.FAILED,
        mc_iterations=100,
        inputs_hash=uuid.uuid4().hex * 2,
        controls_snapshot=[],
        control_ids_used=[str(control_id)],
    )
    db.add(queued_run)
    db.add(failed_run)
    await db.flush()

    count = await flag_runs_stale_for_control(db, org_id, control_id)

    assert count == 0
    await db.refresh(queued_run)
    await db.refresh(failed_run)
    assert queued_run.status == RunStatus.QUEUED
    assert queued_run.is_stale is False
    assert failed_run.status == RunStatus.FAILED
    assert failed_run.is_stale is False


@pytest.mark.asyncio
async def test_staleness_cross_org_run_not_flagged(db: AsyncSession, org_id: UUID) -> None:
    """Run in a DIFFERENT org is NOT flagged — org-scope is enforced (I-1 contract)."""
    from tests.factories import create_org

    other_org = await create_org(db, name="Other Org")
    control_id = uuid.uuid4()
    # Run in the target org — must be flagged.
    own_run = _make_completed_run(org_id, [control_id])
    # Run in another org — must NOT be flagged even with the same control.
    other_run = _make_completed_run(other_org.id, [control_id])
    db.add(own_run)
    db.add(other_run)
    await db.flush()

    count = await flag_runs_stale_for_control(db, org_id, control_id)

    assert count == 1
    await db.refresh(own_run)
    await db.refresh(other_run)
    assert own_run.is_stale is True
    assert other_run.is_stale is False  # cross-org run must not be touched


@pytest.mark.asyncio
async def test_staleness_idempotent(db: AsyncSession, org_id: UUID) -> None:
    """Second call on an already-stale run returns 0 — the is_stale==False filter
    excludes already-flagged runs so they are not double-written."""
    control_id = uuid.uuid4()
    run = _make_completed_run(org_id, [control_id])
    db.add(run)
    await db.flush()

    count1 = await flag_runs_stale_for_control(db, org_id, control_id)
    assert count1 == 1

    count2 = await flag_runs_stale_for_control(db, org_id, control_id)
    assert count2 == 0  # run is already is_stale=True, filtered out

    await db.refresh(run)
    assert run.is_stale is True
    assert run.status == RunStatus.COMPLETED


@pytest.mark.asyncio
async def test_staleness_count_across_multiple_runs(db: AsyncSession, org_id: UUID) -> None:
    """Multiple COMPLETED runs referencing the same control are all flagged;
    the return value equals the number of transitions made."""
    control_id = uuid.uuid4()
    unrelated_control_id = uuid.uuid4()

    for _ in range(3):
        db.add(_make_completed_run(org_id, [control_id]))
    # one run with a DIFFERENT control must NOT be counted
    db.add(_make_completed_run(org_id, [unrelated_control_id]))
    await db.flush()

    count = await flag_runs_stale_for_control(db, org_id, control_id)
    assert count == 3
