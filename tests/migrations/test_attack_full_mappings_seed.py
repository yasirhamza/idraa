"""P2 full-mapping seed guards (#475): completeness, provenance honesty,
catalog integrity, version-bump covenant, slug-scoped downgrade."""

from __future__ import annotations

import json
import re
from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import Engine

import idraa
from idraa.schemas.attack_catalog import EntryAttackMappingSeed

_PRE_REV = "291038b726fd"  # UPDATE if `uv run alembic heads` differs at write time
_SEED_REV = "617f5ca862c3"
_BACKFILL2_REV = "f9330f3b7208"

AI_ENTRY_SLUG = "generative-ai-prompt-injection"
EXEMPLAR_SLUGS = {
    "ransomware-on-ehr",
    "phishing-ad-compromise-ransomware",
    "unauthorized-plc-modification",
    "denial-of-control",
    "ddos-extortion-financial",
}


def _data_dir() -> Path:
    return Path(idraa.__file__).resolve().parent.parent.parent / "data"


def _full_payload() -> dict:
    return json.loads((_data_dir() / "seed_attack_full_mappings.json").read_text())


def _full_mappings() -> list[dict]:
    return _full_payload()["mappings"]


def _library_slugs() -> set[str]:
    slugs: set[str] = set()
    for name in ("seed_library_entries.json", "seed_library_entries_extension.json"):
        payload = json.loads((_data_dir() / name).read_text())
        entries = payload["entries"] if isinstance(payload, dict) else payload
        slugs |= {e["slug"] for e in entries}
    return slugs


def _catalog() -> dict:
    return json.loads((_data_dir() / "seed_attack_catalog.json").read_text())


def test_every_row_validates_and_slugs_exist():
    rows = [EntryAttackMappingSeed.model_validate(m) for m in _full_mappings()]
    library = _library_slugs()
    assert all(r.entry_slug in library for r in rows)
    # Exemplar entries are curated in the OTHER file; never duplicated here.
    assert not {r.entry_slug for r in rows} & EXEMPLAR_SLUGS


def _d_iii_b_full() -> list[dict]:
    """Epic D-iii-b (#497): the 8 new entries' full-mapping rows live in a
    SEPARATE seed file (data/seed_attack_d_iii_b_full.json) so the historical
    fail-loud attack-seed migrations that own seed_attack_full_mappings.json
    stay undisturbed. This completeness guard is the ONE place that must union
    the two files -- do NOT fold this into the shared _full_mappings() helper,
    which feeds test_migration_up_seeds_and_downgrade_is_slug_scoped (that
    test upgrades only to 617f5ca862c3, BEFORE rev2 seeds the D-iii-b file;
    inflating _full_mappings() by 8 would break its count assertion)."""
    return json.loads((_data_dir() / "seed_attack_d_iii_b_full.json").read_text())["mappings"]


def _avgapfill_full() -> list[dict]:
    """Attack-coverage gap-fill epic (#529): the 9 new entries' (+ 3 ICS-twin
    mapping additions to existing entries, §6.1) full-mapping rows live in a
    SEPARATE seed file (data/seed_attack_avgapfill_full.json), mirroring the
    _d_iii_b_full() pattern above -- so the historical fail-loud attack-seed
    migrations stay undisturbed. This completeness guard is the ONE place
    that must union it in -- do NOT fold this into the shared
    _full_mappings() helper (same rationale as _d_iii_b_full())."""
    return json.loads((_data_dir() / "seed_attack_avgapfill_full.json").read_text())["mappings"]


def test_completeness_every_published_entry_mapped_or_ai_exempt():
    mapped = (
        {m["entry_slug"] for m in _full_mappings()}
        | {m["entry_slug"] for m in _d_iii_b_full()}
        | {m["entry_slug"] for m in _avgapfill_full()}
        | EXEMPLAR_SLUGS
    )
    unmapped = _library_slugs() - mapped
    assert unmapped == {AI_ENTRY_SLUG}, f"unmapped entries: {sorted(unmapped)}"
    # The deliberate gap must be documented, referencing the ATLAS issue.
    note = _full_payload()["_note"]
    assert AI_ENTRY_SLUG in note and "#482" in note


def test_catalog_integrity_and_provenance_rules():
    techniques = {(t["domain"], t["technique_id"]): t for t in _catalog()["techniques"]}
    for m in _full_mappings():
        key = (m["domain"], m["technique_id"])
        assert key in techniques, f"unknown technique {key} on {m['entry_slug']}"
        assert not techniques[key].get("deprecated"), f"deprecated {key} on {m['entry_slug']}"
        if m["provenance"] == "cited":
            assert any(c.strip() for c in m["citations"])
            assert "ICSA-17-181-01" not in " ".join(m["citations"]), "dead mis-ID advisory"
        else:
            assert not re.search(r"\bcited\b", m["rationale"], re.IGNORECASE)


def test_version_bump_covenant_mappings_pin_latest_entry_version(
    alembic_config: Config, alembic_engine: Engine
) -> None:
    """Arch-I4 covenant, DB-level: after seeding, EVERY curated mapping row
    (exemplar + full) pins its entry's LATEST version. A future recuration
    that bumps an entry version without carrying mappings forward makes the
    new latest version mapping-less and fails this test."""
    command.upgrade(alembic_config, _SEED_REV)
    with alembic_engine.connect() as conn:
        stale = conn.execute(
            sa.text(
                "SELECT m.library_entry_id FROM library_entry_attack_mappings m "
                "JOIN scenario_library_entries e ON e.id = m.library_entry_id "
                "GROUP BY m.library_entry_id, m.library_entry_version "
                "HAVING m.library_entry_version < ("
                "  SELECT MAX(e2.version) FROM scenario_library_entries e2 "
                "  WHERE e2.id = m.library_entry_id)"
            )
        ).all()
    assert stale == [], f"mappings pinned to stale entry versions: {stale}"


def test_migration_up_seeds_and_downgrade_is_slug_scoped(
    alembic_config: Config, alembic_engine: Engine
) -> None:
    command.upgrade(alembic_config, _SEED_REV)
    with alembic_engine.connect() as conn:
        total = conn.execute(sa.text("SELECT COUNT(*) FROM library_entry_attack_mappings")).scalar()
    expected_new = len(_full_mappings())
    assert total == 14 + expected_new  # exemplars + this file

    command.downgrade(alembic_config, _PRE_REV)
    with alembic_engine.connect() as conn:
        remaining = conn.execute(
            sa.text("SELECT COUNT(*) FROM library_entry_attack_mappings")
        ).scalar()
    assert remaining == 14, "slug-scoped downgrade must leave the exemplar rows"


def test_backfill_rerun_revision_applies_and_prints_counts(
    alembic_config: Config, alembic_engine: Engine, capfd
) -> None:
    command.upgrade(alembic_config, _BACKFILL2_REV)
    out = capfd.readouterr().out
    assert "inserted" in out and "skipped" in out
