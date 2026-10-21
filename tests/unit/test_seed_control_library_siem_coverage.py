"""#199: SIEM's lec_det_visibility coverage is the library's uniform Visibility default 0.8
(coverage = reach: the share of the estate whose log sources the SIEM ingests), expert-estimate,
with exactly one coverage citation. Overlap with re-ingested carriers is the analyst's call
(rubric 2.2; register C11), not an engine rule."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

SEED_PATH = Path(__file__).parents[2] / "data" / "seed_control_library_entries.json"
SIEM = "security-information-event-management"
VIS = "lec_det_visibility"


def _entries() -> list[dict[str, Any]]:
    doc = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    return cast(list[dict[str, Any]], doc["entries"])


def _siem_visibility() -> dict[str, Any]:
    entry = next(e for e in _entries() if e["slug"] == SIEM)
    rows = [a for a in entry["assignments"] if a["sub_function"] == VIS]
    assert len(rows) == 1
    return cast(dict[str, Any], rows[0])


def test_siem_visibility_coverage_is_the_uniform_default() -> None:
    a = _siem_visibility()
    assert a["coverage_default"] == 0.8
    assert a["coverage_provenance"] == "expert-estimate"
    assert (a["capability_default"], a["reliability_default"]) == (0.7, 0.8)


def test_siem_coverage_equals_every_peer_visibility_carrier() -> None:
    peers = {
        e["slug"]: a["coverage_default"]
        for e in _entries()
        for a in e["assignments"]
        if a["sub_function"] == VIS
    }
    assert SIEM in peers and len(peers) > 1
    assert {v for slug, v in peers.items() if slug != SIEM} == {peers[SIEM]}  # uniform default


def test_siem_coverage_citation_states_reach_and_analyst_overlap_guidance() -> None:
    cites = _siem_visibility()["coverage_citations"]
    assert len(cites) == 1
    text = cites[0]
    assert "reach" in text
    assert "uniform 0.8 Visibility default" in text
    assert "the analyst lowers this coverage" in text
    assert "interim" not in text.lower()


def test_siem_capability_citation_states_the_uniform_default_magnitude() -> None:
    entry = next(e for e in _entries() if e["slug"] == SIEM)
    a = next(x for x in entry["assignments"] if x["sub_function"] == VIS)
    assert len(a["capability_citations"]) == 1
    text = a["capability_citations"][0]
    assert "(coverage 0.8, reliability 0.8)" in text
    assert "(coverage 0.3, reliability 0.8)" not in text
    assert "independent OR" in text and "register C11" in text
    assert "engine follow-up" not in text
