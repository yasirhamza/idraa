"""sigma-recalibration migration tests (c4e4d441087c).

Per docs/superpowers/plans/2026-07-25-sigma-recal-pr1.md Task 3:

- library entries: blind replay of the Task-2 re-authored seed JSONs onto
  ``scenario_library_entries`` WHERE slug AND version = 1;
- scenarios: NARROW-ONLY sweep — a lognormal/lognormal_mixture loss field
  with sigma > 1.7 is narrowed to 1.7 (mu/component means held); sigma <= 1.7,
  ``analyst_pin``-stamped fields, and non-lognormal (PERT, TEF) fields are
  left untouched; audit_log row INSERTed before the scenarios UPDATE, one row
  per changed field, full 8-column shape; the entire prior fit record moves
  under ``distribution_fit_metadata.superseded_fit``, no schema_version bump.

Uses the shared alembic_config/alembic_engine fixtures (sync engine queries,
async-driver alembic URL) per tests/migrations/conftest.py. The direct-module
import pattern (case 8, atomicity) mirrors
tests/migrations/test_insert_attack_coverage.py's spec_from_file_location use.
"""

from __future__ import annotations

import importlib.util
import json
import uuid
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import Engine

import idraa

_PRE_REV = "e4636e6fb4eb"  # single head before this migration
_REV = "c4e4d441087c"  # the sigma-recalibration migration under test

_SIGMA_TARGET = 1.7

# Independently-maintained copy of the migration's _FIT_RECORD_KEYS (defined
# INSIDE _recalibrate_dist, so not importable as a module attribute) — the
# full-partition assertion (case 1) needs to know every key that must move
# under superseded_fit.
_FIT_RECORD_KEYS = (
    "pooled_meanlog",
    "pooled_sdlog",
    "component_meanlogs",
    "component_sdlogs",
    "pooling_method",
    "pooled_min_support",
    "pooled_max_support",
    "q_low_quantile",
    "q_high_quantile",
    "n_smes",
    "sme_ids",
    "weights",
    "source",
    "fitter",
    "fitted_at",
    "schema_version",
)

_FULL_FIT_METADATA = {
    "pooled_meanlog": 14.5,
    "pooled_sdlog": 2.9269,
    "component_meanlogs": [14.5],
    "component_sdlogs": [2.9269],
    "pooling_method": "linear_opinion_pool_v1",
    "pooled_min_support": 0.0,
    "pooled_max_support": None,
    "q_low_quantile": 0.05,
    "q_high_quantile": 0.95,
    "n_smes": 1,
    "sme_ids": ["11111111-1111-1111-1111-111111111111"],
    "weights": [1.0],
    "source": "quantile_lognormal_pool",
    "fitter": "lognorm_native",
    "fitted_at": "2026-07-08T10:22:00+00:00",
    "schema_version": 3,
}


def _root() -> Path:
    return Path(idraa.__file__).resolve().parent.parent.parent


def _versions_dir() -> Path:
    return _root() / "alembic" / "versions"


def _seed_json_entries() -> list[dict]:
    out: list[dict] = []
    for n in ("seed_library_entries.json", "seed_library_entries_extension.json"):
        out.extend(json.loads((_root() / "data" / n).read_text(encoding="utf-8")))
    return out


