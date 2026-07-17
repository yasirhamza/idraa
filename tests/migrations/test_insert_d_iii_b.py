"""Epic D-iii-b migration test: the two insert-if-absent migrations
(f4a1c2b3d4e5 entries, a5b6c7d8e9f0 ATT&CK mappings) land the 8 new attested
vertical entries + their ATT&CK mappings on an EXISTING DB that already ran
through d3f1a7c9e5b2.

**CRITICAL -- un-masking the test:** on a fresh alembic test DB,
``0897a0ff350e`` (an ancestor of ``d3f1a7c9e5b2``) reads the LIVE extension
JSON and already inserts all 62 entries incl. the 8 new D-iii-b slugs, and
``d3f1a7c9e5b2``'s UPDATE sets their ``loss_tier``/``loss_form_profile`` --
so ``f4a1c2b3d4e5``'s INSERT would be a no-op and this test would assert
nothing. We reproduce the genuine PROD state by DELETING the 8 D-iii-b slugs
(version=1) + their overrides AFTER migrating to ``d3f1a7c9e5b2``, then
running ONLY the two D-iii-b migrations -- exactly the state a real prod DB
that ran ``d3f1a7c9e5b2`` before Task 1 appended the 8 entries would be in.
"""

from __future__ import annotations

import json
from pathlib import Path

import sqlalchemy as sa
from pytest_alembic import MigrationContext
from sqlalchemy.engine import Engine

import idraa
from idraa.services.seed_library_loader import LibraryEntrySeed

_PRE_REV = "d3f1a7c9e5b2"  # down_revision of f4a1c2b3d4e5 (D-iii-a head)
_ENTRIES_REV = "f4a1c2b3d4e5"
_MAPPINGS_REV = "a5b6c7d8e9f0"

_NEW_SLUGS = (
    "physician-practice-clearinghouse-revenue-disruption",
    "law-enforcement-records-extortion-breach",
    "casino-ransomware-operational-disruption",
    "telecom-lawful-intercept-nationstate-compromise",
    "law-firm-privileged-data-ransomware-extortion",
    "k12-edtech-vendor-breach",
    "higher-ed-insider-ddos",
    "judiciary-court-system-ransomware",
)


def _root() -> Path:
    return Path(idraa.__file__).resolve().parent.parent.parent


def _extension() -> list[dict]:
    return json.loads((_root() / "data" / "seed_library_entries_extension.json").read_text())


def _attack_d_iii_b_mappings() -> list[dict]:
    payload = json.loads((_root() / "data" / "seed_attack_d_iii_b_full.json").read_text())
    return payload["mappings"]


def _count_entries(engine: Engine) -> int:
    with engine.connect() as conn:
        return conn.execute(
            sa.text("SELECT COUNT(*) FROM scenario_library_entries WHERE version = 1")
        ).scalar_one()


def _slugs(engine: Engine) -> set[str]:
    with engine.connect() as conn:
        return {
            r[0]
            for r in conn.execute(
                sa.text("SELECT slug FROM scenario_library_entries WHERE version = 1")
            ).fetchall()
        }


def _delete_d_iii_b_slugs_and_overrides(engine: Engine) -> None:
    """Reproduce the genuine prod state: delete the 8 new slugs (version=1)
    and any overrides referencing them, so the D-iii-b migrations have
    something real to insert."""
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "DELETE FROM scenario_library_overrides "
                "WHERE library_entry_id IN ("
                "  SELECT id FROM scenario_library_entries "
                "  WHERE slug IN :slugs AND version = 1"
                ")"
            ).bindparams(sa.bindparam("slugs", _NEW_SLUGS, expanding=True))
        )
        conn.execute(
            sa.text(
                "DELETE FROM scenario_library_entries WHERE slug IN :slugs AND version = 1"
            ).bindparams(sa.bindparam("slugs", _NEW_SLUGS, expanding=True))
        )


