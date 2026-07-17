"""Gate §7 (P2b): validate a control-library entry's claimed FAIR-CAM functions
against the P2a crosswalk. Only NIST CSF + CIS tags are crosswalk-seeded; ISO/CSA
tags are carried for forward-compat but contribute no support (gate F-4) and are
NOT routed into validation. Returns the set of claimed functions NOT grounded by
the entry's framework tags (empty = fully grounded)."""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.enums import FairCamSubFunction
from idraa.services.crosswalk import CrosswalkService

# Seeded crosswalk frameworks (P2a). ISO/CSA deliberately excluded (gate F-4).
SEEDED_FRAMEWORKS = ("nist_csf", "cis")


async def unsupported_claims_for_entry(
    db: AsyncSession,
    *,
    nist_csf_subcategories: list[str],
    cis_safeguards: list[str],
    claimed: Iterable[FairCamSubFunction],
) -> set[FairCamSubFunction]:
    framework_tags: dict[str, list[str]] = {}
    if nist_csf_subcategories:
        framework_tags["nist_csf"] = nist_csf_subcategories
    if cis_safeguards:
        framework_tags["cis"] = cis_safeguards
    if not framework_tags:
        # No crosswalk-seeded tags at all → every claim is ungrounded.
        return set(claimed)
    return await CrosswalkService(db).validate_claims(framework_tags, claimed)
