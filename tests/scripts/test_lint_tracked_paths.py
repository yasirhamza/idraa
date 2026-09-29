"""Tracked-path guard: curation run folders and intake files never reach the public repo (S-I4)."""

from __future__ import annotations

from scripts.lint_tracked_paths import find_offenders


def test_curation_runs_are_never_tracked() -> None:
    assert find_offenders(["curation-runs/2026-10-01-pilot/report.md"]) == [
        "curation-runs/2026-10-01-pilot/report.md"
    ]


def test_only_report_files_are_allowed_under_docs_curation() -> None:
    folder = "docs/curation/2026-10-01-pilot/"
    tracked = [
        folder + n
        for n in ("report.md", "run.json", "responses.jsonl", "intake.jsonl", "notes.txt")
    ]
    assert find_offenders(tracked) == [folder + "intake.jsonl", folder + "notes.txt"]


def test_existing_rules_still_apply() -> None:
    assert find_offenders([".env", ".env.example", "src/app.py", "docs/superpowers/x.md"]) == [
        ".env",
        "docs/superpowers/x.md",
    ]
