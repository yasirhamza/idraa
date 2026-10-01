"""PDF 'Scenarios by threat community' section (Task 12).

View-model derivation, NOT FAIR-grounded (register D10): groups page-4 rows by
the community recorded in the run's scenario_inputs_snapshot, same grouping
rule as the dashboard's build_threat_community_summary
(services/threat_community_summary.py) — grouped by SLUG, displayed by the
smallest snapshot NAME seen for that slug; rows lacking a community group
under NEEDS_REVIEW_LABEL; non-finite ALE / unparseable scenario_id rows are
dropped (count-only log, never the raw id).
"""

from __future__ import annotations

import uuid

from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph

from idraa.services.reports import (
    PerScenarioRow,
    ThreatCommunityGroup,
    build_threat_community_groups,
)
from idraa.threat_community_provenance import NEEDS_REVIEW_LABEL


def _r(name, ale, tc, sid=None):
    """tc = (slug, display name) or None. scenario_id defaults to a deterministic UUID (the
    builder drops non-UUID ids, matching the dashboard); pass sid= to test that drop."""
    slug, label = tc if tc else (None, None)
    return PerScenarioRow(
        scenario_id=sid if sid is not None else str(uuid.uuid5(uuid.NAMESPACE_URL, name)),
        scenario_name=name,
        base_ale=ale * 2,
        residual_ale=ale,
        reduction=ale,
        threat_community_slug=slug,
        threat_community_name=label,
    )


def test_groups_sorted_by_subtotal_desc_with_shares() -> None:
    g = build_threat_community_groups(
        [
            _r("a", 100.0, ("cybercriminals", "Cybercriminals")),
            _r("b", 300.0, ("nation_state", "Nation-state")),
            _r("c", 50.0, ("cybercriminals", "Cybercriminals")),
        ]
    )
    assert [x.name for x in g] == ["Nation-state", "Cybercriminals"] and g[
        1
    ].residual_ale_subtotal == 150.0
    assert abs(g[0].share - 300.0 / 450.0) < 1e-12 and [r.scenario_name for r in g[1].rows] == [
        "a",
        "c",
    ]


def test_pdf_groups_legacy_snapshot_under_needs_review() -> None:
    """Review Focus #5 (PDF half)."""
    assert {
        x.name
        for x in build_threat_community_groups(
            [_r("a", 10.0, None), _r("b", 5.0, ("hacktivists", "Hacktivists"))]
        )
    } == {"Hacktivists", NEEDS_REVIEW_LABEL}


def test_pdf_groups_by_slug_not_name_and_labels_with_the_smallest_name() -> None:
    """Two slugs sharing a snapshot name stay two groups (same rule as the dashboard); display = the
    SMALLEST name seen for the slug — "Zeta" then "Alpha" labels "Alpha", not the first seen."""
    g = build_threat_community_groups(
        [
            _r("a", 1.0, ("cybercriminals", "Same")),
            _r("b", 2.0, ("nation_state", "Zeta")),
            _r("c", 3.0, ("nation_state", "Alpha")),
        ]
    )
    assert (
        len(g) == 2
        and g[0].residual_ale_subtotal == 5.0
        and g[0].name == "Alpha"
        and g[1].name == "Same"
    )


def test_pdf_drops_unparseable_ids_like_the_dashboard() -> None:
    rows = [_r("a", 10.0, ("hacktivists", "Hacktivists")), _r("bad", 5.0, None, sid="not-a-uuid")]
    g = build_threat_community_groups(rows)
    assert [x.name for x in g] == [
        "Hacktivists"
    ]  # the malformed row is dropped, not bucketed under Needs review


async def test_pdf_data_groups_from_the_real_producer_snapshot(
    db_session, seed_threat_communities, seed_organization
) -> None:
    """The consumer seam: build_executive_pdf_data -> community_by_scenario(run.scenario_inputs_snapshot) -> groups.
    Uses the producer-shaped fixture (tests/integration/_reports_fixtures._make_completed_aggregate_run, extended below)."""
    from idraa.services.reports import build_executive_pdf_data
    from tests.integration._reports_fixtures import _make_completed_aggregate_run

    run = await _make_completed_aggregate_run(
        db_session,
        seed_organization,
        scenario_names=["A", "B"],
        threat_community_slugs=["cybercriminals", "nation_state"],
        snapshot=True,
    )
    data = await build_executive_pdf_data(db_session, run, seed_organization)
    assert {g.name for g in data.scenarios_by_threat_community} >= {
        seed_threat_communities["cybercriminals"].name,
        seed_threat_communities["nation_state"].name,
    }
    assert abs(sum(g.share for g in data.scenarios_by_threat_community) - 1.0) < 1e-9
    legacy = await _make_completed_aggregate_run(
        db_session,
        seed_organization,
        scenario_names=["C", "D"],
        threat_community_slugs=["cybercriminals", "nation_state"],
        snapshot=False,
    )
    data2 = await build_executive_pdf_data(db_session, legacy, seed_organization)
    assert [g.name for g in data2.scenarios_by_threat_community] == [NEEDS_REVIEW_LABEL]


def test_section_suppressed_for_single_group_and_has_heading_otherwise() -> None:
    from idraa.services.pdf_report import _draw_threat_community_page
    from tests.unit.test_pdf_report import _canonical_data

    styles = getSampleStyleSheet()
    one = _canonical_data(
        scenarios_by_threat_community=[ThreatCommunityGroup("Hacktivists", [], 0.0, 1.0)]
    )
    assert _draw_threat_community_page(one, styles) == []
    two = _canonical_data(
        scenarios_by_threat_community=[
            ThreatCommunityGroup(
                "Hacktivists", [_r("<b>x</b>", 1.0, ("hacktivists", "Hacktivists"))], 1.0, 0.5
            ),
            ThreatCommunityGroup(NEEDS_REVIEW_LABEL, [], 1.0, 0.5),
        ]
    )
    flow = _draw_threat_community_page(two, styles)
    texts = [f.getPlainText() for f in flow if isinstance(f, Paragraph)]
    assert any("Scenarios by threat community" in t for t in texts) and any(
        "mean basis" in t for t in texts
    )
    assert not any("<b>" in getattr(f, "text", "") for f in flow if isinstance(f, Paragraph))
