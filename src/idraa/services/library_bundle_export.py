"""Scenario-library export — entry → ``LibraryEntrySeed``-shaped dict (authored fields).

The export serializer is the inverse of the import path: it emits the EXACT
authored fields of ``LibraryEntrySeed`` (``data/seed_library_entries.json``
shape) and nothing else. DB-managed fields (``id`` / ``version`` /
``row_version`` / ``source`` / ``created_at`` / ``updated_at``) are EXCLUDED so a
downloaded bundle is content-only and re-imports as fresh ``imported`` entries.

Round-trip invariant (load-bearing, methodology-reviewed):
``EXPORT_FIELDS`` == the authored seed fields minus ``LEGACY_SEED_FIELDS`` — the
authored fields the import side still accepts, derived directly from the seed
model so any field added to the authored seed schema is automatically exported.
Distributions are emitted exactly (JSON preserves int vs float; there is NO
``collapse_num`` on the JSON bundle path), so export → ``parse_bundle`` →
``_validate_entries`` reproduces the source authored fields identically.

The JSON columns on ``ScenarioLibraryEntry`` (tags, distributions,
calibration_anchor, …) use SQLAlchemy's ``JSON`` type, so ``getattr`` returns
already-deserialized Python list/dict objects — ``json.dumps`` re-serializes
them correctly with no intermediate ``json.loads``. The two enum-typed columns
(threat_event_type / asset_class) return enum members, so they are serialized
via ``.value``; ``threat_community`` serialises as the related community's slug.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from fastapi import Response

from idraa.models.scenario_library import ScenarioLibraryEntry
from idraa.services.seed_library_loader import LEGACY_SEED_FIELDS, LibraryEntrySeed
from idraa.utils.download import attachment_disposition

# Exactly the authored seed fields the import side still accepts, in
# declaration order. Deriving from the seed model is the contract: it can
# never drift from the import-side schema. LEGACY_SEED_FIELDS (threat_actor_type)
# is excluded -- the canonical export emits threat_community only.
EXPORT_FIELDS: list[str] = [f for f in LibraryEntrySeed.model_fields if f not in LEGACY_SEED_FIELDS]


def entry_to_seed_obj(entry: ScenarioLibraryEntry) -> dict[str, Any]:
    """Serialize one entry to a ``LibraryEntrySeed``-shaped dict (authored fields).

    Enum-valued attributes emit their ``.value`` string. JSON columns emit
    already-deserialized Python objects. ``threat_community`` emits the
    related community's slug (callers pass loaded entries -- the repo's
    ``lazy="joined", innerjoin=True`` relationship -- or a transient entry with
    the relationship set explicitly). DB-managed fields are excluded by
    construction (they are not in ``EXPORT_FIELDS``).
    """
    out: dict[str, Any] = {}
    for f in EXPORT_FIELDS:
        if f == "threat_community":
            out[f] = entry.threat_community.slug
            continue
        v = getattr(entry, f)
        out[f] = v.value if hasattr(v, "value") else v
    return out


def export_bundle_response(
    entries: Iterable[ScenarioLibraryEntry],
    *,
    filename: str,
) -> Response:
    """Build a JSON-array attachment ``Response`` from entries."""
    payload = json.dumps([entry_to_seed_obj(e) for e in entries], indent=2)
    return Response(
        content=payload.encode("utf-8"),
        media_type="application/json",
        headers={
            "Content-Disposition": attachment_disposition(filename),
            # idraa#110: bulk-egress no-store parity (csv_response / PDF / #109).
            "Cache-Control": "private, no-store",
        },
    )
