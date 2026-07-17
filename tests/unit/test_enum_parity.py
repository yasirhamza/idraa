"""Parity tests: v3 enums must mirror fair_cam enums where overlap exists.

Spec: docs/superpowers/specs/2026-04-28-phase-1.5a-scenario-library-design.md §6.3

PR pi F12: dropped the IndustrySubSector parity test since
``fair_cam.parameters.sub_sector_overlays`` is deleted in F11. The enum
in v3 is now standalone — no parity check possible after sub_sector
overlays are excised.
"""

from __future__ import annotations

from fair_cam.parameters.industry_calibration import (
    ThreatActorType as FCThreatActorType,
)

from idraa.models.enums import (
    AssetClass,
    IndustryType,
    ScenarioSource,
    ThreatActorType,
    ThreatCategory,
)


def test_threat_actor_type_parity_v3_equals_fair_cam() -> None:
    """v3 ThreatActorType MUST equal fair_cam ThreatActorType value-for-value."""
    v3_values = {member.value for member in ThreatActorType}
    fair_cam_values = {member.value for member in FCThreatActorType}
    assert v3_values == fair_cam_values, (
        f"ThreatActorType parity drift: v3 - fair_cam = {v3_values - fair_cam_values}; "
        f"fair_cam - v3 = {fair_cam_values - v3_values}"
    )


def test_industry_type_includes_all_naics2_buckets() -> None:
    """v3 IndustryType MUST include all 20 NAICS-2 supersector values."""
    expected = {
        "agriculture",
        "mining",
        "utilities",
        "construction",
        "manufacturing",
        "trade",
        "retail",
        "transportation",
        "information",
        "financial",
        "real_estate",
        "professional",
        "management",
        "administrative",
        "education",
        "healthcare",
        "entertainment",
        "hospitality",
        "public",
        "other",
    }
    actual = {member.value for member in IndustryType}
    assert expected <= actual, f"missing NAICS-2 values: {expected - actual}"


def test_scenario_source_includes_library_derived() -> None:
    """D6: ScenarioSource.LIBRARY_DERIVED is now a real value (was a comment in PR ε)."""
    assert ScenarioSource.LIBRARY_DERIVED.value == "library_derived"


def test_threat_category_includes_ot_first_values() -> None:
    """OT-first: ThreatCategory includes availability, safety-tampering, and integrity
    (process-view/manipulation — the CIA "I")."""
    values = {member.value for member in ThreatCategory}
    assert "ot_safety_tampering" in values
    assert "ot_availability" in values
    assert "ot_integrity" in values  # NEW


def test_asset_class_includes_ot_first_values() -> None:
    """OT-first commitment: AssetClass must include OT_SYSTEMS + SAFETY_SYSTEMS."""
    values = {member.value for member in AssetClass}
    assert "ot_systems" in values
    assert "safety_systems" in values


# --- PR iota: FairCamSubFunction slug freeze and member count ---


def test_fair_cam_sub_function_has_26_members() -> None:
    """FairCamSubFunction must have exactly 26 members.

    The count is load-bearing: it is the DB CHECK constraint cardinality,
    the migration backfill domain, and the audit §3 canonical table size.
    Changing the count requires a spec amendment + slug rename procedure (§15).
    """
    from idraa.models.enums import FairCamSubFunction

    assert len(FairCamSubFunction) == 26, (
        f"Expected 26 FairCamSubFunction members; got {len(FairCamSubFunction)}. "
        "Adding or removing values requires a spec amendment and data migration — "
        "see docs/superpowers/specs/2026-04-30-pr-iota-v3-control-reshape-design.md §15."
    )


def test_fair_cam_sub_function_slugs_match_audit_s3_verbatim() -> None:
    """All 26 slugs must match audit §3 table verbatim (case-sensitive).

    Slugs appear in serialized risk_analysis_runs.snapshot JSON. Renaming
    after PR iota ships requires a data migration touching immutable audit
    records. This test is the freeze guard.
    """
    from idraa.models.enums import FairCamSubFunction

    expected_slugs = {
        # LEC — 9
        "lec_prev_avoidance",
        "lec_prev_deterrence",
        "lec_prev_resistance",
        "lec_det_visibility",
        "lec_det_monitoring",
        "lec_det_recognition",
        "lec_resp_event_termination",
        "lec_resp_resilience",
        "lec_resp_loss_reduction",
        # VMC — 6
        "vmc_prev_reduce_change_freq",
        "vmc_prev_reduce_variance_prob",
        "vmc_id_threat_intelligence",
        "vmc_id_control_monitoring",
        "vmc_corr_treatment_selection",
        "vmc_corr_implementation",
        # DSC — 11
        "dsc_prev_defined_expectations",
        "dsc_prev_communication",
        "dsc_prev_sa_data_asset",
        "dsc_prev_sa_data_threat",
        "dsc_prev_sa_data_controls",
        "dsc_prev_sa_analysis",
        "dsc_prev_sa_reporting",
        "dsc_prev_ensure_capability",
        "dsc_prev_incentives",
        "dsc_id_misaligned",
        "dsc_corr_misaligned",
    }
    actual_slugs = {m.value for m in FairCamSubFunction}
    assert actual_slugs == expected_slugs, (
        f"Slug mismatch. Extra: {actual_slugs - expected_slugs}. "
        f"Missing: {expected_slugs - actual_slugs}."
    )
