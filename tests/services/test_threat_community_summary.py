from __future__ import annotations

import uuid

from idraa.models.threat_community import canonical_threat_community_id
from idraa.services.threat_community_summary import build_threat_community_summary
from idraa.threat_community_provenance import NEEDS_REVIEW_LABEL, NEEDS_REVIEW_SLUG

_ORG = uuid.uuid4()


class _Run:
    def __init__(self, per_scenario, snapshot):
        self.simulation_results = {"per_scenario": per_scenario}
        self.scenario_inputs_snapshot = snapshot


def _snap(sid, slug, name, prov="assigned"):
    tc = (
        {"id": str(canonical_threat_community_id(slug)), "version": 1, "slug": slug, "name": name}
        if slug
        else None
    )
    return {
        "scenario_id": sid,
        "scenario_name": "s",
        "threat_community": tc,
        "threat_community_provenance": prov,
    }


def _ps(sid, ale):
    return {"scenario_id": sid, "residual_risk": {"annualized_loss_expectancy": ale}}


async def test_rows_from_snapshot_with_needs_review_row_summing_to_one(
    db_session, seed_threat_communities
) -> None:
    a, b, c, d, e = (str(uuid.uuid4()) for _ in range(5))
    run = _Run(
        [_ps(a, 300.0), _ps(b, 100.0), _ps(c, 600.0), _ps(d, 1000.0), _ps(e, 250.0)],
        {
            "scenarios": [
                _snap(a, "cybercriminals", "Cybercriminals"),
                _snap(b, "cybercriminals", "Cybercriminals"),
                _snap(c, "nation_state", "Nation-state"),
                _snap(d, "privileged_insider", "Privileged insider", "migrated_split_default"),
                # M11-N1: 'migrated' is non-review -- this scenario's ALE must
                # attribute to hacktivists, NOT pool into the needs-review row.
                _snap(e, "hacktivists", "Hacktivists", "migrated"),
            ]
        },
    )
    rows = await build_threat_community_summary(
        db_session,
        organization_id=_ORG,
        latest_aggregate=run,
        sector_entries=[],
        pinned_library_ids=[],
    )
    by = {r.slug: r for r in rows}
    assert (
        len(rows) == 10
        and rows[-1].slug == NEEDS_REVIEW_SLUG
        and rows[-1].name == NEEDS_REVIEW_LABEL
    )
    assert by["cybercriminals"].scenario_count == 2 and by["cybercriminals"].residual_ale == 400.0
    assert (
        by[NEEDS_REVIEW_SLUG].residual_ale == 1000.0
        and by["privileged_insider"].scenario_count == 0
    )
    assert by["hacktivists"].scenario_count == 1 and by["hacktivists"].residual_ale == 250.0
    assert abs(sum(r.residual_ale_share for r in rows) - 1.0) < 1e-9


async def test_summary_pre_p1_snapshot_and_stale_slug_shares_sum_to_one(
    db_session, seed_threat_communities
) -> None:
    """Review Focus #5 (dashboard half): pre-P1 rows -> needs-review; a renamed/retired slug keeps its own row;
    malformed and non-finite rows are skipped."""
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    run = _Run(
        [
            _ps(a, 50.0),
            _ps(b, 150.0),
            {"scenario_id": "not-a-uuid", "residual_risk": {"annualized_loss_expectancy": 1.0}},
            {
                "scenario_id": str(uuid.uuid4()),
                "residual_risk": {"annualized_loss_expectancy": float("nan")},
            },
        ],
        {
            "scenarios": [
                {"scenario_id": a, "scenario_name": "legacy"},
                _snap(b, "retired_slug", "Old Name v1"),
            ]
        },
    )
    rows = await build_threat_community_summary(
        db_session,
        organization_id=_ORG,
        latest_aggregate=run,
        sector_entries=[],
        pinned_library_ids=[],
    )
    by = {r.slug: r for r in rows}
    assert (
        by[NEEDS_REVIEW_SLUG].residual_ale == 50.0
        and by["retired_slug"].name == "Old Name v1"
        and by["retired_slug"].residual_ale == 150.0
    )
    assert abs(sum(r.residual_ale_share for r in rows) - 1.0) < 1e-9


async def test_two_slugs_sharing_a_name_stay_separate_and_rows_show_the_smallest_snapshot_name(
    db_session, seed_threat_communities
) -> None:
    a, b, c = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    run = _Run(
        [_ps(a, 10.0), _ps(b, 30.0), _ps(c, 5.0)],
        {
            "scenarios": [
                _snap(a, "cybercriminals", "Same Name"),
                _snap(b, "nation_state", "Zeta"),
                _snap(c, "nation_state", "Alpha"),
            ]
        },
    )
    rows = await build_threat_community_summary(
        db_session,
        organization_id=_ORG,
        latest_aggregate=run,
        sector_entries=[],
        pinned_library_ids=[],
    )
    by = {r.slug: r for r in rows}
    assert by["cybercriminals"].residual_ale == 10.0 and by["nation_state"].residual_ale == 35.0
    assert (
        by["cybercriminals"].name == "Same Name" and by["nation_state"].name == "Alpha"
    )  # smallest, not first seen
    assert by["hacktivists"].name == seed_threat_communities["hacktivists"].name


async def test_no_run_counts_only_and_coverage_from_sector_entries(
    db_session, seed_threat_communities
) -> None:
    rows = await build_threat_community_summary(
        db_session,
        organization_id=_ORG,
        latest_aggregate=None,
        sector_entries=[],
        pinned_library_ids=[],
    )
    assert (
        all(r.residual_ale == 0.0 and r.residual_ale_share == 0.0 for r in rows)
        and rows[-1].slug == NEEDS_REVIEW_SLUG
    )
