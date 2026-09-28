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
                "curated **people** score 0.05; judge prefers **cash_or_equivalent** (0.95)",
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
                "not labelled **Visibility**; judge's yes-score 0.93",
                {"direction": "missing"},
            ),
            Flag(
                "control-functions",
                "control-functions:siem:m",
                "siem · Monitoring",
                0.92,
                "labelled **Monitoring**; judge's yes-score 0.08",
                {"direction": "wrong"},
            ),
            Flag(
                "control-functions",
                "control-functions:siem:i",
                "siem · Incentives",
                0.97,
                "not labelled **Incentives**; judge's yes-score 0.97",
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
                "judge's none-score 0.85; closest: **Wiper** (0.15)",
                {"closest": "Wiper", "closest_p": 0.15, "closest_tied": ["Wiper"]},
            ),
            Flag(
                "gaps",
                "gaps:A2",
                "A2 · Ransomware (CISA)",
                0.02,
                "judge's none-score 0.02; closest: **Ransomware on EHR** (0.97)",
                {
                    "closest": "Ransomware on EHR",
                    "closest_p": 0.97,
                    "closest_tied": ["Ransomware on EHR"],
                },
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
    assert "Judge's closest match (not reviewed; not evidence of coverage)" in text
    assert "A2 · Ransomware (CISA) → Ransomware on EHR: closest-score 0.97" in text


def test_md_cell_escapes_pipes_newlines_and_html() -> None:
    assert md_cell("a | b\nc <b>") == "a \\| b c &lt;b&gt;"
    assert md_cell("odd `tick") == "odd 'tick"


def test_parse_fails_loudly_on_rows_it_cannot_attribute(tmp_path: Path) -> None:
    edited = _report(
        tmp_path, "edited", {}, {"Scenario label audit (reviewed)": [("a", "accepted")]}
    )
    with pytest.raises(ValueError, match="outside a known section"):
        parse_dispositions(edited.read_text(), str(edited))
    extra = tmp_path / "extra.md"
    extra.write_text("## Scenario label audit\n\n| 1 | 0.90 | a | f | accepted | a | b |\n")
    with pytest.raises(ValueError, match="expected 6 cells, found 7"):
        parse_dispositions(extra.read_text(), str(extra))


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
    assert "1 left the queue (fixed, outranked or renamed), 1 still flagged, 0 new" in text
    assert "left the queue: `scenario-labels:old:asset_class`" in text


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
    run_a1 = {"model": "jev-1.13.0", "campaign": "a", "date": "2026-10-01", "top": 15}
    run_a2 = {"model": "jev-1.13.0", "campaign": "a", "date": "2026-10-20", "top": 15}
    run_b = {"model": "jev-1.13.0", "campaign": "b", "date": "2026-11-01", "top": 15}
    run_new = {"model": "jev-2.0.0", "campaign": "c", "date": "2026-12-01", "top": 15}
    run_wide = {"model": "jev-1.13.0", "campaign": "d", "date": "2026-12-05", "top": 30}
    t = "Scenario label audit"
    paths = [
        _report(tmp_path, "a1", run_a1, {t: [("s1", "accepted"), ("s2", "rejected")]}),
        _report(
            tmp_path, "a2", run_a2, {t: [("s1", ""), ("s2", "accepted")]}
        ),  # blank never erases; later decision wins
        _report(
            tmp_path, "b", run_b, {t: [("s1", "accepted"), ("s3", "deferred"), ("s2", "deferred")]}
        ),
        _report(tmp_path, "c", run_new, {t: [("s9", "accepted")]}),
        _report(tmp_path, "d", run_wide, {t: [("s1", "rejected")]}),
    ]
    counts = tally(paths)
    old = counts[("jev-1.13.0", "scenario-labels", 15)]
    # s1 once across campaigns; s2's later deferral never erases its accepted
    assert (old["accepted"], old["rejected"], old["deferred"], old["decided"]) == (2, 0, 1, 2)
    assert old["verdict"].startswith("not enough decided rows")
    assert counts[("jev-2.0.0", "scenario-labels", 15)]["accepted"] == 1
    assert (
        counts[("jev-1.13.0", "scenario-labels", 30)]["rejected"] == 1
    )  # another queue length is not pooled


def test_tally_verdicts_fire_only_with_enough_rows(tmp_path: Path) -> None:
    t = "Scenario label audit"
    run = {"model": "m", "campaign": "x", "date": "2026-10-01", "top": 12}
    retire = _report(tmp_path, "retire", run, {t: [(f"s{i}", "rejected") for i in range(12)]})
    assert (
        tally([retire])[("m", "scenario-labels", 12)]["verdict"]
        == "shrink --top or retire the check"
    )
    run2 = {"model": "m2", "campaign": "y", "date": "2026-10-01", "top": 30}
    raise_ = _report(tmp_path, "raise", run2, {t: [(f"s{i}", "accepted") for i in range(30)]})
    assert tally([raise_])[("m2", "scenario-labels", 30)]["verdict"] == "raise --top"
    run3 = {"model": "m3", "campaign": "z", "date": "2026-10-01", "top": 15}
    few_low = _report(tmp_path, "few-low", run3, {t: [(f"s{i}", "accepted") for i in range(15)]})
    # 15 decided but only 5 in the lowest third: too few to judge raising --top
    assert tally([few_low])[("m3", "scenario-labels", 15)]["verdict"].startswith(
        "keep (lowest third has 5 of 10"
    )


def test_queue_keys_follow_rank_order_and_sub_queues() -> None:
    keys = queue_keys(_results(), top=2)
    assert keys["scenario-labels"] == [
        "scenario-labels:fraud:asset_class",
        "scenario-labels:ot:threat_actor_type",
    ]
    assert keys["control-functions"] == ["control-functions:siem:v", "control-functions:siem:m"]
