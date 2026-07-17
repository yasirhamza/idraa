"""Machine-readable citation completeness for the per-industry magnitude priors.

The primary-cited gate previously lived only in prose ``notes`` narratives —
unverifiable by tests or lints. ``IndustryMagnitudePrior.citation`` is the
structured counterpart; this test pins the coverage invariants:

- every ``_SECTOR_TABLE_CITED`` industry carries the Figure-A3 citation
  (source + page + figure, the paginated anchor for BOTH p50/p95 legs);
- the two excluded industries (AGRICULTURE, MINING — anecdotal anchors,
  near-point-mass sector rows) carry ``citation=None``;
- all 20 IndustryType members are present.
"""

from __future__ import annotations

from fair_cam.parameters._iris_2025_calibration import (
    _FIGURE_A3_CITATION,
    _SECTOR_TABLE_CITED,
    PER_INDUSTRY_MAGNITUDE_PRIORS_2025,
    PrimaryCitation,
)
from fair_cam.parameters.industry_calibration import IndustryType


def test_all_industry_types_have_a_prior() -> None:
    assert set(PER_INDUSTRY_MAGNITUDE_PRIORS_2025) == set(IndustryType)


def test_every_sector_table_cited_industry_has_the_figure_a3_citation() -> None:
    for industry in _SECTOR_TABLE_CITED:
        citation = PER_INDUSTRY_MAGNITUDE_PRIORS_2025[industry].citation
        assert citation == _FIGURE_A3_CITATION, (
            f"{industry}: _SECTOR_TABLE_CITED membership requires the "
            f"paginated Figure-A3 citation, got {citation!r}"
        )


def test_excluded_industries_have_no_citation() -> None:
    """AGRICULTURE/MINING are anecdotal-anchored (module docstring rationale);
    a non-None citation here would falsely claim primary-cited status."""
    for industry in (IndustryType.AGRICULTURE, IndustryType.MINING):
        assert PER_INDUSTRY_MAGNITUDE_PRIORS_2025[industry].citation is None


def test_citation_set_exactly_matches_allowlist() -> None:
    """Bidirectional: citations and the cited-σ allowlist must never drift —
    a cited entry outside the allowlist (or vice versa) is a methodology bug."""
    with_citation = {
        i for i, p in PER_INDUSTRY_MAGNITUDE_PRIORS_2025.items() if p.citation is not None
    }
    assert with_citation == set(_SECTOR_TABLE_CITED)


def test_citation_fields_are_complete() -> None:
    assert PrimaryCitation(source="IRIS 2025", page=35, figure="A3") == _FIGURE_A3_CITATION
