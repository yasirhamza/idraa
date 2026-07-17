"""seed_control_library — create + seed the control library catalog (P2b).

Task 2 added the ORM models (control_library_entries +
control_library_entry_assignments) but NO migration, so this migration FIRST
creates both tables (matching src/idraa/models/control_library.py columns and
constraints exactly), THEN seeds them from
data/seed_control_library_entries.json.

NOT-NULL foot-gun (Task 5 spec): the INSERT statements include EVERY not-null
column — the scenario-library seed migration (c1d2e3f4a5b6) omitted a not-null
column and only passed because SQLite does not enforce NOT NULL on a value that
is absent from the column list of an INSERT. We do not repeat that here.

Every entry is validated via ControlLibraryEntrySeed.model_validate before
insert so seed-load failures surface at migration time, not at first browse.

Revision ID: d4f6a2b9c8e1
Revises: 7137e121145d
Create Date: 2026-06-02
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4f6a2b9c8e1"
down_revision: str | Sequence[str] | None = "7137e121145d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The 26-member FairCamSubFunction value list (matches the Enum value list the ORM
# emits via values_callable; mirrors a1b2c3d4e5f6's control_function_assignments).
_SUB_FUNCTION_VALUES = (
    "lec_prev_avoidance",
    "lec_prev_deterrence",
    "lec_prev_resistance",
    "lec_det_visibility",
    "lec_det_monitoring",
    "lec_det_recognition",
    "lec_resp_event_termination",
    "lec_resp_resilience",
    "lec_resp_loss_reduction",
    "vmc_prev_reduce_change_freq",
    "vmc_prev_reduce_variance_prob",
    "vmc_id_threat_intelligence",
    "vmc_id_control_monitoring",
    "vmc_corr_treatment_selection",
    "vmc_corr_implementation",
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
)

_CONTROL_TYPE_VALUES = ("technical", "administrative", "physical")


def _create_tables() -> None:
    op.create_table(
        "control_library_entries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("slug", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column(
            "control_type",
            sa.Enum(
                *_CONTROL_TYPE_VALUES,
                name="controltype",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("reference_annual_cost", sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column("nist_csf_subcategories", sa.JSON(), nullable=False),
        sa.Column("cis_safeguards", sa.JSON(), nullable=False),
        sa.Column("iso_27001_controls", sa.JSON(), nullable=False),
        sa.Column("compliance_mappings", sa.JSON(), nullable=False),
        sa.Column("applicable_industries", sa.JSON(), nullable=False),
        sa.Column("applicable_org_sizes", sa.JSON(), nullable=False),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("source_citations", sa.JSON(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "draft",
                "published",
                "deprecated",
                name="control_library_entry_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column(
            "row_version",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", "version", name="pk_control_library_entries"),
        sa.UniqueConstraint(
            "slug", "version", name="uq_control_library_entry_slug_version"
        ),
    )
    op.create_index(
        "ix_control_library_entry_status",
        "control_library_entries",
        ["status"],
        unique=False,
    )
    op.create_index(
        "ix_control_library_entry_control_type",
        "control_library_entries",
        ["control_type"],
        unique=False,
    )

    op.create_table(
        "control_library_entry_assignments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("library_entry_id", sa.Uuid(), nullable=False),
        sa.Column("library_entry_version", sa.Integer(), nullable=False),
        sa.Column(
            "sub_function",
            sa.Enum(
                *_SUB_FUNCTION_VALUES,
                name="faircamsubfunction",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("capability_default", sa.Float(), nullable=True),
        sa.Column("coverage_default", sa.Float(), nullable=False),
        sa.Column("reliability_default", sa.Float(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["library_entry_id", "library_entry_version"],
            ["control_library_entries.id", "control_library_entries.version"],
            name="fk_clea_entry",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "library_entry_id",
            "library_entry_version",
            "sub_function",
            name="uq_clea_entry_sub_function",
        ),
    )
    op.create_index(
        "ix_clea_entry",
        "control_library_entry_assignments",
        ["library_entry_id", "library_entry_version"],
        unique=False,
    )


def _seed() -> None:
    # F25 anchor: resolve via the package root (parent of the idraa package),
    # with a Path(__file__)-relative fallback for non-standard layouts. Mirrors
    # c1d2e3f4a5b6 / 3fc33f8e7ddc.
    import idraa

    project_root = Path(idraa.__file__).resolve().parent.parent.parent
    seed_path = project_root / "data" / "seed_control_library_entries.json"
    if not seed_path.exists():
        seed_path = (
            Path(__file__).resolve().parent.parent.parent
            / "data"
            / "seed_control_library_entries.json"
        )
    payload = json.loads(seed_path.read_text(encoding="utf-8"))

    # Validate every entry before insert so seed-load failures surface at
    # migration time (mirrors c1d2e3f4a5b6's LibraryEntrySeed wiring).
    from idraa.schemas.control_library import ControlLibraryEntrySeed

    validated = [ControlLibraryEntrySeed.model_validate(e) for e in payload["entries"]]

    bind = op.get_bind()
    now = datetime.now(UTC).isoformat()

    entry_insert = sa.text(
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
    assignment_insert = sa.text(
        """
        INSERT INTO control_library_entry_assignments
          (id, library_entry_id, library_entry_version, sub_function,
           capability_default, coverage_default, reliability_default,
           created_at, updated_at)
        VALUES
          (:id, :library_entry_id, 1, :sub_function,
           :capability_default, :coverage_default, :reliability_default,
           :now, :now)
        """
    )

    for seed in validated:
        eid = uuid.uuid4()
        cost: Decimal | None = seed.reference_annual_cost
        bind.execute(
            entry_insert,
            {
                "id": str(eid),
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
        for assignment in seed.assignments:
            bind.execute(
                assignment_insert,
                {
                    "id": str(uuid.uuid4()),
                    "library_entry_id": str(eid),
                    "sub_function": assignment.sub_function.value,
                    "capability_default": assignment.capability_default,
                    "coverage_default": assignment.coverage_default,
                    "reliability_default": assignment.reliability_default,
                    "now": now,
                },
            )


def upgrade() -> None:
    _create_tables()
    _seed()


def downgrade() -> None:
    op.drop_table("control_library_entry_assignments")
    op.drop_table("control_library_entries")
