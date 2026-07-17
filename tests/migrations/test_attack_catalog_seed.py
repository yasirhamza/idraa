"""Pinning tests for data/seed_attack_catalog.json (issue #475).

Mirrors tests/migrations/test_crosswalk_seed.py: the pytest harness builds
schema via Base.metadata.create_all (not migrations), so these are pure-JSON
assertions of what the Task-5 migration inserts. Counts are HARD PINS — a
catalog version refresh must update them consciously in the same commit.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import idraa
from idraa.schemas.attack_catalog import AttackTacticSeed, AttackTechniqueSeed

# PIN these from the Task-3 generation output (see its success line).
# ATT&CK v19.1: v19 split Defense Evasion into Stealth (TA0005) + Defense
# Impairment (TA0112) — enterprise tactic count of 15 is genuinely correct.
EXPECTED_TACTICS = {"enterprise": 15, "ics": 12}
EXPECTED_TECHNIQUES = {"enterprise": 222, "ics": 79}
EXPECTED_ATTACK_VERSION = "19.1"


def _payload():
    p = Path(idraa.__file__).resolve().parent.parent.parent / "data" / "seed_attack_catalog.json"
    return json.loads(p.read_text(encoding="utf-8"))


def test_every_row_validates():
    payload = _payload()
    for t in payload["tactics"]:
        AttackTacticSeed.model_validate(t)
    for t in payload["techniques"]:
        AttackTechniqueSeed.model_validate(t)


def test_row_counts_pinned():
    payload = _payload()
    for domain in ("enterprise", "ics"):
        assert (
            len([t for t in payload["tactics"] if t["domain"] == domain])
            == EXPECTED_TACTICS[domain]
        )
        assert (
            len([t for t in payload["techniques"] if t["domain"] == domain])
            == EXPECTED_TECHNIQUES[domain]
        )


def test_technique_tactics_resolve_to_seeded_shortnames():
    payload = _payload()
    for domain in ("enterprise", "ics"):
        shortnames = {t["shortname"] for t in payload["tactics"] if t["domain"] == domain}
        for tech in payload["techniques"]:
            if tech["domain"] == domain:
                assert set(tech["tactics"]) <= shortnames, tech["technique_id"]


def test_attribution_pinned():
    attribution = _payload()["_attribution"]
    for domain in ("enterprise", "ics"):
        block = attribution[domain]
        assert block["source"] == "MITRE ATT&CK"
        assert block["license"] == "MITRE ATT&CK Terms of Use"
        assert block["attack_version"] == EXPECTED_ATTACK_VERSION
        assert "MITRE Corporation" in block["copyright"]
        # Sec-N2: pinned fetch commit — hex-only, 7-40 chars (short or full SHA),
        # never an interpreter-inherited "truthy length" check.
        assert re.fullmatch(r"[0-9a-f]{7,40}", block["source_commit"])
    # T4b: both domains are fetched from the same MITRE ATT&CK Terms of Use —
    # the copyright statement must be byte-identical across domains, never
    # drift independently between the two STIX bundle fetches.
    assert attribution["ics"]["copyright"] == attribution["enterprise"]["copyright"]


def test_notice_carries_the_attribution_sentence():
    """Meth2-N2: the hand-authored NOTICE must not drift from the builder's
    verified copyright constant — pin that the NOTICE contains the JSON
    _attribution copyright sentence verbatim."""
    attribution = _payload()["_attribution"]
    notice = (
        Path(idraa.__file__).resolve().parent.parent.parent
        / "data"
        / "seed_attack_catalog.NOTICE.md"
    ).read_text(encoding="utf-8")
    assert attribution["enterprise"]["copyright"] in notice


def test_uniqueness_and_display_order():
    payload = _payload()
    keys = [(t["domain"], t["technique_id"]) for t in payload["techniques"]]
    assert len(keys) == len(set(keys))
    for domain in ("enterprise", "ics"):
        orders = sorted(t["display_order"] for t in payload["tactics"] if t["domain"] == domain)
        assert orders == list(range(len(orders)))  # dense kill-chain ordering
    # T4a: tactic-key uniqueness — UNIQUE (domain, tactic_id) AND
    # UNIQUE (domain, shortname) per the design doc's AttackTactic table
    # (techniques join on shortname, so a shortname collision would silently
    # cross-wire technique->tactic membership across two rows).
    tactic_id_keys = [(t["domain"], t["tactic_id"]) for t in payload["tactics"]]
    assert len(tactic_id_keys) == len(set(tactic_id_keys))
    shortname_keys = [(t["domain"], t["shortname"]) for t in payload["tactics"]]
    assert len(shortname_keys) == len(set(shortname_keys))
