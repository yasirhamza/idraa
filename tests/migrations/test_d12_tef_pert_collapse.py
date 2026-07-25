"""D12 migration: collapse legacy lognormal TEF to PERT (b3f8a2d94c1e).

Mirrors the sigma-recalibration migration-test harness: command.upgrade with
the PRAGMA-driven placeholder-fill seeder, direct module import for the
atomicity seam, prior-committed seed rows.
"""

from __future__ import annotations

import importlib.util
import json
import math
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine

from tests.migrations.test_sigma_recalibration_migration import _seed_scenario

_ROOT = Path(__file__).resolve().parents[2]
_REV = "b3f8a2d94c1e"
_PRE_REV = "c4e4d441087c"
_Z95 = 1.6448536269514722

# The real legacy shape: Cloud ATO's Epic-B-era TEF snapshot.
_LOGN_TEF = {
    "distribution": "lognormal",
    "mean": 0.6609612263841528,
    "sigma": 0.5768152408900864,
    "distribution_fit_metadata": {
        "source": "quantile_lognormal_pool",
        "fitter": "lognorm_native",
        "schema_version": 2,
        "n_smes": 1,
    },
}
_WIDE_PL = {"distribution": "lognormal", "mean": 12.0, "sigma": 1.7}


def _load_migration():
    matches = list((_ROOT / "alembic" / "versions").glob(f"{_REV}_*.py"))
    assert len(matches) == 1
    spec = importlib.util.spec_from_file_location("d12_mig", matches[0])
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _get_tef(conn: sa.Connection, sid: str) -> dict | None:
    raw = conn.execute(
        sa.text("SELECT threat_event_frequency FROM scenarios WHERE id = :id"), {"id": sid}
    ).scalar_one()
    return json.loads(raw) if isinstance(raw, str) else raw


def _audit_rows(conn: sa.Connection, sid: str) -> list:
    return conn.execute(
        sa.text(
            "SELECT changes FROM audit_log WHERE entity_id = :id "
            "AND action = 'scenario.collapse_tef_to_pert'"
        ),
        {"id": sid},
    ).fetchall()


def test_lognormal_tef_collapsed_quantiles_and_mode(
    alembic_config: Config, alembic_engine: Engine
) -> None:
    command.upgrade(alembic_config, _PRE_REV)
    with alembic_engine.begin() as conn:
        sid = _seed_scenario(conn, pl_node=_WIDE_PL, tef_node=_LOGN_TEF, row_version=1)
    command.upgrade(alembic_config, _REV)

    mu, s = _LOGN_TEF["mean"], _LOGN_TEF["sigma"]
    with alembic_engine.connect() as conn:
        tef = _get_tef(conn, sid)
        assert tef["distribution"] == "PERT"
        assert tef["low"] == pytest.approx(math.exp(mu - _Z95 * s))  # p5 preserved
        assert tef["high"] == pytest.approx(math.exp(mu + _Z95 * s))  # p95 preserved
        assert tef["mode"] == pytest.approx(math.exp(mu - s * s))  # interior true mode
        meta = tef["distribution_fit_metadata"]
        assert meta["tef_pert_collapse"]["source"] == "migration_tef_pert_collapse"
        assert meta["superseded_fit"]["fitter"] == "lognorm_native"
        assert "fitter" not in meta  # moved, not duplicated
        rows = _audit_rows(conn, sid)
        assert len(rows) == 1
        assert json.loads(rows[0][0])["prior"] == _LOGN_TEF  # full prior verbatim


def test_pert_tef_and_lognormal_loss_untouched(
    alembic_config: Config, alembic_engine: Engine
) -> None:
    """Loss columns keep lognormal LEGALLY; PERT TEF is out of scope by kind."""
    pert_tef = {"distribution": "PERT", "low": 0.2, "mode": 0.5, "high": 1.5}
    command.upgrade(alembic_config, _PRE_REV)
    with alembic_engine.begin() as conn:
        sid = _seed_scenario(conn, pl_node=_WIDE_PL, tef_node=pert_tef, row_version=1)
    command.upgrade(alembic_config, _REV)

    with alembic_engine.connect() as conn:
        assert _get_tef(conn, sid) == pert_tef
        pl = json.loads(
            conn.execute(
                sa.text("SELECT primary_loss FROM scenarios WHERE id = :id"), {"id": sid}
            ).scalar_one()
        )
        assert pl["distribution"] == "lognormal"  # loss lognormal survives D12
        assert _audit_rows(conn, sid) == []


def test_analyst_pin_and_degenerate_shapes_skipped(
    alembic_config: Config, alembic_engine: Engine
) -> None:
    pinned = dict(_LOGN_TEF)
    pinned["distribution_fit_metadata"] = {"sigma_recalibration": {"source": "analyst_pin"}}
    command.upgrade(alembic_config, _PRE_REV)
    with alembic_engine.begin() as conn:
        sid_pin = _seed_scenario(conn, pl_node=_WIDE_PL, tef_node=pinned, row_version=1)
        sid_null = _seed_scenario(conn, pl_node=_WIDE_PL, tef_raw="null", row_version=1)
    command.upgrade(alembic_config, _REV)

    with alembic_engine.connect() as conn:
        assert _get_tef(conn, sid_pin) == pinned
        assert _get_tef(conn, sid_null) is None
        assert _audit_rows(conn, sid_pin) == []


def test_idempotent_and_mixture_skipped(alembic_config: Config, alembic_engine: Engine) -> None:
    mixture_tef = {
        "distribution": "lognormal_mixture",
        "components": [{"mean": 0.5, "sigma": 0.4, "weight": 1.0}],
    }
    command.upgrade(alembic_config, _PRE_REV)
    with alembic_engine.begin() as conn:
        sid_mix = _seed_scenario(conn, pl_node=_WIDE_PL, tef_node=mixture_tef, row_version=1)
        sid_logn = _seed_scenario(conn, pl_node=_WIDE_PL, tef_node=_LOGN_TEF, row_version=1)
    command.upgrade(alembic_config, _REV)

    mod = _load_migration()
    with alembic_engine.begin() as conn:
        assert _get_tef(conn, sid_mix) == mixture_tef  # skipped with WARNING
        assert mod._upgrade_scenarios(conn) == 0  # second pass sweeps nothing
    with alembic_engine.connect() as conn:
        assert _get_tef(conn, sid_logn)["distribution"] == "PERT"


def test_atomicity_via_seam(alembic_config: Config, alembic_engine: Engine, monkeypatch) -> None:
    command.upgrade(alembic_config, _PRE_REV)
    with alembic_engine.begin() as conn:
        sid = _seed_scenario(conn, pl_node=_WIDE_PL, tef_node=_LOGN_TEF, row_version=1)
    command.upgrade(alembic_config, _PRE_REV)  # stay below _REV; run helper directly

    mod = _load_migration()

    def _raise(*a, **k):
        raise RuntimeError("simulated write failure")

    monkeypatch.setattr(mod, "_apply_scenario_update", _raise)
    with (
        alembic_engine.connect() as conn,
        pytest.raises(RuntimeError, match="simulated write failure"),
        conn.begin(),
    ):
        mod._upgrade_scenarios(conn)
    with alembic_engine.connect() as conn:
        assert _audit_rows(conn, sid) == []  # shared-fate rollback
        assert _get_tef(conn, sid) == _LOGN_TEF
