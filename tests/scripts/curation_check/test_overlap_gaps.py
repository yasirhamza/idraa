"""Scenario overlap (spec §4.3) and coverage gaps from an intake list (spec §4.4)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from scripts.curation_check.checks.gaps import load_intake, score_gap
from scripts.curation_check.checks.overlap import merge_pairs, score_overlap
from scripts.curation_check.criteria import NONE_COVERED, NONE_DISTINCT
from scripts.curation_check.library import ScenarioItem


def _scen(slug: str, name: str) -> ScenarioItem:
    return ScenarioItem(
        slug, name, "desc", "published", "denial_of_service", "systems", "cybercriminals"
    )


NAME_TO_SLUG = {"DDoS Extortion": "ddos-extortion", "DDoS Peak": "ddos-peak", "Wiper": "wiper"}


def test_overlap_scores_one_minus_distinct_and_shows_two_matches() -> None:  # M-I6
    item = _scen("ddos-peak", "DDoS Peak")
    answers = {"match": {"DDoS Extortion": 0.20, "Wiper": 0.10, NONE_DISTINCT: 0.70}}
    [flag] = score_overlap(item, answers, name_to_slug=NAME_TO_SLUG)
    assert flag.score == pytest.approx(0.30)
    assert flag.key == "overlap:ddos-extortion:ddos-peak"  # pair slugs sorted
    assert flag.detail["top2"] == [
        ["DDoS Extortion", pytest.approx(0.20)],
        ["Wiper", pytest.approx(0.10)],
    ]
    assert "merge, or sharpen" in flag.finding and "**Wiper**" in flag.finding


def test_overlap_with_only_none_gives_no_flag() -> None:
    assert (
        score_overlap(
            _scen("wiper", "Wiper"), {"match": {NONE_DISTINCT: 1.0}}, name_to_slug=NAME_TO_SLUG
        )
        == []
    )


def test_merge_pairs_keeps_the_stronger_direction() -> None:
    a = score_overlap(
        _scen("ddos-peak", "DDoS Peak"),
        {"match": {"DDoS Extortion": 0.29, NONE_DISTINCT: 0.71}},
        name_to_slug=NAME_TO_SLUG,
    )
    b = score_overlap(
        _scen("ddos-extortion", "DDoS Extortion"),
        {"match": {"DDoS Peak": 0.40, NONE_DISTINCT: 0.60}},
        name_to_slug=NAME_TO_SLUG,
    )
    [merged] = merge_pairs(a + b)
    assert merged.score == pytest.approx(0.40)


def _write_intake(tmp_path: Path, lines: list[str]) -> Path:
    path = tmp_path / "intake.jsonl"
    path.write_text("\n".join(lines) + "\n")
    return path


ROW = {
    "id": "AA24-001",
    "title": "Edge device exploitation",
    "text": "Actors exploit VPN appliances.",
    "source": "CISA",
}


def test_load_intake_reads_valid_rows(tmp_path: Path) -> None:
    items = load_intake(
        _write_intake(tmp_path, [json.dumps(ROW), json.dumps({**ROW, "id": "AA24-002"})])
    )
    assert [i.id for i in items] == ["AA24-001", "AA24-002"]
    assert items[0].state() == {"threat_item_title": ROW["title"], "threat_item_text": ROW["text"]}


def test_load_intake_caps_long_titles(tmp_path: Path) -> None:  # S-I3: titles are published
    [item] = load_intake(_write_intake(tmp_path, [json.dumps({**ROW, "title": "x" * 200})]))
    assert len(item.title) == 80 and item.title.endswith("…")


@pytest.mark.parametrize(
    ("lines", "message"),
    [
        (["{not json"], r"line 1: not valid JSON"),
        (
            [json.dumps({k: v for k, v in ROW.items() if k != "source"})],
            r"line 1: missing or empty 'source'",
        ),
        ([json.dumps({**ROW, "title": "  "})], r"line 1: missing or empty 'title'"),
        ([json.dumps(ROW), json.dumps(ROW)], r"line 2: duplicate id 'AA24-001'"),
    ],
)
def test_load_intake_rejects_malformed_files(
    tmp_path: Path, lines: list[str], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        load_intake(_write_intake(tmp_path, lines))


def test_gap_score_is_the_none_score(tmp_path: Path) -> None:
    [item] = load_intake(_write_intake(tmp_path, [json.dumps(ROW)]))
    [flag] = score_gap(
        item, {"match": {NONE_COVERED: 0.8, "Wiper": 0.2}}, name_to_slug=NAME_TO_SLUG
    )
    assert flag.score == pytest.approx(0.8)
    assert flag.key == "gaps:AA24-001"
    assert flag.detail == {"closest": "Wiper", "closest_p": pytest.approx(0.2)}
