"""Milestone B (#loss-pert-overhaul): the loss_shape schema migration adds the
column with server_default 'capped' and flips exactly the 10 owner-approved
catastrophic shortlist slugs to 'catastrophic'.

**Pre-#529-epic snapshot pin (do not grep-swap 83).** ``_CATASTROPHIC_SLUGS``
and the ``83`` capped count pin the state of the world as it existed right
before the attack-coverage gap-fill epic (#529) authored 9 more library
entries. ``0897a0ff350e`` (the additive extension-seed migration, an
ancestor of ``b8c4f2e6a1d3`` in the chain) reads the extension JSON file
LIVE at migration-run time rather than a frozen historical snapshot, so once
#529 appended its 9 entries to that JSON, a fresh ``migrate_up_to(_HEAD)``
started inserting them too -- 9 entries early, before ``loss_shape`` even
existed as a differentiated concept for them (all default to 'capped',
including W1 which is semantically catastrophic but not yet corrected --
that correction is a LATER migration, out of scope at this revision).
Deleting the 9 #529 slugs immediately after migrating restores the exact
pre-#529 93-entry state this test was written against, so the pinned
83/10 values stay literally unchanged."""

from __future__ import annotations

import sqlalchemy as sa
from pytest_alembic import MigrationContext
from sqlalchemy.engine import Engine

_HEAD = "b8c4f2e6a1d3"

_CATASTROPHIC_SLUGS = frozenset(
    {
        "chemical-process-safety-attack",
        "safety-system-bypass",
        "unauthorized-plc-modification",
        "field-instrument-spoofing",
        "grid-protective-relay-manipulation",
        "denial-of-control",
        "pipeline-scada-integrity",
        "nation-state-ics-supply-chain",
        "solarwinds-class-supply-chain",
        "telecom-lawful-intercept-nationstate-compromise",
    }
)

# The 9 new attack-coverage gap-fill entries (#529). 0897a0ff350e (an
# ancestor migration) reads the live extension JSON and inserts these
# incidentally on a fresh DB, well before this revision -- see module
# docstring. Deleted post-migration so this test observes the pre-#529
# snapshot it was originally pinned against.
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


def test_loss_shape_column_added_and_shortlist_flipped(
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
            sa.text("SELECT slug, loss_shape FROM scenario_library_entries WHERE version = 1")
        ).fetchall()
    by_shape: dict[str, set[str]] = {"capped": set(), "catastrophic": set()}
    for slug, shape in rows:
        by_shape[shape].add(slug)
    assert by_shape["catastrophic"] == set(_CATASTROPHIC_SLUGS)
    assert len(by_shape["capped"]) == 83
