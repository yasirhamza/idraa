"""attack catalog and mappings

Revision ID: 6108f3899a78
Revises: d054442ed13b
Create Date: 2026-07-04 19:33:25.391935

Issue #475 PR 1: creates the ATT&CK catalog tables (attack_tactics,
attack_techniques), the curated library-entry mapping table
(library_entry_attack_mappings), and the org scenario mapping table
(scenario_attack_mappings), then seeds the catalog from
data/seed_attack_catalog.json (MITRE ATT&CK Enterprise + ICS, techniques only).

Every seed row is validated via the Task-2 seed schemas before insert.
Inserts use parameterized sa.table().insert() with native uuid.uuid4()
objects, never the hyphenated-string form (the raw-text no-hyphen foot-gun).
"""

import json
import uuid
from pathlib import Path
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "6108f3899a78"
down_revision: Union[str, Sequence[str], None] = "d054442ed13b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "attack_tactics",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("domain", sa.String(length=16), nullable=False),
        sa.Column("tactic_id", sa.String(length=16), nullable=False),
        sa.Column("shortname", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("display_order", sa.Integer(), nullable=False),
        sa.Column("url", sa.String(length=256), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("domain", "tactic_id", name="uq_attack_tactic_id"),
        sa.UniqueConstraint("domain", "shortname", name="uq_attack_tactic_shortname"),
    )
    op.create_index("ix_attack_tactics_domain", "attack_tactics", ["domain"])

    op.create_table(
        "attack_techniques",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("domain", sa.String(length=16), nullable=False),
        sa.Column("technique_id", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("tactics", sa.JSON(), nullable=False),
        sa.Column("parent_technique_id", sa.String(length=16), nullable=True),
        # SC-N5 (Task-1 methodology review): mirrors the repo's existing
        # Boolean server_default idiom (risk_analysis_run.py's ``is_stale``) —
        # a quoted string "0" literal, NOT sa.text("0") / sa.false().
        sa.Column("deprecated", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("catalog_version", sa.String(length=16), nullable=False),
        sa.Column("url", sa.String(length=256), nullable=False),
        sa.Column("citation", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("domain", "technique_id", name="uq_attack_technique_id"),
    )
    op.create_index("ix_attack_techniques_domain", "attack_techniques", ["domain"])

    op.create_table(
        "library_entry_attack_mappings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("library_entry_id", sa.Uuid(), nullable=False),
        sa.Column("library_entry_version", sa.Integer(), nullable=False),
        sa.Column("technique_id", sa.Uuid(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("provenance", sa.String(length=32), nullable=False),
        sa.Column("citations", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "library_entry_id",
            "library_entry_version",
            "technique_id",
            name="uq_library_entry_attack_mapping",
        ),
        sa.ForeignKeyConstraint(
            ["library_entry_id", "library_entry_version"],
            ["scenario_library_entries.id", "scenario_library_entries.version"],
            name="fk_leam_entry_version",
        ),
        sa.ForeignKeyConstraint(["technique_id"], ["attack_techniques.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "ix_leam_entry",
        "library_entry_attack_mappings",
        ["library_entry_id", "library_entry_version"],
    )
    op.create_index(
        "ix_library_entry_attack_mappings_technique_id",
        "library_entry_attack_mappings",
        ["technique_id"],
    )

    op.create_table(
        "scenario_attack_mappings",
        sa.Column("id", sa.Uuid(), nullable=False),
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
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("scenario_id", sa.Uuid(), nullable=False),
        sa.Column("technique_id", sa.Uuid(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("scenario_id", "technique_id", name="uq_scenario_attack_mapping"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["scenario_id"], ["scenarios.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["technique_id"], ["attack_techniques.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "ix_scenario_attack_mappings_organization_id",
        "scenario_attack_mappings",
        ["organization_id"],
    )
    op.create_index(
        "ix_scenario_attack_mappings_scenario_id",
        "scenario_attack_mappings",
        ["scenario_id"],
    )
    op.create_index(
        "ix_scenario_attack_mappings_technique_id",
        "scenario_attack_mappings",
        ["technique_id"],
    )

    # ── Seed the catalog ────────────────────────────────────────────────
    import idraa

    project_root = Path(idraa.__file__).resolve().parent.parent.parent
    seed_path = project_root / "data" / "seed_attack_catalog.json"
    if not seed_path.exists():
        seed_path = (
            Path(__file__).resolve().parent.parent.parent / "data" / "seed_attack_catalog.json"
        )
    payload = json.loads(seed_path.read_text(encoding="utf-8"))

    from idraa.schemas.attack_catalog import AttackTacticSeed, AttackTechniqueSeed

    bind = op.get_bind()
    tactics_tbl = sa.table(
        "attack_tactics",
        sa.column("id", sa.Uuid()),
        sa.column("domain", sa.String()),
        sa.column("tactic_id", sa.String()),
        sa.column("shortname", sa.String()),
        sa.column("name", sa.String()),
        sa.column("description", sa.Text()),
        sa.column("display_order", sa.Integer()),
        sa.column("url", sa.String()),
    )
    techniques_tbl = sa.table(
        "attack_techniques",
        sa.column("id", sa.Uuid()),
        sa.column("domain", sa.String()),
        sa.column("technique_id", sa.String()),
        sa.column("name", sa.String()),
        sa.column("description", sa.Text()),
        sa.column("tactics", sa.JSON()),
        sa.column("parent_technique_id", sa.String()),
        sa.column("deprecated", sa.Boolean()),
        sa.column("catalog_version", sa.String()),
        sa.column("url", sa.String()),
        sa.column("citation", sa.JSON()),
    )

    for raw in payload["tactics"]:
        seed = AttackTacticSeed.model_validate(raw)
        bind.execute(
            tactics_tbl.insert().values(
                id=uuid.uuid4(),
                domain=seed.domain,
                tactic_id=seed.tactic_id,
                shortname=seed.shortname,
                name=seed.name,
                description=seed.description,
                display_order=seed.display_order,
                url=seed.url,
            )
        )
    for raw in payload["techniques"]:
        tseed = AttackTechniqueSeed.model_validate(raw)
        bind.execute(
            techniques_tbl.insert().values(
                id=uuid.uuid4(),
                domain=tseed.domain,
                technique_id=tseed.technique_id,
                name=tseed.name,
                description=tseed.description,
                tactics=tseed.tactics,
                parent_technique_id=None,
                deprecated=False,
                catalog_version=str(tseed.citation["attack_version"]),
                url=tseed.url,
                citation=tseed.citation,
            )
        )


def downgrade() -> None:
    op.drop_table("scenario_attack_mappings")
    op.drop_table("library_entry_attack_mappings")
    op.drop_table("attack_techniques")
    op.drop_table("attack_tactics")
