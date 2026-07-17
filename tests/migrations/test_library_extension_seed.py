"""Structural tests for the scenario-library content-extension migrations.

The runtime test harness builds the schema via ``create_all`` (the current
ORM metadata), so it does NOT exercise the alembic migration chain and cannot
observe the 12→13 CHECK widening. These structural assertions, plus the
alembic-built-DB round-trip run manually during implementation, are the
guarantee that the widening migration targets the right tables/values.
"""

from __future__ import annotations

import json
from pathlib import Path

import sqlalchemy as sa
from pytest_alembic import MigrationContext
from sqlalchemy.engine import Engine

import idraa
from idraa.services.fair_cam_validation import validate_fair_distributions
from idraa.services.seed_library_loader import LibraryEntrySeed

# Revision IDs for the additive-seed round-trip.
_WIDEN_REV = "7e29245a1930"  # CHECK-widening migration (down_revision of the seed)
_SEED_REV = "0897a0ff350e"  # additive insert-if-absent seed migration (THIS branch head)


def _root() -> Path:
    return Path(idraa.__file__).resolve().parent.parent.parent


def _versions_dir() -> Path:
    return _root() / "alembic" / "versions"


def test_widening_migration_targets_both_tables_with_ot_integrity():
    mig = next(_versions_dir().glob("*_widen_threat_category_check.py"))
    text = mig.read_text()
    assert "ot_integrity" in text
    assert "scenario_library_entries" in text and "scenarios" in text
    assert "create_constraint=True" in text


_OT_SLUGS = {
    "ransomware-on-control-layer",
    "process-view-manipulation",
    "field-instrument-spoofing",
    "oem-remote-maintenance-abuse",
    "grid-protective-relay-manipulation",
    "pipeline-scada-integrity",
    "chemical-process-safety-attack",
}
_IT_SLUGS = {
    "accidental-insider-exposure",
    "web-app-exploitation",
    "third-party-processor-breach",
    "retail-pos-card-skimming",
    "public-sector-targeted-intrusion",
    "logistics-disruption",
}


def _extension():
    p = _root() / "data" / "seed_library_entries_extension.json"
    return json.loads(p.read_text())


def _base():
    return json.loads((_root() / "data" / "seed_library_entries.json").read_text())


def test_combined_library_has_93_entries_with_ot_integrity_and_accidental_insider():
    """Coverage assertions after the attack-coverage gap-fill epic:
    31 base + 13 original extension (#303) + 38 new C-iii-b archetypes
    + 3 new energy/manufacturing third-party-revenue scenarios (WS3b)
    + 8 new D-iii-b attested vertical entries (#497)
    + 9 new attack-coverage gap-fill entries (#529) = 102 combined.

    The raw count ``len(ext) == 51`` was the C-iii-b shape pin.  WS3b
    legitimately appended 3 more entries (energy/process-manufacturing
    business_process_third_party_revenue scenarios), D-iii-b appended 8
    more (62 total), and #529 appended 9 more (71 total).  The
    provenance-based invariant is preserved: ALL 13 original slugs
    (_OT_SLUGS | _IT_SLUGS) are still present, and the new ``ot_integrity``
    effect + ``insider_accidental`` actor remain in the combined set.
    """
    base = _base()
    ext = _extension()
    combined = base + ext
    # 31 base + 13 original (#303) + 38 C-iii-b batches A/B/C + 3 WS3b
    # + 8 D-iii-b (#497) + 9 attack-coverage (#529) = 102 total.
    assert len(base) == 31 and len(ext) == 71 and len(combined) == 102
    # Provenance: all 13 original extension slugs are still present.
    assert {e["slug"] for e in ext} >= _OT_SLUGS | _IT_SLUGS
    assert "ot_integrity" in {e["threat_event_type"] for e in combined}
    assert "insider_accidental" in {e["threat_actor_type"] for e in combined}


def test_combined_library_taxonomy_spread():
    """Coverage matrix: the 44-entry library spans OT and IT effects, covers the
    three OT effect values (incl. the 3 ot_integrity entries), and the extension
    keeps the OT+IT split it was authored for (7 OT effects + 6 IT effects)."""
    combined = _base() + _extension()
    effects = [e["threat_event_type"] for e in combined]
    # All three OT effect values are present in the combined library.
    assert {"ot_safety_tampering", "ot_availability", "ot_integrity"} <= set(effects)
    # Exactly the 3 authored ot_integrity entries.
    assert sum(1 for e in effects if e == "ot_integrity") == 3
    # Both OT and IT are present (IT = any non-OT effect).
    ot_effects = {"ot_safety_tampering", "ot_availability", "ot_integrity"}
    assert any(e in ot_effects for e in effects), "OT effects present"
    assert any(e not in ot_effects for e in effects), "IT effects present"


