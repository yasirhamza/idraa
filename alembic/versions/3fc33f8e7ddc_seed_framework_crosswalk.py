"""seed framework crosswalk

Revision ID: 3fc33f8e7ddc
Revises: 7026c068bbaa
Create Date: 2026-06-01 19:28:42.609000

Framework→FAIR-CAM crosswalk (P2a). Creates the ``framework_controls`` and
``framework_control_faircam`` tables (Task-1 models) and seeds them from
``data/seed_framework_crosswalk.json`` (261 entries: 108 nist_csf + 153 cis,
license CC-BY-NC-ND-4.0). Each entry is validated via ``CrosswalkSeed`` before
insert so seed-load failures surface at migration time. Inserts use
parameterized ``sa.text`` (NOT ORM models) for import-stability against future
model changes. Does NOT import openpyxl or scripts.build_crosswalk_seed (Task-6
boundary).
"""

import json
import uuid
from pathlib import Path
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "3fc33f8e7ddc"
down_revision: Union[str, Sequence[str], None] = "7026c068bbaa"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# The 26 FairCamSubFunction slugs (PR iota freeze, spec §6.2). Hard-coded here —
# rather than imported from idraa.models.enums — so the migration stays
# import-stable if the enum is later extended/renamed (the on-disk historical
# DDL must not shift under a model edit). Mirrors the value list in
# a1b2c3d4e5f6_phase_1_5b_alpha_cfa.py.
_FAIRCAM_SUBFUNCTION_VALUES = (
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


def upgrade() -> None:
    """Create the crosswalk tables and seed them from the JSON resource."""
    # Step 1: create framework_controls (matches Task-1 FrameworkControl model).
    op.create_table(
        "framework_controls",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("framework", sa.String(length=32), nullable=False),
        sa.Column("framework_version", sa.String(length=16), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("asset_type", sa.String(length=128), nullable=True),
        sa.Column("security_function", sa.String(length=128), nullable=True),
        sa.Column("citation", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "framework",
            "framework_version",
            "code",
            name="uq_framework_control_code",
        ),
    )
    op.create_index(
        "ix_framework_controls_framework",
        "framework_controls",
        ["framework"],
        unique=False,
    )

    # Step 2: create framework_control_faircam (matches FrameworkControlFairCam).
    # fair_cam_function is Enum(FairCamSubFunction, native_enum=False) — by
    # project convention a VARCHAR + CHECK on SQLite. Name matches the ORM's
    # generated constraint name (faircamsubfunction).
    op.create_table(
        "framework_control_faircam",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("framework_control_id", sa.Uuid(), nullable=False),
        sa.Column(
            "fair_cam_function",
            sa.Enum(
                *_FAIRCAM_SUBFUNCTION_VALUES,
                name="faircamsubfunction",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["framework_control_id"],
            ["framework_controls.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "framework_control_id",
            "fair_cam_function",
            name="uq_framework_control_faircam",
        ),
    )
    op.create_index(
        "ix_framework_control_faircam_framework_control_id",
        "framework_control_faircam",
        ["framework_control_id"],
        unique=False,
    )

    # Step 3: seed from the JSON resource.
    # Paranoid-review (Major-finding F25 path): resolve the seed JSON via an
    # explicit project-root anchor (parent of the package), then walk up to the
    # repo root — same anchor pattern as c1d2e3f4a5b6_seed_library_entries.py.
    import idraa

    project_root = Path(idraa.__file__).resolve().parent.parent.parent
    seed_path = project_root / "data" / "seed_framework_crosswalk.json"
    if not seed_path.exists():
        # Fallback for non-standard layouts (CI artefacts, packaged distros).
        seed_path = (
            Path(__file__).resolve().parent.parent.parent
            / "data"
            / "seed_framework_crosswalk.json"
        )
    payload = json.loads(seed_path.read_text(encoding="utf-8"))
    entries = payload["entries"]

    # Validate each entry via CrosswalkSeed before insert so seed-load failures
    # surface at migration time, not at first browse query.
    from idraa.schemas.crosswalk import CrosswalkSeed

    bind = op.get_bind()
    controls_tbl = sa.table(
        "framework_controls",
        sa.column("id", sa.Uuid()),
        sa.column("framework", sa.String()),
        sa.column("framework_version", sa.String()),
        sa.column("code", sa.String()),
        sa.column("title", sa.String()),
        sa.column("description", sa.Text()),
        sa.column("asset_type", sa.String()),
        sa.column("security_function", sa.String()),
        sa.column("citation", sa.JSON()),
    )
    faircam_tbl = sa.table(
        "framework_control_faircam",
        sa.column("id", sa.Uuid()),
        sa.column("framework_control_id", sa.Uuid()),
        sa.column("fair_cam_function", sa.String()),
    )

    for raw in entries:
        seed = CrosswalkSeed.model_validate(raw)
        control_id = uuid.uuid4()
        bind.execute(
            controls_tbl.insert().values(
                id=control_id,
                framework=seed.framework,
                framework_version=seed.framework_version,
                code=seed.code,
                title=seed.title,
                description=None,
                asset_type=seed.asset_type,
                security_function=seed.security_function,
                # citation column is sa.JSON(); the type's bind processor
                # serializes the dict — pass it directly (no pre-json.dumps,
                # which would double-encode).
                citation=seed.citation,
            )
        )
        # #449: compose the FAIR-Institute base layer with the structurally
        # separate RiskFlow extension layer at load time. This migration predates
        # the ``is_extension`` provenance column (added + backfilled by the #449
        # provenance migration, which runs later in the chain), so both layers
        # insert identically here; the later ext-link migrations (f1a2b3c4d5e6 /
        # c7e2a9b4f1d6) see the links already present and skip idempotently.
        for fn in [*seed.fair_cam_functions, *seed.riskflow_extension_functions]:
            bind.execute(
                faircam_tbl.insert().values(
                    id=uuid.uuid4(),
                    framework_control_id=control_id,
                    # store the enum's .value string
                    fair_cam_function=fn.value,
                )
            )


def downgrade() -> None:
    """Drop the crosswalk tables (child first for the FK)."""
    op.drop_table("framework_control_faircam")
    op.drop_table("framework_controls")