def _import_migration_module() -> Any:
    mig_path = next(_versions_dir().glob(f"{_REV}_*.py"))
    spec = importlib.util.spec_from_file_location("_sigma_recal_mig", mig_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _seed_scenario(
    conn: sa.Connection,
    *,
    pl_node: dict | None = None,
    sl_node: dict | None = None,
    sl_raw: str | None = None,
    tef_node: dict | None = None,
    name: str = "probe",
    row_version: int = 1,
) -> str:
    """Insert a minimal schema-valid scenario row (FKs off) and return its id.

    PRAGMA-driven placeholder fill (precedent:
    tests/migrations/test_audit_f1_repair_divergent_fits.py:55-97) so the
    insert stays durable as later migrations add NOT NULL columns.

    ``sl_raw`` bypasses ``json.dumps`` entirely -- for seeding the exact
    literal-4-char-string "null" shape found in prod (case 12), which
    ``sl_node=None`` cannot reproduce (that path omits the column, leaving
    a real SQL NULL, not the JSON text "null").
    """
    explicit: dict[str, object] = {
        "id": uuid.uuid4().hex,
        "organization_id": uuid.uuid4().hex,
        "name": name,
        "scenario_type": "CUSTOM",
        "threat_category": "ransomware",
        "threat_event_frequency": (
            json.dumps(tef_node)
            if tef_node is not None
            else '{"distribution":"PERT","low":1,"mode":2,"high":3}'
        ),
        "vulnerability": '{"distribution":"PERT","low":0.1,"mode":0.2,"high":0.3}',
        "primary_loss": json.dumps(
            pl_node
            if pl_node is not None
            else {"distribution": "PERT", "low": 1000, "mode": 2000, "high": 3000}
        ),
        "overlay_pins": "[]",
        "source": "expert_judgment",
        "status": "ACTIVE",
        "version": "1.0",
        "row_version": row_version,
    }
    if sl_raw is not None:
        assert sl_node is None, "sl_node and sl_raw are mutually exclusive"
        explicit["secondary_loss"] = sl_raw
    elif sl_node is not None:
        explicit["secondary_loss"] = json.dumps(sl_node)
    cols = conn.execute(sa.text("PRAGMA table_info(scenarios)")).mappings().all()
    values: dict[str, object] = {}
    for col in cols:
        cname = col["name"]
        if cname in explicit:
            values[cname] = explicit[cname]
        elif col["notnull"] and col["dflt_value"] is None:
            values[cname] = "x"
    column_list = ", ".join(values)
    placeholders = ", ".join(f":{c}" for c in values)
    conn.execute(sa.text("PRAGMA foreign_keys = OFF"))
    conn.execute(
        sa.text(f"INSERT INTO scenarios ({column_list}) VALUES ({placeholders})"),  # noqa: S608
        values,
    )
    return str(explicit["id"])


def _get_scenario(conn: sa.Connection, sid: str) -> sa.RowMapping:
    return (
        conn.execute(
            sa.text(
                "SELECT primary_loss, secondary_loss, threat_event_frequency, row_version "
                "FROM scenarios WHERE id = :id"
            ),
            {"id": sid},
        )
        .mappings()
        .one()
    )


def _audit_rows(conn: sa.Connection, sid: str | None = None) -> list[sa.RowMapping]:
    q = "SELECT id, organization_id, entity_type, entity_id, user_id, action, changes, timestamp FROM audit_log WHERE action = 'scenario.recalibrate_loss_sigma'"
    if sid is not None:
        q += " AND entity_id = :sid"
        return list(conn.execute(sa.text(q), {"sid": sid}).mappings().all())
    return list(conn.execute(sa.text(q)).mappings().all())


# ---------------------------------------------------------------------------
# Case 1: wide field 2.9269 -> 1.7, mu held, stamp present, full partition.
# ---------------------------------------------------------------------------


def test_wide_lognormal_field_recalibrated_full_partition(
    alembic_config: Config, alembic_engine: Engine
) -> None:
    wide_pl = {
        "distribution": "lognormal",
        "mean": 14.8674357694,
        "sigma": 2.9269,
        "distribution_fit_metadata": dict(_FULL_FIT_METADATA),
    }
    command.upgrade(alembic_config, _PRE_REV)
    with alembic_engine.begin() as conn:
        sid = _seed_scenario(conn, pl_node=wide_pl, row_version=5)
    command.upgrade(alembic_config, _REV)

    with alembic_engine.connect() as conn:
        row = _get_scenario(conn, sid)
        node = json.loads(row["primary_loss"])
        assert node["distribution"] == "lognormal"
        assert node["sigma"] == pytest.approx(_SIGMA_TARGET)
        assert node["mean"] == pytest.approx(14.8674357694)  # mu held
        assert row["row_version"] == 6  # bumped once

        meta = node["distribution_fit_metadata"]
        # Full top-level partition: no _FIT_RECORD_KEYS member remains
        # top-level; all present ones sit under superseded_fit.
        for key in _FIT_RECORD_KEYS:
            assert key not in meta, f"{key} leaked top-level after sweep"
        superseded = meta["superseded_fit"]
        for key, val in _FULL_FIT_METADATA.items():
            assert superseded[key] == val

        stamp = meta["sigma_recalibration"]
        assert stamp["source"] == "migration_recalibration"
        assert stamp["prior_sigma"] == pytest.approx(2.9269)
        assert stamp["revision"] == _REV


# ---------------------------------------------------------------------------
# Case 2: narrow field untouched.
# ---------------------------------------------------------------------------


def test_narrow_lognormal_field_untouched(alembic_config: Config, alembic_engine: Engine) -> None:
    narrow_pl = {"distribution": "lognormal", "mean": 13.0, "sigma": 1.4254}
    command.upgrade(alembic_config, _PRE_REV)
    with alembic_engine.begin() as conn:
        sid = _seed_scenario(conn, pl_node=narrow_pl, row_version=1)
    command.upgrade(alembic_config, _REV)

    with alembic_engine.connect() as conn:
        row = _get_scenario(conn, sid)
        assert json.loads(row["primary_loss"]) == narrow_pl
        assert row["row_version"] == 1
        assert _audit_rows(conn, sid) == []


# ---------------------------------------------------------------------------
# Case 3: field exactly at the target untouched, no audit row.
# ---------------------------------------------------------------------------


def test_field_exactly_at_target_untouched(alembic_config: Config, alembic_engine: Engine) -> None:
    at_target_pl = {"distribution": "lognormal", "mean": 13.0, "sigma": 1.7}
    command.upgrade(alembic_config, _PRE_REV)
    with alembic_engine.begin() as conn:
        sid = _seed_scenario(conn, pl_node=at_target_pl, row_version=2)
    command.upgrade(alembic_config, _REV)

    with alembic_engine.connect() as conn:
        row = _get_scenario(conn, sid)
        assert json.loads(row["primary_loss"]) == at_target_pl
        assert row["row_version"] == 2
        assert _audit_rows(conn, sid) == []


# ---------------------------------------------------------------------------
# Case 4: analyst_pin-stamped field untouched even though wide.
# ---------------------------------------------------------------------------


def test_analyst_pin_stamped_field_untouched(
    alembic_config: Config, alembic_engine: Engine
) -> None:
    pinned_pl = {
        "distribution": "lognormal",
        "mean": 13.0,
        "sigma": 3.0,
        "distribution_fit_metadata": {"sigma_recalibration": {"source": "analyst_pin"}},
    }
    command.upgrade(alembic_config, _PRE_REV)
    with alembic_engine.begin() as conn:
        sid = _seed_scenario(conn, pl_node=pinned_pl, row_version=1)
    command.upgrade(alembic_config, _REV)

    with alembic_engine.connect() as conn:
        row = _get_scenario(conn, sid)
        assert json.loads(row["primary_loss"]) == pinned_pl
        assert row["row_version"] == 1
        assert _audit_rows(conn, sid) == []


# ---------------------------------------------------------------------------
# Case 5: PERT loss field + TEF field never touched.
# ---------------------------------------------------------------------------


def test_pert_and_tef_fields_untouched(alembic_config: Config, alembic_engine: Engine) -> None:
    pert_pl = {"distribution": "PERT", "low": 100000.0, "mode": 200000.0, "high": 500000.0}
    # A wide-looking lognormal-shaped TEF -- must never be inspected at all,
    # since the sweep only queries primary_loss/secondary_loss columns.
    wide_tef = {"distribution": "lognormal", "mean": 0.5, "sigma": 9.9}
    command.upgrade(alembic_config, _PRE_REV)
    with alembic_engine.begin() as conn:
        sid = _seed_scenario(conn, pl_node=pert_pl, tef_node=wide_tef, row_version=1)
    command.upgrade(alembic_config, _REV)

    with alembic_engine.connect() as conn:
        row = _get_scenario(conn, sid)
        assert json.loads(row["primary_loss"]) == pert_pl
        assert json.loads(row["threat_event_frequency"]) == wide_tef
        assert row["row_version"] == 1
        assert _audit_rows(conn, sid) == []


# ---------------------------------------------------------------------------
# Case 12: secondary_loss stored as the literal JSON text "null" (10 real
# prod rows have this shape) -- a legitimate no-secondary-loss representation,
# silently skipped like SQL NULL, while the PL on the same row still sweeps.
# ---------------------------------------------------------------------------


def test_json_null_text_secondary_loss_silently_skipped(
    alembic_config: Config, alembic_engine: Engine
) -> None:
    wide_pl = {"distribution": "lognormal", "mean": 14.8674357694, "sigma": 2.9269}
    command.upgrade(alembic_config, _PRE_REV)
    with alembic_engine.begin() as conn:
        sid = _seed_scenario(conn, pl_node=wide_pl, sl_raw="null", row_version=1)
    command.upgrade(alembic_config, _REV)  # must complete, not crash

    with alembic_engine.connect() as conn:
        row = _get_scenario(conn, sid)
        # PL swept normally.
        pl_node = json.loads(row["primary_loss"])
        assert pl_node["sigma"] == pytest.approx(_SIGMA_TARGET)
        # SL untouched -- still the literal 4-char text "null".
        assert row["secondary_loss"] == "null"
        # Exactly one audit row (the PL's) -- no WARNING-driven row for SL,
        # no crash.
        rows = _audit_rows(conn, sid)
        assert len(rows) == 1
        assert json.loads(rows[0]["changes"])["field"] == "primary_loss"
        assert row["row_version"] == 2  # bumped once, for the PL only


# ---------------------------------------------------------------------------
# Case 6: audit row full 8-column shape.
# ---------------------------------------------------------------------------


def test_audit_row_full_shape(alembic_config: Config, alembic_engine: Engine) -> None:
    wide_pl = {"distribution": "lognormal", "mean": 14.8674357694, "sigma": 2.9269}
    command.upgrade(alembic_config, _PRE_REV)
    with alembic_engine.begin() as conn:
        sid = _seed_scenario(conn, pl_node=wide_pl, row_version=1)
    command.upgrade(alembic_config, _REV)

    with alembic_engine.connect() as conn:
        rows = _audit_rows(conn, sid)
        assert len(rows) == 1
        audit = rows[0]
        assert audit["entity_type"] == "scenario"
        assert audit["entity_id"] == sid
        assert audit["user_id"] is None
        assert audit["action"] == "scenario.recalibrate_loss_sigma"
        assert audit["organization_id"] is not None
        assert audit["timestamp"] is not None
        assert "-" not in audit["id"]  # no-hyphen hex id
        changes = json.loads(audit["changes"])
        assert changes["field"] == "primary_loss"
        assert changes["prior"] == wide_pl  # full prior dict present
        assert changes["sigma"] == [pytest.approx(2.9269), _SIGMA_TARGET]


# ---------------------------------------------------------------------------
# Case 7: lognormal_mixture -- each component recalibrated, means held.
# ---------------------------------------------------------------------------


def test_mixture_partial_sweep_and_pin_on_mixture(
    alembic_config: Config, alembic_engine: Engine
) -> None:
    """T3-review NTH-1: the mixture branch sweeps ONLY components above the
    target (partial sweep), and an analyst_pin stamp wins for mixtures too."""
    partial = {
        "distribution": "lognormal_mixture",
        "components": [
            {"mean": 10.0, "sigma": 2.4, "weight": 0.7},
            {"mean": 11.0, "sigma": 1.5, "weight": 0.3},
        ],
    }
    pinned_mixture = {
        "distribution": "lognormal_mixture",
        "components": [{"mean": 12.0, "sigma": 3.0, "weight": 1.0}],
        "distribution_fit_metadata": {"sigma_recalibration": {"source": "analyst_pin"}},
    }
    command.upgrade(alembic_config, _PRE_REV)
    with alembic_engine.begin() as conn:
        sid_partial = _seed_scenario(conn, pl_node=partial, row_version=1)
        sid_pinned = _seed_scenario(conn, pl_node=pinned_mixture, row_version=1)
    command.upgrade(alembic_config, _REV)

    with alembic_engine.connect() as conn:
        comps = json.loads(_get_scenario(conn, sid_partial)["primary_loss"])["components"]
        assert comps[0]["sigma"] == pytest.approx(_SIGMA_TARGET)  # 2.4 swept
        assert comps[1]["sigma"] == pytest.approx(1.5)  # below target: untouched
        assert comps[0]["mean"] == pytest.approx(10.0)
        assert comps[1]["mean"] == pytest.approx(11.0)
        pinned_after = json.loads(_get_scenario(conn, sid_pinned)["primary_loss"])
        assert pinned_after == pinned_mixture  # analyst_pin wins for mixtures too
        assert _audit_rows(conn, sid_pinned) == []


def test_audit_prior_preserves_full_metadata_verbatim(
    alembic_config: Config, alembic_engine: Engine
) -> None:
    """T3-review NTH-2: one test that seeds the full realistic fit-record
    metadata AND asserts the audit row's changes.prior carries it verbatim."""
    full_meta = {
        "source": "quantile_lognormal_pool",
        "fitter": "lognorm_native",
        "pooled_meanlog": 12.0421,
        "pooled_sdlog": 2.9269,
        "schema_version": 2,
        "q_low_quantile": 0.05,
        "q_high_quantile": 0.95,
        "pooled_min_support": 0.0,
        "pooled_max_support": None,
        "n_smes": 1,
        "sme_ids": ["20505f1a-1fd1-4b9a-b7da-b5c666f600a9"],
        "weights": [1.0],
        "fitted_at": "2026-07-08T10:37:13.387531+00:00",
    }
    wide = {
        "distribution": "lognormal",
        "mean": 12.0421,
        "sigma": 2.9269,
        "distribution_fit_metadata": dict(full_meta),
    }
    command.upgrade(alembic_config, _PRE_REV)
    with alembic_engine.begin() as conn:
        sid = _seed_scenario(conn, pl_node=wide, row_version=1)
    command.upgrade(alembic_config, _REV)

    with alembic_engine.connect() as conn:
        rows = _audit_rows(conn, sid)
        assert len(rows) == 1
        prior = json.loads(rows[0]["changes"])["prior"]
        assert prior == wide  # byte-fidelity incl. the full metadata dict
        node = json.loads(_get_scenario(conn, sid)["primary_loss"])
        meta = node["distribution_fit_metadata"]
        assert set(meta.keys()) == {"sigma_recalibration", "superseded_fit"}
        assert meta["superseded_fit"] == full_meta  # original values verbatim


def test_mixture_components_each_recalibrated(
    alembic_config: Config, alembic_engine: Engine
) -> None:
    mixture_pl = {
        "distribution": "lognormal_mixture",
        "components": [
            {"mean": 10.0, "sigma": 3.0, "weight": 0.6},
            {"mean": 11.0, "sigma": 2.5, "weight": 0.4},
        ],
    }
    command.upgrade(alembic_config, _PRE_REV)
    with alembic_engine.begin() as conn:
        sid = _seed_scenario(conn, pl_node=mixture_pl, row_version=1)
    command.upgrade(alembic_config, _REV)

    with alembic_engine.connect() as conn:
        row = _get_scenario(conn, sid)
        node = json.loads(row["primary_loss"])
        assert node["distribution"] == "lognormal_mixture"
        comps = node["components"]
        assert len(comps) == 2
        assert comps[0]["sigma"] == pytest.approx(_SIGMA_TARGET)
        assert comps[0]["mean"] == pytest.approx(10.0)  # mean held
        assert comps[1]["sigma"] == pytest.approx(_SIGMA_TARGET)
        assert comps[1]["mean"] == pytest.approx(11.0)  # mean held
        stamp = node["distribution_fit_metadata"]["sigma_recalibration"]
        assert stamp["prior_sigma"] == pytest.approx(3.0)  # max of component sigmas
        assert row["row_version"] == 2


# ---------------------------------------------------------------------------
# Case 8: atomicity via the seam -- shared-fate rollback, not write order.
# ---------------------------------------------------------------------------


def test_atomicity_via_seam_rollback(
    alembic_config: Config, alembic_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    wide_pl = {"distribution": "lognormal", "mean": 14.8674357694, "sigma": 2.9269}
    command.upgrade(alembic_config, _PRE_REV)
    # Seed the row and COMMIT in a separate prior transaction -- else the
    # rollback that proves atomicity also erases the row and "unchanged"
    # is vacuous.
    with alembic_engine.begin() as conn:
        sid = _seed_scenario(conn, pl_node=wide_pl, row_version=1)

    mod = _import_migration_module()

    def _raise(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("simulated write failure")

    monkeypatch.setattr(mod, "_apply_scenario_update", _raise)

    with (
        alembic_engine.connect() as conn,
        pytest.raises(RuntimeError, match="simulated write failure"),
        conn.begin(),
    ):
        mod._upgrade_scenarios(conn)

    with alembic_engine.connect() as conn:
        # NO audit row remains AND the field is unchanged -- shared-fate
        # rollback, not proof of write order.
        assert _audit_rows(conn, sid) == []
        row = _get_scenario(conn, sid)
        assert json.loads(row["primary_loss"]) == wide_pl
        assert row["row_version"] == 1


# ---------------------------------------------------------------------------
# Case 9: library entries match the re-authored seed values.
# ---------------------------------------------------------------------------


def test_library_entries_replayed_from_current_seed(
    alembic_config: Config, alembic_engine: Engine
) -> None:
    """Reproduce genuine prod drift: the ancestor seed-insert migrations read
    the LIVE (already Task-2-recalibrated) JSON on a fresh test DB, which
    would make this migration's replay a no-op and assert nothing. Mutate the
    target rows back to a stale (pre-Task-2) envelope-sigma shape after
    reaching _PRE_REV, then run ONLY this migration and assert the
    CURRENT seed values land -- mirroring the "un-masking" technique in
    tests/migrations/test_insert_attack_coverage.py's conftest.
    """
    seed_by_slug = {e["slug"]: e for e in _seed_json_entries()}
    catastrophic_slug = "destructive-wiper-nationstate"  # extension JSON, lognormal
    capped_slug = "ransomware-on-ehr"  # base JSON, PERT
    assert seed_by_slug[catastrophic_slug]["primary_loss"]["distribution"] == "lognormal"
    assert seed_by_slug[capped_slug]["primary_loss"]["distribution"] == "PERT"

    command.upgrade(alembic_config, _PRE_REV)
    stale_lognormal = json.dumps({"distribution": "lognormal", "mean": 14.0, "sigma": 2.6})
    stale_pert = json.dumps({"distribution": "PERT", "low": 1.0, "mode": 1.0, "high": 2.0})
    with alembic_engine.begin() as conn:
        conn.execute(
            sa.text(
                "UPDATE scenario_library_entries SET primary_loss = :pl "
                "WHERE slug = :slug AND version = 1"
            ),
            {"pl": stale_lognormal, "slug": catastrophic_slug},
        )
        conn.execute(
            sa.text(
                "UPDATE scenario_library_entries SET primary_loss = :pl "
                "WHERE slug = :slug AND version = 1"
            ),
            {"pl": stale_pert, "slug": capped_slug},
        )

    command.upgrade(alembic_config, _REV)

    with alembic_engine.connect() as conn:
        for slug in (catastrophic_slug, capped_slug):
            row = (
                conn.execute(
                    sa.text(
                        "SELECT primary_loss, secondary_loss FROM scenario_library_entries "
                        "WHERE slug = :slug AND version = 1"
                    ),
                    {"slug": slug},
                )
                .mappings()
                .one()
            )
            assert json.loads(row["primary_loss"]) == seed_by_slug[slug]["primary_loss"]
            expected_sl = seed_by_slug[slug].get("secondary_loss")
            actual_sl = json.loads(row["secondary_loss"]) if row["secondary_loss"] else None
            assert actual_sl == expected_sl


# ---------------------------------------------------------------------------
# Case 10: end-to-end upgrade() wiring -- both halves fire from ONE
# command.upgrade() call, not helper-only.
# ---------------------------------------------------------------------------


def test_upgrade_wires_both_halves_together(alembic_config: Config, alembic_engine: Engine) -> None:
    seed_by_slug = {e["slug"]: e for e in _seed_json_entries()}
    target_slug = "destructive-wiper-nationstate"
    wide_pl = {"distribution": "lognormal", "mean": 14.8674357694, "sigma": 2.9269}

    command.upgrade(alembic_config, _PRE_REV)
    with alembic_engine.begin() as conn:
        sid = _seed_scenario(conn, pl_node=wide_pl, row_version=1)
        conn.execute(
            sa.text(
                "UPDATE scenario_library_entries SET primary_loss = :pl "
                "WHERE slug = :slug AND version = 1"
            ),
            {
                "pl": json.dumps({"distribution": "lognormal", "mean": 14.0, "sigma": 2.6}),
                "slug": target_slug,
            },
        )

    command.upgrade(alembic_config, _REV)  # single end-to-end command.upgrade call

    with alembic_engine.connect() as conn:
        # Scenario half fired.
        scen_row = _get_scenario(conn, sid)
        scen_node = json.loads(scen_row["primary_loss"])
        assert scen_node["sigma"] == pytest.approx(_SIGMA_TARGET)
        assert len(_audit_rows(conn, sid)) == 1
        # Library half fired.
        lib_row = (
            conn.execute(
                sa.text(
                    "SELECT primary_loss FROM scenario_library_entries "
                    "WHERE slug = :slug AND version = 1"
                ),
                {"slug": target_slug},
            )
            .mappings()
            .one()
        )
        assert json.loads(lib_row["primary_loss"]) == seed_by_slug[target_slug]["primary_loss"]


def test_audit_action_string_fits_string_64() -> None:
    """Arch-N2 precedent: the new audit action verb fits the String(64) column."""
    assert len("scenario.recalibrate_loss_sigma") <= 64
