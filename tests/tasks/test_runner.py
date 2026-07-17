"""Tests for the idraa.tasks CLI task runner."""

from __future__ import annotations

import subprocess
import sys

import pytest

from idraa.tasks.runner import Task, list_tasks, run_task


def test_list_tasks_includes_known_commands() -> None:
    tasks = list_tasks()
    names = {t.name for t in tasks}
    assert "lint" in names
    assert "typecheck" in names
    assert "test" in names
    assert "ci" in names


def test_run_task_unknown_raises() -> None:
    with pytest.raises(KeyError, match="unknown task"):
        run_task("does-not-exist")


def test_cli_help_exits_zero() -> None:
    """`python -m idraa.tasks --help` must work."""
    result = subprocess.run(
        [sys.executable, "-m", "idraa.tasks", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "lint" in result.stdout
    assert "ci" in result.stdout


def test_task_has_expected_shape() -> None:
    """Task is an immutable dataclass with name/description/fn."""
    tasks = list_tasks()
    assert all(isinstance(t, Task) for t in tasks)
    sample = tasks[0]
    assert sample.name
    assert sample.description
    assert callable(sample.fn)