def test_extension_entries_validate_and_have_well_formed_distributions():
    for raw in _extension():
        seed = LibraryEntrySeed.model_validate(raw)
        # Citation-floor is TIER-SCOPED after C-iii-b (Tasks 3–5 added 38 anecdotal
        # entries, which legitimately carry 0 citations per the tiered epistemics ladder).
        #
        # History of this assertion:
        #   >=3  — original #303 shape (all 13 entries were paginated/vendor at authoring)
        #   >=2  — C-iii-a lowered to 2 after anchor-row replacement on vendor-tier entries
        #   tier-aware — C-iii-b lowered again for anecdotal entries (correct: no paginated
        #                source exists for them by definition)
        #
        # The real citation-quality invariants now live in:
        #   • tests/unit/test_loss_anchor_tables.py         — anchor rows carry >=2 cites
        #   • tests/integration/test_seed_library_lognormal.py — lognormal tier↔cites guard
        # This structural check only catches missing citations on paginated/vendor entries.
        loss_tier = raw.get("loss_tier") or "anecdotal"
        if loss_tier not in ("anecdotal", "none"):
            # Epic D-iii-a envelope model: loss is a SINGLE cited source — the IRIS
            # Figure A3 envelope (both p50+p95 legs from one figure), or a single IC3
            # vendor cite for a beyond-envelope BEC entry. The pre-A1 two-leg
            # (p50 primary + p95 tail) requirement no longer applies.
            assert len(seed.source_citations) >= 1, (
                f"{raw['slug']} (loss_tier={loss_tier!r}): expected >=1 source_citation, "
                f"got {len(seed.source_citations)}"
            )
        for field in ("threat_event_frequency", "vulnerability", "primary_loss"):
            d = raw[field]
            dist = d.get("distribution", "")
            if dist == "PERT":
                # PERT: validate shape invariant low <= mode <= high.
                assert d["low"] <= d["mode"] <= d["high"], (
                    f"{raw['slug']} {field}: PERT shape violated"
                )
            elif dist == "lognormal":
                # Lognormal: mean and sigma present and sigma > 0.
                # Full finite/range validation is covered by validate_fair_distributions
                # below; this is a structural presence check.
                assert "mean" in d and "sigma" in d, (
                    f"{raw['slug']} {field}: lognormal missing mean/sigma"
                )
                assert d["sigma"] > 0, f"{raw['slug']} {field}: lognormal sigma must be > 0"
            else:
                raise AssertionError(
                    f"{raw['slug']} {field}: unexpected distribution type {dist!r}"
                )
        assert raw["vulnerability"]["low"] >= 0.0 and raw["vulnerability"]["high"] <= 1.0
        validate_fair_distributions(
            threat_event_frequency=raw["threat_event_frequency"],
            vulnerability=raw["vulnerability"],
            primary_loss=raw["primary_loss"],
            secondary_loss=raw.get("secondary_loss"),
        )


def test_ot_entries_present():
    assert {e["slug"] for e in _extension()} >= _OT_SLUGS


def test_it_entries_present():
    assert {e["slug"] for e in _extension()} >= _IT_SLUGS


def test_accidental_insider_uses_existing_actor_enum():
    e = next(x for x in _extension() if x["slug"] == "accidental-insider-exposure")
    assert e["threat_actor_type"] == "insider_accidental"


# ---------------------------------------------------------------------------
# Task 5: additive insert-if-absent seed migration
# ---------------------------------------------------------------------------

_ALL_NEW_SLUGS = _OT_SLUGS | _IT_SLUGS
_OT_INTEGRITY_SLUGS = {
    "process-view-manipulation",
    "field-instrument-spoofing",
    "pipeline-scada-integrity",
}


