"""phase_1_5a_scenario_library_taxonomy

Revision ID: b8e0334b7f43
Revises: 922b63358719
Create Date: 2026-04-29 16:44:37.109137

Spec §6.6 — single revision covering F0-F4 model-side changes:
1. Create scenario_library_entries (composite PK id+version)
2. Create scenario_library_overrides (composite FK; deleted_at ships
   upfront so F9 tombstone path doesn't need a follow-up amend)
3. Create wizard_drafts (composite PK user_id+tx_id; organization_id
   forward-compat per CLAUDE.md)
4. Add scenarios.library_pin JSON column + shadow enum columns
   (_threat_actor_type / _threat_category / _asset_class) — combined
   into ONE batch block (SQLite recreates table once vs N times)
5. Backfill existing scenarios into shadow columns via case-insensitive
   substring mapping (most-specific keys first per paranoid-review fix)
6. Drop old free-form scenarios columns
7. Rename shadow → canonical names; threat_category NOT NULL; tighten
   renamed columns to Enum-backed VARCHAR (native_enum=False)
8. Backfill organizations.industry_type AND scenarios.industry for D2
   expansion: tech→information, energy→utilities, government→public
9. Per-org audit_log entries (one row per affected org with scenario
   taxonomy backfill list as JSON in `changes`)
10. risk_analysis_runs: index rename + enum tightening for run_type/status
    (model drift detected by autogenerate from phase_1_4 migration)

Pre-flight audit (2026-04-29):
  organizations: 0 rows → backfill is a no-op (dev DB empty)
  scenarios: 0 rows → backfill is a no-op (dev DB empty)
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b8e0334b7f43"
down_revision: str | Sequence[str] | None = "922b63358719"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ---------------------------------------------------------------------------
# Threat-category mapping (spec §6.2)
#
# Paranoid-review fix (Major: F5 substring map): order matters — most-
# specific keys come BEFORE more-general substrings. "ransomware" wins
# over "malware" if both match. "social engineering" added so phishing
# variants ("social-engineering") catch correctly. "safety" + "social"
# tokens added to handle observed dev-row free-form values that didn't
# map cleanly under the original map.
# ---------------------------------------------------------------------------
_TC_MAP = {
    # case-insensitive substring → enum value, MOST-SPECIFIC FIRST
    "ot_safety_tampering": "ot_safety_tampering",
    "ot_availability": "ot_availability",
    "ransomware": "ransomware",
    "supply chain": "supply_chain",
    "data leak": "data_disclosure",
    "data exposure": "data_disclosure",
    "data breach": "data_disclosure",
    "data tampering": "data_tampering",
    "phish": "social_engineering",
    "social engineering": "social_engineering",
    "social": "social_engineering",
    "denial": "denial_of_service",
    "ddos": "denial_of_service",
    "physical": "physical_tampering",
    "insider": "insider_misuse",
    "malware": "malware",
    "safety": "ot_safety_tampering",
    "ot": "ot_availability",
    "ics": "ot_availability",
}

_TA_MAP = {
    "insider": "insider_malicious",
    "internal": "insider_malicious",
    "accidental": "insider_accidental",
    "external": "cybercriminals",
    "criminal": "cybercriminals",
    "nation": "nation_state",
    "apt": "nation_state",
    "state": "nation_state",
    "hacktivist": "hacktivists",
    "competitor": "competitors",
    "partner": "competitors",
}

_AC_MAP = {
    "data": "data",
    "system": "systems",
    "people": "people",
    "human": "people",
    "facility": "facilities",
    "physical": "facilities",
    "ot": "ot_systems",
    "ics": "ot_systems",
    "scada": "ot_systems",
    "safety": "safety_systems",
    "sis": "safety_systems",
}


def _map_substring(value: str | None, mapping: dict[str, str], fallback: str | None) -> str | None:
    if value is None:
        return fallback
    v = value.lower().strip()
    for key, mapped in mapping.items():
        if key in v:
            return mapped
    return fallback


# ---------------------------------------------------------------------------
# Enum definitions — lowercase values matching StrEnum values in enums.py
# native_enum=False → VARCHAR + CHECK constraint (cross-DB portable)
# ---------------------------------------------------------------------------
_THREAT_CATEGORY_ENUM = sa.Enum(
    "malware",
    "ransomware",
    "data_disclosure",
    "data_tampering",
    "denial_of_service",
    "social_engineering",
    "physical_tampering",
    "supply_chain",
    "insider_misuse",
    "ot_safety_tampering",
    "ot_availability",
    "miscellaneous",
    name="threatcategory",
    native_enum=False,
    create_constraint=True,
)

_THREAT_ACTOR_TYPE_ENUM = sa.Enum(
    "cybercriminals",
    "nation_state",
    "insider_malicious",
    "insider_accidental",
    "hacktivists",
    "competitors",
    name="threatactortype",
    native_enum=False,
    create_constraint=True,
)

_ASSET_CLASS_ENUM = sa.Enum(
    "data",
    "systems",
    "people",
    "facilities",
    "ot_systems",
    "safety_systems",
    "other",
    name="assetclass",
    native_enum=False,
    create_constraint=True,
)

_RUN_TYPE_ENUM = sa.Enum(
    "single",
    "aggregate",
    name="runtype",
    native_enum=False,
    create_constraint=True,
)

_RUN_STATUS_ENUM = sa.Enum(
    "queued",
    "running",
    "completed",
    "failed",
    "cancelled",
    name="runstatus",
    native_enum=False,
    create_constraint=True,
)

_INDUSTRY_TYPE_ENUM = sa.Enum(
    "agriculture",
    "mining",
    "utilities",
    "construction",
    "manufacturing",
    "trade",
    "retail",
    "transportation",
    "information",
    "financial",
    "real_estate",
    "professional",
    "management",
    "administrative",
    "education",
    "healthcare",
    "entertainment",
    "hospitality",
    "public",
    "other",
    name="industrytype",
    native_enum=False,
    create_constraint=True,
)


def upgrade() -> None:
    # -----------------------------------------------------------------------
    # Step 1: scenario_library_entries
    # -----------------------------------------------------------------------
    op.create_table(
        "scenario_library_entries",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("slug", sa.String(128), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "draft",
                "published",
                "deprecated",
                name="library_entry_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
            server_default="draft",
        ),
        # threat_event_type, threat_actor_type, asset_class: stored as Enum-backed
        # VARCHAR (native_enum=False) to match the model's Enum(ThreatCategory, ...) etc.
        sa.Column("threat_event_type", _THREAT_CATEGORY_ENUM, nullable=False),
        sa.Column("threat_actor_type", _THREAT_ACTOR_TYPE_ENUM, nullable=False),
        sa.Column("asset_class", _ASSET_CLASS_ENUM, nullable=False),
        sa.Column("attack_vector", sa.String(128), nullable=True),
        sa.Column("tags", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("example_incidents", sa.Text(), nullable=True),
        sa.Column("source_citations", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("canonical_fair_gap", sa.Text(), nullable=False),
        sa.Column("applicable_industries", sa.JSON(), nullable=True),
        sa.Column("applicable_sub_sectors", sa.JSON(), nullable=True),
        sa.Column("applicable_org_sizes", sa.JSON(), nullable=True),
        sa.Column("threat_event_frequency", sa.JSON(), nullable=False),
        sa.Column("vulnerability", sa.JSON(), nullable=False),
        sa.Column("primary_loss", sa.JSON(), nullable=False),
        sa.Column("secondary_loss", sa.JSON(), nullable=True),
        sa.Column("suggested_control_ids", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("standards_references", sa.JSON(), nullable=True),
        sa.Column("row_version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
        ),
        sa.PrimaryKeyConstraint("id", "version", name="pk_scenario_library_entries"),
        sa.UniqueConstraint("slug", "version", name="uq_library_entry_slug_version"),
    )
    op.create_index(
        "ix_library_entry_status", "scenario_library_entries", ["status"], unique=False
    )
    op.create_index(
        "ix_library_entry_threat_actor",
        "scenario_library_entries",
        ["threat_actor_type"],
        unique=False,
    )
    op.create_index(
        "ix_library_entry_threat_event",
        "scenario_library_entries",
        ["threat_event_type"],
        unique=False,
    )

    # -----------------------------------------------------------------------
    # Step 2: scenario_library_overrides
    #
    # r2 MAJOR (F5/F9 merge): deleted_at ships with the initial create_table
    # so the F9 tombstone path doesn't require a follow-up amend.
    # Active rows have NULL deleted_at; tombstone sets a timestamp.
    # ScenarioLibraryRepo.get_by_org_entry filters deleted_at IS NULL for
    # active-row queries; get_override_by_version does NOT filter — the
    # audit-grade pin lookup must keep returning tombstoned rows (§6.9.4).
    #
    # Index convention: OrgMixin's organization_id mapped_column uses index=True
    # which auto-generates ix_scenario_library_overrides_organization_id.
    # We use op.f() to generate the same name as the model does.
    # -----------------------------------------------------------------------
    op.create_table(
        "scenario_library_overrides",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column(
            "organization_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("library_entry_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("library_entry_version", sa.Integer(), nullable=False),
        sa.Column("threat_event_frequency", sa.JSON(), nullable=True),
        sa.Column("vulnerability", sa.JSON(), nullable=True),
        sa.Column("primary_loss", sa.JSON(), nullable=True),
        sa.Column("secondary_loss", sa.JSON(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("methodology_change_reason", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("row_version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column(
            "created_by",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_scenario_library_overrides"),
        sa.UniqueConstraint(
            "organization_id",
            "library_entry_id",
            name="uq_library_override_org_entry",
        ),
        sa.ForeignKeyConstraint(
            ["library_entry_id", "library_entry_version"],
            ["scenario_library_entries.id", "scenario_library_entries.version"],
            name="fk_library_override_entry_version",
        ),
    )
    # OrgMixin uses index=True on organization_id, which generates this index name.
    op.create_index(
        op.f("ix_scenario_library_overrides_organization_id"),
        "scenario_library_overrides",
        ["organization_id"],
        unique=False,
    )

    # -----------------------------------------------------------------------
    # Step 2b: wizard_drafts (paranoid-review Decision A — DB-backed state).
    # Composite PK (user_id, tx_id); organization_id forward-compat per
    # CLAUDE.md; updated_at drives cleanup_expired (TTL 30min) in F17.
    # -----------------------------------------------------------------------
    op.create_table(
        "wizard_drafts",
        sa.Column(
            "user_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tx_id", sa.Uuid(as_uuid=True), nullable=False),
        # r2 BLOCKER 14: organization_id forward-compat for multi-tenancy per CLAUDE.md.
        sa.Column(
            "organization_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("state_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
        ),
        sa.PrimaryKeyConstraint("user_id", "tx_id", name="pk_wizard_drafts"),
    )

    # -----------------------------------------------------------------------
    # Steps 3 + 4: scenarios.library_pin + shadow enum columns.
    #
    # Paranoid-review consolidation: combine the two batch_alter_table calls
    # so SQLite recreates the scenarios table once instead of twice.
    # -----------------------------------------------------------------------
    with op.batch_alter_table("scenarios", schema=None) as batch:
        batch.add_column(sa.Column("library_pin", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("_threat_actor_type", sa.String(64), nullable=True))
        batch.add_column(sa.Column("_threat_category", sa.String(64), nullable=True))
        batch.add_column(sa.Column("_asset_class", sa.String(64), nullable=True))

    # -----------------------------------------------------------------------
    # Step 5: backfill scenarios into shadow columns
    # -----------------------------------------------------------------------
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT id, threat_category, threat_actor_type, asset_class FROM scenarios"
        )
    ).fetchall()
    backfill_log: list[dict[str, str | None]] = []
    for row in rows:
        s_id = row._mapping["id"]
        tc = row._mapping["threat_category"]
        ta = row._mapping["threat_actor_type"]
        ac = row._mapping["asset_class"]
        new_tc = _map_substring(tc, _TC_MAP, "miscellaneous")
        new_ta = _map_substring(ta, _TA_MAP, None) if ta else None  # nullable
        new_ac = _map_substring(ac, _AC_MAP, None) if ac else None  # nullable
        bind.execute(
            sa.text(
                "UPDATE scenarios SET _threat_category=:c, _threat_actor_type=:a, "
                "_asset_class=:ac WHERE id=:id"
            ),
            {"c": new_tc, "a": new_ta, "ac": new_ac, "id": s_id},
        )
        backfill_log.append(
            {
                "id": str(s_id),
                "tc": tc,
                "→": new_tc,
                "ta": ta,
                "→ta": new_ta,
                "ac": ac,
                "→ac": new_ac,
            }
        )

    # -----------------------------------------------------------------------
    # Step 6: drop old free-form scenarios columns
    # -----------------------------------------------------------------------
    with op.batch_alter_table("scenarios", schema=None) as batch:
        batch.drop_column("threat_actor_type")
        batch.drop_column("threat_category")
        batch.drop_column("asset_class")

    # -----------------------------------------------------------------------
    # Step 7: rename shadow → canonical names; threat_category NOT NULL;
    # tighten types to Enum-backed VARCHAR (native_enum=False) to match model.
    # -----------------------------------------------------------------------
    with op.batch_alter_table("scenarios", schema=None) as batch:
        batch.alter_column(
            "_threat_category",
            new_column_name="threat_category",
            existing_type=sa.String(64),
            type_=_THREAT_CATEGORY_ENUM,
            nullable=False,
            existing_nullable=True,
        )
        batch.alter_column(
            "_threat_actor_type",
            new_column_name="threat_actor_type",
            existing_type=sa.String(64),
            type_=_THREAT_ACTOR_TYPE_ENUM,
            nullable=True,
            existing_nullable=True,
        )
        batch.alter_column(
            "_asset_class",
            new_column_name="asset_class",
            existing_type=sa.String(64),
            type_=_ASSET_CLASS_ENUM,
            nullable=True,
            existing_nullable=True,
        )

    # -----------------------------------------------------------------------
    # Step 8: backfill organizations.industry_type AND scenarios.industry
    # for D2 expansion: tech→information, energy→utilities, government→public.
    # Paranoid-review fix: scenarios.industry must also be backfilled so
    # existing dev rows with industry='tech' don't raise IndustryNotInIrisError
    # at the next calibrate_scenario call.
    # -----------------------------------------------------------------------
    bind.execute(
        sa.text(
            "UPDATE organizations SET industry_type='information' WHERE industry_type='tech'"
        )
    )
    bind.execute(
        sa.text(
            "UPDATE organizations SET industry_type='utilities' WHERE industry_type='energy'"
        )
    )
    bind.execute(
        sa.text(
            "UPDATE organizations SET industry_type='public' WHERE industry_type='government'"
        )
    )
    bind.execute(
        sa.text("UPDATE scenarios SET industry='information' WHERE industry='tech'")
    )
    bind.execute(
        sa.text("UPDATE scenarios SET industry='utilities' WHERE industry='energy'")
    )
    bind.execute(
        sa.text("UPDATE scenarios SET industry='public' WHERE industry='government'")
    )

    # -----------------------------------------------------------------------
    # Step 9: audit-log per-org backfill entries
    #
    # Paranoid-review fix (Blocker 1): real schema is `audit_log` (singular)
    # with columns (id, organization_id, entity_type, entity_id, user_id,
    # action, changes, ip_address, timestamp). organization_id is NOT NULL
    # FK to organizations and entity_id is NOT NULL — so one row per org
    # affected. entity_id is set to a deterministic-but-distinct UUID
    # (entity_type='migration' marks it as not a real entity).
    # -----------------------------------------------------------------------
    if backfill_log:
        # Group backfill_log entries by organization_id so each org gets one
        # row with its own scenario list.
        per_org: dict[str, list[dict[str, str | None]]] = {}
        for entry in backfill_log:
            org_id_scalar = bind.execute(
                sa.text("SELECT organization_id FROM scenarios WHERE id=:id"),
                {"id": entry["id"]},
            ).scalar()
            per_org.setdefault(str(org_id_scalar), []).append(entry)
        now_iso = datetime.now(UTC).isoformat()
        for org_id, rows_for_org in per_org.items():
            bind.execute(
                sa.text("""
                    INSERT INTO audit_log
                      (id, organization_id, entity_type, entity_id, user_id,
                       action, changes, ip_address, timestamp)
                    VALUES
                      (:id, :org_id, 'migration', :entity_id, NULL,
                       'migration.phase_1_5a_backfill', :changes, NULL, :now)
                """),
                {
                    "id": str(uuid.uuid5(uuid.NAMESPACE_OID, f"phase_1_5a_backfill:{org_id}")),
                    "org_id": org_id,
                    "entity_id": "00000000-0000-0000-0000-000000000005",
                    "changes": json.dumps({"scenario_taxonomy_backfill": rows_for_org}),
                    "now": now_iso,
                },
            )

    # -----------------------------------------------------------------------
    # Step 10: risk_analysis_runs — index normalisation + enum tightening.
    #
    # Phase 1.4 migration hand-wrote composite indexes that diverge from what
    # the model's OrgMixin + mapped_column(index=True) produces. The canonical
    # from-base DB has the composite indexes; the dev DB may already have the
    # OrgMixin-generated index. We use IF EXISTS guards so the migration runs
    # cleanly in both states.
    # -----------------------------------------------------------------------
    bind2 = op.get_bind()
    _existing_run_indexes = {
        r[0] for r in bind2.execute(
            sa.text(
                "SELECT name FROM sqlite_master WHERE type='index' "
                "AND tbl_name='risk_analysis_runs'"
            )
        ).fetchall()
    }
    if "ix_risk_analysis_runs_org_status" in _existing_run_indexes:
        op.drop_index(
            "ix_risk_analysis_runs_org_status", table_name="risk_analysis_runs"
        )
    if "ix_risk_analysis_runs_scenario_id_created_at" in _existing_run_indexes:
        op.drop_index(
            "ix_risk_analysis_runs_scenario_id_created_at",
            table_name="risk_analysis_runs",
        )
    if "ix_risk_analysis_runs_organization_id" not in _existing_run_indexes:
        op.create_index(
            op.f("ix_risk_analysis_runs_organization_id"),
            "risk_analysis_runs",
            ["organization_id"],
            unique=False,
        )

    # -----------------------------------------------------------------------
    # Step 11: organizations.industry_type enum tightening
    # The previous migration stored the column as VARCHAR(13) which was the
    # length of the longest old value. The model now declares a 20-value
    # Enum(native_enum=False) with lowercase values.
    # -----------------------------------------------------------------------
    with op.batch_alter_table("organizations", schema=None) as batch:
        batch.alter_column(
            "industry_type",
            existing_type=sa.String(13),
            type_=_INDUSTRY_TYPE_ENUM,
            existing_nullable=False,
        )


def downgrade() -> None:
    """Reverse: drops new tables + new columns + reverts type changes.

    Documented data-loss caveat: enum→string regression loses any free-form
    information that was successfully classified into the enum vocabulary.
    Run on dev environments only.
    """
    # -----------------------------------------------------------------------
    # Reverse Step 11: revert industry_type to VARCHAR(13)
    # -----------------------------------------------------------------------
    with op.batch_alter_table("organizations", schema=None) as batch:
        batch.alter_column(
            "industry_type",
            existing_type=_INDUSTRY_TYPE_ENUM,
            type_=sa.String(13),
            existing_nullable=False,
        )

    # -----------------------------------------------------------------------
    # Reverse Step 10: revert risk_analysis_runs enum tightening + restore
    # original indexes (conditional guards mirror the upgrade path).
    # -----------------------------------------------------------------------
    bind_dg = op.get_bind()
    _dg_run_indexes = {
        r[0] for r in bind_dg.execute(
            sa.text(
                "SELECT name FROM sqlite_master WHERE type='index' "
                "AND tbl_name='risk_analysis_runs'"
            )
        ).fetchall()
    }
    if "ix_risk_analysis_runs_organization_id" in _dg_run_indexes:
        op.drop_index(
            op.f("ix_risk_analysis_runs_organization_id"),
            table_name="risk_analysis_runs",
        )
    if "ix_risk_analysis_runs_scenario_id_created_at" not in _dg_run_indexes:
        op.create_index(
            "ix_risk_analysis_runs_scenario_id_created_at",
            "risk_analysis_runs",
            ["scenario_id", "created_at"],
            unique=False,
        )
    if "ix_risk_analysis_runs_org_status" not in _dg_run_indexes:
        op.create_index(
            "ix_risk_analysis_runs_org_status",
            "risk_analysis_runs",
            ["organization_id", "status"],
            unique=False,
        )

    # -----------------------------------------------------------------------
    # Reverse Steps 7 + 6: revert enum tightening on scenarios columns, then
    # rename canonical → shadow, restore old columns.
    #
    # Split into two batch_alter_table calls to avoid circular dependency:
    # first revert type + rename canonical→shadow, then in a second batch
    # add the original free-form columns + copy data + drop shadows.
    # -----------------------------------------------------------------------
    with op.batch_alter_table("scenarios", schema=None) as batch:
        batch.alter_column(
            "threat_category",
            new_column_name="_threat_category",
            existing_type=_THREAT_CATEGORY_ENUM,
            type_=sa.String(64),
            nullable=True,
            existing_nullable=False,
        )
        batch.alter_column(
            "threat_actor_type",
            new_column_name="_threat_actor_type",
            existing_type=_THREAT_ACTOR_TYPE_ENUM,
            type_=sa.String(64),
            nullable=True,
            existing_nullable=True,
        )
        batch.alter_column(
            "asset_class",
            new_column_name="_asset_class",
            existing_type=_ASSET_CLASS_ENUM,
            type_=sa.String(64),
            nullable=True,
            existing_nullable=True,
        )

    with op.batch_alter_table("scenarios", schema=None) as batch:
        batch.add_column(
            sa.Column("threat_actor_type", sa.String(64), nullable=True)
        )
        batch.add_column(
            sa.Column(
                "threat_category",
                sa.String(64),
                nullable=False,
                server_default="miscellaneous",
            )
        )
        batch.add_column(sa.Column("asset_class", sa.String(128), nullable=True))

    bind = op.get_bind()
    bind.execute(
        sa.text(
            "UPDATE scenarios SET threat_actor_type=_threat_actor_type, "
            "threat_category=_threat_category, asset_class=_asset_class"
        )
    )

    with op.batch_alter_table("scenarios", schema=None) as batch:
        batch.drop_column("_threat_actor_type")
        batch.drop_column("_threat_category")
        batch.drop_column("_asset_class")
        batch.drop_column("library_pin")

    # -----------------------------------------------------------------------
    # Drop new tables (in reverse dependency order)
    # -----------------------------------------------------------------------
    op.drop_table("wizard_drafts")
    op.drop_index(
        op.f("ix_scenario_library_overrides_organization_id"),
        table_name="scenario_library_overrides",
    )
    op.drop_table("scenario_library_overrides")
    op.drop_index("ix_library_entry_threat_event", table_name="scenario_library_entries")
    op.drop_index("ix_library_entry_threat_actor", table_name="scenario_library_entries")
    op.drop_index("ix_library_entry_status", table_name="scenario_library_entries")
    op.drop_table("scenario_library_entries")

    # Reverse industry_type backfill (best-effort; ambiguous mappings left as new values)
    bind.execute(
        sa.text(
            "UPDATE organizations SET industry_type='tech' WHERE industry_type='information'"
        )
    )
    bind.execute(
        sa.text(
            "UPDATE organizations SET industry_type='energy' WHERE industry_type='utilities'"
        )
    )
    bind.execute(
        sa.text(
            "UPDATE organizations SET industry_type='government' WHERE industry_type='public'"
        )
    )
