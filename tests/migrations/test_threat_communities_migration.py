"""a1c9e4d2b7f0: threat_communities seed + FK backfill + enum column drop (spec §4)."""

from __future__ import annotations

import importlib.util
import json
import uuid
from pathlib import Path

import sqlalchemy as sa

import idraa

_ROOT = Path(idraa.__file__).resolve().parent.parent.parent
(_MIG,) = (_ROOT / "alembic" / "versions").glob("a1c9e4d2b7f0_*.py")
_spec = importlib.util.spec_from_file_location("_tc_mig", _MIG)
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)  # type: ignore[union-attr]
PRE, REV = mod.down_revision, mod.revision
_ORG = uuid.uuid4().hex


def _insert_org(conn) -> None:
    conn.execute(
        sa.text(
            "INSERT INTO organizations (id, created_at, updated_at, name, organization_size, industry_type, security_maturity, "
            "risk_appetite, preferred_currency, preferred_language, geographic_regions, compliance_requirements, "
            "regulatory_environment, technology_stack, has_cyber_insurance) VALUES "
            "(:id, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 'T', 'large', 'information', 'defined', 'moderate', 'USD', 'en', '[]', '[]', '[]', '[]', 0)"
        ),
        {"id": _ORG},
    )


def _insert_entry(conn, slug: str, tat: str, source: str = "imported") -> str:
    eid = uuid.uuid4().hex
    conn.execute(
        sa.text(
            "INSERT INTO scenario_library_entries (id, version, slug, name, status, threat_event_type, threat_actor_type, asset_class, tags, "
            "description, source_citations, canonical_fair_gap, threat_event_frequency, vulnerability, primary_loss, suggested_control_ids, "
            "calibration_anchor, loss_tier, loss_shape, loss_form_profile, source, row_version, created_at, updated_at) VALUES "
            "(:id, 1, :slug, 'E', 'published', 'insider_misuse', :tat, 'data', '[]', 'x', '[]', 'x', '{}', '{}', '{}', '[]', '{}', 'anecdotal', "
            "'capped', '[]', :src, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        ),
        {"id": eid, "slug": slug, "tat": tat, "src": source},
    )
    return eid


def _insert_scenario(conn, *, tat: str | None, pin_entry_slug: str | None = None) -> str:
    sid = uuid.uuid4().hex
    pin = None
    if pin_entry_slug:
        eid = conn.execute(
            sa.text("SELECT id FROM scenario_library_entries WHERE slug=:s"), {"s": pin_entry_slug}
        ).scalar_one()
        pin = json.dumps({"entry_id": str(uuid.UUID(eid)), "version": 1})
    conn.execute(
        sa.text(
            "INSERT INTO scenarios (id, organization_id, name, scenario_type, threat_category, threat_actor_type, threat_event_frequency, "
            "vulnerability, primary_loss, status, version, row_version, entry_currency, vuln_framing, library_pin, source, created_at, updated_at) "
            "VALUES (:id, :org, 'S', 'custom', 'malware', :tat, '{}', '{}', '{}', 'active', '1.0', 1, 'USD', 'inherent', :pin, 'expert_judgment', "
            "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        ),
        {"id": sid, "org": _ORG, "tat": tat, "pin": pin},
    )
    return sid


def _community(conn, sid: str) -> tuple[str | None, str]:
    return conn.execute(
        sa.text(
            "SELECT tc.slug, s.threat_community_provenance FROM scenarios s LEFT JOIN threat_communities tc "
            "ON tc.id = s.threat_community_id AND tc.version = s.threat_community_version WHERE s.id=:id"
        ),
        {"id": sid},
    ).one()


def _seed_map(key: str) -> dict[str, str]:
    return {
        e["slug"]: e[key]
        for f in ("seed_library_entries.json", "seed_library_entries_extension.json")
        for e in json.loads((_ROOT / "data" / f).read_text(encoding="utf-8"))
    }


def _legacy_of(conn, slug: str) -> str:
    return conn.execute(
        sa.text("SELECT threat_actor_type FROM scenario_library_entries WHERE slug=:s"), {"s": slug}
    ).scalar_one()


def test_frozen_maps_match_seed_json() -> None:
    assert _seed_map("threat_community") == mod._ENTRY_COMMUNITY
    assert _seed_map("threat_actor_type") == mod._ENTRY_LEGACY


def test_upgrade_rules(alembic_runner, alembic_engine) -> None:
    seed = _seed_map("threat_community")
    remapped_slug = next(
        s
        for s, c in seed.items()
        if c in ("third_party", "opportunistic_hackers", "nonprivileged_insider")
    )
    split_slug = next(s for s, c in seed.items() if c == "privileged_insider")
    alembic_runner.migrate_up_to(PRE)
    with alembic_engine.begin() as conn:
        _insert_org(conn)
        _insert_entry(conn, "imported-insider", "insider_malicious")
        s_null = _insert_scenario(conn, tat=None)
        s_split = _insert_scenario(conn, tat="insider_malicious")
        s_one = _insert_scenario(conn, tat="hacktivists")
        s_1to1 = {
            v: _insert_scenario(conn, tat=v)
            for v in ("cybercriminals", "nation_state", "competitors", "insider_accidental")
        }
        s_agree = _insert_scenario(
            conn, tat=_legacy_of(conn, split_slug), pin_entry_slug=split_slug
        )
        s_disagree = _insert_scenario(conn, tat="competitors", pin_entry_slug=split_slug)
        s_remapped = _insert_scenario(
            conn, tat=_legacy_of(conn, remapped_slug), pin_entry_slug=remapped_slug
        )
    alembic_runner.migrate_up_to(REV)
    with alembic_engine.connect() as conn:
        assert conn.execute(sa.text("SELECT count(*) FROM threat_communities")).scalar_one() == 9
        assert conn.execute(
            sa.text("SELECT slug FROM threat_communities WHERE tcap_landmark IS NULL")
        ).scalars().all() == ["insider_accidental"]
        assert (
            conn.execute(
                sa.text(
                    "SELECT typeof(tcap_landmark) FROM threat_communities WHERE slug='insider_accidental'"
                )
            ).scalar_one()
            == "null"
        )
        for t in ("scenarios", "scenario_library_entries"):
            cols = {r[1] for r in conn.execute(sa.text(f"PRAGMA table_info({t})"))}
            assert "threat_actor_type" not in cols and "threat_community_id" in cols
            assert conn.execute(sa.text(f"PRAGMA foreign_key_check({t})")).all() == []
        rows = conn.execute(
            sa.text(
                "SELECT e.slug, tc.slug FROM scenario_library_entries e JOIN threat_communities tc "
                "ON tc.id=e.threat_community_id AND tc.version=e.threat_community_version WHERE e.source='seed'"
            )
        ).all()
        assert rows and all(seed[s] == c for s, c in rows)
        (imp,) = conn.execute(
            sa.text(
                "SELECT tc.slug FROM scenario_library_entries e JOIN threat_communities tc ON tc.id=e.threat_community_id "
                "AND tc.version=e.threat_community_version WHERE e.slug='imported-insider'"
            )
        ).one()
        assert imp == "privileged_insider"
        assert _community(conn, s_null) == (None, "unassigned")
        assert _community(conn, s_split) == ("privileged_insider", "migrated_split_default")
        assert _community(conn, s_one) == ("hacktivists", "migrated")
        for v, sid in s_1to1.items():  # all six legacy values covered (spec §6)
            assert _community(conn, sid) == (v, "migrated")
        assert _community(conn, s_agree) == (seed[split_slug], "migrated")
        assert _community(conn, s_disagree) == ("competitors", "migrated")  # own value wins
        assert _community(conn, s_remapped) == (seed[remapped_slug], "migrated")
        # half-NULL pair rejected
        import pytest

        with pytest.raises(sa.exc.IntegrityError):
            conn.execute(
                sa.text("UPDATE scenarios SET threat_community_version=NULL WHERE id=:id"),
                {"id": s_one},
            )


def test_downgrade_exact_for_seed_entries_lossy_for_scenarios_and_reupgrade(
    alembic_runner, alembic_engine
) -> None:
    alembic_runner.migrate_up_to(PRE)
    with alembic_engine.begin() as conn:
        _insert_org(conn)
        s_split = _insert_scenario(conn, tat="insider_malicious")
        s_a, s_b, s_c = (_insert_scenario(conn, tat="competitors") for _ in range(3))
        before = dict(
            conn.execute(
                sa.text(
                    "SELECT slug, threat_actor_type FROM scenario_library_entries WHERE source='seed'"
                )
            ).all()
        )
    alembic_runner.migrate_up_to(REV)
    with alembic_engine.begin() as conn:
        for sid, slug in (
            (s_a, "nonprivileged_insider"),
            (s_b, "third_party"),
            (s_c, "opportunistic_hackers"),
        ):
            conn.execute(
                sa.text(
                    "UPDATE scenarios SET threat_community_id=(SELECT id FROM threat_communities WHERE slug=:s), "
                    "threat_community_version=1, threat_community_provenance='assigned' WHERE id=:id"
                ),
                {"s": slug, "id": sid},
            )
    alembic_runner.migrate_down_to(PRE)
    with alembic_engine.connect() as conn:
        after = dict(
            conn.execute(
                sa.text(
                    "SELECT slug, threat_actor_type FROM scenario_library_entries WHERE source='seed'"
                )
            ).all()
        )
        assert after == before  # exact restore from _ENTRY_LEGACY
        got = {
            sid: conn.execute(
                sa.text("SELECT threat_actor_type FROM scenarios WHERE id=:id"), {"id": sid}
            ).scalar_one()
            for sid in (s_split, s_a, s_b, s_c)
        }
        assert got == {
            s_split: "insider_malicious",
            s_a: "insider_malicious",
            s_b: "insider_malicious",
            s_c: "cybercriminals",
        }
        assert "threat_communities" not in {
            r[0] for r in conn.execute(sa.text("SELECT name FROM sqlite_master WHERE type='table'"))
        }
        # CHECK + NOT NULL restored on BOTH tables
        import pytest

        with pytest.raises(sa.exc.IntegrityError):
            conn.execute(
                sa.text("UPDATE scenarios SET threat_actor_type='martians' WHERE id=:id"),
                {"id": s_a},
            )
        with pytest.raises(sa.exc.IntegrityError):
            conn.execute(
                sa.text(
                    "UPDATE scenario_library_entries SET threat_actor_type=NULL WHERE source='seed'"
                )
            )
        with pytest.raises(sa.exc.IntegrityError):
            conn.execute(
                sa.text(
                    "UPDATE scenario_library_entries SET threat_actor_type='martians' WHERE source='seed'"
                )
            )
    alembic_runner.migrate_up_to(REV)  # up -> down -> up must be clean
    with alembic_engine.connect() as conn:
        assert conn.execute(sa.text("SELECT count(*) FROM threat_communities")).scalar_one() == 9
        assert _community(conn, s_split) == ("privileged_insider", "migrated_split_default")
    alembic_runner.migrate_down_to(PRE)  # and down again
