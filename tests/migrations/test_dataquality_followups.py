"""Epic D data-quality migration (#510 + #505): the content UPDATE overwrites the
forbidden-cite narrative + templated TEF with the cleaned/differentiated seed JSON.
Dirty-then-run so the UPDATE's effect is actually exercised (the seed migrations
already read the edited JSON, so the fresh DB is clean before this migration)."""

from __future__ import annotations

import json

import sqlalchemy as sa
from pytest_alembic import MigrationContext
from sqlalchemy.engine import Engine

_DOWN = "4616e1b032fe"
_HEAD = "c8e2f1a4b6d3"
_FORBIDDEN = ("I-091019-PSA", "15-1433", "AA22-186A", "PREPA")
_NARR = (
    "agri-coop-bec-fraud",
    "crop-science-ip-exfiltration",
    "education-research-ip-exfiltration",
    "energy-billing-system-tamper",
)


def _row(engine: Engine, slug: str) -> dict:
    with engine.connect() as conn:
        r = conn.execute(
            sa.text(
                "SELECT example_incidents, threat_event_frequency "
                "FROM scenario_library_entries WHERE slug = :s AND version = 1"
            ),
            {"s": slug},
        ).fetchone()
    j = lambda v: json.loads(v) if isinstance(v, str) else v  # noqa: E731
    return {"ex": r[0], "tef": j(r[1])}


def test_dataquality_migration_cleans_and_differentiates(
    alembic_runner: MigrationContext, alembic_engine: Engine
) -> None:
    alembic_runner.migrate_up_to(_DOWN)
    # Inject DIRTY values so the UPDATE has something to overwrite.
    with alembic_engine.begin() as conn:
        for slug in _NARR:
            conn.execute(
                sa.text(
                    "UPDATE scenario_library_entries SET example_incidents = :v "
                    "WHERE slug = :s AND version = 1"
                ),
                {"v": "DIRTY FBI PSA I-091019-PSA DOJ 15-1433 CISA AA22-186A PREPA", "s": slug},
            )
        for slug in ("chemical-process-safety-attack", "grid-protective-relay-manipulation"):
            conn.execute(
                sa.text(
                    "UPDATE scenario_library_entries SET threat_event_frequency = :v "
                    "WHERE slug = :s AND version = 1"
                ),
                {
                    "v": json.dumps(
                        {"distribution": "PERT", "low": 0.9, "mode": 0.95, "high": 0.99}
                    ),
                    "s": slug,
                },
            )
    # Run this migration.
    alembic_runner.migrate_up_to(_HEAD)
    # #510: the UPDATE cleaned the injected forbidden tokens.
    for slug in _NARR:
        ex = _row(alembic_engine, slug)["ex"]
        for tok in _FORBIDDEN:
            assert tok not in ex, f"{slug} still cites {tok} after migration"
    # #505: the UPDATE overwrote the injected TEF with the seed values. TEF is
    # bounded PERT again (#tef-pert-revert), so the overwrite lands a PERT node
    # (its specific value is owned by test_tef_representation + the revert migration).
    assert _row(alembic_engine, "chemical-process-safety-attack")["tef"]["distribution"] == "PERT"
    assert (
        _row(alembic_engine, "grid-protective-relay-manipulation")["tef"]["distribution"] == "PERT"
    )
