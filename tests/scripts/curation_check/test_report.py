"""Report rendering, disposition parsing and the tally (spec §5)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from scripts.curation_check.flags import CheckResult, Flag
from scripts.curation_check.report import (
    md_cell,
    parse_dispositions,
    queue_keys,
    render,
    tally,
    wilson,
)

META = {
    "campaign": "pilot",
    "date": "2026-10-01",
    "judge": "replay",
    "model": "jev-1.13.0",
    "git_commit": "0123456789abcdef",
}


def _results() -> dict[str, CheckResult]:
    labels = CheckResult(
        check="scenario-labels",
        items=2,
        flags=[
            Flag(
                "scenario-labels",
                "scenario-labels:fraud:asset_class",
                "fraud · asset_class",
                0.95,
                "curated **people** (0.05); judge prefers **cash_or_equivalent** (0.95)",
            ),
            Flag(
                "scenario-labels",
                "scenario-labels:ot:threat_actor_type",
                "ot · threat_actor_type",
                0.30,
                "description | with pipe\nand <b>html</b>",
            ),
        ],
        errored=[("scenario-labels:broken", "answers missing for ['asset_class']")],
    )
    controls = CheckResult(
        check="control-functions",
        items=1,
        flags=[
            Flag(
                "control-functions",
                "control-functions:siem:v",
                "siem · Visibility",
                0.93,
                "not labelled **Visibility**",
                {"direction": "missing"},
            ),
            Flag(
                "control-functions",
                "control-functions:siem:m",
                "siem · Monitoring",
                0.92,
                "labelled **Monitoring**",
                {"direction": "wrong"},
            ),
            Flag(
                "control-functions",
                "control-functions:siem:i",
                "siem · Incentives",
                0.97,
                "dropped",
                {"direction": "missing", "suppressed": True, "reason": "r"},
            ),
        ],
    )
    gaps = CheckResult(
        check="gaps",
        items=2,
        flags=[
            Flag(
                "gaps",
                "gaps:A1",
                "A1 · Edge exploit (CISA)",
                0.85,
                "judge's none-score 0.85",
                {"closest": "Wiper", "closest_p": 0.15},
            ),
            Flag(
                "gaps",
                "gaps:A2",
                "A2 · Ransomware (CISA)",
                0.02,
                "covered",
                {"closest": "Ransomware on EHR", "closest_p": 0.97},
            ),
        ],
    )
    return {"scenario-labels": labels, "control-functions": controls, "gaps": gaps}


def test_render_matches_snapshot(snapshot) -> None:  # type: ignore[no-untyped-def]
    assert render(META, _results(), top=15) == snapshot


def test_render_states_scores_are_not_probabilities_and_hides_suppressed() -> None:  # M-I4, M-I3
    text = render(META, _results(), top=15)
    assert "not calibrated probabilities" in text
    assert "### Possibly missing" in text and "### Possibly wrong" in text
    assert "1 suppressed as deliberately dropped claims" in text
    tables = [line for line in text.splitlines() if line.startswith("|")]
    assert not any("siem · Incentives" in line for line in tables)
    assert "<details><summary>Suppressed" in text and "siem · Incentives: yes-score 0.97" in text
    assert "Judge's closest match (not reviewed)" in text


def test_md_cell_escapes_pipes_newlines_and_html() -> None:
    assert md_cell("a | b\nc <b>") == "a \\| b c &lt;b&gt;"


def test_rendered_tables_round_trip_through_the_parser() -> None:
    parsed = parse_dispositions(render(META, _results(), top=15), "r.md")
    assert [r[0] for r in parsed["scenario-labels"]] == [1, 2]
    assert [r[1] for r in parsed["control-functions/missing"]] == ["siem · Visibility"]
    assert [r[1] for r in parsed["control-functions/wrong"]] == ["siem · Monitoring"]
    assert all(r[2] == "" for rows in parsed.values() for r in rows)


def test_before_after_section_classifies_flags() -> None:
    previous = {
        "scenario-labels": ["scenario-labels:fraud:asset_class", "scenario-labels:old:asset_class"]
    }
    text = render(META, _results(), top=1, previous=previous)
    assert "1 gone, 1 still flagged, 0 new" in text
    assert "gone: `scenario-labels:old:asset_class`" in text


def _report(
    tmp_path: Path, folder: str, run: dict[str, str], sections: dict[str, list[tuple[str, str]]]
) -> Path:
    """Write a minimal dispositioned report + run.json. sections: title -> [(subject, disposition)]."""
    lines = ["# Curation check", ""]
    for title, rows in sections.items():
        lines += [
            f"## {title}",
            "",
            "| # | Score | Subject | Finding | Disposition | Reason |",
            "|---|---|---|---|---|---|",
        ]
        lines += [f"| {i} | 0.90 | {s} | f | {d} | r |" for i, (s, d) in enumerate(rows, 1)]
        lines.append("")
    d = tmp_path / folder
    d.mkdir()
    (d / "report.md").write_text("\n".join(lines))
    (d / "run.json").write_text(json.dumps(run))
    return d / "report.md"


def test_parse_normalises_case_and_space_and_keeps_blanks(tmp_path: Path) -> None:
    p = _report(
        tmp_path,
        "r1",
        {},
        {"Scenario label audit": [("a", " Accepted "), ("b", "REJECTED"), ("c", "")]},
    )
    rows = parse_dispositions(p.read_text(), str(p))["scenario-labels"]
    assert [r[2] for r in rows] == ["accepted", "rejected", ""]


def test_parse_rejects_unknown_dispositions_with_location(tmp_path: Path) -> None:
    p = _report(tmp_path, "r1", {}, {"Scenario label audit": [("a", "maybe")]})
    with pytest.raises(ValueError, match=r"r1/report.md:\d+: unknown disposition 'maybe'"):
        parse_dispositions(p.read_text(), str(p))


def test_wilson_matches_hand_values() -> None:
    assert wilson(0, 12)[1] == pytest.approx(0.1840, abs=5e-4)
    assert wilson(5, 5)[0] == pytest.approx(0.6488, abs=5e-4)
    assert wilson(0, 0) == (0.0, 1.0)


def test_tally_pools_campaigns_groups_by_model_and_dedupes(tmp_path: Path) -> None:  # M-I5
    run_a1 = {"model": "jev-1.13.0", "campaign": "a", "date": "2026-10-01"}
    run_a2 = {"model": "jev-1.13.0", "campaign": "a", "date": "2026-10-20"}
    run_b = {"model": "jev-1.13.0", "campaign": "b", "date": "2026-11-01"}
    run_new = {"model": "jev-2.0.0", "campaign": "c", "date": "2026-12-01"}
    t = "Scenario label audit"
    paths = [
        _report(tmp_path, "a1", run_a1, {t: [("s1", "accepted"), ("s2", "rejected")]}),
        _report(
            tmp_path, "a2", run_a2, {t: [("s1", ""), ("s2", "accepted")]}
        ),  # blank never erases; later decision wins
        _report(tmp_path, "b", run_b, {t: [("s1", "accepted"), ("s3", "deferred")]}),
        _report(tmp_path, "c", run_new, {t: [("s9", "accepted")]}),
    ]
    counts = tally(paths)
    old = counts[("jev-1.13.0", "scenario-labels")]
    assert (old["accepted"], old["rejected"], old["deferred"], old["decided"]) == (
        2,
        0,
        1,
        2,
    )  # s1 once across campaigns
    assert old["verdict"].startswith("not enough decided rows")
    assert counts[("jev-2.0.0", "scenario-labels")]["accepted"] == 1


def test_tally_verdicts_fire_only_with_enough_rows(tmp_path: Path) -> None:
    t = "Scenario label audit"
    run = {"model": "m", "campaign": "x", "date": "2026-10-01"}
    retire = _report(tmp_path, "retire", run, {t: [(f"s{i}", "rejected") for i in range(12)]})
    assert (
        tally([retire])[("m", "scenario-labels")]["verdict"] == "shrink --top or retire the check"
    )
    run2 = {"model": "m2", "campaign": "y", "date": "2026-10-01"}
    raise_ = _report(tmp_path, "raise", run2, {t: [(f"s{i}", "accepted") for i in range(15)]})
    assert tally([raise_])[("m2", "scenario-labels")]["verdict"] == "raise --top"


def test_queue_keys_follow_rank_order_and_sub_queues() -> None:
    keys = queue_keys(_results(), top=2)
    assert keys["scenario-labels"] == [
        "scenario-labels:fraud:asset_class",
        "scenario-labels:ot:threat_actor_type",
    ]
    assert keys["control-functions"] == ["control-functions:siem:v", "control-functions:siem:m"]