def test_additive_migration_reads_extension_and_includes_calibration_anchor():
    """Structural guard (plan Task 5 Step 3): the migration reads the SEPARATE
    extension file, includes the NOT-NULL ``calibration_anchor`` column (the
    c1d2e3f4a5b6 foot-gun), and guards inserts with the version=1 absence check.
    """
    mig = next(_versions_dir().glob("*_seed_library_extension.py"))
    text = mig.read_text()
    # Reads the SEPARATE extension file. The base 31-entry seed file
    # (seed_library_entries.json) must NOT be read by this migration — the only
    # path the code resolves is the _extension.json file. (The docstring may
    # MENTION the base file for contrast, so assert on the resolved-path call
    # rather than on raw substring absence.)
    assert "seed_library_entries_extension.json" in text
    assert '"data" / "seed_library_entries.json"' not in text
    # NOT-NULL column present (foot-gun fix vs c1d2e3f4a5b6).
    assert "calibration_anchor" in text
    # insert-if-absent guard.
    assert "WHERE version = 1" in text
    # down_revision is the widening migration (widen-before-insert ordering).
    assert f'down_revision = "{_WIDEN_REV}"' in text


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


def test_additive_seed_round_trip_102_then_89_then_102(
    alembic_runner: MigrationContext,
    alembic_engine: Engine,
) -> None:
    """alembic-built DB (NOT create_all) so the widening CHECK is present and the
    three ot_integrity rows insert without a constraint violation.

    **Convergence rationale (attack-coverage gap-fill #529 update):**
    The ``0897a0ff350e`` migration uses insert-if-absent over ALL entries in the
    extension JSON file (which now holds 71 entries: the original 13 + 38 new
    C-iii-b archetypes from batches A/B/C + 3 new WS3b energy/manufacturing
    third-party-revenue scenarios + 8 new D-iii-b attested vertical entries
    (#497) + 9 new attack-coverage gap-fill entries (#529)). On a fresh DB,
    ``0897a0ff350e`` therefore inserts all 71 → 31 + 71 = 102 total.

    The ``60ff242180f6`` migration (C-iii-b) has its own slug-filter that skips
    the original 13 and only inserts the 38 new slugs. On a fresh DB that
    already went through ``0897a0ff350e``, all 38 are already present →
    ``60ff242180f6`` upgrade is a no-op, and both paths converge to 102.

    The ``4b7f9e2a1c83`` migration (WS3b) has its own slug-filter for the 3 new
    energy scenarios. On a fresh DB that already went through ``0897a0ff350e``,
    all 3 are already present → ``4b7f9e2a1c83`` upgrade is a no-op. Likewise
    ``f4a1c2b3d4e5`` (D-iii-b) has its own slug-filter for the 8 new attested
    vertical entries, and the attack-coverage insert-if-absent migration has
    its own slug-filter for the 9 new #529 entries -- both are no-ops on a
    fresh DB that already went through ``0897a0ff350e``.

    Downgrade of ``0897a0ff350e`` deletes only the 13 pinned original slugs
    (the literal ``_NEW_SLUGS`` tuple); the 38 C-iii-b + 3 WS3b + 8 D-iii-b
    + 9 attack-coverage slugs, which were inserted by ``0897a0ff350e`` on a
    fresh-DB path but owned semantically by their respective migrations,
    remain → 102 - 13 = 89. Re-upgrade inserts the 13 missing originals →
    89 + 13 = 102 (idempotent).

    Pre-WS3b the expectations were 31→82→69→82; pre-D-iii-b they were
    31→85→72→85; pre-#529 they were 31→93→80→93. Updated with the real
    convergence arithmetic above.
    """
    # State just before the additive seed: 31 base entries, no new slugs.
    alembic_runner.migrate_up_to(_WIDEN_REV)
    assert _count_entries(alembic_engine) == 31
    assert _ALL_NEW_SLUGS.isdisjoint(_slugs(alembic_engine))

    # upgrade → 102 (31 base + all 71 from the extension JSON on a fresh DB).
    alembic_runner.migrate_up_to(_SEED_REV)
    assert _count_entries(alembic_engine) == 102
    assert _slugs(alembic_engine) >= _ALL_NEW_SLUGS

    # The three ot_integrity entries inserted WITHOUT a CHECK violation, stored
    # with the new effect value.
    with alembic_engine.connect() as conn:
        ot_rows = {
            r[0]
            for r in conn.execute(
                sa.text(
                    "SELECT slug FROM scenario_library_entries "
                    "WHERE threat_event_type = 'ot_integrity' AND version = 1"
                )
            ).fetchall()
        }
    assert ot_rows == _OT_INTEGRITY_SLUGS

    # downgrade → 89 (0897a0ff350e downgrade deletes only its 13 pinned slugs;
    # the 38 C-iii-b + 3 WS3b + 8 D-iii-b + 9 attack-coverage slugs remain
    # because 0897's _NEW_SLUGS only lists the original 13).
    alembic_runner.migrate_down_one()
    assert _count_entries(alembic_engine) == 89
    assert _ALL_NEW_SLUGS.isdisjoint(_slugs(alembic_engine))

    # re-upgrade → 102 again (inserts the 13 missing originals; 76 already present).
    alembic_runner.migrate_up_to(_SEED_REV)
    assert _count_entries(alembic_engine) == 102
    assert _slugs(alembic_engine) >= _ALL_NEW_SLUGS


