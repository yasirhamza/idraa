"""Milestone B: the loss content UPDATE overwrites DIRTY (injected lognormal)
PL/SL with the converted PERT seed values for capped entries, and leaves
catastrophic entries lognormal. Dirty-then-run so the UPDATE's effect is
exercised (the insert migrations already read the converted JSON, so a fresh
DB is clean before this migration).

**Pre-#529-epic snapshot pin (do not grep-swap 93/10).** Same mechanism as
``test_loss_shape_migration.py``: ``0897a0ff350e`` (an ancestor of
``d9e5a3c7f2b4`` in the chain) reads the extension JSON LIVE at
migration-run time, so once the attack-coverage gap-fill epic (#529)
appended 9 entries to that JSON, a fresh ``migrate_up_to(_HEAD)`` inserts
them too -- before the loss_shape correction migration (out of scope at
this revision) runs. W1 (``destructive-wiper-nationstate``) in particular
would land here with ``loss_shape='capped'`` (wrong, not yet corrected) but
``primary_loss.distribution='lognormal'`` (already correct -- this
migration replays PL/SL straight from the JSON, which stores W1's node as
lognormal regardless of the DB's loss_shape column), an internal
inconsistency ``test_loss_migration_all_entries_by_shape``'s per-shape loop
would trip on. Deleting the 9 #529 slugs immediately after migrating
restores the exact pre-#529 93-entry snapshot this test was pinned
against, so the ``93``/``n_cat==10`` literals stay unchanged."""

from __future__ import annotations

import json

import sqlalchemy as sa
from pytest_alembic import MigrationContext
from sqlalchemy.engine import Engine

_DOWN = "b8c4f2e6a1d3"
_HEAD = "d9e5a3c7f2b4"

# The 9 new attack-coverage gap-fill entries (#529) -- see module docstring.
_ATTACK_COVERAGE_SLUGS = frozenset(
    {
        "edge-ransomware-perimeter-gateway",
        "edge-espionage-nationstate",
        "edge-device-orb-foothold",
        "transient-cyber-asset-ot-intrusion",
        "browser-zeroday-driveby",
        "email-client-zeroclick-espionage",
        "removable-media-airgap-ot",
        "ot-wireless-field-network-compromise",
        "destructive-wiper-nationstate",
    }
)


def _loss(engine: Engine, slug: str) -> dict:
    with engine.connect() as conn:
        r = conn.execute(
            sa.text(
                "SELECT primary_loss, secondary_loss, loss_shape "
                "FROM scenario_library_entries WHERE slug = :s AND version = 1"
            ),
            {"s": slug},
        ).fetchone()
    j = lambda v: json.loads(v) if isinstance(v, str) else v  # noqa: E731
    return {"pl": j(r[0]), "sl": j(r[1]), "shape": r[2]}


def test_loss_migration_lands_converted_pert(
    alembic_runner: MigrationContext, alembic_engine: Engine
) -> None:
    alembic_runner.migrate_up_to(_DOWN)
    dirty = json.dumps({"distribution": "lognormal", "mean": 1.0, "sigma": 1.0})
    with alembic_engine.begin() as conn:
        conn.execute(
            sa.text(
                "UPDATE scenario_library_entries SET primary_loss = :v "
                "WHERE slug = 'ransomware-on-ehr' AND version = 1"
            ),
            {"v": dirty},
        )
    alembic_runner.migrate_up_one()
    row = _loss(alembic_engine, "ransomware-on-ehr")
    assert row["pl"]["distribution"] == "PERT"
    assert (row["pl"]["low"], row["pl"]["high"]) == (15955.6628554057, 10080000.000343738)
    assert row["pl"]["mode"] == row["pl"]["low"]


def test_loss_migration_all_entries_by_shape(
    alembic_runner: MigrationContext, alembic_engine: Engine
) -> None:
    alembic_runner.migrate_up_to(_HEAD)
    with alembic_engine.begin() as conn:
        conn.execute(
            sa.text(
                "DELETE FROM scenario_library_entries WHERE slug IN :slugs AND version = 1"
            ).bindparams(sa.bindparam("slugs", tuple(_ATTACK_COVERAGE_SLUGS), expanding=True))
        )
    with alembic_engine.connect() as conn:
        rows = conn.execute(
            sa.text(
                "SELECT slug, primary_loss, secondary_loss, loss_shape "
                "FROM scenario_library_entries WHERE version = 1"
            )
        ).fetchall()
    assert len(rows) == 93
    j = lambda v: json.loads(v) if isinstance(v, str) else v  # noqa: E731
    n_cat = 0
    for slug, pl_raw, sl_raw, shape in rows:
        nodes = [j(pl_raw)] + ([j(sl_raw)] if sl_raw else [])
        if shape == "catastrophic":
            n_cat += 1
            for n in nodes:
                assert n["distribution"] == "lognormal", (slug, n)
        else:
            for n in nodes:
                assert n["distribution"] == "PERT", (slug, n)
                assert n["low"] == n["mode"] < n["high"], (slug, n)
    assert n_cat == 10
