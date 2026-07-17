"""Scenario-library export — entry → ``LibraryEntrySeed``-shaped dict (authored fields).

The export serializer is the inverse of the import path: it emits the EXACT
authored fields of ``LibraryEntrySeed`` (``data/seed_library_entries.json``
shape) and nothing else. DB-managed fields (``id`` / ``version`` /
``row_version`` / ``source`` / ``created_at`` / ``updated_at``) are EXCLUDED so a
downloaded bundle is content-only and re-imports as fresh ``imported`` entries.

Round-trip invariant (load-bearing, methodology-reviewed):
``set(EXPORT_FIELDS) == set(LibraryEntrySeed.model_fields)`` — guaranteed by
deriving ``EXPORT_FIELDS`` directly from the seed model — so any field added to
the authored seed schema is automatically exported. Distributions are emitted
exactly (JSON preserves int vs float; there is NO ``collapse_num`` on the JSON
bundle path), so export → ``parse_bundle`` → ``_validate_entries`` reproduces
the source authored fields identically.

The JSON columns on ``ScenarioLibraryEntry`` (tags, distributions,
calibration_anchor, …) use SQLAlchemy's ``JSON`` type, so ``getattr`` returns
already-deserialized Python list/dict objects — ``json.dumps`` re-serializes
them correctly with no intermediate ``json.loads``. The three enum-typed columns
(threat_event_type / threat_actor_type / asset_class) return enum members, so
they are serialized via ``.value``.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from fastapi import Response

from idraa.models.scenario_library import ScenarioLibraryEntry
from idraa.services.seed_library_loader import LibraryEntrySeed

# Exactly the authored seed fields, in declaration order. Deriving from the seed
# model is the contract: it can never drift from the import-side schema.
EXPORT_FIELDS: list[str] = list(LibraryEntrySeed.model_fields)


def entry_to_seed_obj(entry: ScenarioLibraryEntry) -> dict[str, Any]:
    """Serialize one entry to a ``LibraryEntrySeed``-shaped dict (authored fields).

    Enum-valued attributes emit their ``.value`` string. JSON columns emit
    already-deserialized Python objects. DB-managed fields are excluded by
    construction (they are not in ``EXPORT_FIELDS``).
    """
    out: dict[str, Any] = {}
    for f in EXPORT_FIELDS:
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
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
