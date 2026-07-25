# tests/contracts/test_fair_cam_tests_collected.py
"""Pin: fair_cam/tests must be part of the DEFAULT (merge-path) pytest selection.

Why this exists: scripts/run_local_gate.py's pytest step
(``pytest -q --no-cov``, no path argument) is governed entirely by
pyproject.toml's ``[tool.pytest.ini_options] testpaths``. Before this test
existed, ``testpaths = ["tests"]`` meant ``fair_cam/tests`` collected 0 tests
in the merge path — despite collecting 552+ tests on an explicit
``fair_cam/tests`` path — so fair_cam's own regression/pin suite (including
PR2's most load-bearing mixture and determinism pins) never ran in CI or the
local gate.

``docs/superpowers/specs/surface-map.generated.txt`` documents this hole but
is gitignored (not part of the repo), so it cannot be the durable guard. This
test is: a future edit that drops ``fair_cam/tests`` from ``testpaths`` (or
otherwise breaks its collection under the default invocation) fails this
test, not silently reopening the hole.

Two checks, cheap-to-expensive:
1. Config check — ``testpaths`` literally lists ``fair_cam/tests``. Catches
   the direct regression (line reverted/edited) in milliseconds.
2. Behavioral check — actually invoke ``pytest --collect-only`` with no path
   (the same invocation shape the local gate and CI use) and assert
   fair_cam test items show up. Catches indirect regressions the config
   check alone would miss (ini precedence, rootdir drift, a conftest that
   swallows the directory, etc.).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from tests.contracts._registries import _load_pyproject, _project_root


def test_testpaths_includes_fair_cam_tests() -> None:
    """pyproject.toml's testpaths must literally list fair_cam/tests."""
    config = _load_pyproject()
    testpaths = config.get("tool", {}).get("pytest", {}).get("ini_options", {}).get("testpaths", [])
    assert "fair_cam/tests" in testpaths, (
        "testpaths no longer includes 'fair_cam/tests' — this reopens the "
        "merge-path collection hole (fair_cam's regression/pin suite would "
        "silently stop running in CI and the local gate). "
        f"Current testpaths: {testpaths!r}"
    )


def test_default_pytest_invocation_collects_fair_cam_tests() -> None:
    """The gate's exact invocation shape (no path) must collect fair_cam tests.

    Mirrors scripts/run_local_gate.py's pytest step invocation
    (``[sys.executable, "-m", "pytest", "-q", "--no-cov"]``, no path argument)
    but with --collect-only so this test doesn't recursively run the full
    suite from inside itself.
    """
    repo_root: Path = _project_root()
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "--no-cov"],
        capture_output=True,
        text=True,
        cwd=repo_root,
        check=False,
    )
    assert result.returncode == 0, (
        f"default pytest collection failed (exit {result.returncode}):\n"
        f"stdout:\n{result.stdout[-3000:]}\nstderr:\n{result.stderr[-2000:]}"
    )
    assert "fair_cam/tests/" in result.stdout, (
        "fair_cam/tests items did not appear in the default (no-path) "
        "pytest collection — check testpaths in pyproject.toml.\n"
        f"stdout tail:\n{result.stdout[-3000:]}"
    )
