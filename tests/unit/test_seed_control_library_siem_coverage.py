"""#199 slice 1 (R-T-9): SIEM's lec_det_visibility coverage is the derived residual
share 0.3, expert-estimate, with exactly one coverage citation that carries the
derivation. Guards the interim until the C11 correlated-Visibility operator lands."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

SEED_PATH = Path(__file__).parents[2] / "data" / "seed_control_library_entries.json"
SIEM = "security-information-event-management"


def _siem_visibility() -> dict[str, Any]:
    doc = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    entry = next(e for e in doc["entries"] if e["slug"] == SIEM)
    rows = [a for a in entry["assignments"] if a["sub_function"] == "lec_det_visibility"]
    assert len(rows) == 1
    return cast(dict[str, Any], rows[0])


def test_siem_visibility_coverage_is_the_residual_share() -> None:
    a = _siem_visibility()
    assert a["coverage_default"] == 0.3
    assert a["coverage_provenance"] == "expert-estimate"
    assert (a["capability_default"], a["reliability_default"]) == (0.7, 0.8)


def test_siem_visibility_has_exactly_one_derivation_citation() -> None:
    a = _siem_visibility()
    assert len(a["coverage_citations"]) == 1
    text = a["coverage_citations"][0]
    assert "residual" in text
    assert "3 residual classes / (3 + 7 carrier classes) = 0.3" in text  # verbatim in the citation
    assert "interim" in text.lower()
    assert "#199" in text


def test_siem_capability_citation_states_the_corrected_coverage() -> None:
    a = _siem_visibility()
    assert len(a["capability_citations"]) == 1
    text = a["capability_citations"][0]
    assert "(coverage 0.3, reliability 0.8)" in text
    assert "(coverage 0.8, reliability 0.8)" not in text
    assert "tracked as an engine follow-up (#199), not a curation rule" in text  # kept (R-SIEM-5)
    assert "can both count toward Detection" not in text  # the pre-interim C11 clause is gone
    # R-SIEM-5 / plan-gate M2-N4: "removes the seven named carriers' share", not "stops
    # double-claiming" — partial overlap with SSPM, EDR/HIDS and WAF/APM remains at 0.3.
    assert "removes the seven named carriers' share from the claim" in text


def test_siem_coverage_citation_states_scope_and_guidance() -> None:
    """M-I3: the record supports itself — upper end, 3/17 corner, contact share, sole-carrier
    guidance (spec §4.2.1)."""
    text = _siem_visibility()["coverage_citations"][0]
    assert "share of threat contact" in text
    assert "3 / 17" in text
    assert "sole Visibility carrier" in text
    assert "0.18" in text and "0.4" in text
