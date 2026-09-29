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
    assert "merge, sharpen, or keep" in flag.finding and "**Wiper**" in flag.finding
    assert flag.finding.startswith(
        "from ddos-peak: judge's top score is 'distinct' (0.70)"
    )  # never "reads like"


def test_overlap_tie_flags_every_tied_partner() -> None:  # Task-5 review
    item = _scen("wiper", "Wiper")
    answers = {"match": {"DDoS Extortion": 0.40, "DDoS Peak": 0.40, NONE_DISTINCT: 0.20}}
    flags = score_overlap(item, answers, name_to_slug=NAME_TO_SLUG)
    assert {f.key for f in flags} == {"overlap:ddos-extortion:wiper", "overlap:ddos-peak:wiper"}
    assert all("**DDoS Extortion**, **DDoS Peak** (0.40, tied)" in f.finding for f in flags)


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
    assert merged.detail["reverse_p"] == pytest.approx(0.29)
    assert (
        "; reverse: ddos-peak's score for this pair 0.29: merge, sharpen, or keep" in merged.finding
    )


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
        (
            [json.dumps(ROW), json.dumps({**ROW, "id": " AA24-001 "})],
            r"line 2: duplicate id 'AA24-001'",
        ),
        ([json.dumps({**ROW, "id": "x" * 65})], r"line 1: id must be 1-64"),
        ([json.dumps({**ROW, "id": "a b"})], r"line 1: id must be 1-64"),
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
    assert flag.detail["closest"] == "Wiper" and flag.detail["closest_p"] == pytest.approx(0.2)
    assert flag.detail["source"] == "CISA"


def test_gap_tie_names_every_tied_entry(tmp_path: Path) -> None:  # Task-5 review
    [item] = load_intake(_write_intake(tmp_path, [json.dumps(ROW)]))
    [flag] = score_gap(
        item,
        {"match": {NONE_COVERED: 0.2, "Wiper": 0.4, "DDoS Peak": 0.4}},
        name_to_slug=NAME_TO_SLUG,
    )
    assert "closest (tied): **DDoS Peak**, **Wiper** (0.40)" in flag.finding
    assert flag.detail["closest_tied"] == ["DDoS Peak", "Wiper"]


def test_load_intake_missing_file_is_a_value_error(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not readable"):
        load_intake(tmp_path / "missing.jsonl")


def _flat(n: int, none_key: str, none_p: float) -> tuple[dict[str, float], dict[str, str]]:
    names = {f"Scenario {i:03d}": f"scenario-{i:03d}" for i in range(n)}
    rest = (1.0 - none_p) / n
    return {**dict.fromkeys(names, rest), none_key: none_p}, names


def test_overlap_thin_spread_names_nobody_at_library_scale() -> None:  # final review I-1
    dist, names = _flat(101, NONE_DISTINCT, 0.95)
    [flag] = score_overlap(_scen("wiper", "Wiper"), {"match": dist}, name_to_slug=names)
    assert flag.key == "overlap:wiper" and flag.score == pytest.approx(0.05)
    assert "no single scenario scored 0.05 or more" in flag.finding and len(flag.finding) < 200


def test_overlap_caps_named_ties() -> None:  # final review I-1
    dist, names = _flat(60, NONE_DISTINCT, 0.40)  # 60 x 0.01 each
    dist.update(
        {"Scenario 000": 0.1, "Scenario 001": 0.1, "Scenario 002": 0.1, "Scenario 003": 0.1}
    )
    total = sum(dist.values())
    dist = {k: v / total for k, v in dist.items()}
    flags = score_overlap(_scen("wiper", "Wiper"), {"match": dist}, name_to_slug=names)
    assert len(flags) == 3 and all("+1 more" in f.finding for f in flags)


def test_gap_thin_spread_names_nobody(tmp_path: Path) -> None:  # final review I-1
    [item] = load_intake(_write_intake(tmp_path, [json.dumps(ROW)]))
    dist, names = _flat(101, NONE_COVERED, 0.97)
    [flag] = score_gap(item, {"match": dist}, name_to_slug=names)
    assert flag.finding == "judge's none-score 0.97; closest: none scored 0.05 or more"
    assert flag.detail["closest"] == "(none)" and flag.detail["closest_tied"] == []
