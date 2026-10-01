"""Report rendering, disposition parsing and the tally (spec §5)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from scripts.curation_check.flags import CheckResult, Flag
from scripts.curation_check.library import ChangedSince
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
                "scenario-labels:ot:threat_community",
                "ot · threat_community",
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


def test_before_after_numbers_positions_within_control_sub_queues() -> None:  # final review M-2
    text = render(
        META, _results(), top=15, previous={"control-functions": ["control-functions:siem:m"]}
    )
    assert "control-functions:siem:m (now Possibly wrong #1)" in text


def test_changed_entries_distinguishes_errored_from_a_true_no_flag_result() -> None:  # M1
    results = _results()
    results["control-functions"].errored = [("control-functions:missing-ctrl", "boom")]
    results["control-functions"].subjects = ["control-functions:missing-ctrl"]
    # a genuinely-judged, zero-flag subject (overlap's score_overlap returns [] only with an empty
    # choice set) — a real, if rare, case distinct from "errored"
    results["overlap"] = CheckResult(
        check="overlap", items=1, subjects=["overlap:lonely-scenario"], flags=[], errored=[]
    )
    changed = ChangedSince(
        ref_sha="a" * 40,
        merge_base="b" * 40,
        slugs=frozenset({"fraud", "missing-ctrl", "lonely-scenario"}),
        deprecated=frozenset(),
    )
    text = render(META, results, top=15, changed=changed)
    assert "## Changed entries" in text
    section = text.split("## Changed entries", 1)[1]
    control_section = section.split("### Control function audit", 1)[1].split(
        "### Scenario overlap", 1
    )[0]
    assert "fraud · asset_class" in section  # matched flag (key intersects the changed slugs)
    assert "errored — not judged" in control_section and "missing-ctrl" in control_section
    assert "no flag" not in control_section  # M1: an error is never rendered as "no flag"
    overlap_section = section.split("### Scenario overlap", 1)[1]
    assert "lonely-scenario" in overlap_section and "no flag" in overlap_section
    assert "### Coverage gaps" not in section  # N4: gaps is excluded from this view entirely


def test_changed_entries_suppressed_flag_is_not_a_live_candidate() -> None:  # M2
    results = _results()
    changed = ChangedSince(
        ref_sha="a" * 40,
        merge_base="b" * 40,
        slugs=frozenset({"siem"}),
        deprecated=frozenset(),
    )
    text = render(META, results, top=15, changed=changed)
    control_section = text.split("### Control function audit", 1)[1].split(
        "### Scenario overlap", 1
    )[0]
    # siem · Incentives is suppressed (detail suppressed=True, reason="r" in _results()); its
    # finding must carry the suppression suffix, distinguishing it from a live candidate, and sort
    # after siem's live (non-suppressed) rows (Visibility, Monitoring)
    assert "(suppressed: deliberately dropped claim — r)" in control_section
    assert control_section.index("siem · Visibility") < control_section.index("siem · Incentives")
    assert control_section.index("siem · Monitoring") < control_section.index("siem · Incentives")


def test_changed_entries_ranked_flag_cross_references_the_queue_row() -> None:  # M3
    changed = ChangedSince(
        ref_sha="a" * 40, merge_base="b" * 40, slugs=frozenset({"fraud"}), deprecated=frozenset()
    )
    # top=1: "fraud · asset_class" (score 0.95) is the #1 ranked scenario-labels flag
    text = render(META, _results(), top=1, changed=changed)
    section = text.split("## Changed entries", 1)[1]
    scenario_section = section.split("### Scenario label audit", 1)[1].split(
        "### Control function audit", 1
    )[0]
    assert "see Scenario label audit #1" in scenario_section


def test_changed_entries_never_includes_a_gaps_sub_heading() -> None:  # N4
    # "A1" collides with a gaps intake id in _results() — even a coincidental id/slug match must
    # never surface a Coverage-gaps row, since gaps subjects are intake items, not library slugs.
    changed = ChangedSince(
        ref_sha="a" * 40, merge_base="b" * 40, slugs=frozenset({"A1"}), deprecated=frozenset()
    )
    text = render(META, _results(), top=15, changed=changed)
    section = text.split("## Changed entries", 1)[1]
    assert "### Coverage gaps" not in section


def test_changed_entries_deprecated_rows_are_emitted_once_not_per_check() -> None:  # N1
    results = _results()
    changed = ChangedSince(
        ref_sha="a" * 40,
        merge_base="b" * 40,
        slugs=frozenset(),
        deprecated=frozenset({"old-scenario"}),
    )
    text = render(META, results, top=15, changed=changed)
    assert "### Not checked (not published)" in text
    assert text.count("old-scenario") == 1
    section = text.split("## Changed entries", 1)[1]
    per_check = section.split("### Not checked (not published)", 1)[0]
    assert "old-scenario" not in per_check  # never repeated under a per-check sub-heading


def test_changed_section_leaves_the_ranked_queue_rendering_untouched() -> None:  # (iii)
    changed = ChangedSince(
        ref_sha="a" * 40, merge_base="b" * 40, slugs=frozenset({"fraud"}), deprecated=frozenset()
    )
    without = render(META, _results(), top=15)
    with_changed = render(META, _results(), top=15, changed=changed)
    ranked_queue_portion = with_changed.split("## Changed entries", 1)[0]
    assert ranked_queue_portion.rstrip("\n") == without.rstrip("\n")
    assert "## Changed entries" not in without
    assert "## Changed entries" in with_changed


def test_changed_entries_section_is_excluded_from_tally(tmp_path: Path) -> None:  # A-9, (i)
    run = {"model": "m", "campaign": "x", "date": "2026-10-01", "top": 15}
    lines = [
        "# Curation check",
        "",
        "## Changed entries",
        "",
        "Every entry whose seed record differs from merge base `abc123`.",
        "",
        "### Scenario label audit",
        "",
        "| # | Score | Subject | Finding | Disposition | Reason |",
        "|---|---|---|---|---|---|",
        "| 1 | 0.90 | s1 | f | accepted | r |",
        "",
    ]
    d = tmp_path / "r1"
    d.mkdir()
    (d / "report.md").write_text("\n".join(lines))
    (d / "run.json").write_text(json.dumps(run))
    assert tally([d / "report.md"]) == {}


def test_changed_entries_between_ranked_queue_and_before_after_parses_cleanly(
    tmp_path: Path,
) -> None:  # (ii)
    run = {"model": "m", "campaign": "x", "date": "2026-10-01", "top": 15}
    lines = [
        "# Curation check",
        "",
        "## Scenario label audit",
        "",
        "| # | Score | Subject | Finding | Disposition | Reason |",
        "|---|---|---|---|---|---|",
        "| 1 | 0.90 | s1 | f | accepted | r |",
        "",
        "## Changed entries",
        "",
        "Every entry whose seed record differs from merge base `abc123`.",
        "",
        "### Scenario label audit",
        "",
        "| # | Score | Subject | Finding | Disposition | Reason |",
        "|---|---|---|---|---|---|",
        "| 1 | — | s9 | no flag |  |  |",
        "",
        "## Before / after",
        "",
        "- left the queue: `x`",
        "",
    ]
    d = tmp_path / "r1"
    d.mkdir()
    (d / "report.md").write_text("\n".join(lines))
    (d / "run.json").write_text(json.dumps(run))
    counts = tally([d / "report.md"])
    assert counts[("m", "scenario-labels", 15)]["accepted"] == 1
    assert counts[("m", "scenario-labels", 15)]["decided"] == 1


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
        "scenario-labels:ot:threat_community",
    ]
    assert keys["control-functions"] == ["control-functions:siem:v", "control-functions:siem:m"]
