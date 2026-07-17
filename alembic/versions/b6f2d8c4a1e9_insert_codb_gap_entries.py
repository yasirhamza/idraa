"""Insert the #459 CODB-gap control-library entries (MDR + EASM).

Revision ID: b6f2d8c4a1e9
Revises: e7b1c9d4a2f8
Create Date: 2026-07-10

Two new canonical control-library entries closing the IBM CODB 2025 Fig 39
product-category gaps (#459): Managed Detection and Response (MDR/MSSP; threat
hunting folded in per plan D3) and External Attack Surface Management (EASM).
Plan: docs/superpowers/plans/2026-07-10-codb-gap-entries-459.md (4-reviewer
plan-gate converged 0/0).

Convergence: payloads are read from ``data/seed_control_library_entries.json``
via ``ControlLibraryEntrySeed`` (single source; fresh DBs get the same rows
from d4f6a2b9c8e1 reading the same JSON, and this migration's insert-if-absent
guard no-ops there). Assignment INSERT enumerates the FULL column list
including the six provenance/citations columns added by e14c75d22129 —
``capability_provenance`` has no server_default, so omitting it would write
NULL against the model's "set iff capability_default set" invariant; the
validated seed objects carry the auto-filled 'expert-estimate' values.
All ids are ``uuid4().hex`` (no-hyphen; do NOT copy d4f6a2b9c8e1's hyphenated
``str(eid)``, later format-fixed by b3e9c1a47d52). Durable guard:
tests/migrations/test_control_library_uuid_format_fix.py (runs through head).

This migration guards EXISTENCE only: a post-merge edit to either payload's
values reaches fresh DBs (d4f6a2b9c8e1 re-reads the JSON) but NOT deployed
DBs (the exists-guard no-ops here) — value changes require a NEW recuration
migration, per the #437 precedent.

Downgrade deletes the assignment rows FIRST (SQLite runs Alembic with
foreign_keys OFF, so the FK CASCADE will not fire), then the entries by
slug + version = 1. No ``source`` guard — control_library_entries has no such
column and no user-writable path targets the table (entries are seed-only).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b6f2d8c4a1e9"
down_revision: str | Sequence[str] | None = "e7b1c9d4a2f8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Pinned slugs this migration owns (sync-guard test asserts ⊆ seed JSON slugs).
GAP_SLUGS: tuple[str, ...] = (
    "managed-detection-response",
    "external-attack-surface-management",
)


def _seed_by_slug() -> dict:
    import idraa
    from idraa.schemas.control_library import ControlLibraryEntrySeed

    project_root = Path(idraa.__file__).resolve().parent.parent.parent
    seed_path = project_root / "data" / "seed_control_library_entries.json"
    if not seed_path.exists():
        seed_path = (
            Path(__file__).resolve().parent.parent.parent
            / "data"
            / "seed_control_library_entries.json"
        )
    payload = json.loads(seed_path.read_text(encoding="utf-8"))
    by_slug = {
        e["slug"]: ControlLibraryEntrySeed.model_validate(e)
        for e in payload["entries"]
        if e["slug"] in GAP_SLUGS
    }
    missing = set(GAP_SLUGS) - set(by_slug)
    if missing:  # pragma: no cover - defensive: seed must contain both gap slugs
        raise RuntimeError(f"#459 gap slug(s) absent from seed JSON: {sorted(missing)}")
    return by_slug


_ENTRY_INSERT = sa.text(
    """
    INSERT INTO control_library_entries
      (id, version, slug, name, description, control_type,
       reference_annual_cost, nist_csf_subcategories, cis_safeguards,
       iso_27001_controls, compliance_mappings, applicable_industries,
       applicable_org_sizes, tags, source_citations, status,
       row_version, created_at, updated_at)
    VALUES
      (:id, 1, :slug, :name, :description, :control_type,
       :reference_annual_cost, :nist_csf_subcategories, :cis_safeguards,
       :iso_27001_controls, :compliance_mappings, :applicable_industries,
       :applicable_org_sizes, :tags, :source_citations, :status,
       1, :now, :now)
    """
)

_ASSIGNMENT_INSERT = sa.text(
    """
    INSERT INTO control_library_entry_assignments
      (id, library_entry_id, library_entry_version, sub_function,
       capability_default, coverage_default, reliability_default,
       capability_provenance, capability_citations,
       coverage_provenance, coverage_citations,
       reliability_provenance, reliability_citations,
       created_at, updated_at)
    VALUES
      (:id, :library_entry_id, 1, :sub_function,
       :capability_default, :coverage_default, :reliability_default,
       :capability_provenance, :capability_citations,
       :coverage_provenance, :coverage_citations,
       :reliability_provenance, :reliability_citations,
       :now, :now)
    """
)


def upgrade() -> None:
    bind = op.get_bind()
    now = datetime.now(UTC).isoformat()
    by_slug = _seed_by_slug()

    for slug in GAP_SLUGS:
        exists = bind.execute(
            sa.text("SELECT 1 FROM control_library_entries WHERE slug = :slug"),
            {"slug": slug},
        ).scalar()
        if exists:
            # Fresh DB already seeded all 63 from the JSON via d4f6a2b9c8e1 — no-op.
            continue
        seed = by_slug[slug]
        eid = uuid.uuid4().hex
        cost = seed.reference_annual_cost
        bind.execute(
            _ENTRY_INSERT,
            {
                "id": eid,
                "slug": seed.slug,
                "name": seed.name,
                "description": seed.description,
                "control_type": seed.control_type.value,
                "reference_annual_cost": str(cost) if cost is not None else None,
                "nist_csf_subcategories": json.dumps(seed.nist_csf_subcategories),
                "cis_safeguards": json.dumps(seed.cis_safeguards),
                "iso_27001_controls": json.dumps(seed.iso_27001_controls),
                "compliance_mappings": json.dumps(seed.compliance_mappings),
                "applicable_industries": json.dumps(seed.applicable_industries),
                "applicable_org_sizes": json.dumps(seed.applicable_org_sizes),
                "tags": json.dumps(seed.tags),
                "source_citations": json.dumps(seed.source_citations),
                "status": seed.status,
                "now": now,
            },
        )
        for a in seed.assignments:
            bind.execute(
                _ASSIGNMENT_INSERT,
                {
                    "id": uuid.uuid4().hex,
                    "library_entry_id": eid,
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


def downgrade() -> None:
    bind = op.get_bind()
    for slug in GAP_SLUGS:
        eid = bind.execute(
            sa.text(
                "SELECT id FROM control_library_entries "
                "WHERE slug = :slug AND version = 1"
            ),
            {"slug": slug},
        ).scalar()
        if eid is None:
            continue
        # Assignments first: SQLite Alembic runs with foreign_keys OFF, so the
        # FK ondelete=CASCADE will NOT fire.
        bind.execute(
            sa.text(
                "DELETE FROM control_library_entry_assignments "
                "WHERE library_entry_id = :eid AND library_entry_version = 1"
            ),
            {"eid": eid},
        )
        bind.execute(
            sa.text(
                "DELETE FROM control_library_entries WHERE slug = :slug AND version = 1"
            ),
            {"slug": slug},
        )