def test_insert_d_iii_b_entries_and_mappings_on_existing_db(
    alembic_runner: MigrationContext,
    alembic_engine: Engine,
) -> None:
    # Migrate to the D-iii-a head, then reproduce the genuine pre-D-iii-b prod
    # state by deleting the 8 new slugs that a fresh-DB 0897a0ff350e run would
    # have already inserted from the (now-updated) live extension JSON.
    alembic_runner.migrate_up_to(_PRE_REV)
    _delete_d_iii_b_slugs_and_overrides(alembic_engine)
    pre_count = _count_entries(alembic_engine)
    assert set(_NEW_SLUGS).isdisjoint(_slugs(alembic_engine)), (
        "D-iii-b slugs still present after simulated-prod delete"
    )

    # Run ONLY the two D-iii-b migrations.
    alembic_runner.migrate_up_to(_ENTRIES_REV)
    assert _count_entries(alembic_engine) == pre_count + 8
    assert set(_NEW_SLUGS) <= _slugs(alembic_engine), "not all 8 new slugs inserted"

    alembic_runner.migrate_up_to(_MAPPINGS_REV)

    # --- Entry-level assertions ---
    with alembic_engine.connect() as conn:
        rows = conn.execute(
            sa.text(
                "SELECT id, slug, loss_tier, loss_form_profile, primary_loss, "
                "secondary_loss FROM scenario_library_entries "
                "WHERE slug IN :slugs AND version = 1"
            ).bindparams(sa.bindparam("slugs", _NEW_SLUGS, expanding=True))
        ).fetchall()
    assert len(rows) == 8
    ext_by_slug = {e["slug"]: e for e in _extension() if e["slug"] in _NEW_SLUGS}
    for row in rows:
        eid, slug, loss_tier, loss_form_profile, primary_loss, secondary_loss = row
        # 32-hex no-hyphen UUID.
        eid_str = str(eid)
        assert len(eid_str) == 32 and "-" not in eid_str, (
            f"{slug}: id not 32-hex no-hyphen: {eid_str!r}"
        )
        # loss_tier / loss_form_profile explicitly set (the column-omission trap).
        assert loss_tier == "paginated", (
            f"{slug}: expected loss_tier='paginated', got {loss_tier!r}"
        )
        lfp = (
            json.loads(loss_form_profile)
            if isinstance(loss_form_profile, str)
            else loss_form_profile
        )
        assert lfp, f"{slug}: loss_form_profile must be non-empty"
        # loss nodes match the seed JSON exactly.
        pl = json.loads(primary_loss) if isinstance(primary_loss, str) else primary_loss
        assert pl == ext_by_slug[slug]["primary_loss"], (
            f"{slug}: primary_loss mismatch vs seed JSON"
        )
        sl = json.loads(secondary_loss) if isinstance(secondary_loss, str) else secondary_loss
        assert sl == ext_by_slug[slug]["secondary_loss"], (
            f"{slug}: secondary_loss mismatch vs seed JSON"
        )
        LibraryEntrySeed.model_validate(ext_by_slug[slug])

    # --- ATT&CK mapping assertions: every slug has >=1 row ---
    slug_by_id = {str(r[0]): r[1] for r in rows}
    with alembic_engine.connect() as conn:
        mapping_rows = conn.execute(
            sa.text(
                "SELECT library_entry_id FROM library_entry_attack_mappings "
                "WHERE library_entry_id IN :ids"
            ).bindparams(sa.bindparam("ids", tuple(slug_by_id), expanding=True))
        ).fetchall()
    mapped_slugs = {slug_by_id[str(r[0])] for r in mapping_rows}
    assert mapped_slugs == set(_NEW_SLUGS), (
        f"not every D-iii-b slug has a mapping row; missing: {set(_NEW_SLUGS) - mapped_slugs}"
    )
    expected_mapping_count = len(_attack_d_iii_b_mappings())
    assert len(mapping_rows) == expected_mapping_count, (
        f"expected {expected_mapping_count} mapping rows, got {len(mapping_rows)}"
    )

    # --- Idempotency: re-running both migrations inserts once ---
    alembic_runner.migrate_up_to(_ENTRIES_REV)
    alembic_runner.migrate_up_to(_MAPPINGS_REV)
    assert _count_entries(alembic_engine) == pre_count + 8
    with alembic_engine.connect() as conn:
        remapped = conn.execute(
            sa.text(
                "SELECT COUNT(*) FROM library_entry_attack_mappings WHERE library_entry_id IN :ids"
            ).bindparams(sa.bindparam("ids", tuple(slug_by_id), expanding=True))
        ).scalar_one()
    assert remapped == expected_mapping_count, "re-running the mapping migration duplicated rows"

    # --- Downgrade: removes the 8 entries + their mappings + any overrides ---
    alembic_runner.migrate_down_to(_PRE_REV)
    assert _count_entries(alembic_engine) == pre_count
    assert set(_NEW_SLUGS).isdisjoint(_slugs(alembic_engine)), "D-iii-b slugs survived downgrade"
    with alembic_engine.connect() as conn:
        leftover_mappings = conn.execute(
            sa.text(
                "SELECT COUNT(*) FROM library_entry_attack_mappings WHERE library_entry_id IN :ids"
            ).bindparams(sa.bindparam("ids", tuple(slug_by_id), expanding=True))
        ).scalar_one()
    assert leftover_mappings == 0, "downgrade left orphaned ATT&CK mapping rows"
