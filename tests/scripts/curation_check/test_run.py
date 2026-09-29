"""Orchestration: jobs, error isolation, run folder, replay round-trip, refusals (spec §5, §7)."""

from __future__ import annotations

import io
import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from scripts.curation_check.__main__ import main
from scripts.curation_check.checks.gaps import load_intake
from scripts.curation_check.criteria import load_criteria
from scripts.curation_check.judge import Recorder
from scripts.curation_check.library import load_controls, load_scenarios
from scripts.curation_check.run import build_jobs, run_check

from tests.scripts.curation_check.fakes import FakeJudge

FIXTURES = Path(__file__).parent / "fixtures"
ROOT = FIXTURES / "root"
INTAKE = FIXTURES / "intake.jsonl"
CHECKS = ("scenario-labels", "control-functions", "overlap", "gaps")


def _jobs(root: Path = ROOT) -> dict[str, Any]:
    kw: dict[str, Any] = {
        "scenarios": load_scenarios(root),
        "controls": load_controls(root),
        "criteria": load_criteria(),
        "intake": load_intake(INTAKE),
    }
    return {c: build_jobs(c, **kw) for c in CHECKS}


def test_build_jobs_covers_every_item_and_defines_same_risk() -> None:
    jobs = _jobs()
    assert [len(jobs[c]) for c in CHECKS] == [3, 3, 3, 2]
    overlap_q = jobs["overlap"][0].questions["match"]
    assert "DDoS Extortion" not in overlap_q["criteria"]  # self excluded
    assert "FAIR scenario scope" in overlap_q["instructions"]["definition"]


def test_build_jobs_never_includes_deprecated_entries(tmp_path: Path) -> None:  # S-11
    root = tmp_path / "root"
    shutil.copytree(ROOT, root)
    scenario_path = root / "data" / "seed_library_entries.json"
    scenarios_raw = json.loads(scenario_path.read_text())
    published_scenario = scenarios_raw[0]  # DDoS Extortion
    dup_name = f"{published_scenario['name']} (deprecated duplicate)"
    dup_slug = f"{published_scenario['slug']}-deprecated-dup"
    scenarios_raw.append(
        {**published_scenario, "slug": dup_slug, "name": dup_name, "status": "deprecated"}
    )
    scenario_path.write_text(json.dumps(scenarios_raw))

    control_path = root / "data" / "seed_control_library_entries.json"
    control_doc = json.loads(control_path.read_text())
    published_control = control_doc["entries"][0]  # siem
    dup_control_slug = f"{published_control['slug']}-deprecated-dup"
    control_doc["entries"].append(
        {**published_control, "slug": dup_control_slug, "status": "deprecated"}
    )
    control_path.write_text(json.dumps(control_doc))

    scenarios = load_scenarios(root)
    controls = load_controls(root)
    # load_scenarios/load_controls keep every entry regardless of status (seed hash stays whole-file)
    assert any(s.slug == dup_slug and s.status == "deprecated" for s in scenarios)
    assert any(c.slug == dup_control_slug and c.status == "deprecated" for c in controls)

    kw: dict[str, Any] = {
        "scenarios": scenarios,
        "controls": controls,
        "criteria": load_criteria(),
        "intake": load_intake(INTAKE),
    }
    for check in ("scenario-labels", "overlap", "gaps"):
        jobs = build_jobs(check, **kw)
        assert not any(dup_slug in job.item_key for job in jobs)
        for job in jobs:
            match_q = job.questions.get("match")
            if match_q is not None:
                assert dup_name not in match_q["criteria"]

    control_jobs = build_jobs("control-functions", **kw)
    assert not any(dup_control_slug in job.item_key for job in control_jobs)


def test_malformed_answers_error_one_item_and_the_run_continues(tmp_path: Path) -> None:
    jobs = _jobs()["scenario-labels"]
    judge = FakeJudge(broken=frozenset({jobs[0].item_key}))
    res = run_check("scenario-labels", jobs, judge, Recorder(tmp_path / "r.jsonl"))
    assert [k for k, _ in res.errored] == [jobs[0].item_key]
    assert res.items == 3 and len(res.flags) == 6  # two healthy scenarios x three fields
    lines = (tmp_path / "r.jsonl").read_text().splitlines()
    assert len(lines) == 3 and "error" in json.loads(lines[0])


def _record_all(path: Path, root: Path = ROOT) -> None:
    recorder = Recorder(path)
    for check, jobs in _jobs(root).items():
        run_check(check, jobs, FakeJudge(), recorder)


def _replay(recording: Path, out: Path, *extra: str, root: Path = ROOT) -> int:
    return main(
        [
            "all",
            "--judge",
            "replay",
            "--replay",
            str(recording),
            "--root",
            str(root),
            "--campaign",
            "test",
            "--out",
            str(out),
            *extra,
        ]
    )


