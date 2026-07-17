"""Resolve a scenario library entry's suggested_control_ids (catalog slugs) into
displayable recommendations, marking which the caller's org already adopted (P2c).
Pure read; reuses the P2b catalog. Shared by the library-detail, scenario-detail,
and wizard control-step surfaces."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.control import Control
from idraa.models.control_library import ControlLibraryEntry
from idraa.models.enums import ControlSource
from idraa.models.scenario_library import ScenarioLibraryEntry
from idraa.services.control_library import ControlLibraryService


@dataclass
class ControlRecommendation:
    catalog_entry: ControlLibraryEntry
    adopted: bool
    adopted_control_id: uuid.UUID | None


async def _adopted_entry_id_to_control(db: AsyncSession, org_id: uuid.UUID) -> dict[str, uuid.UUID]:
    """Map catalog-entry-id (str) -> the org's adopted Control id (org-scoped).
    Deterministic: ordered by (created_at, id) so the EARLIEST adoption wins when an
    org adopted the same catalog entry twice (re-adopt is non-blocking in P2b) — avoids
    cross-backend flakiness (Arch-N1/Spec-NTH-3)."""
    rows = (
        await db.execute(
            select(Control.id, Control.library_pin)
            .where(
                Control.organization_id == org_id,
                Control.source == ControlSource.LIBRARY_DERIVED,
            )
            .order_by(Control.created_at, Control.id)
        )
    ).all()
    out: dict[str, uuid.UUID] = {}
    for control_id, pin in rows:
        if pin and pin.get("entry_id") and pin["entry_id"] not in out:
            out[pin["entry_id"]] = control_id
    return out


async def recommended_controls_for(
    db: AsyncSession, *, entry: ScenarioLibraryEntry, org_id: uuid.UUID
) -> list[ControlRecommendation]:
    slugs = list(entry.suggested_control_ids or [])
    if not slugs:
        return []
    svc = ControlLibraryService(db)
    adopted = await _adopted_entry_id_to_control(db, org_id)
    recs: list[ControlRecommendation] = []
    # N+1 (one get_published_by_slug per slug) is acceptable + intentional here: the
    # curation caps suggestions at <=6 per scenario against a ~61-row catalog. Do NOT
    # prematurely batch into an IN-query (Arch-N2).
    for slug in slugs:  # preserve curated order
        catalog = await svc.get_published_by_slug(slug)
        if catalog is None:
            continue  # unresolvable / unpublished — skip (referential-integrity test guards the seed)
        control_id = adopted.get(str(catalog.id))
        recs.append(
            ControlRecommendation(
                catalog_entry=catalog, adopted=control_id is not None, adopted_control_id=control_id
            )
        )
    return recs
