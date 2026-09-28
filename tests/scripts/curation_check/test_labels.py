"""Ranking and the scenario / control label audit scoring (spec §4.1, §4.2)."""

from __future__ import annotations

import pytest
from scripts.curation_check.checks.labels import score_control, score_scenario
from scripts.curation_check.criteria import NONE_FITS
from scripts.curation_check.flags import CheckResult, Flag, rank
from scripts.curation_check.library import ControlItem, ScenarioItem

SCEN = ScenarioItem(
    "fraud", "Fraud", "desc", "published", "social_engineering", "people", "cybercriminals"
)


def _flag(subject: str, score: float, **detail: object) -> Flag:
    return Flag(
        check="c", key=subject, subject=subject, score=score, finding="f", detail=dict(detail)
    )


def test_rank_orders_by_score_then_subject_and_counts_near_misses() -> None:
    flags = [_flag("b", 0.9), _flag("a", 0.9), _flag("c", 0.5), _flag("d", 0.45), _flag("e", 0.1)]
    queue, below = rank(flags, top=3, window=0.10)
    assert [f.subject for f in queue] == ["a", "b", "c"]
    assert below == 1  # "d" is within 0.10 of the last kept score (0.5); "e" is not


def test_rank_skips_suppressed_flags_and_handles_empty_input() -> None:
    queue, _ = rank([_flag("a", 0.9, suppressed=True), _flag("b", 0.2)], top=5)
    assert [f.subject for f in queue] == ["b"]
    assert rank([], top=15) == ([], 0)
    assert CheckResult("c", flags=[_flag("a", 0.9, suppressed=True)]).suppressed == 1


def test_scenario_flags_one_per_field_scored_by_disagreement() -> None:
    answers = {
        "threat_event_type": {"social_engineering": 0.9, "malware": 0.1},
        "asset_class": {"cash_or_equivalent": 0.95, "people": 0.05},
        "threat_actor_type": {NONE_FITS: 0.8, "cybercriminals": 0.2},
    }
    flags = {f.key: f for f in score_scenario(SCEN, answers)}
    assert set(flags) == {
        "scenario-labels:fraud:threat_event_type",
        "scenario-labels:fraud:asset_class",
        "scenario-labels:fraud:threat_actor_type",
    }
    assert flags["scenario-labels:fraud:threat_event_type"].score == pytest.approx(0.1)
    assert flags["scenario-labels:fraud:asset_class"].score == pytest.approx(0.95)
    assert "prefers **cash_or_equivalent**" in flags["scenario-labels:fraud:asset_class"].finding
    assert "not enough information" in flags["scenario-labels:fraud:threat_actor_type"].finding
    assert "next: **malware** 0.10" in flags["scenario-labels:fraud:threat_event_type"].finding


def test_scenario_ties_never_claim_a_preference() -> None:  # Task-4 review
    answers = {
        "threat_event_type": {"malware": 0.5, "social_engineering": 0.5},
        "asset_class": {"people": 1.0},
        "threat_actor_type": {NONE_FITS: 0.5, "cybercriminals": 0.5},
    }
    flags = score_scenario(SCEN, answers)
    assert all("agrees with curated" in f.finding for f in flags)
    assert "next:" not in next(f for f in flags if f.key.endswith(":asset_class")).finding


def test_scenario_no_named_type_is_reported_distinctly() -> None:  # M-N1
    answers = {
        "threat_event_type": {"miscellaneous": 0.8, "social_engineering": 0.2},
        "asset_class": {"people": 1.0},
        "threat_actor_type": {"cybercriminals": 1.0},
    }
    flags = {f.key: f for f in score_scenario(SCEN, answers)}
    assert "no named type fits" in flags["scenario-labels:fraud:threat_event_type"].finding


def test_scenario_missing_curated_option_scores_as_full_disagreement() -> None:
    answers = {
        f: {"malware": 1.0} for f in ("threat_event_type", "asset_class", "threat_actor_type")
    }
    assert all(f.score == pytest.approx(1.0) for f in score_scenario(SCEN, answers))


def test_control_flags_carry_direction_and_suppress_dropped_claims() -> None:  # M-I3
    item = ControlItem(
        "siem",
        "SIEM",
        "d",
        "published",
        frozenset({"lec_det_monitoring"}),
        dropped={"dsc_prev_incentives": "not groundable"},
    )
    labels = {
        "lec_det_monitoring": "Monitoring",
        "lec_det_visibility": "Visibility",
        "dsc_prev_incentives": "Incentives",
    }
    answers = {
        "fn:lec_det_monitoring": 0.08,
        "fn:lec_det_visibility": 0.93,
        "fn:dsc_prev_incentives": 0.97,
    }
    flags = {f.key: f for f in score_control(item, answers, labels=labels)}
    wrong = flags["control-functions:siem:lec_det_monitoring"]
    missing = flags["control-functions:siem:lec_det_visibility"]
    dropped = flags["control-functions:siem:dsc_prev_incentives"]
    assert wrong.detail["direction"] == "wrong" and wrong.score == pytest.approx(0.92)
    assert missing.detail["direction"] == "missing" and missing.score == pytest.approx(0.93)
    assert "yes-score" in missing.finding  # M-I4: scores, not "the judge says yes"
    assert all("possibly" not in f.finding and "only" not in f.finding for f in (wrong, missing))
    assert dropped.detail["suppressed"] is True and dropped.detail["reason"] == "not groundable"
