"""Tests for the css staleness step in scripts/run_local_gate.py."""

from __future__ import annotations

# scripts/ is importable (scripts/__init__.py exists); match the sibling
# tests/scripts/test_run_local_gate.py import style.
from scripts.run_local_gate import SKIP_CSS_ENV, SKIP_TESTS_ENV, steps_to_run


def test_css_step_present_by_default():
    labels = [label for label, _ in steps_to_run(env={})]
    assert "css staleness" in labels


def test_skip_css_env_removes_step():
    labels = [label for label, _ in steps_to_run(env={SKIP_CSS_ENV: "1"})]
    assert "css staleness" not in labels


def test_skip_tests_still_keeps_css():
    labels = [label for label, _ in steps_to_run(env={SKIP_TESTS_ENV: "1"})]
    assert "css staleness" in labels
    assert not any(lbl.startswith("pytest") for lbl in labels)
