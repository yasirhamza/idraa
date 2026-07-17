"""#detemplating: the within-sector de-templating content UPDATE overwrites
DIRTY (injected shared) threat_event_frequency / vulnerability / secondary_loss
with the differentiated seed JSON values. Dirty-then-run so the UPDATE's effect
is actually exercised (the seed migrations already read the edited JSON, so a
fresh DB is clean before this migration -- mirrors
tests/migrations/test_dataquality_followups.py)."""

from __future__ import annotations

import json
import math

import sqlalchemy as sa
from pytest_alembic import MigrationContext
from sqlalchemy.engine import Engine

_DOWN = "c8e2f1a4b6d3"
_HEAD = "d4918202a23a"

# technology_saas sector envelope mean (data/loss_form_envelopes.json).
_MU_S_TECHNOLOGY_SAAS = 13.4842248480


def _row(engine: Engine, slug: str) -> dict:
    with engine.connect() as conn:
        r = conn.execute(
            sa.text(
                "SELECT threat_event_frequency, vulnerability, secondary_loss "
                "FROM scenario_library_entries WHERE slug = :s AND version = 1"
            ),
            {"s": slug},
        ).fetchone()
    j = lambda v: json.loads(v) if isinstance(v, str) else v  # noqa: E731
    return {"tef": j(r[0]), "vuln": j(r[1]), "sl": j(r[2])}


def test_within_sector_detemplating_migration_lands_differentiated_values(
    alembic_runner: MigrationContext, alembic_engine: Engine
) -> None:
    alembic_runner.migrate_up_to(_DOWN)
    # Inject a shared DIRTY value onto a few touched slugs so the UPDATE has
    # something to overwrite.
    dirty_pert = json.dumps({"distribution": "PERT", "low": 0.9, "mode": 0.95, "high": 0.99})
    dirty_sl = json.dumps({"distribution": "lognormal", "mean": 1.0, "sigma": 1.0})
    with alembic_engine.begin() as conn:
        for slug in ("web-app-exploitation", "ddos-financial-seasonal-peak"):
            conn.execute(
                sa.text(
                    "UPDATE scenario_library_entries SET threat_event_frequency = :v "
                    "WHERE slug = :s AND version = 1"
                ),
                {"v": dirty_pert, "s": slug},
            )
        for slug in ("insider-ip-theft-manufacturing", "ip-theft-by-competitor"):
            conn.execute(
                sa.text(
                    "UPDATE scenario_library_entries SET vulnerability = :v "
                    "WHERE slug = :s AND version = 1"
                ),
                {"v": dirty_pert, "s": slug},
            )
        for slug in ("solarwinds-class-supply-chain", "cloud-account-takeover"):
            conn.execute(
                sa.text(
                    "UPDATE scenario_library_entries SET secondary_loss = :v "
                    "WHERE slug = :s AND version = 1"
                ),
                {"v": dirty_sl, "s": slug},
            )
    # Run this migration.
    alembic_runner.migrate_up_to(_HEAD)

    web_app = _row(alembic_engine, "web-app-exploitation")
    # TEF is bounded PERT again (#tef-pert-revert): the #518 migration reads the
    # seed (now PERT) and overwrites the injected dirty value. Its specific value
    # is owned by test_tef_representation + the revert migration test.
    assert web_app["tef"]["distribution"] == "PERT"

    insider_ip = _row(alembic_engine, "insider-ip-theft-manufacturing")
    assert insider_ip["vuln"]["mode"] == 0.25
    assert insider_ip["vuln"]["low"] == 0.08
    assert insider_ip["vuln"]["high"] == 0.5

    solarwinds = _row(alembic_engine, "solarwinds-class-supply-chain")
    expected_mean = round(_MU_S_TECHNOLOGY_SAAS + math.log(0.20), 10)
    assert solarwinds["sl"]["mean"] == expected_mean
    assert solarwinds["sl"]["sigma"] == 3.4721527617
