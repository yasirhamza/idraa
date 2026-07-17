"""Tests for scripts/run_local_gate.py — the pre-push verification gate.

Unit-level only: the gate's step LIST and escape-hatch resolution are
tested directly; the gate is never executed end-to-end from inside pytest
(it would recurse into the suite that is running it).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

# Anchor on __file__, not CWD: `pytest tests/scripts/` from a subdirectory
# would otherwise fail collection (review finding).
_GATE_PATH = Path(__file__).resolve().parent.parent.parent / "scripts" / "run_local_gate.py"
_SPEC = importlib.util.spec_from_file_location("run_local_gate", _GATE_PATH)
assert _SPEC is not None and _SPEC.loader is not None
run_local_gate = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(run_local_gate)


def test_gate_includes_all_five_tools() -> None:
    labels = [label for label, _ in run_local_gate.GATE_STEPS]
    assert labels == [
        "ruff check",
        "ruff format --check",
        "mypy",
        "css staleness",
        "pytest (fast suite)",
    ]


def test_gate_order_is_cheap_to_expensive() -> None:
    """pytest must be LAST so lint failures fire in seconds, not minutes."""
    labels = [label for label, _ in run_local_gate.GATE_STEPS]
    assert labels[-1].startswith("pytest")


def test_skip_tests_env_drops_only_pytest() -> None:
    steps = run_local_gate.steps_to_run(env={run_local_gate.SKIP_TESTS_ENV: "1"})
    labels = [label for label, _ in steps]
    assert labels == ["ruff check", "ruff format --check", "mypy", "css staleness"]


def test_no_skip_env_keeps_pytest() -> None:
    steps = run_local_gate.steps_to_run(env={})
    labels = [label for label, _ in steps]
    assert "pytest (fast suite)" in labels


def test_pytest_step_disables_coverage_for_speed() -> None:
    pytest_argv = dict(run_local_gate.GATE_STEPS)["pytest (fast suite)"]
    assert "--no-cov" in pytest_argv


def test_mypy_step_uses_pyproject_config() -> None:
    mypy_argv = dict(run_local_gate.GATE_STEPS)["mypy"]
    assert "--config-file=pyproject.toml" in mypy_argv