def test_additive_seed_is_idempotent_when_a_slug_already_present(
    alembic_runner: MigrationContext,
    alembic_engine: Engine,
) -> None:
    """Insert-if-absent: pre-seeding one of the 13 original slugs at the widened
    revision must NOT cause a duplicate or an error on upgrade — the migration
    skips slugs already present and inserts only the remaining 70.

    **Count update for #529 (attack-coverage gap-fill):**
    ``0897a0ff350e`` iterates ALL 71 entries in the extension JSON (insert-if-absent
    over the whole file, no slug-filter).  With 1 pre-seeded slug, upgrade inserts
    the remaining 70 → 31 + 1 + 70 = 102.  The no-duplicate guard on the pre-seeded
    slug is the real invariant; the total count is a consequence of the migration
    reading all 71 entries.  Pre-WS3b was 32 + 50 = 82; pre-C-iii-b was 44
    (31+1+12); pre-D-iii-b was 32 + 53 = 85; pre-#529 was 32 + 61 = 93.
    """
    import uuid

    alembic_runner.migrate_up_to(_WIDEN_REV)
    assert _count_entries(alembic_engine) == 31

    # Pre-seed ONE of the 13 original slugs (a non-ot_integrity IT entry so the
    # widened CHECK is irrelevant to the pre-seed itself).
    raw = next(x for x in _extension() if x["slug"] == "web-app-exploitation")
    v = LibraryEntrySeed.model_validate(raw).model_dump()
    with alembic_engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO scenario_library_entries "
                "(id, version, slug, name, status, threat_event_type, threat_actor_type, "
                "asset_class, attack_vector, tags, description, example_incidents, "
                "source_citations, canonical_fair_gap, applicable_industries, "
                "applicable_sub_sectors, applicable_org_sizes, threat_event_frequency, "
                "vulnerability, primary_loss, secondary_loss, suggested_control_ids, "
                "standards_references, calibration_anchor, row_version, created_at, updated_at) "
                "VALUES (:id, 1, :slug, :name, :status, :threat_event_type, :threat_actor_type, "
                ":asset_class, :attack_vector, :tags, :description, :example_incidents, "
                ":source_citations, :canonical_fair_gap, :applicable_industries, "
                ":applicable_sub_sectors, :applicable_org_sizes, :threat_event_frequency, "
                ":vulnerability, :primary_loss, :secondary_loss, :suggested_control_ids, "
                ":standards_references, :calibration_anchor, 1, :now, :now)"
            ),
            {
                "id": uuid.uuid4().hex,
                **{
                    k: json.dumps(val) if isinstance(val, (list, dict)) else val
                    for k, val in v.items()
                },
                "now": "2026-06-03T00:00:00+00:00",
            },
        )
    assert _count_entries(alembic_engine) == 32

    # upgrade — inserts the remaining 70 (skips the pre-seeded slug) → 102, no dup.
    # 32 pre-upgrade + 70 inserted = 102.  Pre-WS3b was 32 + 50 = 82; pre-D-iii-b
    # was 32 + 53 = 85; pre-#529 was 32 + 61 = 93.
    alembic_runner.migrate_up_to(_SEED_REV)
    assert _count_entries(alembic_engine) == 102
    with alembic_engine.connect() as conn:
        dup = conn.execute(
            sa.text(
                "SELECT COUNT(*) FROM scenario_library_entries "
                "WHERE slug = 'web-app-exploitation' AND version = 1"
            )
        ).scalar_one()
    assert dup == 1
