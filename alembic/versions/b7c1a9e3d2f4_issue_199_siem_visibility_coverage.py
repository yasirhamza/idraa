"""#199 slice 1: SIEM lec_det_visibility coverage 0.8 -> 0.3 (residual-share interim).

Revision ID: b7c1a9e3d2f4
Revises: b00d4a63cd9c
Create Date: 2026-10-15

A SCORING value changes, so it ships as a VERSION BUMP of the one entry
(``security-information-event-management``), never an in-place cell update:
``control_library_entry_assignments`` is keyed by (entry id, entry version) and the
#438 resync machinery keys on ``ControlLibraryEntry.version``. Combines the
f6a2b0d4e8c3 delete-children -> bump-version -> insert-children pattern (same
``_seed_path()``, ``ControlLibraryEntrySeed`` validation, ``uuid4().hex`` ids) with
the b00d4a63cd9c already/apply/drift classification of the STORED cell at the
entry's latest version:

  stored coverage == 0.8  -> apply   (delete children, version + 1, re-insert every
                                      assignment of the current JSON entry)
  stored coverage == 0.3  -> already (no write; the fresh seed path lands here)
  anything else / no row  -> drift   (skip the slug; one WARNING naming slug and
                                      column only — never a value or a name)

No JSON-value guard (R-SIEM-8a): the JSON is the single source of truth, so a
lagging database on ``apply`` converges to whatever the JSON holds at upgrade time
— when the C11 milestone moves the cell past 0.3 a fresh DB must still boot
(docker-entrypoint.sh runs ``alembic upgrade head`` under ``set -e``). The frozen
literals classify only the STORED cell. No ``import math`` (M-B lane rule,
tests/scripts/test_clerk_config.py) and no ``assert`` (S101).

Never touches ``controls``, ``control_function_assignments`` or
``risk_analysis_runs``; adopted SIEM controls simply become resync-stale (#438) and
are handled post-deploy by ``python -m idraa library siem-resync`` (the service
module src/idraa/services/siem_visibility_resync.py). Parent framework-tag columns
are not rewritten (they are unchanged by this slice).

Downgrade: documented no-op (f6a2b0d4e8c3 precedent — the JSON seed is the single
source of truth). Because the downgrade writes nothing, a downgrade + re-upgrade
classifies ``already`` and does NOT bump the entry again, unless the stored cell is
back at exactly the old value (0.8), which classifies ``apply`` and bumps once more.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "b7c1a9e3d2f4"
down_revision: str | Sequence[str] | None = "b00d4a63cd9c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger("alembic.runtime.migration")

_SLUG = "security-information-event-management"
_SUB_FUNCTION = "lec_det_visibility"
_OLD_COVERAGE = 0.8
_NEW_COVERAGE = 0.3
_TOL = 1e-9


def _seed_path() -> Path:
    """data/seed_control_library_entries.json via the package root, with a
    __file__-relative fallback (mirrors f6a2b0d4e8c3._seed_path)."""
    import idraa

    root = Path(idraa.__file__).resolve().parent.parent.parent
    seed = root / "data" / "seed_control_library_entries.json"
    if not seed.exists():
        seed = (
            Path(__file__).resolve().parent.parent.parent
            / "data"
            / "seed_control_library_entries.json"
        )
    return seed


def _close(a: float | None, b: float) -> bool:
    return a is not None and abs(a - b) <= _TOL


def _classify(stored: float | None) -> str:
    if _close(stored, _OLD_COVERAGE):
        return "apply"
    if _close(stored, _NEW_COVERAGE):
        return "already"
    return "drift"


def upgrade() -> None:
    applied, already, drift = _apply(
        op.get_bind(), json.loads(_seed_path().read_text(encoding="utf-8"))
    )
    logger.info(
        "issue-199 siem visibility coverage: applied=%d already=%d drift=%d",
        applied,
        already,
        drift,
    )


def _apply(bind: sa.engine.Connection, payload: dict[str, Any]) -> tuple[int, int, int]:
    """Classify the stored cell and apply the bump. Separate from ``upgrade()`` so the
    lagging-JSON case is testable without monkeypatching alembic's module loader."""
    from idraa.schemas.control_library import ControlLibraryEntrySeed

    seeds = [e for e in payload["entries"] if e["slug"] == _SLUG]
    if len(seeds) != 1:
        raise RuntimeError(f"seed JSON must carry exactly one {_SLUG!r} entry")
    seed = ControlLibraryEntrySeed.model_validate(seeds[0])
    if not any(a.sub_function.value == _SUB_FUNCTION for a in seed.assignments):
        raise RuntimeError(f"seed JSON {_SLUG} carries no {_SUB_FUNCTION} assignment")

    applied = already = drift = 0

    row = bind.execute(
        sa.text(
            "SELECT id, version FROM control_library_entries "
            "WHERE slug = :slug ORDER BY version DESC LIMIT 1"
        ),
        {"slug": _SLUG},
    ).first()
    stored: float | None = None
    entry_id: object = None
    cur_version = 0
    if row is not None:
        entry_id, cur_version = row[0], int(row[1])
        cell = bind.execute(
            sa.text(
                "SELECT coverage_default FROM control_library_entry_assignments "
                "WHERE library_entry_id = :eid AND library_entry_version = :v "
                "AND sub_function = :sf"
            ),
            {"eid": entry_id, "v": cur_version, "sf": _SUB_FUNCTION},
        ).first()
        stored = None if cell is None or cell[0] is None else float(cell[0])

    cls = _classify(stored)
    if cls == "drift":
        drift += 1
        logger.warning(
            "issue-199 siem visibility coverage: drift in slug=%s column=%s.coverage_default; "
            "skipping slug",
            _SLUG,
            _SUB_FUNCTION,
        )
    elif cls == "already":
        already += 1
    else:
        now = datetime.now(UTC).isoformat()
        new_version = cur_version + 1
        bind.execute(
            sa.text(
                "DELETE FROM control_library_entry_assignments "
                "WHERE library_entry_id = :eid AND library_entry_version = :v"
            ),
            {"eid": entry_id, "v": cur_version},
        )
        bind.execute(
            sa.text(
                "UPDATE control_library_entries SET version = :nv, updated_at = :now "
                "WHERE id = :eid AND version = :v"
            ),
            {"nv": new_version, "now": now, "eid": entry_id, "v": cur_version},
        )
        insert = sa.text(
            """
            INSERT INTO control_library_entry_assignments
              (id, library_entry_id, library_entry_version, sub_function,
               capability_default, coverage_default, reliability_default,
               capability_provenance, capability_citations,
               coverage_provenance, coverage_citations,
               reliability_provenance, reliability_citations,
               created_at, updated_at)
            VALUES
              (:id, :library_entry_id, :library_entry_version, :sub_function,
               :capability_default, :coverage_default, :reliability_default,
               :capability_provenance, :capability_citations,
               :coverage_provenance, :coverage_citations,
               :reliability_provenance, :reliability_citations,
               :now, :now)
            """
        )
        for a in seed.assignments:
            bind.execute(
                insert,
                {
                    "id": uuid.uuid4().hex,
                    "library_entry_id": entry_id,
                    "library_entry_version": new_version,
                    "sub_function": a.sub_function.value,
                    "capability_default": a.capability_default,
                    "coverage_default": a.coverage_default,
                    "reliability_default": a.reliability_default,
                    "capability_provenance": a.capability_provenance,
                    "capability_citations": json.dumps(a.capability_citations),
                    "coverage_provenance": a.coverage_provenance,
                    "coverage_citations": json.dumps(a.coverage_citations),
                    "reliability_provenance": a.reliability_provenance,
                    "reliability_citations": json.dumps(a.reliability_citations),
                    "now": now,
                },
            )
        applied += 1

    return applied, already, drift


def downgrade() -> None:
    """No-op -- policy choice (f6a2b0d4e8c3 precedent): the pre-#199 coverage is
    recoverable from git history only; restoring it inline would dual-source the data."""
