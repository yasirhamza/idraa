"""Intake guard and commit helper: fail-closed on any git failure (Sec3-1).

Every scratch repo is built through a `_scratch_env()` that drops GIT_* variables (mirroring
tests/scripts/curation_check/test_wrapper.py::_git_env and test_changed_since.py); an autouse
fixture also scrubs GIT_* from the test process itself."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from scripts.curation_check import __main__ as main_mod

GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com"]


@pytest.fixture(autouse=True)
def _no_inherited_git_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in list(os.environ):
        if k.startswith("GIT_"):
            monkeypatch.delenv(k, raising=False)


def _scratch_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def _run_git(root: Path, *args: str) -> None:
    subprocess.run([*GIT, *args], cwd=root, check=True, env=_scratch_env())


def _init_repo(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    _run_git(root, "init", "-q")
    (root / "README.md").write_text("x\n")
    _run_git(root, "add", "-A")
    _run_git(root, "commit", "-qm", "init")
    return root


def test_in_repo_intake_file_not_ignored_is_unsafe(tmp_path: Path) -> None:  # (i)
    root = _init_repo(tmp_path / "repo")
    intake = root / "intake.jsonl"
    intake.write_text("{}\n")
    assert main_mod._intake_is_safe(intake, root) is False


def test_in_repo_intake_file_ignored_is_safe(tmp_path: Path) -> None:  # (ii)
    root = _init_repo(tmp_path / "repo")
    (root / ".gitignore").write_text("intake.jsonl\n")
    _run_git(root, "add", "-A")
    _run_git(root, "commit", "-qm", "ignore intake")
    intake = root / "intake.jsonl"
    intake.write_text("{}\n")
    assert main_mod._intake_is_safe(intake, root) is True


def test_path_outside_the_repo_is_safe(tmp_path: Path) -> None:  # (iii)
    root = _init_repo(tmp_path / "repo")
    outside_dir = tmp_path / "sibling"
    outside_dir.mkdir()
    outside = outside_dir / "intake.jsonl"
    outside.write_text("{}\n")
    assert main_mod._intake_is_safe(outside, root) is True


@pytest.mark.parametrize("exc", [OSError, ValueError])
def test_git_failure_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, exc: type[Exception]
) -> None:  # (iv)
    root = _init_repo(tmp_path / "repo")
    intake = root / "intake.jsonl"
    intake.write_text("{}\n")

    def _boom(*args: object, **kwargs: object) -> int:
        raise exc("boom")

    monkeypatch.setattr(main_mod, "_git_rc", _boom)
    assert main_mod._intake_is_safe(intake, root) is False


def test_git_commit_returns_the_head_sha(tmp_path: Path) -> None:  # (v)
    root = _init_repo(tmp_path / "repo")
    expected = subprocess.run(
        [*GIT, "rev-parse", "HEAD"],
        cwd=root,
        check=True,
        env=_scratch_env(),
        capture_output=True,
        text=True,
    ).stdout.strip()
    sha = main_mod._git_commit(root)
    assert sha == expected
    assert len(sha) == 40 and all(c in "0123456789abcdef" for c in sha)


def test_git_commit_returns_unknown_for_a_non_repo(tmp_path: Path) -> None:  # (v)
    non_repo = tmp_path / "not-a-repo"
    non_repo.mkdir()
    assert main_mod._git_commit(non_repo) == "unknown"
