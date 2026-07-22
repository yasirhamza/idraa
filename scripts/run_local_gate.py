#!/usr/bin/env python3
"""Local verification gate — pre-push hook running the real quality tools.

On the public repo GitHub Actions is free and CI (.github/workflows/ci.yml)
re-runs this gate verbatim as the branch-protection merge authority; this
pre-push stage is the fast local mirror. Before this script existed the pre-push gate only checked test-count
regression + working-tree cleanliness (scripts/lint_branch_state.py) — it
never executed pytest, ruff, or mypy, so a broken push was caught only by
the developer remembering to run them. This gate makes that deterministic.

Steps (each via ``sys.executable -m`` so the venv that runs the hook is the
venv that runs the tools):

1. ruff check src tests scripts
2. ruff format --check src tests scripts
3. mypy src/idraa (pyproject-configured, strict)
4. css staleness — ``python -m idraa.tasks.build_css --check`` (fails if
   the committed ``tailwind.css`` output is stale relative to its inputs)
5. pytest fast suite (default addopts markers: not e2e / not slow /
   not ci_only) with coverage disabled for speed

Escape hatches:
- ``IDRAA_GATE_SKIP_TESTS=1`` skips step 5 only (lints + css check still
  run) — for emergency pushes; document the reason in the next commit.
- ``IDRAA_GATE_SKIP_CSS=1`` skips step 4 only (css staleness check) —
  for emergency pushes when the Tailwind binary is unavailable; document
  the reason in the next commit.
- ``git push --no-verify`` skips the whole pre-push stage (rare; document).

Runtime: steps 1-3 ~30s; step 4 ~1s; step 5 ~3min on the reference machine.
That cost is the point — it is the only automated gate this repo has.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# (label, argv-after-sys.executable) — order is cheap-to-expensive so the
# fast failures fire first.
GATE_STEPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("ruff check", ("-m", "ruff", "check", "src", "tests", "scripts", "fair_cam")),
    (
        "ruff format --check",
        ("-m", "ruff", "format", "--check", "src", "tests", "scripts", "fair_cam"),
    ),
    # mypy scope is src/idraa + fair_cam SOURCE. tests/ and fair_cam/tests/
    # are EXCLUDED: tests/ carries ~409 pre-existing errors (issue #359) and
    # fair_cam/tests/ is untyped (relaxed in pyproject). fair_cam source was
    # burned down to 0 errors when it became first-party. Once tests/ burns
    # down, drop the explicit paths so the pyproject `files` key drives scope.
    (
        "mypy",
        (
            "-m",
            "mypy",
            "--config-file=pyproject.toml",
            "src/idraa",
            "fair_cam",
            "--exclude",
            "fair_cam/tests",
        ),
    ),
    ("css staleness", ("-m", "idraa.tasks.build_css", "--check")),
    ("pytest (fast suite)", ("-m", "pytest", "-q", "--no-cov")),
)

SKIP_TESTS_ENV = "IDRAA_GATE_SKIP_TESTS"
SKIP_CSS_ENV = "IDRAA_GATE_SKIP_CSS"


def steps_to_run(env: dict[str, str] | None = None) -> list[tuple[str, tuple[str, ...]]]:
    """Resolve the step list honoring the skip-tests and skip-css escape hatches."""
    env = os.environ if env is None else env  # type: ignore[assignment]
    steps = list(GATE_STEPS)
    if env.get(SKIP_TESTS_ENV) == "1":
        steps = [(label, argv) for label, argv in steps if not label.startswith("pytest")]
    if env.get(SKIP_CSS_ENV) == "1":
        steps = [(label, argv) for label, argv in steps if label != "css staleness"]
    return steps


def run_step(label: str, argv: tuple[str, ...]) -> int:
    print(f"== local gate: {label} ==", flush=True)
    proc = subprocess.run(  # noqa: S603 — argv is a module-constant list
        [sys.executable, *argv], cwd=REPO_ROOT, check=False
    )
    return proc.returncode


def main() -> int:
    skipped_tests = os.environ.get(SKIP_TESTS_ENV) == "1"
    if skipped_tests:
        print(f"local gate: {SKIP_TESTS_ENV}=1 — SKIPPING pytest (lints still run)")
    skipped_css = os.environ.get(SKIP_CSS_ENV) == "1"
    if skipped_css:
        print(f"local gate: {SKIP_CSS_ENV}=1 — SKIPPING css staleness check")

    # Dev-path lockfile freshness — matches Docker's `uv sync --frozen`.
    # Runs the uv BINARY (not python -m), so it sits outside GATE_STEPS.
    print("local gate: uv lock --check")
    # Args are a fully-literal list — ruff's S603 doesn't fire on this shape
    # (unlike the sys.executable/*argv call above), so no noqa is needed.
    lock = subprocess.run(["uv", "lock", "--check"], cwd=REPO_ROOT, check=False)
    if lock.returncode != 0:
        print("local gate: FAILED at uv lock --check (pyproject/uv.lock drift)")
        return lock.returncode

    if os.environ.get("IDRAA_GATE_SKIP_AUDIT") == "1":
        print("local gate: IDRAA_GATE_SKIP_AUDIT=1 — SKIPPING pip-audit")
    else:
        print("local gate: pip-audit (fixable-vuln policy)")
        # Fully-literal list, same shape as the uv lock --check call above —
        # ruff's S603 doesn't fire on this shape, so no noqa is needed.
        audit = subprocess.run([sys.executable, "scripts/sca_gate.py"], cwd=REPO_ROOT, check=False)
        if audit.returncode != 0:
            print("local gate: FAILED at pip-audit — fix, or suppress with rationale")
            return audit.returncode

    for label, argv in steps_to_run():
        rc = run_step(label, argv)
        if rc != 0:
            print()
            print(f"local gate FAILED at: {label} (exit {rc})")
            print("Fix and re-push, or bypass with `git push --no-verify` (document why).")
            return rc
    print("local gate: all steps passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
