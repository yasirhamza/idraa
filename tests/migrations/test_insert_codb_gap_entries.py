"""#459 gap-entry migration (b6f2d8c4a1e9) sync-guard.

Mirrors tests/migrations/test_insert_d_iii_b.py: the migration's pinned
downgrade slug tuple must stay a subset of the live seed JSON so a future
JSON slug edit cannot silently desync the migration's insert/downgrade scope.
"""

import json
from pathlib import Path

import idraa

ROOT = Path(idraa.__file__).resolve().parent.parent.parent


def _seed_slugs() -> set[str]:
    payload = json.loads(
        (ROOT / "data" / "seed_control_library_entries.json").read_text(encoding="utf-8")
    )
    return {e["slug"] for e in payload["entries"]}


def test_gap_slug_tuple_subset_of_seed_json() -> None:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config(str(ROOT / "alembic.ini"))
    script = ScriptDirectory.from_config(cfg)
    mod = script.get_revision("b6f2d8c4a1e9").module
    assert set(mod.GAP_SLUGS) <= _seed_slugs()
    assert len(mod.GAP_SLUGS) == 2


def test_gap_entries_present_in_seed_with_expected_claims() -> None:
    payload = json.loads(
        (ROOT / "data" / "seed_control_library_entries.json").read_text(encoding="utf-8")
    )
    by_slug = {e["slug"]: e for e in payload["entries"]}
    mdr = by_slug["managed-detection-response"]
    easm = by_slug["external-attack-surface-management"]
    # #459 plan-gate converged claim sets (Meth-I1: NO lec_resp_resilience).
    assert [a["sub_function"] for a in mdr["assignments"]] == [
        "lec_det_visibility",
        "lec_det_monitoring",
        "lec_det_recognition",
        "lec_resp_event_termination",
    ]
    assert "RS.MI-2" not in mdr["nist_csf_subcategories"]
    assert [a["sub_function"] for a in easm["assignments"]] == ["vmc_id_control_monitoring"]


def test_insert_gap_entries_on_existing_db(alembic_runner, alembic_engine) -> None:
    """Behavioral round-trip (architect PR-gate finding): on a fresh alembic
    test DB the ancestor seed migration d4f6a2b9c8e1 reads the LIVE 63-entry
    JSON and already inserts both gap slugs, so b6f2d8c4a1e9's INSERT would
    no-op and assert nothing. Reproduce the genuine pre-#459 prod state by
    DELETING the 2 gap slugs (+ assignments) at the pre-rev, then run ONLY
    b6f2d8c4a1e9 — the deployed-DB path where the exists-guard, the full
    provenance-column INSERT, and the FK-off downgrade ordering all matter."""
    import sqlalchemy as sa

    pre_rev = "e7b1c9d4a2f8"
    gap_rev = "b6f2d8c4a1e9"
    slugs = ("managed-detection-response", "external-attack-surface-management")

    alembic_runner.migrate_up_to(pre_rev)
    with alembic_engine.begin() as conn:
        conn.execute(
            sa.text(
                "DELETE FROM control_library_entry_assignments "
                "WHERE library_entry_id IN ("
                "  SELECT id FROM control_library_entries "
                "  WHERE slug IN :slugs AND version = 1)"
            ).bindparams(sa.bindparam("slugs", slugs, expanding=True))
        )
        conn.execute(
            sa.text(
                "DELETE FROM control_library_entries WHERE slug IN :slugs AND version = 1"
            ).bindparams(sa.bindparam("slugs", slugs, expanding=True))
        )

    # Un-masking guards (architect re-check): prove the simulate-delete took
    # effect AND the migration under test actually inserted — without these,
    # the seed-path rows (byte-shape-identical) would satisfy every assertion
    # below even if b6f2d8c4a1e9 no-oped.
    with alembic_engine.connect() as conn:
        present = conn.execute(
            sa.text("SELECT COUNT(*) FROM control_library_entries WHERE slug IN :slugs").bindparams(
                sa.bindparam("slugs", slugs, expanding=True)
            )
        ).scalar_one()
    assert present == 0, "gap slugs still present after simulated-prod delete"

    alembic_runner.migrate_up_to(gap_rev)

    with alembic_engine.connect() as conn:
        present = conn.execute(
            sa.text("SELECT COUNT(*) FROM control_library_entries WHERE slug IN :slugs").bindparams(
                sa.bindparam("slugs", slugs, expanding=True)
            )
        ).scalar_one()
    assert present == 2, "b6f2d8c4a1e9 did not insert the 2 gap entries"

    with alembic_engine.connect() as conn:
        rows = conn.execute(
            sa.text(
                "SELECT e.id, e.slug, a.sub_function, a.capability_default, "
                "a.capability_provenance, a.id "
                "FROM control_library_entry_assignments a "
                "JOIN control_library_entries e ON e.id = a.library_entry_id "
                "WHERE e.slug IN :slugs AND e.version = 1"
            ).bindparams(sa.bindparam("slugs", slugs, expanding=True))
        ).fetchall()
    assert len(rows) == 5  # MDR 4 + EASM 1
    for eid, slug, sub_fn, cap, cap_prov, aid in rows:
        assert len(str(eid)) == 32 and "-" not in str(eid), (slug, eid)
        assert len(str(aid)) == 32 and "-" not in str(aid), (slug, sub_fn, aid)
        # provenance invariant: 'expert-estimate' iff capability set, NULL otherwise
        assert (cap_prov == "expert-estimate") == (cap is not None), (slug, sub_fn)
    assert sum(1 for r in rows if r[4] == "expert-estimate") == 3  # 2 MDR + 1 EASM

    # Downgrade: entries AND assignments gone, zero orphans.
    alembic_runner.migrate_down_to(pre_rev)
    with alembic_engine.connect() as conn:
        n = conn.execute(
            sa.text("SELECT COUNT(*) FROM control_library_entries WHERE slug IN :slugs").bindparams(
                sa.bindparam("slugs", slugs, expanding=True)
            )
        ).scalar_one()
        orphans = conn.execute(
            sa.text(
                "SELECT COUNT(*) FROM control_library_entry_assignments a "
                "WHERE NOT EXISTS (SELECT 1 FROM control_library_entries e "
                "WHERE e.id = a.library_entry_id)"
            )
        ).scalar_one()
    assert n == 0 and orphans == 0
