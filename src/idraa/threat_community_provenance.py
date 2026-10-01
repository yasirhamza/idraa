"""Threat-community provenance constants + the ONE snapshot classification rule.

Pure module: NO idraa.db / idraa.models / sqlalchemy imports — services/reports.py and
pdf_report.py import it and are purity-tested (subprocess) against idraa.db. The dashboard
lens and the PDF both call community_by_scenario so a scenario is bucketed identically on
both surfaces: grouped by SLUG (names are neither unique nor stable across versions),
displayed by the snapshot's NAME.
"""

from __future__ import annotations

import uuid
from typing import Any

PROVENANCE_VALUES = ("assigned", "migrated", "migrated_split_default", "unassigned")
REVIEW_PROVENANCES = frozenset({"migrated_split_default", "unassigned"})
NEEDS_REVIEW_SLUG = "__needs_review__"
NEEDS_REVIEW_LABEL = "Needs review / unassigned"


def community_by_scenario(snapshot: dict[str, Any] | None) -> dict[str, tuple[str, str] | None]:
    """scenario_id.hex -> (slug, name), or None (review state / no or malformed community / pre-P1)."""
    out: dict[str, tuple[str, str] | None] = {}
    if not isinstance(snapshot, dict):
        return {}
    scenarios = snapshot.get("scenarios")
    if not isinstance(scenarios, list):
        return {}
    for sc in scenarios:
        if not isinstance(sc, dict):
            continue
        try:
            sid = uuid.UUID(str(sc.get("scenario_id"))).hex
        except (ValueError, TypeError, AttributeError):
            continue
        tc = sc.get("threat_community")
        prov = sc.get("threat_community_provenance", "assigned")
        if (
            not isinstance(tc, dict)
            or not tc.get("slug")
            or not isinstance(prov, str)
            or prov in REVIEW_PROVENANCES
        ):
            out[sid] = None
        else:
            out[sid] = (str(tc["slug"]), str(tc.get("name") or tc["slug"]))
    return out
