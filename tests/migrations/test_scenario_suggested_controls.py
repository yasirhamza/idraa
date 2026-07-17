import json
from pathlib import Path

import pytest

import idraa
from idraa.models.control_library import ControlLibraryEntry


def _root() -> Path:
    return Path(idraa.__file__).resolve().parent.parent.parent


def _scenario_entries():
    """All curated scenario entries = base 31 + the 13-entry extension file.

    The content-extension (2026-06-02) added 13 entries (7 OT + 6 IT) in a
    SEPARATE ``seed_library_entries_extension.json`` so the original base file
    stays at 31 untouched. Referential integrity must cover all 44, so the
    13 new entries' ``suggested_control_ids`` are checked too.
    """
    base = json.loads((_root() / "data" / "seed_library_entries.json").read_text())
    ext_path = _root() / "data" / "seed_library_entries_extension.json"
    ext = json.loads(ext_path.read_text()) if ext_path.exists() else []
    return base + ext


def _catalog_slugs() -> set[str]:
    payload = json.loads((_root() / "data" / "seed_control_library_entries.json").read_text())
    return {e["slug"] for e in payload["entries"]}


def test_every_suggested_slug_is_a_real_catalog_slug():
    catalog = _catalog_slugs()
    total = 0
    for entry in _scenario_entries():
        for slug in entry.get("suggested_control_ids", []):
            total += 1
            assert slug in catalog, f"{entry['slug']} suggests unknown control slug {slug!r}"
    assert total >= 100, (
        "expected the 44 scenarios (31 base + 13 extension) to be curated (>=100 suggestions total)"
    )


def test_every_scenario_has_at_least_three_suggestions():
    for entry in _scenario_entries():
        n = len(entry.get("suggested_control_ids", []))
        assert n >= 3, f"{entry['slug']} has only {n} suggested controls"


@pytest.mark.asyncio
async def test_seeded_suggestions_resolve_to_published_catalog_entries(db_session):
    # Gate Arch-I2: load both seeds via ORM (harness uses create_all, not Alembic),
    # then assert each scenario's suggested slugs resolve to a PUBLISHED catalog entry.
    cat_payload = json.loads((_root() / "data" / "seed_control_library_entries.json").read_text())
    for e in cat_payload["entries"]:
        db_session.add(
            ControlLibraryEntry(
                version=1,
                slug=e["slug"],
                name=e["name"],
                description=e["description"],
                control_type=e["control_type"],
                nist_csf_subcategories=e.get("nist_csf_subcategories", []),
                cis_safeguards=e.get("cis_safeguards", []),
                iso_27001_controls=e.get("iso_27001_controls", []),
                compliance_mappings=e.get("compliance_mappings", {}),
                applicable_industries=e.get("applicable_industries", []),
                applicable_org_sizes=e.get("applicable_org_sizes", []),
                tags=e.get("tags", []),
                source_citations=e.get("source_citations", []),
                status=e["status"],
            )
        )
    await db_session.flush()

    from sqlalchemy import select

    published = {
        r
        for (r,) in (
            await db_session.execute(
                select(ControlLibraryEntry.slug).where(ControlLibraryEntry.status == "published")
            )
        ).all()
    }
    for entry in _scenario_entries():
        for slug in entry.get("suggested_control_ids", []):
            assert slug in published, (
                f"{entry['slug']} suggests non-published catalog slug {slug!r}"
            )


def test_migration_uses_the_seed_json_as_single_source():
    # The UPDATE migration must derive suggested_control_ids from data/seed_library_entries.json
    # (single source). Assert the migration file reads that JSON and binds suggested_control_ids,
    # so fresh-seed and back-fill paths converge by construction.
    import re

    mig_dir = _root() / "alembic" / "versions"
    text = next(p for p in mig_dir.glob("*_seed_scenario_suggested_controls.py")).read_text()
    assert "seed_library_entries.json" in text
    assert "suggested_control_ids" in text
    assert "UPDATE scenario_library_entries" in text
    assert re.search(r"WHERE slug = :slug AND version = 1", text)
