"""Dashboard 'by threat community' lens -- VIEW-MODEL DERIVATION, not FAIR-grounded (register D10).

Groups the latest aggregate run's per-scenario residual ALE MEANS by the community recorded in the
run's scenario_inputs_snapshot, through the shared rule idraa.threat_community_provenance
.community_by_scenario (grouped by SLUG, displayed by the snapshot NAME; the PDF uses the same rule).
Rows with a review-state provenance, no community, or missing from the snapshot fall into a final
"Needs review / unassigned" row; a snapshot slug with no current canonical row keeps its own row so
ALE is never dropped; shares therefore sum to 1. Library coverage reuses the org's sub-sector-
applicable entries and pinned ids that build_dashboard already fetched -- no extra queries.
Malformed ids and non-finite ALE values are skipped, never raised.
"""

from __future__ import annotations

import math
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from idraa.services.coverage import CoverageResult, coverage
from idraa.services.threat_communities import ThreatCommunityService
from idraa.threat_community_provenance import (
    NEEDS_REVIEW_LABEL,
    NEEDS_REVIEW_SLUG,
    community_by_scenario,
)


@dataclass(frozen=True)
class ThreatCommunitySummaryRow:
    slug: str
    name: str
    origin: str
    scenario_count: int  # scenarios in the latest run attributed to this row
    residual_ale: float  # sum of per-scenario residual ALE means (mean basis)
    residual_ale_share: float
    library_coverage: CoverageResult


def _ale_by_scenario(run: Any) -> dict[str, float]:
    out: dict[str, float] = {}
    for ps in (getattr(run, "simulation_results", None) or {}).get("per_scenario", []) or []:
        if not isinstance(ps, dict):
            continue
        try:
            sid = uuid.UUID(str(ps.get("scenario_id"))).hex
            v = float((ps.get("residual_risk") or {}).get("annualized_loss_expectancy", 0.0) or 0.0)
        except (ValueError, TypeError, AttributeError):
            continue
        if math.isfinite(v):
            out[sid] = v
    return out


async def build_threat_community_summary(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    latest_aggregate: Any | None,
    sector_entries: list[Any],
    pinned_library_ids: Iterable[str],
) -> list[ThreatCommunitySummaryRow]:
    communities = await ThreatCommunityService(db, organization_id=organization_id).list_published()
    ale = _ale_by_scenario(latest_aggregate) if latest_aggregate is not None else {}
    by_sid = (
        community_by_scenario(getattr(latest_aggregate, "scenario_inputs_snapshot", None))
        if latest_aggregate is not None
        else {}
    )
    ale_by: dict[str | None, float] = {}
    count_by: dict[str | None, int] = {}
    name_of: dict[str, str] = {}
    for sid, v in ale.items():
        pair = by_sid.get(sid)
        key = pair[0] if pair else None
        if pair:
            name_of[pair[0]] = min(
                name_of.get(pair[0], pair[1]), pair[1]
            )  # deterministic: smallest name wins (PDF uses the same rule)
        ale_by[key] = ale_by.get(key, 0.0) + v
        count_by[key] = count_by.get(key, 0) + 1
    total = sum(ale_by.values())
    pinned = {str(x) for x in pinned_library_ids}
    entries_by_slug: dict[str, list[str]] = {}
    for e in sector_entries:
        entries_by_slug.setdefault(e.threat_community.slug, []).append(str(e.id))

    def _row(slug: str, name: str, origin: str) -> ThreatCommunitySummaryRow:
        v = ale_by.get(slug, 0.0)
        return ThreatCommunitySummaryRow(
            slug=slug,
            name=name,
            origin=origin,
            scenario_count=count_by.get(slug, 0),
            residual_ale=v,
            residual_ale_share=(v / total) if total > 0 else 0.0,
            library_coverage=coverage(entries_by_slug.get(slug, []), pinned),
        )

    # Display = the SMALLEST snapshot name seen for the slug (deterministic; the PDF's rule
    # too); the current canonical name only when the run holds no scenario for it.
    rows = [_row(c.slug, name_of.get(c.slug, c.name), c.origin) for c in communities]
    canonical = {c.slug for c in communities}
    rows += [
        _row(slug, name_of[slug], "")
        for slug in sorted(k for k in ale_by if k is not None and k not in canonical)
    ]
    v = ale_by.get(None, 0.0)
    rows.append(
        ThreatCommunitySummaryRow(
            slug=NEEDS_REVIEW_SLUG,
            name=NEEDS_REVIEW_LABEL,
            origin="",
            scenario_count=count_by.get(None, 0),
            residual_ale=v,
            residual_ale_share=(v / total) if total > 0 else 0.0,
            library_coverage=coverage([], pinned),
        )
    )
    return rows
