"""The shared snapshot classification rule + purity of the module."""

from __future__ import annotations

import subprocess
import sys
import uuid

from idraa.threat_community_provenance import REVIEW_PROVENANCES, community_by_scenario


def test_classification_rule() -> None:
    a, b, c, d, e = (uuid.uuid4() for _ in range(5))
    snap = {
        "scenarios": [
            {
                "scenario_id": str(a),
                "threat_community": {"slug": "x", "name": "X"},
                "threat_community_provenance": "assigned",
            },
            {
                "scenario_id": str(b),
                "threat_community": {"slug": "y", "name": "Y"},
                "threat_community_provenance": "migrated_split_default",
            },
            {
                "scenario_id": str(c),
                "threat_community": None,
                "threat_community_provenance": "unassigned",
            },
            {"scenario_id": str(d)},  # pre-P1 row
            {
                "scenario_id": str(e),
                "threat_community": "not-a-dict",
            },  # malformed -> None, never raises
            {"scenario_id": "not-a-uuid", "threat_community": {"slug": "z", "name": "Z"}},
        ]
    }
    out = community_by_scenario(snap)
    assert out == {a.hex: ("x", "X"), b.hex: None, c.hex: None, d.hex: None, e.hex: None}
    assert community_by_scenario(None) == {} and "migrated_split_default" in REVIEW_PROVENANCES
    assert community_by_scenario(
        {"scenarios": [{"scenario_id": str(a), "threat_community": {"name": "no slug"}}]}
    ) == {a.hex: None}
    # total on malformed snapshots: non-dict snapshot, non-list scenarios, unhashable provenance
    assert (
        community_by_scenario("junk") == {} and community_by_scenario({"scenarios": "junk"}) == {}
    )
    assert community_by_scenario(
        {
            "scenarios": [
                {
                    "scenario_id": str(a),
                    "threat_community": {"slug": "x", "name": "X"},
                    "threat_community_provenance": ["list"],
                }
            ]
        }
    ) == {a.hex: None}


def test_module_imports_without_db_or_models() -> None:
    """Purity, checked in a SUBPROCESS (the tests/unit/test_pdf_report.py:593 idiom). Never pop sys.modules in-process."""
    code = "import sys, idraa.threat_community_provenance; print(sorted(m for m in sys.modules if m.startswith(('idraa.db','idraa.models','sqlalchemy'))))"
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    ).stdout.strip()
    assert out == "[]", out
