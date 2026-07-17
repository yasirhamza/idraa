"""Schema + coverage guard for data/loss_form_targets.json (Epic D-ii-a).

Mirrors tests/unit/test_target_archetypes.py coverage discipline, re-keyed to
(sector, form) cells + the extended archetype list."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

_FORMS = {"productivity", "response", "replacement", "fines", "competitive_advantage", "reputation"}
_CORE_SECTORS = {
    "manufacturing",
    "energy_utilities",
    "healthcare",
    "financial_services",
    "retail_ecommerce",
    "technology_saas",
    "government_public",
    "education",
    "professional_services",
    "transportation_logistics",
    "telecom",
    "hospitality",
    "food_agriculture",
}
_DISPOSITIONS = {"has_source", "needs_fresh_research", "defer_to_diii_profile"}
# canonical-form gap-report mis-cites (must match _FORBIDDEN_CITES in the corpus test)
_FORBIDDEN_CITES = {"FBI PSA I-091019-PSA", "DOJ 15-1433", "CISA AA22-186A", "PREPA/AMI"}


def _doc() -> dict:
    return json.loads(Path("data/loss_form_targets.json").read_text(encoding="utf-8"))


def test_cell_rows_shape_and_sources() -> None:
    for c in _doc()["cells"]:
        assert c["sector"] in _CORE_SECTORS, f"bad sector {c.get('sector')!r}"
        assert c["form"] in _FORMS, f"bad form {c.get('form')!r}"
        assert isinstance(c["candidate_sources"], list)
        assert c["priority"] in ("high", "medium", "low")
        assert c["disposition"] in _DISPOSITIONS, f"{c['sector']}/{c['form']}: bad disposition"
        if c["candidate_sources"]:
            assert c["disposition"] == "has_source"
        else:
            assert c["disposition"] in ("needs_fresh_research", "defer_to_diii_profile")


def test_all_78_sector_form_cells_present() -> None:
    cells = {(c["sector"], c["form"]) for c in _doc()["cells"]}
    missing = {(s, f) for s in _CORE_SECTORS for f in _FORMS} - cells
    assert not missing, f"missing (sector, form) cells: {sorted(missing)}"


def test_response_and_productivity_are_high_priority_everywhere() -> None:
    for c in _doc()["cells"]:
        if c["form"] in ("response", "productivity"):
            assert c["priority"] == "high", f"{c['sector']}/{c['form']} must be high priority"


def test_extended_archetypes_cover_all_85_existing_slugs() -> None:
    doc = _doc()
    existing = set()
    for f in ("seed_library_entries.json", "seed_library_entries_extension.json"):
        for e in json.loads(Path("data", f).read_text(encoding="utf-8")):
            existing.add(e["slug"])
    listed = {a["slug"] for a in doc["archetypes"]}
    missing = existing - listed
    assert not missing, f"target archetype list misses existing seed slugs: {sorted(missing)}"


def test_keep_row_existing_slug_backrefs_a_real_seed_slug() -> None:
    real = set()
    for f in ("seed_library_entries.json", "seed_library_entries_extension.json"):
        for e in json.loads(Path("data", f).read_text(encoding="utf-8")):
            real.add(e["slug"])
    for a in _doc()["archetypes"]:
        if a["keep_or_new"] == "keep":
            assert a["existing_slug"] in real, f"{a['slug']}: existing_slug not a real seed slug"


def test_new_vertical_archetypes_are_attested_or_flagged() -> None:
    for a in _doc()["archetypes"]:
        if a["keep_or_new"] != "new":
            continue
        assert a.get("attestation") or a.get("needs_fresh_research") is True, (
            f"{a['slug']}: new vertical archetype needs a repo attestation pointer or needs_fresh_research"
        )
        assert a["sector"] in _CORE_SECTORS


def test_no_forbidden_citation_in_attestation() -> None:
    for a in _doc()["archetypes"]:
        att = a.get("attestation") or ""
        for bad in _FORBIDDEN_CITES:
            assert bad not in att, (
                f"{a['slug']}: attestation uses forbidden mis-cited source {bad!r}"
            )


def test_candidate_sources_exist_in_corpus_for_that_form_and_sector() -> None:
    corpus = json.loads(Path("data/loss_form_source_corpus.json").read_text(encoding="utf-8"))
    by_source_form: dict[tuple[str, str], dict] = {
        (r["source"], r["form"]): r for r in corpus if r["source"]
    }
    for c in _doc()["cells"]:
        for s in c["candidate_sources"]:
            row = by_source_form.get((s, c["form"]))
            assert row is not None, (
                f"{c['sector']}/{c['form']} cites {s!r} not catalogued for form {c['form']!r}"
            )
            assert c["sector"] in row["sectors_covered"], (
                f"{c['sector']}/{c['form']} cites {s!r} which does not cover sector {c['sector']!r}"
            )


@pytest.mark.skipif(
    os.environ.get("LOSS_FORM_RESEARCH_COMPLETE") != "1",
    reason="armed at D-ii-b closure: set LOSS_FORM_RESEARCH_COMPLETE=1 to assert the sweep drained every fresh-research target",
)
def test_no_needs_fresh_research_remaining() -> None:
    doc = _doc()
    open_cells = [
        f"{c['sector']}/{c['form']}"
        for c in doc["cells"]
        if c["disposition"] == "needs_fresh_research"
    ]
    open_arch = [a["slug"] for a in doc["archetypes"] if a.get("needs_fresh_research") is True]
    assert not open_cells and not open_arch, (
        f"unresearched cells: {open_cells}; unattested archetypes: {open_arch}"
    )
