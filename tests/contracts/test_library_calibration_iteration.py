"""F7: adapter iteration contract for library_calibrated_pre_fill.

CLAUDE.md "Data contract enforcement is project-wide policy". Even
though library_calibrated_pre_fill is single-entry (not a list adapter),
this test guards the wizard pre-fill path against future refactors that
might compose it across multiple entries (e.g., bulk-recalibrate, aggregate
sub-flows). N=5 entries with distinct primary_loss must produce 5 distinct
entry-absolute outputs — no [0] indexing, no shared state, no de-duplication.
(Org loss-scaling was removed 2026-07-07 — outputs are entry-absolute.)

Plus CalibrationContext field-count guard: enforce only the two FAIR-CAM-
native fields (industry, revenue_tier). Adding back security_maturity or
industry_sub_sector requires explicit re-litigation per spec §3 out-of-scope.
"""

from __future__ import annotations

import dataclasses
import uuid
from collections import Counter

from idraa.models.enums import AssetClass, ThreatActorType, ThreatCategory
from idraa.models.scenario_library import ScenarioLibraryEntry
from idraa.services.calibration import CalibrationContext
from idraa.services.library_calibration import library_calibrated_pre_fill


def _entry(
    slug: str, anchor: dict[str, str] | None = None, pl_mean: float = 13.8155
) -> ScenarioLibraryEntry:
    return ScenarioLibraryEntry(
        id=uuid.uuid4(),
        version=1,
        slug=slug,
        name=slug,
        status="published",
        threat_event_type=ThreatCategory.RANSOMWARE,
        threat_actor_type=ThreatActorType.CYBERCRIMINALS,
        asset_class=AssetClass.SYSTEMS,
        tags=[],
        description="d",
        canonical_fair_gap="g",
        source_citations=[],
        threat_event_frequency={"distribution": "PERT", "low": 1.0, "mode": 4.0, "high": 12.0},
        vulnerability={"distribution": "PERT", "low": 0.05, "mode": 0.20, "high": 0.50},
        primary_loss={"distribution": "lognormal", "mean": pl_mean, "sigma": 1.9602},
        secondary_loss={"distribution": "lognormal", "mean": pl_mean - 1.0, "sigma": 1.9602},
        suggested_control_ids=[],
        calibration_anchor=anchor,
    )


def test_calibration_context_has_only_industry_and_revenue_tier_fields() -> None:
    """F7: CalibrationContext is industry + revenue_tier only per FAIR-CAM-native scope.

    security_maturity and industry_sub_sector were reserved in PR #104 but
    are removed in PR γ-2 (#103) — controls express maturity. Adding either
    field back requires explicit re-litigation per spec §3 out-of-scope.
    """
    fields = {f.name for f in dataclasses.fields(CalibrationContext)}
    assert fields == {"industry", "revenue_tier"}, (
        f"CalibrationContext fields changed: {sorted(fields)}. "
        f"FAIR-CAM-native model expects industry + revenue_tier only."
    )


def test_library_calibrated_pre_fill_preserves_cardinality_across_distinct_entries() -> None:
    """N=5 entries with distinct primary_loss → 5 distinct entry-absolute outputs.

    Guards against future refactors where a caller might map over a list of
    entries; the per-entry pre-fill must not share state, [0]-index, or
    de-duplicate. Org loss-scaling was removed 2026-07-07, so each output's PL is
    the entry's own value — distinctness here comes from distinct base PL means,
    and every output carries None metadata (no scaling).
    """
    entries = [
        _entry(
            "e1", anchor={"industry": "healthcare", "revenue_tier": "10b_to_100b"}, pl_mean=13.0
        ),
        _entry("e2", anchor={"industry": "healthcare", "revenue_tier": "1b_to_10b"}, pl_mean=13.5),
        _entry("e3", anchor={"industry": "financial", "revenue_tier": "100m_to_1b"}, pl_mean=12.0),
        _entry(
            "e4", anchor={"industry": "manufacturing", "revenue_tier": "10m_to_100m"}, pl_mean=11.5
        ),
        _entry("e5-no-anchor", anchor=None, pl_mean=14.0),
    ]
    outputs = [library_calibrated_pre_fill(e, override=None) for e in entries]

    # Cardinality guard: 5 calls → 5 outputs (no de-duplication anywhere)
    assert len(outputs) == 5

    # Each output's PL is the entry's own value (no scaling), and all 5 are distinct.
    for entry, (form_dict, metadata) in zip(entries, outputs, strict=True):
        assert metadata is None
        assert form_dict["pl"] == entry.primary_loss
    pl_means = Counter(out[0]["pl"]["mean"] for out in outputs)
    assert all(count == 1 for count in pl_means.values()), (
        f"PL.mean collisions — cardinality/dedup bug: {pl_means}"
    )
