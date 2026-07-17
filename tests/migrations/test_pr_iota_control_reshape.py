"""Migration tests for PR iota — control_function_assignments upgrade/downgrade.

Uses pytest-alembic's alembic_runner fixture to drive migrations against an
isolated SQLite DB. Each test that needs pre-seeded data uses raw SQL to insert
rows at a specific revision level before running upgrade/downgrade.

Of the 15 test functions: 12 are listed in spec §11.3 (M12), the 13th
(`test_smart_backfill_uses_uppercase_domain_keys`) is the B4 anti-regression
sentinel added during paranoid review, and tests 14–15 are F7 §11.3 gap tests
(negative case + row-count + column preservation checks). Tests are self-contained
and do not depend on each other's order. The alembic_runner fixture resets
to the base migration state between tests.

Query pattern: `alembic_runner` drives migrations; `alembic_engine` (sync) is
used directly for all pre-seed INSERTs and post-migration SELECTs.
`alembic_runner.connection()` does NOT exist on MigrationContext — the correct
API is `alembic_engine.connect()` (sync engine provided by conftest.py).
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from pytest_alembic import MigrationContext
from sqlalchemy.engine import Engine

from idraa.models.enums import SUB_FUNCTION_UNITS, FairCamSubFunction, UnitType

# Revision IDs
# _DOWN_REV is the latest pre-PR-iota head (PR theta seed_library_entries).
# After the chain-fix at 57bcb3c, PR iota's down_revision points here.
_DOWN_REV = "c1d2e3f4a5b6"  # PR theta seed_library_entries (latest pre-PR-iota head)
_OUR_REV = "a1b2c3d4e5f6"  # PR iota CFA migration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _insert_org(conn: sa.Connection) -> str:
    """Insert a minimal Organization row; return its UUID string.

    All NOT NULL columns without server_default are supplied. industry_type
    uses lowercase 'manufacturing' — after the PR theta (b8e0334b7f43)
    migration the industrytype CHECK constraint uses lowercase enum values.
    Other enum columns (organization_size, security_maturity, risk_appetite)
    have no database-level CHECK constraints after the batch alter; any string
    is accepted. JSON columns use '[]' as a minimal valid empty JSON array.
    """
    org_id = str(uuid.uuid4())
    conn.execute(
        sa.text(
            "INSERT INTO organizations "
            "(id, name, organization_size, industry_type, security_maturity, "
            "has_cyber_insurance, risk_appetite, compliance_requirements, "
            "regulatory_environment, technology_stack, geographic_regions, "
            "preferred_currency, preferred_language, "
            "created_at, updated_at) "
            "VALUES (:id, 'TestOrg', 'MEDIUM', 'manufacturing', 'BASIC', "
            "0, 'MODERATE', '[]', '[]', '[]', '[]', "
            "'USD', 'en', "
            "(CURRENT_TIMESTAMP), (CURRENT_TIMESTAMP))"
        ),
        {"id": org_id},
    )
    return org_id


def _insert_control(
    conn: sa.Connection,
    *,
    org_id: str,
    domain: str = "LOSS_EVENT",
    function: str = "PREVENTIVE",
    strength: float = 0.7,
    coverage: float = 0.8,
    reliability: float = 0.9,
) -> str:
    """Insert a minimal Control row at the Phase 1.2 schema. Return UUID string.

    domain MUST be UPPERCASE (the on-disk StrEnum-value form per project
    convention; see Phase 1.2 migration 355450b21719's ``Enum(...)`` column
    definition).  The B4 paranoid-review fix verified the backfill _SUBFN_MAP
    keys uppercase to match.
    """
    ctrl_id = str(uuid.uuid4())
    conn.execute(
        sa.text(
            "INSERT INTO controls "
            "(id, name, domain, function, type, control_strength, control_reliability, "
            " control_coverage, cost_model, nist_csf_functions, iso_27001_domains, "
            " compliance_mappings, skill_requirements, technology_dependencies, "
            " applicable_industries, applicable_org_sizes, status, version, "
            " organization_id, created_at, updated_at) "
            "VALUES (:id, :name, :domain, :function, 'TECHNICAL', "
            ":strength, :reliability, :coverage, '{}', '[]', '[]', '{}', '[]', '[]', "
            "'[]', '[]', 'ACTIVE', '1.0', :org_id, "
            "(CURRENT_TIMESTAMP), (CURRENT_TIMESTAMP))"
        ),
        {
            "id": ctrl_id,
            "name": f"Control {ctrl_id[:8]}",
            "domain": domain,
            "function": function,
            "strength": strength,
            "reliability": reliability,
            "coverage": coverage,
            "org_id": org_id,
        },
    )
    return ctrl_id


# ---------------------------------------------------------------------------
# Test functions
# ---------------------------------------------------------------------------


def test_upgrade_creates_assignments_table_with_unique_index(
    alembic_runner: MigrationContext,
    alembic_engine: Engine,
) -> None:
    """After upgrade, control_function_assignments table exists with required indexes.

    SQLite NOTE: UniqueConstraint created inline in CREATE TABLE is stored as
    'sqlite_autoindex_<table>_N' in sqlite_master — the explicit constraint name
    'uq_cfa_control_sub_function' does not appear as a standalone index row.
    We verify the unique constraint exists via PRAGMA index_list, looking for
    an index with origin='u' (user-defined unique constraint). The explicit
    ix_cfa_control_id non-unique index IS named and queryable in sqlite_master.
    """
    alembic_runner.migrate_up_to(_OUR_REV)
    with alembic_engine.connect() as conn:
        result = conn.execute(
            sa.text(
                "SELECT COUNT(*) FROM sqlite_master "
                "WHERE type='table' AND name='control_function_assignments'"
            )
        ).scalar()
        assert result == 1, "control_function_assignments table not found after upgrade"

        # Verify the unique constraint exists on (control_id, sub_function) via
        # PRAGMA index_list. origin='u' means user-defined UNIQUE constraint.
        idx_rows = conn.execute(
            sa.text("PRAGMA index_list(control_function_assignments)")
        ).fetchall()
        unique_origins = [r[3] for r in idx_rows if r[2] == 1 and r[3] == "u"]
        assert len(unique_origins) >= 1, (
            "No user-defined unique constraint found on control_function_assignments. "
            "Expected UniqueConstraint(control_id, sub_function) to create one. "
            f"index_list: {idx_rows}"
        )

        idx2 = conn.execute(
            sa.text(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='index' AND name='ix_cfa_control_id'"
            )
        ).scalar()
        assert idx2 == 1, "ix_cfa_control_id index not found"


def test_backfill_creates_one_assignment_per_control(
    alembic_runner: MigrationContext,
    alembic_engine: Engine,
) -> None:
    """After upgrade, one CFA row exists for each Control row."""
    alembic_runner.migrate_up_to(_DOWN_REV)
    with alembic_engine.connect() as conn:
        org_id = _insert_org(conn)
        _insert_control(conn, org_id=org_id, domain="LOSS_EVENT", function="PREVENTIVE")
        _insert_control(conn, org_id=org_id, domain="VARIANCE_MANAGEMENT", function="DETECTIVE")
        _insert_control(conn, org_id=org_id, domain="DECISION_SUPPORT", function="CORRECTIVE")
        conn.commit()

    alembic_runner.migrate_up_to(_OUR_REV)
    with alembic_engine.connect() as conn:
        ctrl_count = conn.execute(sa.text("SELECT COUNT(*) FROM controls")).scalar()
        cfa_count = conn.execute(
            sa.text("SELECT COUNT(*) FROM control_function_assignments")
        ).scalar()
        assert ctrl_count == cfa_count, (
            f"CFA count ({cfa_count}) != Control count ({ctrl_count}). "
            "Backfill must create exactly one assignment per control."
        )


def test_backfill_preserves_strength_reliability_coverage(
    alembic_runner: MigrationContext,
    alembic_engine: Engine,
) -> None:
    """Backfill preserves control_strength->capability_value for PROBABILITY-unit sub_functions,
    and control_coverage/reliability for all rows."""
    alembic_runner.migrate_up_to(_DOWN_REV)
    with alembic_engine.connect() as conn:
        org_id = _insert_org(conn)
        _insert_control(
            conn,
            org_id=org_id,
            domain="LOSS_EVENT",
            function="PREVENTIVE",
            strength=0.85,
            coverage=0.75,
            reliability=0.95,
        )
        conn.commit()

    alembic_runner.migrate_up_to(_OUR_REV)
    with alembic_engine.connect() as conn:
        row = conn.execute(
            sa.text(
                "SELECT sub_function, capability_value, coverage, reliability "
                "FROM control_function_assignments LIMIT 1"
            )
        ).fetchone()
        assert row is not None, "No CFA row found after backfill"
        assert row[0] == "lec_prev_resistance", (
            f"Expected sub_function=lec_prev_resistance for (loss_event, PREVENTIVE), got {row[0]}"
        )
        assert abs(row[1] - 0.85) < 1e-6, (
            f"Expected capability_value=0.85 (preserved from control_strength), got {row[1]}"
        )
        assert abs(row[2] - 0.75) < 1e-6, f"Expected coverage=0.75, got {row[2]}"
        assert abs(row[3] - 0.95) < 1e-6, f"Expected reliability=0.95, got {row[3]}"


def test_backfill_assigns_sub_function_per_domain_function_table(
    alembic_runner: MigrationContext,
    alembic_engine: Engine,
) -> None:
    """Backfill maps (domain, function) to the correct sub_function per OQ2 table (spec §6.6)."""
    cases = [
        ("LOSS_EVENT", "PREVENTIVE", "lec_prev_resistance"),
        ("LOSS_EVENT", "DETECTIVE", "lec_det_visibility"),
        ("LOSS_EVENT", "CORRECTIVE", "lec_resp_event_termination"),
        ("LOSS_EVENT", "COMPENSATING", "lec_prev_resistance"),
        ("VARIANCE_MANAGEMENT", "PREVENTIVE", "vmc_prev_reduce_variance_prob"),
        ("VARIANCE_MANAGEMENT", "DETECTIVE", "vmc_id_control_monitoring"),
        ("VARIANCE_MANAGEMENT", "CORRECTIVE", "vmc_corr_implementation"),
        ("VARIANCE_MANAGEMENT", "COMPENSATING", "vmc_prev_reduce_variance_prob"),
        ("DECISION_SUPPORT", "PREVENTIVE", "dsc_prev_defined_expectations"),
        ("DECISION_SUPPORT", "DETECTIVE", "dsc_id_misaligned"),
        ("DECISION_SUPPORT", "CORRECTIVE", "dsc_id_misaligned"),  # NOT virtual!
        ("DECISION_SUPPORT", "COMPENSATING", "dsc_prev_defined_expectations"),
    ]

    alembic_runner.migrate_up_to(_DOWN_REV)
    with alembic_engine.connect() as conn:
        org_id = _insert_org(conn)
        ctrl_ids = []
        for domain, function, _ in cases:
            ctrl_ids.append(_insert_control(conn, org_id=org_id, domain=domain, function=function))
        conn.commit()

    alembic_runner.migrate_up_to(_OUR_REV)
    with alembic_engine.connect() as conn:
        for ctrl_id, (domain, function, expected_subfn) in zip(ctrl_ids, cases, strict=True):
            row = conn.execute(
                sa.text(
                    "SELECT sub_function FROM control_function_assignments "
                    "WHERE control_id = :ctrl_id"
                ),
                {"ctrl_id": ctrl_id},
            ).fetchone()
            assert row is not None, (
                f"No CFA row for control {ctrl_id} (domain={domain}, fn={function})"
            )
            assert row[0] == expected_subfn, (
                f"(domain={domain}, function={function}) -> expected sub_function="
                f"{expected_subfn}, got {row[0]}"
            )


def test_backfill_null_capability_for_elapsed_time_sub_functions(
    alembic_runner: MigrationContext,
    alembic_engine: Engine,
) -> None:
    """ELAPSED_TIME-unit sub_functions get capability_value=NULL (OQ1).

    Cases: loss_event+CORRECTIVE -> lec_resp_event_termination (ELAPSED_TIME),
           variance_management+DETECTIVE -> vmc_id_control_monitoring (ELAPSED_TIME),
           decision_support+CORRECTIVE -> dsc_id_misaligned (ELAPSED_TIME).
    """
    alembic_runner.migrate_up_to(_DOWN_REV)
    with alembic_engine.connect() as conn:
        org_id = _insert_org(conn)
        for domain, function in [
            ("LOSS_EVENT", "CORRECTIVE"),
            ("VARIANCE_MANAGEMENT", "DETECTIVE"),
            ("DECISION_SUPPORT", "CORRECTIVE"),
        ]:
            _insert_control(conn, org_id=org_id, domain=domain, function=function, strength=0.7)
        conn.commit()

    alembic_runner.migrate_up_to(_OUR_REV)
    with alembic_engine.connect() as conn:
        null_caps = conn.execute(
            sa.text(
                "SELECT COUNT(*) FROM control_function_assignments WHERE capability_value IS NULL"
            )
        ).scalar()
        assert null_caps == 3, (
            f"Expected 3 NULL-capability rows (one per ELAPSED_TIME-unit control), got {null_caps}"
        )


def test_backfill_dsc_corrective_maps_to_dsc_id_misaligned_not_virtual(
    alembic_runner: MigrationContext,
    alembic_engine: Engine,
) -> None:
    """DECISION_SUPPORT+CORRECTIVE must map to dsc_id_misaligned, NOT dsc_corr_misaligned.

    dsc_corr_misaligned is a VIRTUAL sub-function (spec §4.3, §5.3 page 50).
    The virtual-function CHECK constraint forbids assigning it to a distinct control.
    """
    alembic_runner.migrate_up_to(_DOWN_REV)
    with alembic_engine.connect() as conn:
        org_id = _insert_org(conn)
        _insert_control(conn, org_id=org_id, domain="DECISION_SUPPORT", function="CORRECTIVE")
        conn.commit()

    alembic_runner.migrate_up_to(_OUR_REV)
    with alembic_engine.connect() as conn:
        row = conn.execute(
            sa.text("SELECT sub_function FROM control_function_assignments LIMIT 1")
        ).fetchone()
        assert row is not None
        assert row[0] == "dsc_id_misaligned", (
            f"Expected dsc_id_misaligned (not virtual dsc_corr_misaligned), got {row[0]}"
        )
        assert row[0] != "dsc_corr_misaligned", (
            "dsc_corr_misaligned is a virtual sub-function and must never appear "
            "as a distinct control assignment in a backfill row."
        )


def test_confirmed_by_user_at_null_for_all_backfill_rows(
    alembic_runner: MigrationContext,
    alembic_engine: Engine,
) -> None:
    """All backfilled CFA rows have confirmed_by_user_at=NULL (spec §4.8 Decision 8)."""
    alembic_runner.migrate_up_to(_DOWN_REV)
    with alembic_engine.connect() as conn:
        org_id = _insert_org(conn)
        for domain, fn in [
            ("LOSS_EVENT", "PREVENTIVE"),
            ("VARIANCE_MANAGEMENT", "CORRECTIVE"),
            ("DECISION_SUPPORT", "DETECTIVE"),
        ]:
            _insert_control(conn, org_id=org_id, domain=domain, function=fn)
        conn.commit()

    alembic_runner.migrate_up_to(_OUR_REV)
    with alembic_engine.connect() as conn:
        non_null = conn.execute(
            sa.text(
                "SELECT COUNT(*) FROM control_function_assignments "
                "WHERE confirmed_by_user_at IS NOT NULL"
            )
        ).scalar()
        assert non_null == 0, (
            f"Expected 0 confirmed backfill rows; got {non_null}. "
            "Backfilled rows must have confirmed_by_user_at=NULL until a human "
            "explicitly confirms via the confirm endpoint (spec §4.8)."
        )


def test_downgrade_restores_function_column(
    alembic_runner: MigrationContext,
    alembic_engine: Engine,
) -> None:
    """After downgrade, controls table has a 'function' column."""
    alembic_runner.migrate_up_to(_OUR_REV)
    alembic_runner.migrate_down_to(_DOWN_REV)
    with alembic_engine.connect() as conn:
        pragma = conn.execute(sa.text("PRAGMA table_info(controls)")).fetchall()
        col_names = {row[1] for row in pragma}
        assert "function" in col_names, (
            "controls.function column not present after downgrade. "
            "Downgrade must re-add the dropped column."
        )


def test_downgrade_restores_non_null_control_strength(
    alembic_runner: MigrationContext,
    alembic_engine: Engine,
) -> None:
    """After upgrade+downgrade, control_strength is non-NULL for all rows.

    NULL-capability rows (ELAPSED_TIME-unit backfills) restore to 0.5 via COALESCE.
    """
    alembic_runner.migrate_up_to(_DOWN_REV)
    with alembic_engine.connect() as conn:
        org_id = _insert_org(conn)
        _insert_control(
            conn, org_id=org_id, domain="LOSS_EVENT", function="CORRECTIVE", strength=0.9
        )
        conn.commit()

    alembic_runner.migrate_up_to(_OUR_REV)
    alembic_runner.migrate_down_to(_DOWN_REV)
    with alembic_engine.connect() as conn:
        row = conn.execute(sa.text("SELECT control_strength FROM controls LIMIT 1")).fetchone()
        assert row is not None
        assert row[0] is not None, "control_strength is NULL after downgrade"
        assert abs(row[0] - 0.5) < 1e-6, (
            f"Expected COALESCE default 0.5 for NULL-capability row after downgrade, got {row[0]}"
        )


def test_round_trip_idempotent(
    alembic_runner: MigrationContext,
    alembic_engine: Engine,
) -> None:
    """upgrade -> downgrade -> upgrade leaves controls table structurally intact."""
    alembic_runner.migrate_up_to(_DOWN_REV)
    with alembic_engine.connect() as conn:
        org_id = _insert_org(conn)
        _insert_control(conn, org_id=org_id, domain="LOSS_EVENT", function="PREVENTIVE")
        _insert_control(conn, org_id=org_id, domain="DECISION_SUPPORT", function="COMPENSATING")
        conn.commit()

    alembic_runner.migrate_up_to(_OUR_REV)
    alembic_runner.migrate_down_to(_DOWN_REV)
    alembic_runner.migrate_up_to(_OUR_REV)

    with alembic_engine.connect() as conn:
        ctrl_count = conn.execute(sa.text("SELECT COUNT(*) FROM controls")).scalar()
        cfa_count = conn.execute(
            sa.text("SELECT COUNT(*) FROM control_function_assignments")
        ).scalar()
        assert ctrl_count == 2, f"Expected 2 controls after round-trip, got {ctrl_count}"
        assert cfa_count == 2, f"Expected 2 CFA rows after round-trip, got {cfa_count}"

        pragma = conn.execute(sa.text("PRAGMA table_info(controls)")).fetchall()
        col_names = {row[1] for row in pragma}
        for dropped in ("function", "control_strength", "control_reliability", "control_coverage"):
            assert dropped not in col_names, (
                f"controls.{dropped} unexpectedly present after round-trip upgrade"
            )


def test_controls_table_lacks_dropped_columns_after_upgrade(
    alembic_runner: MigrationContext,
    alembic_engine: Engine,
) -> None:
    """After upgrade, controls table has NO function/control_strength/reliability/coverage columns."""
    alembic_runner.migrate_up_to(_OUR_REV)
    with alembic_engine.connect() as conn:
        pragma = conn.execute(sa.text("PRAGMA table_info(controls)")).fetchall()
        col_names = {row[1] for row in pragma}
        for dropped in ("function", "control_strength", "control_reliability", "control_coverage"):
            assert dropped not in col_names, (
                f"controls.{dropped} found after upgrade — it should have been dropped "
                "in the PR iota migration (spec §6.4)."
            )


def test_smart_backfill_uses_uppercase_domain_keys() -> None:
    """B4 anti-regression: _SUBFN_MAP keys MUST be UPPERCASE to match the
    StrEnum-value form that SA writes for ``domain`` and ``function`` (Phase 1.2
    migration uses ``Enum(<StrEnum>, native_enum=False)`` — values are stored as
    the enum member's value, which in this codebase is the UPPERCASE name).

    A regression to lowercase keys silently misses every row and the fallback
    is taken for the entire table — backfill data integrity is wrong, the
    pytest-alembic tests above catch it indirectly, and this sentinel makes
    the failure mode immediately diagnosable.

    The lookup value below is sourced from the spec §6.6 OQ2 backfill table.
    """
    from pathlib import Path

    mig_path = (
        Path(__file__).resolve().parent.parent.parent
        / "alembic"
        / "versions"
        / "a1b2c3d4e5f6_phase_1_5b_alpha_cfa.py"
    )
    src = mig_path.read_text(encoding="utf-8")
    assert (
        '("LOSS_EVENT",          "PREVENTIVE"):' in src or '("LOSS_EVENT", "PREVENTIVE"):' in src
    ), (
        "B4: expected UPPERCASE _SUBFN_MAP key for (LOSS_EVENT, PREVENTIVE); "
        "lowercase keys silently miss every row at backfill time."
    )
    assert '("loss_event", "PREVENTIVE")' not in src, (
        "B4 regression: lowercase domain key found in _SUBFN_MAP. "
        "Domain is stored as the StrEnum value (UPPERCASE) — keys must match."
    )


# ---------------------------------------------------------------------------
# F7 §11.3 gap test 1: NULL-cap-only-on-time-unit (negative case)
# ---------------------------------------------------------------------------


def test_backfill_assigns_non_null_capability_for_probability_units(
    alembic_runner: MigrationContext,
    alembic_engine: Engine,
) -> None:
    """After upgrade, every CFA row whose sub_function has PROBABILITY/PERCENT_REDUCTION
    unit must have non-NULL capability_value (NULL is reserved for ELAPSED_TIME/CURRENCY units).

    Regression guard for spec §6.6 backfill: the smarter backfill copies
    control_strength → capability_value only for non-time-unit sub_functions.
    """
    alembic_runner.migrate_up_to(_DOWN_REV)
    with alembic_engine.connect() as conn:
        org_id = _insert_org(conn)
        # LOSS_EVENT/PREVENTIVE → backfills to LEC_PREV_RESISTANCE (PROBABILITY unit)
        _insert_control(
            conn,
            org_id=org_id,
            domain="LOSS_EVENT",
            function="PREVENTIVE",
            strength=0.65,
            coverage=0.7,
            reliability=0.75,
        )
        conn.commit()

    alembic_runner.migrate_up_to(_OUR_REV)

    with alembic_engine.connect() as conn:
        rows = conn.execute(
            sa.text("SELECT sub_function, capability_value FROM control_function_assignments")
        ).fetchall()
        assert len(rows) == 1
        sub_function, capability_value = rows[0]
        unit = SUB_FUNCTION_UNITS[FairCamSubFunction(sub_function)]
        if unit in (UnitType.PROBABILITY, UnitType.PERCENT_REDUCTION):
            assert capability_value is not None, (
                f"PROBABILITY/PERCENT_REDUCTION sub_function {sub_function!r} "
                f"must not have NULL capability_value after backfill"
            )


# ---------------------------------------------------------------------------
# F7 §11.3 gap test 2: distinct upgrade row count
# ---------------------------------------------------------------------------


def test_upgrade_creates_exactly_one_cfa_per_control(
    alembic_runner: MigrationContext,
    alembic_engine: Engine,
) -> None:
    """Backfill creates exactly one CFA row per pre-existing Control row (1:1).

    Regression guard for the OQ3 hard-cap precondition: the migration
    must produce no duplicate rows even if the smarter backfill table
    has overlapping (domain, function) → sub_function mappings.
    """
    alembic_runner.migrate_up_to(_DOWN_REV)
    with alembic_engine.connect() as conn:
        org_id = _insert_org(conn)
        _insert_control(conn, org_id=org_id, domain="LOSS_EVENT", function="PREVENTIVE")
        _insert_control(conn, org_id=org_id, domain="VARIANCE_MANAGEMENT", function="DETECTIVE")
        _insert_control(conn, org_id=org_id, domain="DECISION_SUPPORT", function="CORRECTIVE")
        conn.commit()

    alembic_runner.migrate_up_to(_OUR_REV)

    with alembic_engine.connect() as conn:
        controls_count = conn.execute(sa.text("SELECT COUNT(*) FROM controls")).scalar_one()
        cfa_count = conn.execute(
            sa.text("SELECT COUNT(*) FROM control_function_assignments")
        ).scalar_one()
        assert controls_count == cfa_count == 3, (
            f"controls={controls_count}, cfa={cfa_count}; expected 3 each (1:1 backfill)"
        )


# ---------------------------------------------------------------------------
# F7 §11.3 gap test 3: name/domain/type round-trip
# ---------------------------------------------------------------------------


def test_upgrade_preserves_control_name_domain_type(
    alembic_runner: MigrationContext,
    alembic_engine: Engine,
) -> None:
    """Upgrade does not mutate Control.name / domain / type values.

    Regression guard for §6.5 (in-place column drop): the `function`,
    `control_strength`, `control_reliability`, `control_coverage`
    column drops must not affect adjacent columns.
    """
    alembic_runner.migrate_up_to(_DOWN_REV)
    with alembic_engine.connect() as conn:
        org_id = _insert_org(conn)
        ctrl_id = _insert_control(
            conn,
            org_id=org_id,
            domain="LOSS_EVENT",
            function="PREVENTIVE",
        )
        # Capture pre-upgrade state
        pre = conn.execute(
            sa.text("SELECT name, domain, type FROM controls WHERE id = :id"),
            {"id": ctrl_id},
        ).fetchone()
        conn.commit()

    alembic_runner.migrate_up_to(_OUR_REV)

    with alembic_engine.connect() as conn:
        post = conn.execute(
            sa.text("SELECT name, domain, type FROM controls WHERE id = :id"),
            {"id": ctrl_id},
        ).fetchone()
    assert pre == post, f"Control fields mutated by upgrade: pre={pre!r}, post={post!r}"
