"""changed_subjects: git-derived changed-entry detection for --changed-since (spec §4.5.2).

Every scratch repo is built through a `_scratch_env()` that drops GIT_* variables (a git hook, such
as the pre-push gate this suite itself may run under, can leak GIT_DIR/GIT_INDEX_FILE that would
point these git calls at the real repository instead of the scratch one); an autouse fixture also
scrubs GIT_* from the test process itself, belt-and-suspenders (a previous leak corrupted the real
repo)."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest
from scripts.curation_check.library import (
    CONTROL_FILE,
    SCENARIO_FILES,
    _git_env,
    changed_subjects,
)

GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com"]


@pytest.fixture(autouse=True)
def _no_inherited_git_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in list(os.environ):
        if k.startswith("GIT_"):
            monkeypatch.delenv(k, raising=False)


def _scratch_env() -> dict[str, str]:
    """Mirrors the production `_git_env()` for the test's OWN git calls, kept separate so a bug in
    one is never masked by the other."""
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def _run_git(root: Path, *args: str) -> None:
    subprocess.run([*GIT, *args], cwd=root, check=True, env=_scratch_env())


def _rev_parse(root: Path, ref: str) -> str:
    return subprocess.run(
        [*GIT, "rev-parse", ref],
        cwd=root,
        check=True,
        env=_scratch_env(),
        capture_output=True,
        text=True,
    ).stdout.strip()


def _scenario(slug: str, name: str, status: str = "published") -> dict[str, Any]:
    return {
        "slug": slug,
        "name": name,
        "status": status,
        "description": f"{name} description.",
        "threat_event_type": "ransomware",
        "asset_class": "data",
        "threat_actor_type": "cybercriminals",
    }


def _control(slug: str, name: str, status: str = "published") -> dict[str, Any]:
    return {
        "slug": slug,
        "name": name,
        "status": status,
        "description": f"{name} description.",
        "assignments": [{"sub_function": "lec_det_monitoring"}],
    }


def _write_seed(
    root: Path,
    scenarios: list[dict[str, Any]],
    extension: list[dict[str, Any]],
    controls: list[dict[str, Any]],
    meta: dict[str, Any] | None = None,
) -> None:
    (root / "data").mkdir(parents=True, exist_ok=True)
    (root / SCENARIO_FILES[0]).write_text(json.dumps(scenarios, indent=2) + "\n", encoding="utf-8")
    (root / SCENARIO_FILES[1]).write_text(json.dumps(extension, indent=2) + "\n", encoding="utf-8")
    (root / CONTROL_FILE).write_text(
        json.dumps({"_meta": meta or {}, "entries": controls}, indent=2) + "\n", encoding="utf-8"
    )


def _base3() -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    scenarios = [_scenario("s1", "S1"), _scenario("s2", "S2"), _scenario("s3", "S3")]
    extension = [_scenario("e1", "E1"), _scenario("e2", "E2"), _scenario("e3", "E3")]
    controls = [_control("c1", "C1"), _control("c2", "C2"), _control("c3", "C3")]
    return scenarios, extension, controls


def _init_repo(
    root: Path,
    *,
    scenarios: list[dict[str, Any]],
    extension: list[dict[str, Any]],
    controls: list[dict[str, Any]],
    meta: dict[str, Any] | None = None,
) -> None:
    root.mkdir(parents=True, exist_ok=True)
    _run_git(root, "init", "-q")
    _write_seed(root, scenarios, extension, controls, meta)
    _run_git(root, "add", "-A")
    _run_git(root, "commit", "-qm", "c1")


def test_changed_subjects_covers_edits_add_delete_and_key_reorder(tmp_path: Path) -> None:  # (a)
    root = tmp_path / "repo"
    scenarios, extension, controls = _base3()
    meta = {"claim_drops": [{"slug": "c1", "dropped": ["dsc_prev_incentives"], "reason": "r"}]}
    _init_repo(root, scenarios=scenarios, extension=extension, controls=controls, meta=meta)

    s3_reordered = {  # same content, keys in a different order: parsed-equal, not changed
        "asset_class": scenarios[2]["asset_class"],
        "threat_actor_type": scenarios[2]["threat_actor_type"],
        "slug": scenarios[2]["slug"],
        "name": scenarios[2]["name"],
        "status": scenarios[2]["status"],
        "description": scenarios[2]["description"],
        "threat_event_type": scenarios[2]["threat_event_type"],
    }
    scenarios2 = [
        {**scenarios[0], "description": "S1 edited description."},
        s3_reordered,
        _scenario("s4", "S4"),
    ]  # s2 deleted, s1 edited, s3 key-reordered only, s4 added
    extension2 = [
        {**extension[0], "description": "E1 edited description."},
        extension[1],
        extension[2],
    ]
    controls2 = [
        controls[0],
        {**controls[1], "description": "C2 edited description."},
        controls[2],
    ]
    _write_seed(root, scenarios2, extension2, controls2, meta)
    _run_git(root, "add", "-A")
    _run_git(root, "commit", "-qm", "c2")

    result = changed_subjects(root, "HEAD~1")
    assert result.slugs == {"s1", "e1", "c2", "s4"}
    assert result.deprecated == frozenset()


def test_claim_drops_only_change_marks_the_control_changed(tmp_path: Path) -> None:  # (b)
    root = tmp_path / "repo"
    scenarios, extension, controls = _base3()
    meta1 = {"claim_drops": [{"slug": "c1", "dropped": ["dsc_prev_incentives"], "reason": "old"}]}
    _init_repo(root, scenarios=scenarios, extension=extension, controls=controls, meta=meta1)

    meta2 = {"claim_drops": [{"slug": "c1", "dropped": ["dsc_prev_incentives"], "reason": "new"}]}
    _write_seed(root, scenarios, extension, controls, meta2)  # control entries unchanged
    _run_git(root, "add", "-A")
    _run_git(root, "commit", "-qm", "c2")

    result = changed_subjects(root, "HEAD~1")
    assert result.slugs == {"c1"}
    assert result.deprecated == frozenset()


def test_status_transition_to_deprecated_is_reported_separately(tmp_path: Path) -> None:  # (c)
    root = tmp_path / "repo"
    scenarios, extension, controls = _base3()
    _init_repo(root, scenarios=scenarios, extension=extension, controls=controls)

    scenarios2 = [{**scenarios[0], "status": "deprecated"}, scenarios[1], scenarios[2]]
    _write_seed(root, scenarios2, extension, controls)
    _run_git(root, "add", "-A")
    _run_git(root, "commit", "-qm", "c2")

    result = changed_subjects(root, "HEAD~1")
    assert result.deprecated == {"s1"}
    assert "s1" not in result.slugs


def test_uses_merge_base_not_the_ref_tip(tmp_path: Path) -> None:  # (d)
    root = tmp_path / "repo"
    scenarios, extension, controls = _base3()
    _init_repo(root, scenarios=scenarios, extension=extension, controls=controls)
    _run_git(root, "branch", "-m", "main")
    c1 = _rev_parse(root, "HEAD")

    _run_git(root, "checkout", "-qb", "feature")
    feature_scenarios = [
        {**scenarios[0], "description": "feature edit"},
        scenarios[1],
        scenarios[2],
    ]
    _write_seed(root, feature_scenarios, extension, controls)
    _run_git(root, "add", "-A")
    _run_git(root, "commit", "-qm", "feature edit")

    _run_git(root, "checkout", "-q", "main")
    main_scenarios = [scenarios[0], {**scenarios[1], "description": "main edit"}, scenarios[2]]
    _write_seed(root, main_scenarios, extension, controls)
    _run_git(root, "add", "-A")
    _run_git(root, "commit", "-qm", "main edit")

    _run_git(
        root, "checkout", "-q", "feature"
    )  # working tree must match HEAD used by changed_subjects

    result = changed_subjects(root, "main")
    assert result.slugs == {"s1"}
    assert result.merge_base == c1


def test_seed_file_absent_at_merge_base_counts_every_entry_as_changed(
    tmp_path: Path,
) -> None:  # (e)
    root = tmp_path / "repo"
    root.mkdir(parents=True)
    _run_git(root, "init", "-q")
    (root / "README.md").write_text("x\n")
    _run_git(root, "add", "-A")
    _run_git(root, "commit", "-qm", "c1 (no seed files)")

    scenarios, extension, controls = _base3()
    _write_seed(root, scenarios, extension, controls)
    _run_git(root, "add", "-A")
    _run_git(root, "commit", "-qm", "c2 (add seed files)")

    result = changed_subjects(root, "HEAD~1")
    assert result.slugs == {"s1", "s2", "s3", "e1", "e2", "e3", "c1", "c2", "c3"}
    assert result.deprecated == frozenset()


@pytest.mark.parametrize("ref", ["-x", "--output=/tmp/x", "HEAD:data/x", "a b", "a..b"])
def test_invalid_ref_never_calls_git(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ref: str
) -> None:  # (f)
    def _boom(*args: object, **kwargs: object) -> Any:
        raise AssertionError("git must not be called for a structurally invalid ref")

    monkeypatch.setattr(subprocess, "run", _boom)
    with pytest.raises(ValueError, match="invalid ref"):
        changed_subjects(tmp_path, ref)


def test_unknown_ref_is_reported(tmp_path: Path) -> None:  # (f)
    root = tmp_path / "repo"
    scenarios, extension, controls = _base3()
    _init_repo(root, scenarios=scenarios, extension=extension, controls=controls)
    with pytest.raises(ValueError, match="unknown ref"):
        changed_subjects(root, "does-not-exist-branch")


def test_shallow_clone_reports_no_merge_base(tmp_path: Path) -> None:  # (f)
    # Two branches diverging from a common ancestor (as in test (d)), then a shallow, all-branches
    # clone (`--depth 1 --no-single-branch`): each branch tip is fetched independently at depth 1,
    # so the shared ancestor is never fetched and `merge-base` cannot find it.
    origin = tmp_path / "origin"
    scenarios, extension, controls = _base3()
    _init_repo(origin, scenarios=scenarios, extension=extension, controls=controls)
    _run_git(origin, "branch", "-m", "main")

    _run_git(origin, "checkout", "-qb", "feature")
    feature_scenarios = [
        {**scenarios[0], "description": "feature edit"},
        scenarios[1],
        scenarios[2],
    ]
    _write_seed(origin, feature_scenarios, extension, controls)
    _run_git(origin, "add", "-A")
    _run_git(origin, "commit", "-qm", "feature edit")

    _run_git(origin, "checkout", "-q", "main")
    main_scenarios = [scenarios[0], {**scenarios[1], "description": "main edit"}, scenarios[2]]
    _write_seed(origin, main_scenarios, extension, controls)
    _run_git(origin, "add", "-A")
    _run_git(origin, "commit", "-qm", "main edit")

    clone = tmp_path / "clone"
    subprocess.run(
        [
            *GIT,
            "clone",
            "-q",
            "--depth",
            "1",
            "--no-single-branch",
            "--branch",
            "feature",
            f"file://{origin}",
            str(clone),
        ],
        check=True,
        env=_scratch_env(),
    )
    with pytest.raises(ValueError, match="no merge base"):
        changed_subjects(clone, "origin/main")


def test_decoy_git_dir_is_ignored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:  # (g)
    decoy = tmp_path / "decoy.git"
    subprocess.run(["git", "init", "-q", "--bare", str(decoy)], check=True, env=_scratch_env())
    before = sorted(p.relative_to(decoy).as_posix() for p in decoy.rglob("*"))
    config_before = (decoy / "config").read_text()

    root = tmp_path / "repo"
    scenarios, extension, controls = _base3()
    _init_repo(root, scenarios=scenarios, extension=extension, controls=controls)
    scenarios2 = [{**scenarios[0], "description": "edited"}, scenarios[1], scenarios[2]]
    _write_seed(root, scenarios2, extension, controls)
    _run_git(root, "add", "-A")
    _run_git(root, "commit", "-qm", "c2")

    monkeypatch.setenv("GIT_DIR", str(decoy))
    monkeypatch.setenv("GIT_INDEX_FILE", str(decoy / "index"))

    result = changed_subjects(root, "HEAD~1")
    assert result.slugs == {"s1"}
    assert sorted(p.relative_to(decoy).as_posix() for p in decoy.rglob("*")) == before
    assert (decoy / "config").read_text() == config_before


def test_git_env_drops_git_vars_and_forces_lc_all(monkeypatch: pytest.MonkeyPatch) -> None:  # (h)
    monkeypatch.setenv("GIT_DIR", "/somewhere")
    monkeypatch.setenv("GIT_INDEX_FILE", "/somewhere/index")
    monkeypatch.setenv("LC_ALL", "de_DE.UTF-8")
    env = _git_env()
    assert not any(k.startswith("GIT_") for k in env)
    assert env["LC_ALL"] == "C"
