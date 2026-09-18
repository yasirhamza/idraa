# tests/migrations/test_secondary_response_stakeholder_test.py
"""Issue #175 migration (b5e2c7a9d413): the regulator-and-judgment reclassification
lands the seed JSON's loss nodes + loss_form_profile for the 29 touched slugs and
leaves every other row byte-identical. Follows tests/migrations/test_recalibrate_d_iii_a.py.
NO population count or scenario name from any deployment appears anywhere in this
file -- every fixture value here is synthetic.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import sqlalchemy as sa
from pytest_alembic import MigrationContext
from sqlalchemy.engine import Engine

import idraa
from idraa.services.seed_library_loader import LibraryEntrySeed

_HEAD = "b5e2c7a9d413"
_PREV = "ffed7c509563"
_SENTINEL_NODE = {"distribution": "PERT", "low": 1.0, "mode": 1.0, "high": 2.0}
_SENTINEL_PROFILE = [
    {
        "form": "productivity",
        "kind": "primary",
        "magnitude_basis": "sentinel",
        "citations": [],
        "verified": False,
        "composition_role": "dominant",
        "share": 0.5,
    }
]


def _seed_entries() -> list[dict]:
    root = Path(idraa.__file__).resolve().parent.parent.parent
    return json.loads((root / "data" / "seed_library_entries.json").read_text()) + json.loads(
        (root / "data" / "seed_library_entries_extension.json").read_text()
    )


def _seed_by_slug(slug: str) -> dict:
    return next(e for e in _seed_entries() if e["slug"] == slug)


def _row(engine: Engine, slug: str) -> dict:
    with engine.connect() as conn:
        r = conn.execute(
            sa.text(
                "SELECT primary_loss, secondary_loss, loss_form_profile, row_version "
                "FROM scenario_library_entries WHERE slug = :s AND version = 1"
            ),
            {"s": slug},
        ).fetchone()
    j = lambda v: json.loads(v) if isinstance(v, str) else v  # noqa: E731
    return {
        "primary_loss": j(r[0]),
        "secondary_loss": j(r[1]),
        "loss_form_profile": j(r[2]),
        "row_version": r[3],
    }


def _poison(engine: Engine, slug: str) -> None:
    """Overwrite every column the migration SETs so a from-scratch replay (which already
    lands the current JSON) cannot mask a missing SET or a missing slug."""
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "UPDATE scenario_library_entries SET primary_loss = :pl, secondary_loss = :sl, "
                "loss_form_profile = :lfp WHERE slug = :s AND version = 1"
            ),
            {
                "pl": json.dumps(_SENTINEL_NODE),
                "sl": json.dumps(_SENTINEL_NODE),
                "lfp": json.dumps(_SENTINEL_PROFILE),
                "s": slug,
            },
        )


def _migration_module():
    root = Path(idraa.__file__).resolve().parent.parent.parent
    path = root / "alembic" / "versions" / f"{_HEAD}_secondary_response_stakeholder_test.py"
    spec = importlib.util.spec_from_file_location("mig_b5e2c7a9d413", path)
    assert spec is not None and spec.loader is not None
    mig = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mig)
    return mig


def test_touched_rows_converge_to_seed_and_untouched_rows_are_byte_identical(
    alembic_runner: MigrationContext, alembic_engine: Engine
) -> None:
    alembic_runner.migrate_up_to(_PREV)
    touched = ("ransomware-on-ehr", "safety-system-bypass", "insider-ip-theft-manufacturing")
    for slug in touched:
        _poison(alembic_engine, slug)
        assert _row(alembic_engine, slug)["primary_loss"] == _SENTINEL_NODE
    touched_row_version_before = {s: _row(alembic_engine, s)["row_version"] for s in touched}
    untouched_before = {
        s: _row(alembic_engine, s)
        for s in ("ot-network-scanning-reconnaissance", "edge-espionage-nationstate")
    }

    alembic_runner.migrate_up_to(_HEAD)

    for slug in touched:
        landed, seed = _row(alembic_engine, slug), _seed_by_slug(slug)
        assert landed["primary_loss"] == seed["primary_loss"], slug
        assert landed["secondary_loss"] == seed["secondary_loss"], slug
        assert landed["loss_form_profile"] == seed["loss_form_profile"], slug
        assert landed["row_version"] == touched_row_version_before[slug], (
            f"{slug}: row_version must not be bumped"
        )

    ehr = _row(alembic_engine, "ransomware-on-ehr")
    assert ehr["primary_loss"]["distribution"] == "PERT"
    assert any(
        p["form"] == "response" and p["kind"] == "secondary" for p in ehr["loss_form_profile"]
    )
    ssb = _row(alembic_engine, "safety-system-bypass")
    assert ssb["primary_loss"] == {"distribution": "lognormal", "mean": 13.5923670067, "sigma": 1.7}
    ca = next(
        p
        for p in _row(alembic_engine, "insider-ip-theft-manufacturing")["loss_form_profile"]
        if p["form"] == "competitive_advantage"
    )
    assert "stakeholder test: primary" in ca["magnitude_basis"]

    for slug, before in untouched_before.items():
        assert _row(alembic_engine, slug) == before, slug


def test_touched_slugs_are_29_valid_seed_entries() -> None:
    mig = _migration_module()
    assert len(mig.TOUCHED_SLUGS) == 29
    assert mig.down_revision == _PREV
    # Pin the literal slug set to the reclassification script's own sets (24 renumbered + 5 justified)
    root = Path(idraa.__file__).resolve().parent.parent.parent
    rspec = importlib.util.spec_from_file_location(
        "build_secondary_response_reclass", root / "scripts" / "build_secondary_response_reclass.py"
    )
    assert rspec is not None and rspec.loader is not None
    reclass = importlib.util.module_from_spec(rspec)
    rspec.loader.exec_module(reclass)
    assert set(mig.TOUCHED_SLUGS) == set(reclass.RENUMBERED_SLUGS) | set(reclass.JUSTIFIED_SLUGS)
    by_slug = {e["slug"]: e for e in _seed_entries()}
    for slug in mig.TOUCHED_SLUGS:
        LibraryEntrySeed.model_validate(by_slug[slug])  # DTO re-validation of every rewritten row
