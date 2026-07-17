"""phase_1_5b_alpha_cfa — PR iota Control schema reshape.

Creates control_function_assignments, smart backfill from (domain, function),
drops control_strength / control_reliability / control_coverage / function
from the controls table.

Revision ID: a1b2c3d4e5f6
Revises: c1d2e3f4a5b6
Create Date: 2026-04-30

Pre-merge runbook (operator executes before merging to production):
1. Backup: cp riskflow.db riskflow.db.backup-$(date +%Y%m%d%H%M%S)
2. Run migration: uv run alembic upgrade head
3. Verify CFA rows: SELECT COUNT(*) FROM control_function_assignments
   Expected: equal to SELECT COUNT(*) FROM controls
4. Verify dropped columns absent: PRAGMA table_info(controls) -- SQLite
   Expected: no row for control_strength, control_reliability, control_coverage, function
5. Verify valid sub_functions: SELECT DISTINCT sub_function FROM control_function_assignments
   Expected: every value in the 26-member FairCamSubFunction enum
6. Verify NULL capability restricted to TIME/CURRENCY-unit sub-functions:
   SELECT COUNT(*) FROM control_function_assignments
   WHERE capability_value IS NULL
   AND sub_function IN ('lec_prev_avoidance','lec_prev_deterrence','lec_prev_resistance',
     'lec_det_visibility','lec_det_recognition','vmc_prev_reduce_change_freq',
     'vmc_prev_reduce_variance_prob','dsc_prev_defined_expectations',
     'dsc_prev_communication','dsc_prev_sa_data_asset','dsc_prev_sa_data_threat',
     'dsc_prev_sa_data_controls','dsc_prev_sa_analysis','dsc_prev_sa_reporting',
     'dsc_prev_ensure_capability','dsc_prev_incentives')
   Expected: 0
7. Verify confirmed_by_user_at NULL on all backfilled rows:
   SELECT COUNT(*) FROM control_function_assignments WHERE confirmed_by_user_at IS NOT NULL
   Expected: 0
8. Run test suite: uv run pytest tests/ -q

Rollback: uv run alembic downgrade -1
Lossy downgrade note: capability_value=NULL rows (TIME/CURRENCY-unit backfills)
restore to control_strength=0.5 via COALESCE. Original value is not recoverable.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: str | Sequence[str] | None = "c1d2e3f4a5b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create control_function_assignments, smart backfill, drop legacy columns.

    Steps:
      1. Create control_function_assignments table with all constraints.
      2. Smart backfill: read (domain, function) BEFORE dropping function column;
         map to Standard sub_function per OQ2 table; write one CFA row per Control.
         Python-side uuid.uuid4() for IDs (SQLite-compatible; no gen_random_uuid()).
      3. Drop control_strength, control_reliability, control_coverage, function
         from controls table.
    """
    # Step 1: Create control_function_assignments table
    op.create_table(
        "control_function_assignments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("control_id", sa.Uuid(), nullable=False),
        # sub_function is a 26-value StrEnum; project convention uses
        # Enum(<StrEnum>, native_enum=False) (matches the existing ``domain``
        # column in 355450b21719_phase_1_2_controls.py).  The enum value list
        # is the 26 ``FairCamSubFunction`` slugs (PR iota freeze, spec §6.2).
        sa.Column(
            "sub_function",
            sa.Enum(
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
                name="faircamsubfunction",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("capability_value", sa.Float(), nullable=True),
        sa.Column("coverage", sa.Float(), nullable=False),
        sa.Column("reliability", sa.Float(), nullable=False),
        sa.Column(
            "confirmed_by_user_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column("derived_from_assignment_id", sa.Uuid(), nullable=True),
        sa.Column("measured_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("measured_by", sa.Uuid(), nullable=True),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "capability_value IS NULL OR capability_value >= 0.0",
            name="ck_cfa_capability_nonneg",
        ),
        sa.CheckConstraint(
            "coverage >= 0.0 AND coverage <= 1.0",
            name="ck_cfa_coverage_range",
        ),
        sa.CheckConstraint(
            "reliability >= 0.0 AND reliability <= 1.0",
            name="ck_cfa_reliability_range",
        ),
        sa.CheckConstraint(
            "sub_function != 'dsc_corr_misaligned' OR derived_from_assignment_id IS NOT NULL",
            name="ck_cfa_virtual_requires_derived",
        ),
        sa.ForeignKeyConstraint(
            ["control_id"], ["controls.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["derived_from_assignment_id"],
            ["control_function_assignments.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["measured_by"], ["users.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "control_id", "sub_function", name="uq_cfa_control_sub_function"
        ),
    )
    op.create_index(
        "ix_cfa_control_id",
        "control_function_assignments",
        ["control_id"],
        unique=False,
    )
    op.create_index(
        "ix_cfa_organization_id",
        "control_function_assignments",
        ["organization_id"],
        unique=False,
    )

    # Step 2: Smart backfill.
    # Read (domain, function, control_strength, control_coverage, control_reliability,
    # created_by, organization_id) BEFORE dropping the function column.
    # Map (domain, function) -> (sub_function_slug, preserve_capability).
    # preserve_capability=True  -> PROBABILITY/PERCENT_REDUCTION unit; write control_strength.
    # preserve_capability=False -> ELAPSED_TIME/CURRENCY unit; write capability_value=NULL (OQ1).
    # Python-side uuid.uuid4() per spec §6.6 / B1 (SQLite has no gen_random_uuid()).
    # Pattern mirrors c1d2e3f4a5b6_seed_library_entries.py:76.
    # Both ``domain`` and ``function`` are stored in the controls table as
    # ``Enum(<StrEnum>, native_enum=False)`` columns (see Phase 1.2 migration
    # 355450b21719) — SA writes the StrEnum value, which by project convention
    # is UPPERCASE for ControlDomain and ControlFunction.  Map keys MUST match
    # the on-disk case (UPPERCASE), or the lookup silently misses every row
    # and the fallback is taken (B4 paranoid-review fix).
    _subfn_map: dict[tuple[str, str], tuple[str, bool]] = {
        # (DOMAIN_VALUE, FUNCTION_VALUE) -> (sub_function_slug, preserve_capability)
        ("LOSS_EVENT",          "PREVENTIVE"):   ("lec_prev_resistance",           True),
        ("LOSS_EVENT",          "DETECTIVE"):    ("lec_det_visibility",             True),
        ("LOSS_EVENT",          "CORRECTIVE"):   ("lec_resp_event_termination",     False),
        ("LOSS_EVENT",          "COMPENSATING"): ("lec_prev_resistance",            True),
        ("VARIANCE_MANAGEMENT", "PREVENTIVE"):   ("vmc_prev_reduce_variance_prob",  True),
        ("VARIANCE_MANAGEMENT", "DETECTIVE"):    ("vmc_id_control_monitoring",      False),
        ("VARIANCE_MANAGEMENT", "CORRECTIVE"):   ("vmc_corr_implementation",        False),
        ("VARIANCE_MANAGEMENT", "COMPENSATING"): ("vmc_prev_reduce_variance_prob",  True),
        ("DECISION_SUPPORT",    "PREVENTIVE"):   ("dsc_prev_defined_expectations",  True),
        ("DECISION_SUPPORT",    "DETECTIVE"):    ("dsc_id_misaligned",              False),
        # CORRECTIVE falls back to dsc_id_misaligned NOT dsc_corr_misaligned (virtual!)
        ("DECISION_SUPPORT",    "CORRECTIVE"):   ("dsc_id_misaligned",              False),
        ("DECISION_SUPPORT",    "COMPENSATING"): ("dsc_prev_defined_expectations",  True),
    }
    # Safe fallback for any unexpected (domain, function) combo not in the map.
    _fallback_subfn = ("lec_prev_resistance", True)

    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT id, domain, function, control_strength, control_coverage, "
            "control_reliability, created_by, organization_id FROM controls"
        )
    ).fetchall()

    for row in rows:
        control_id    = row[0]
        domain        = row[1]
        function      = row[2]
        strength      = row[3]
        coverage      = row[4]
        reliability   = row[5]
        created_by    = row[6]
        org_id        = row[7]

        # domain and function are stored as StrEnum values (UPPERCASE per project
        # convention).  ``.upper()`` here is defense-in-depth — not strictly
        # required after the case fix in _subfn_map keys (B4), but harmless and
        # protects against a hypothetical regression where SA returns lowercase.
        domain_upper = domain.upper() if domain else "LOSS_EVENT"
        fn_upper = function.upper() if function else "PREVENTIVE"
        subfn, preserve = _subfn_map.get((domain_upper, fn_upper), _fallback_subfn)
        cap_val = strength if preserve else None

        bind.execute(
            sa.text(
                "INSERT INTO control_function_assignments "
                "(id, control_id, sub_function, capability_value, coverage, reliability, "
                " confirmed_by_user_at, derived_from_assignment_id, measured_at, measured_by, "
                " organization_id, created_at, updated_at) "
                "VALUES (:id, :control_id, :sub_function, :capability_value, :coverage, "
                ":reliability, NULL, NULL, NULL, :measured_by, :org_id, "
                "(CURRENT_TIMESTAMP), (CURRENT_TIMESTAMP))"
            ),
            {
                "id": str(uuid.uuid4()),
                "control_id": str(control_id),
                "sub_function": subfn,
                "capability_value": cap_val,
                "coverage": float(coverage) if coverage is not None else 0.8,
                "reliability": float(reliability) if reliability is not None else 0.8,
                "measured_by": str(created_by) if created_by else None,
                "org_id": str(org_id),
            },
        )

    # Step 3: Drop legacy effectiveness columns and function column.
    op.drop_column("controls", "control_strength")
    op.drop_column("controls", "control_reliability")
    op.drop_column("controls", "control_coverage")
    op.drop_column("controls", "function")


def downgrade() -> None:
    """Re-add legacy Control columns, restore via correlated subquery, drop CFA table.

    LOSSY: capability_value=NULL rows (ELAPSED_TIME/CURRENCY-unit backfills) restore
    to control_strength=0.5 via COALESCE. The original pre-migration control_strength
    value is NOT recoverable for these rows. Document this in the operator runbook
    and as a docstring warning here.

    The correlated subquery is SQLite + Postgres compatible (spec §6.6, B2).
    No UPDATE...FROM syntax (not SQLite-compatible).
    """
    bind = op.get_bind()

    # Step 1: Re-add the four dropped columns as NULLABLE first (SQLite ALTER TABLE
    # cannot add a NOT NULL column without a default in a single statement).
    op.add_column(
        "controls",
        sa.Column("function", sa.String(), nullable=True),
    )
    op.add_column(
        "controls",
        sa.Column("control_strength", sa.Float(), nullable=True),
    )
    op.add_column(
        "controls",
        sa.Column("control_reliability", sa.Float(), nullable=True),
    )
    op.add_column(
        "controls",
        sa.Column("control_coverage", sa.Float(), nullable=True),
    )

    # Step 2: Restore values using correlated subquery (SQLite + Postgres compatible).
    # COALESCE handles NULL capability_value rows from TIME-unit backfills (OQ1, B2):
    #   capability_value=NULL -> control_strength=0.5 (safe default; original not recoverable).
    # LIMIT 1 is SQLite syntax and is harmless on Postgres where the subquery
    # returns at most one row per the UNIQUE constraint.
    bind.execute(
        sa.text("""
            UPDATE controls
            SET
                control_strength = (
                    SELECT COALESCE(cfa.capability_value, 0.5)
                    FROM control_function_assignments cfa
                    WHERE cfa.control_id = controls.id
                    LIMIT 1
                ),
                control_reliability = (
                    SELECT cfa.reliability
                    FROM control_function_assignments cfa
                    WHERE cfa.control_id = controls.id
                    LIMIT 1
                ),
                control_coverage = (
                    SELECT cfa.coverage
                    FROM control_function_assignments cfa
                    WHERE cfa.control_id = controls.id
                    LIMIT 1
                ),
                function = 'PREVENTIVE'
        """)
    )

    # Step 3: Apply NOT NULL fallback for any control with no CFA rows.
    # These would be orphaned controls created after the upgrade but before this
    # downgrade — pathological case but must not leave NULL in a NOT NULL column.
    bind.execute(
        sa.text("""
            UPDATE controls
            SET control_strength    = 0.5,
                control_reliability = 0.5,
                control_coverage    = 0.5,
                function            = 'PREVENTIVE'
            WHERE control_strength IS NULL
        """)
    )

    # Step 4: Tighten columns to NOT NULL (SQLite does not support ALTER COLUMN;
    # use Alembic's batch_alter_table for portability).
    with op.batch_alter_table("controls") as batch_op:
        batch_op.alter_column("function", nullable=False)
        batch_op.alter_column("control_strength", nullable=False)
        batch_op.alter_column("control_reliability", nullable=False)
        batch_op.alter_column("control_coverage", nullable=False)

    # Step 5: Drop CFA table (indexes first, then table).
    op.drop_index("ix_cfa_organization_id", table_name="control_function_assignments")
    op.drop_index("ix_cfa_control_id", table_name="control_function_assignments")
    op.drop_table("control_function_assignments")
