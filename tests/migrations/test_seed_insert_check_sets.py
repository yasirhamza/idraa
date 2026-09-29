"""Fresh-DB boot guard: every seed entry must satisfy the CHECK sets in force
at the revision that first INSERTs it (Epic F #192, spec §4.1.8 / A-1, A2-4).

Why this exists: ``c1d2e3f4a5b6`` inserts ``data/seed_library_entries.json``
while the ``assetclass`` CHECK is still ``b8e0334b7f43``'s 7-value set
(``cash_or_equivalent`` only arrives with ``bf920a18ef0c``). A relabel in the
base file to a later-widened value would crash-loop a fresh boot at
``alembic upgrade head``, while every already-migrated DB (which never re-runs
``c1d2e3f4a5b6``) would be fine — the failure is invisible until the next
fresh deploy or the next migration test. ``c1d2e3f4a5b6._PRE_WIDENING`` is the
documented, fresh-path-only escape hatch: the pre-widening value is inserted
there and the Epic F data migration converges it on both paths.

The extension file (``data/seed_library_entries_extension.json``) is inserted
by several revisions, each with its own pinned ``_NEW_SLUGS``: ``0897a0ff350e``
first, then ``60ff242180f6``, ``4b7f9e2a1c83``, ``f4a1c2b3d4e5`` and
``63cfe62ef5a7``. No migration widens the threat-category, asset-class or
actor-type CHECKs after ``0897a0ff350e`` (verified at Epic F plan-gate 3), so
the sets in force at ``0897a0ff350e`` hold for every extension entry today and
this module checks them all against those sets with no pre-widening map. A
future widening that lands AFTER ``0897a0ff350e`` must split that case per
inserting revision (each later revision's entries against the sets in force at
that revision) — a relabel to a value added after ``0897a0ff350e`` then fails
here with a clear message instead of crash-looping a fresh boot.

No revision literal is hard-coded into the alembic run beyond the one this
guard is about (``c1d2e3f4a5b6``); the migration modules are imported by path.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest
import sqlalchemy as sa
from pytest_alembic import MigrationContext
from sqlalchemy.engine import Engine

import idraa

_ROOT = Path(idraa.__file__).resolve().parent.parent.parent
_VERSIONS = _ROOT / "alembic" / "versions"
_BASE_SEED = _ROOT / "data" / "seed_library_entries.json"
_EXT_SEED = _ROOT / "data" / "seed_library_entries_extension.json"

_BASE_INSERT_REV = "c1d2e3f4a5b6"  # inserts the base file
_TAXONOMY_REV = "b8e0334b7f43"  # original CHECK sets (7-value assetclass, 12-value threatcategory)
_EXT_FIRST_INSERT_REV = "0897a0ff350e"  # earliest extension insert
_THREAT_WIDEN_REV = "7e29245a1930"  # threatcategory 12 -> 13 (ot_integrity)
_ASSET_WIDEN_REV = "bf920a18ef0c"  # assetclass 7 -> 11 (FAIR Feb 2025 classes)

_FIELDS = ("threat_event_type", "threat_actor_type", "asset_class")


def _load_migration(rev: str) -> ModuleType:
    (path,) = _VERSIONS.glob(f"{rev}_*.py")
    spec = importlib.util.spec_from_file_location(f"_mig_{rev}", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _entries(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def _b8e0_sets() -> dict[str, frozenset[str]]:
    mod = _load_migration(_TAXONOMY_REV)
    return {
        "threat_event_type": frozenset(mod._THREAT_CATEGORY_ENUM.enums),
        "threat_actor_type": frozenset(mod._THREAT_ACTOR_TYPE_ENUM.enums),
        "asset_class": frozenset(mod._ASSET_CLASS_ENUM.enums),
    }


def _sets_at_0897() -> dict[str, frozenset[str]]:
    """CHECK sets in force at 0897a0ff350e: threatcategory widened by
    7e29245a1930, assetclass widened by bf920a18ef0c, actor type unchanged
    since b8e0334b7f43."""
    return {
        "threat_event_type": frozenset(_load_migration(_THREAT_WIDEN_REV)._VALUES_13),
        "threat_actor_type": frozenset(
            _load_migration(_TAXONOMY_REV)._THREAT_ACTOR_TYPE_ENUM.enums
        ),
        "asset_class": frozenset(_load_migration(_ASSET_WIDEN_REV)._ASSET_CLASS_VALUES_NEW),
    }


# ---------------------------------------------------------------------------
# (i) base file vs the b8e0334b7f43 sets, modulo _PRE_WIDENING
# ---------------------------------------------------------------------------


def test_base_seed_entries_satisfy_b8e0_check_sets_or_are_pre_widened() -> None:
    sets = _b8e0_sets()
    pre_widening: dict[tuple[str, str], str] = _load_migration(_BASE_INSERT_REV)._PRE_WIDENING
    violations: list[str] = []
    for e in _entries(_BASE_SEED):
        for field in _FIELDS:
            value = e[field]
            if value in sets[field]:
                continue
            if (e["slug"], field) in pre_widening:
                continue
            violations.append(
                f"{e['slug']}.{field} = {value!r} is outside the {_TAXONOMY_REV} CHECK set "
                f"and has no _PRE_WIDENING entry in {_BASE_INSERT_REV} — a fresh "
                f"`alembic upgrade head` would fail at the base-seed INSERT"
            )
    assert not violations, "\n".join(violations)


def test_pre_widening_values_are_inside_the_b8e0_sets_and_target_real_cells() -> None:
    sets = _b8e0_sets()
    pre_widening: dict[tuple[str, str], str] = _load_migration(_BASE_INSERT_REV)._PRE_WIDENING
    by_slug = {e["slug"]: e for e in _entries(_BASE_SEED)}
    assert pre_widening, "_PRE_WIDENING must carry at least the Epic F bec-fraud-financial cell"
    for (slug, field), value in pre_widening.items():
        assert field in _FIELDS, (slug, field)
        assert value in sets[field], (
            f"_PRE_WIDENING[{(slug, field)!r}] = {value!r} not in CHECK set"
        )
        assert slug in by_slug, f"_PRE_WIDENING names {slug!r}, which is not in the base seed"
        # A map entry whose seed value is already inside the set is dead: it
        # would silently mask a future relabel of that cell.
        assert by_slug[slug][field] not in sets[field], (
            f"_PRE_WIDENING[{(slug, field)!r}] is stale: the seed value "
            f"{by_slug[slug][field]!r} already satisfies the {_TAXONOMY_REV} CHECK"
        )


def test_epic_f_bec_cell_is_the_pre_widened_one() -> None:
    pre_widening = _load_migration(_BASE_INSERT_REV)._PRE_WIDENING
    assert pre_widening[("bec-fraud-financial", "asset_class")] == "data"
    by_slug = {e["slug"]: e for e in _entries(_BASE_SEED)}
    assert by_slug["bec-fraud-financial"]["asset_class"] == "cash_or_equivalent"


# ---------------------------------------------------------------------------
# (ii) an empty DB reaches c1d2e3f4a5b6 and holds the pre-widening value
# ---------------------------------------------------------------------------


def test_fresh_db_migrates_to_base_insert_and_holds_pre_widened_value(
    alembic_runner: MigrationContext, alembic_engine: Engine
) -> None:
    alembic_runner.migrate_up_to(_BASE_INSERT_REV)
    with alembic_engine.connect() as conn:
        row = conn.execute(
            sa.text(
                "SELECT asset_class, status FROM scenario_library_entries "
                "WHERE slug = :s AND version = 1"
            ),
            {"s": "bec-fraud-financial"},
        ).fetchone()
        n = conn.execute(sa.text("SELECT COUNT(*) FROM scenario_library_entries")).scalar_one()
    assert row is not None
    assert row[0] == "data", "fresh path must insert the pre-widening value at c1d2e3f4a5b6"
    assert row[1] == "published"
    assert n == len(_entries(_BASE_SEED))


# ---------------------------------------------------------------------------
# (iii) extension file vs the sets in force at 0897a0ff350e (no pre-widening)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("field", _FIELDS)
def test_extension_seed_entries_satisfy_check_sets_at_first_insert(field: str) -> None:
    allowed = _sets_at_0897()[field]
    violations = [
        f"{e['slug']}.{field} = {e[field]!r} is outside the CHECK set in force at "
        f"{_EXT_FIRST_INSERT_REV} ({sorted(allowed)}); a fresh `alembic upgrade head` would "
        f"fail at the extension INSERT — if this value was added by a widening AFTER "
        f"{_EXT_FIRST_INSERT_REV}, split this test per inserting revision (module docstring)"
        for e in _entries(_EXT_SEED)
        if e[field] not in allowed
    ]
    assert not violations, "\n".join(violations)


def test_check_set_constants_are_the_expected_widenings() -> None:
    """Sanity on the imported constants: the extension-era sets are strict
    supersets of the b8e0334b7f43 sets, differing by exactly the documented
    widenings, so the guard above is checking the right thing."""
    old = _b8e0_sets()
    new = _sets_at_0897()
    assert new["threat_event_type"] - old["threat_event_type"] == {"ot_integrity"}
    assert new["asset_class"] - old["asset_class"] == {
        "cash_or_equivalent",
        "business_process_revenue",
        "business_process_third_party_revenue",
        "business_process_cost",
    }
    assert new["threat_actor_type"] == old["threat_actor_type"]
    for field in _FIELDS:
        assert old[field] <= new[field]
