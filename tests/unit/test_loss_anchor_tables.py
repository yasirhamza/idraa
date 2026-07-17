import json
from pathlib import Path

_ANCHORS = Path("data/loss_anchor_tables.json")
_ARCHETYPES = Path("data/target_archetypes.json")

_VALID_TIERS = {"paginated", "vendor", "anecdotal"}
_VALID_ANCHOR_TYPES = {"quantile_pair", "median_mean", "multiplier_over_baseline", "none"}


def _anchors():
    return json.loads(_ANCHORS.read_text(encoding="utf-8"))


def _archetype_slugs():
    return {r["archetype"] for r in json.loads(_ARCHETYPES.read_text(encoding="utf-8"))}


def test_every_row_has_required_fields():
    for row in _anchors():
        for f in ("archetype", "sector", "loss_tier", "anchor_type", "citations", "verified"):
            assert f in row, f"{row.get('archetype')}: missing {f}"
        assert row["loss_tier"] in _VALID_TIERS
        assert row["anchor_type"] in _VALID_ANCHOR_TYPES
        assert isinstance(row["citations"], list)
        assert isinstance(row["verified"], bool)


# NAICS-family map for the multiplier carve-out (sub-sector over its OWN family
# baseline is legitimate refinement; any other pairing is forbidden cross-sector
# borrowing — plan-gate B-METH-4/B-SC-1). Extend ONLY with methodology sign-off.
_NAICS_FAMILY = {
    # sector -> allowed baseline sectors (self always allowed)
    "energy_utilities": {"energy_utilities"},
    "manufacturing": {"manufacturing"},
    "transportation_logistics": {"transportation_logistics"},
    # ... every sector maps at least to itself; sub-sector rows use their parent
}


def test_anchor_shape_matches_type():
    for row in _anchors():
        t = row["anchor_type"]
        if t == "quantile_pair":
            assert row.get("p50") and row.get("p95") and row["p95"] > row["p50"] > 0
        elif t == "median_mean":
            assert row.get("median") and row.get("mean") and row["mean"] > row["median"] > 0
        elif t == "multiplier_over_baseline":
            # B-METH-4: baseline MUST be the row's own sector or its NAICS family
            # (within-family sub-sector refinement) — NEVER an unrelated sector.
            allowed = _NAICS_FAMILY.get(row["sector"], {row["sector"]})
            assert row.get("baseline_sector") in allowed, (
                f"{row['archetype']}: baseline {row.get('baseline_sector')} not in own NAICS family {allowed}"
            )
            # B-METH-9: bounded multiplier (catalogue range is 1.7-3.0; >10 needs explicit methodology sign-off)
            assert 0 < row.get("multiplier", 0) <= 10, (
                f"{row['archetype']}: multiplier out of (0,10]"
            )
            # B-METH-1/5/10: >=2 citations, incl. one for the MULTIPLIER itself
            assert len(row.get("citations", [])) >= 2, (
                f"{row['archetype']}: multiplier rows need >=2 citations"
            )
            assert any(c.get("supports") == "multiplier" for c in row["citations"]), (
                f"{row['archetype']}: no citation marked supports=multiplier"
            )
            # the baseline leg must itself be a cited anchor row in the table
            assert row["baseline_sector"] in {r["sector"] for r in _anchors()}
        elif t == "none":
            assert row.get("no_source_reason"), (
                f"{row['archetype']}: anchor_type none requires no_source_reason"
            )
            assert row["loss_tier"] == "anecdotal"


def test_tier_citation_consistency():
    # TIER-1 (paginated): >=1 citation with a figure/table/page locator.
    # TIER-2 (vendor): >=1 citation with a named source+year; non-paginated cites carry permalink+accessed.
    # TIER-3 (anecdotal): no anchor values asserted (anchor_type none or context-only).
    import re

    for row in _anchors():
        cites = row["citations"]
        if row["loss_tier"] == "paginated":
            assert any(
                re.search(r"(Figure|Fig\.|Table|p\.|page)\s*\S", c.get("locator", ""))
                for c in cites
            ), f"{row['archetype']}: paginated tier without figure/page locator"
        if row["loss_tier"] == "vendor":
            assert cites, f"{row['archetype']}: vendor tier without citations"
            for c in cites:
                assert ("http" in c.get("locator", "")) or re.search(
                    r"(Figure|Table|p\.)", c.get("locator", "")
                ), f"{row['archetype']}: vendor citation lacks permalink or locator"
                if "http" in c.get("locator", ""):
                    assert c.get("accessed"), (
                        f"{row['archetype']}: web citation lacks accessed date"
                    )
        if row["loss_tier"] == "anecdotal":
            # B-METH-2: an anecdotal row may assert NO anchor value of any shape
            # (multiplier included) — anchor_type MUST be "none", unconditionally.
            assert row["anchor_type"] == "none", (
                f"{row['archetype']}: anecdotal tier must have anchor_type none (no asserted values)"
            )


def test_verified_required_for_anchor_values():
    # any row asserting values (non-'none') MUST be verified=True (the adversarial gate passed)
    for row in _anchors():
        if row["anchor_type"] != "none":
            assert row["verified"] is True, f"{row['archetype']}: unverified anchor values"


def test_no_duplicates_or_unknown_archetypes():
    # always-on structural guard during the sector-by-sector build-out
    rows = _anchors()
    slugs = [r["archetype"] for r in rows]
    assert len(slugs) == len(set(slugs)), "duplicate archetype rows"
    unknown = set(slugs) - _archetype_slugs()
    assert not unknown, f"anchor rows for unknown archetypes: {unknown}"


def test_full_coverage_final_gate():
    # Env-gated completeness gate (plan-gate Arch-I1/I-SC-1: no mutable in-test
    # flag). SKIPPED during Tasks 2-14; Task 15 runs it with
    # LOSS_ANCHORS_COMPLETE=1 and adds that env to the documented full-suite
    # verify command so it stays armed at the PR-gate.
    import os

    import pytest

    if not os.getenv("LOSS_ANCHORS_COMPLETE"):
        pytest.skip("completeness gate not yet armed — set LOSS_ANCHORS_COMPLETE=1 (Task 15)")
    missing = _archetype_slugs() - {r["archetype"] for r in _anchors()}
    assert not missing, f"archetypes without anchor rows: {missing}"
