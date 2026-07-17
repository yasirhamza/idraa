"""Epic D-iii-a migration test: the envelope×share recalibration (d3f1a7c9e5b2)
lands the seed JSON's recalibrated loss nodes + loss_form_profile in the DB."""

from __future__ import annotations

import json
import math
from pathlib import Path

import sqlalchemy as sa
from pytest_alembic import MigrationContext
from sqlalchemy.engine import Engine

import idraa

_HEAD = "d3f1a7c9e5b2"


def _seed_by_slug(slug: str) -> dict:
    root = Path(idraa.__file__).resolve().parent.parent.parent
    entries = json.loads((root / "data" / "seed_library_entries.json").read_text()) + json.loads(
        (root / "data" / "seed_library_entries_extension.json").read_text()
    )
    return next(e for e in entries if e["slug"] == slug)


def _row(engine: Engine, slug: str) -> dict:
    with engine.connect() as conn:
        r = conn.execute(
            sa.text(
                "SELECT primary_loss, secondary_loss, loss_form_profile, loss_tier "
                "FROM scenario_library_entries WHERE slug = :s AND version = 1"
            ),
            {"s": slug},
        ).fetchone()
    j = lambda v: json.loads(v) if isinstance(v, str) else v  # noqa: E731
    return {
        "primary_loss": j(r[0]),
        "secondary_loss": j(r[1]),
        "loss_form_profile": j(r[2]),
        "loss_tier": r[3],
    }


def test_recalibration_lands_envelope_share_values(
    alembic_runner: MigrationContext, alembic_engine: Engine
) -> None:
    alembic_runner.migrate_up_to(_HEAD)

    # envelope entry: recon has a tiny share -> tiny loss (flattening fixed).
    # Milestone B (#loss-pert-overhaul): both sample slugs are capped -> the
    # migration replays the CONVERTED seed, so the landed nodes are PERT.
    recon = _row(alembic_engine, "ot-network-scanning-reconnaissance")
    seed_recon = _seed_by_slug("ot-network-scanning-reconnaissance")
    assert recon["primary_loss"] == seed_recon["primary_loss"]
    assert recon["primary_loss"]["distribution"] == "PERT"
    assert recon["loss_tier"] == "paginated"
    assert recon["loss_form_profile"], "loss_form_profile must be populated"

    # ...and ransomware in the same sector has a much larger primary loss.
    # The PERT (low, high) are exp(mu -/+ Z*sigma), so (ln low + ln high)/2
    # recovers mu EXACTLY -- the original mu ordering is preserved verbatim.
    rans = _row(alembic_engine, "ransomware-on-historian")

    def _log_mid(node: dict) -> float:
        return (math.log(node["low"]) + math.log(node["high"])) / 2

    assert _log_mid(rans["primary_loss"]) > _log_mid(recon["primary_loss"]) + 2.0, (
        "flattening not fixed: ransomware PL should dwarf recon PL"
    )

    # beyond-envelope BEC: own IC3-derived node, vendor tier (capped -> PERT)
    bec = _row(alembic_engine, "bec-fraud-financial")
    seed_bec = _seed_by_slug("bec-fraud-financial")
    assert bec["primary_loss"] == seed_bec["primary_loss"]
    assert bec["primary_loss"]["distribution"] == "PERT"
    assert bec["loss_tier"] == "vendor"

    # an entry with no secondary forms stores a null secondary_loss
    assert recon["secondary_loss"] is None
