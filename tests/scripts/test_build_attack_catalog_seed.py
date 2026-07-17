"""Builder tests against tiny synthetic STIX bundles (issue #475 T3).

Real MITRE bundles are ~40MB and are NOT committed; the builder's extraction
logic is exercised on minimal synthetic bundles with the same object shapes.
"""

from __future__ import annotations

import pytest
from scripts.build_attack_catalog_seed import (
    build_catalog,
    extract_tactics,
    extract_techniques,
)


def _mk_bundle(kill_chain: str, *, include_matrix: bool = True) -> dict:
    tactic = {
        "type": "x-mitre-tactic",
        "id": "x-mitre-tactic--aaa",
        "name": "Initial Access",
        "description": "First paragraph.\n\nSecond paragraph.",
        "x_mitre_shortname": "initial-access",
        "external_references": [
            {
                "source_name": "mitre-attack",
                "external_id": "TA0001",
                "url": "https://attack.mitre.org/tactics/TA0001/",
            }
        ],
    }
    technique = {
        "type": "attack-pattern",
        "name": "Phishing",
        "description": "Adversaries phish. (Citation: XYZ)\n\nMore detail.",
        "x_mitre_is_subtechnique": False,
        "kill_chain_phases": [{"kill_chain_name": kill_chain, "phase_name": "initial-access"}],
        "external_references": [
            {
                "source_name": "mitre-attack",
                "external_id": "T1566",
                "url": "https://attack.mitre.org/techniques/T1566/",
            }
        ],
    }
    sub_technique = {
        **technique,
        "x_mitre_is_subtechnique": True,
        "external_references": [
            {
                "source_name": "mitre-attack",
                "external_id": "T1566.001",
                "url": "https://attack.mitre.org/techniques/T1566/001/",
            }
        ],
    }
    revoked = {
        **technique,
        "revoked": True,
        "external_references": [
            {
                "source_name": "mitre-attack",
                "external_id": "T9999",
                "url": "https://attack.mitre.org/techniques/T9999/",
            }
        ],
    }
    objects = [
        {"type": "x-mitre-collection", "x_mitre_version": "18.0"},
        tactic,
        technique,
        sub_technique,
        revoked,
    ]
    if include_matrix:
        objects.insert(1, {"type": "x-mitre-matrix", "tactic_refs": ["x-mitre-tactic--aaa"]})
    return {"objects": objects}


def test_extract_tactics_orders_by_matrix_and_trims_description():
    tactics = extract_tactics(_mk_bundle("mitre-attack"), "enterprise")
    assert tactics == [
        {
            "domain": "enterprise",
            "tactic_id": "TA0001",
            "shortname": "initial-access",
            "name": "Initial Access",
            "description": "First paragraph.",
            "display_order": 0,
            "url": "https://attack.mitre.org/tactics/TA0001/",
        }
    ]


def test_extract_techniques_skips_subtechniques_and_revoked_and_strips_citations():
    bundle = _mk_bundle("mitre-attack")
    techs = extract_techniques(bundle, "enterprise", {"initial-access"})
    assert [t["technique_id"] for t in techs] == ["T1566"]
    assert techs[0]["tactics"] == ["initial-access"]
    assert "(Citation:" not in techs[0]["description"]


def test_unresolved_phase_name_fails_loud():
    bundle = _mk_bundle("mitre-attack")
    with pytest.raises(SystemExit):
        extract_techniques(bundle, "enterprise", {"some-other-tactic"})


def test_missing_matrix_fails_loud():
    with pytest.raises(SystemExit):
        extract_tactics(_mk_bundle("mitre-attack", include_matrix=False), "enterprise")


def test_build_catalog_validates_and_stamps_attribution():
    catalog = build_catalog(
        _mk_bundle("mitre-attack"),
        _mk_bundle("mitre-ics-attack"),
        accessed="2026-07-04",
        source_commit="abc1234def",
    )
    assert set(catalog) == {"_attribution", "tactics", "techniques"}
    assert catalog["_attribution"]["enterprise"]["attack_version"] == "18.0"
    assert catalog["_attribution"]["ics"]["attack_version"] == "18.0"
    assert catalog["_attribution"]["enterprise"]["source_commit"] == "abc1234def"
    domains = {t["domain"] for t in catalog["techniques"]}
    assert domains == {"enterprise", "ics"}
    for t in catalog["techniques"]:
        assert t["citation"]["accessed"] == "2026-07-04"