def test_replay_run_writes_the_run_folder(tmp_path: Path, snapshot) -> None:  # type: ignore[no-untyped-def]
    recording = tmp_path / "rec" / "responses.jsonl"
    _record_all(recording)
    out = tmp_path / "run"
    assert _replay(recording, out, "--intake", str(INTAKE), "--top", "5") == 0
    run = json.loads((out / "run.json").read_text())
    assert run["judge"] == "replay" and run["model"] == "fake-1" and run["top"] == 5
    assert set(run["checks"]) == set(CHECKS) and run["failed_checks"] == []
    assert all(run["checks"][c]["errored_items"] == [] for c in CHECKS)  # SC-I4(a)
    assert set(run["seed_sha256"]) and len(run["criteria_sha256"]) == 64
    assert set(run["queues"]) == set(CHECKS)
    assert len((out / "responses.jsonl").read_text().splitlines()) == 11
    report = (out / "report.md").read_text()
    assert (
        "\n".join(report.splitlines()[3:]) == snapshot
    )  # SC-N1: exact report, dated header lines stripped


def test_refuses_to_overwrite_an_existing_report(tmp_path: Path) -> None:  # SC-I4(b)
    recording = tmp_path / "rec" / "responses.jsonl"
    _record_all(recording)
    out = tmp_path / "run"
    assert _replay(recording, out) == 0
    with pytest.raises(SystemExit) as exc:
        _replay(recording, out)
    assert exc.value.code == 2
    assert _replay(recording, out, "--force") == 0


def test_replay_refuses_a_stale_recording(tmp_path: Path) -> None:
    recording = tmp_path / "rec" / "responses.jsonl"
    _record_all(recording)
    changed = tmp_path / "root"
    shutil.copytree(ROOT, changed)
    seed = changed / "data" / "seed_library_entries.json"
    seed.write_text(seed.read_text().replace("demand a ransom", "demand payment"))
    assert _replay(recording, tmp_path / "run", root=changed) == 3


def test_all_without_intake_skips_gaps(tmp_path: Path) -> None:
    recording = tmp_path / "rec" / "responses.jsonl"
    _record_all(recording)
    assert _replay(recording, tmp_path / "run") == 0
    run = json.loads((tmp_path / "run" / "run.json").read_text())
    assert "gaps" not in run["checks"]
    assert "gaps skipped: no --intake" in run["warnings"]


def test_a_missing_recording_is_fatal(tmp_path: Path) -> None:
    assert _replay(tmp_path / "missing.jsonl", tmp_path / "run") == 3


def test_refuses_an_intake_file_inside_the_repo_that_git_does_not_ignore(
    tmp_path: Path,
) -> None:  # S-I4
    root = tmp_path / "root"
    shutil.copytree(ROOT, root)
    shutil.copy(INTAKE, root / "intake.jsonl")  # tmp root is not a git repo, so nothing ignores it
    with pytest.raises(SystemExit) as exc:
        main(
            [
                "gaps",
                "--judge",
                "replay",
                "--replay",
                str(tmp_path / "x.jsonl"),
                "--root",
                str(root),
                "--campaign",
                "test",
                "--out",
                str(tmp_path / "run"),
                "--intake",
                str(root / "intake.jsonl"),
            ]
        )
    assert exc.value.code == 2


def test_live_runs_need_the_key_on_stdin(tmp_path: Path) -> None:  # S-I1
    with pytest.raises(SystemExit) as exc:
        main(
            ["labels", "--root", str(ROOT), "--campaign", "test", "--out", str(tmp_path / "run")],
            stdin=io.StringIO(""),
        )
    assert exc.value.code == 2


def test_tally_with_no_reports_says_so(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    assert main(["tally", "--root", str(tmp_path)]) == 0
    assert "No committed reports" in capsys.readouterr().out


def test_tally_reports_an_unparseable_report_without_a_traceback(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    d = tmp_path / "docs" / "curation" / "x"
    d.mkdir(parents=True)
    (d / "report.md").write_text("## Renamed section\n\n| 1 | 0.90 | a | f | accepted | r |\n")
    assert main(["tally", "--root", str(tmp_path)]) == 3
    assert "outside a known section" in capsys.readouterr().err


def test_compare_to_without_a_run_is_a_usage_error_before_any_judging(tmp_path: Path) -> None:
    recording = tmp_path / "rec" / "responses.jsonl"
    _record_all(recording)
    with pytest.raises(SystemExit) as exc:
        _replay(recording, tmp_path / "run", "--compare-to", str(tmp_path / "nowhere"))
    assert exc.value.code == 2
    assert not (tmp_path / "run" / "responses.jsonl").exists()
