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
3. org-scoped lookups (scripts/lint_org_scoped_lookups.py --all) — flags a
   bare ``<expr>.get(Model, id)`` on an org-scoped model with no
   organization_id check anywhere in its enclosing function (IDOR guard,
   docs/security/threat-model.md §7). AST-based, full src/idraa/{routes,
   services,repositories} sweep; negligible runtime (pure-Python parse of
   a few hundred files).
4. mypy src/idraa (pyproject-configured, strict)
5. css staleness — ``python -m idraa.tasks.build_css --check`` (fails if
   the committed ``tailwind.css`` output is stale relative to its inputs)
6. pytest fast suite (default addopts markers: not e2e / not slow /
   not ci_only) with coverage disabled for speed — this collects
   ``fair_cam/tests`` too (``pyproject.toml``'s ``testpaths`` lists both
   ``tests`` and ``fair_cam/tests``; see
   ``tests/contracts/test_fair_cam_tests_collected.py`` for the tracked pin
   that guards the merge-path collection hole from reopening — PR2 Task 1b).
   Measured: ``fair_cam/tests`` alone runs in ~3.2-4.5s (605 tests), negligible
   against the ~3-4 min gate budget.
7. equivalence harness, LABELED (PR2 Task 9) — the native-engine equivalence
   goldens (``tests/equivalence/test_engine_equivalence_harness.py``) are
   ``@pytest.mark.slow``, so step 5's default ``not slow`` addopts deselect
   them — they would otherwise NEVER run in the merge path even though they
   are the regression anchor for the native FAIREngine (Epic A #324) that
   PR2's truncated-lognormal sampler builds directly on top of. Scoped to
   this ONE file with an explicit ``-m slow`` override (not a bare ``-m
   slow`` across the whole tree) because other ``@pytest.mark.slow`` tests
   exist elsewhere (``tests/smoke/test_notebooks.py``,
   ``fair_cam/tests/risk_engine/test_native_lognormal.py`` /
   ``test_truncation.py`` / ``test_mixture_sampling.py``) that are NOT part
   of this budget and would blow it up if swept in by accident. Measured:
   ~1.6-3.4s (8 passed, 3 skipped by design — the analytic-anchor layer only
   applies to no-control fixtures), negligible against the ~3-4 min budget —
   so, per the same hygiene Task 1b used (cite the rule, measure the
   runtime, land a LABELED step rather than a silent addition): no fallback
   hedge is needed here either.

Escape hatches:
- ``IDRAA_GATE_SKIP_TESTS=1`` skips step 6 only (lints + css check still
  run) — for emergency pushes; document the reason in the next commit.
- ``IDRAA_GATE_SKIP_CSS=1`` skips step 5 only (css staleness check) —
  for emergency pushes when the Tailwind binary is unavailable; document
  the reason in the next commit.
- ``git push --no-verify`` skips the whole pre-push stage (rare; document).

Runtime: steps 1-4 ~30s; step 5 ~1s; step 6 (pytest) runs under
``pytest-xdist -n auto`` — wall-clock scales with core count (the ~5.9k-test
merge-path suite was the serial bottleneck; parallel it is a few minutes on a
multi-core machine and ~a quarter of that on CI's 4-core runner).
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
    ("ruff check", ("-m", "ruff", "check", "src", "tests", "scripts", "fair_cam", "security")),
    (
        "ruff format --check",
        ("-m", "ruff", "format", "--check", "src", "tests", "scripts", "fair_cam", "security"),
    ),
    ("org-scoped lookups", ("-m", "scripts.lint_org_scoped_lookups", "--all")),
    # mypy scope is src/idraa + fair_cam SOURCE + security (the committed
    # DAST harness, first-class-gated per arch-I4). tests/ and fair_cam/tests/
    # are EXCLUDED: tests/ carries ~409 pre-existing errors (issue #359) and
    # fair_cam/tests/ is untyped (relaxed in pyproject). fair_cam source was
    # burned down to 0 errors when it became first-party; security has no
    # tests/ subtree of its own. Once tests/ burns down, drop the explicit
    # paths so the pyproject `files` key drives scope.
    (
        "mypy",
        (
            "-m",
            "mypy",
            "--config-file=pyproject.toml",
            "src/idraa",
            "fair_cam",
            "security",
            "--exclude",
            "fair_cam/tests",
        ),
    ),
    ("css staleness", ("-m", "idraa.tasks.build_css", "--check")),
    # `-n auto` (pytest-xdist) parallelizes across the machine's cores — the
    # merge-path suite is ~5.9k tests and was the gate's dominant wall-clock
    # cost when run serially. Safe here because every test gets its own tmp
    # sqlite DB (tests/conftest.py db_url fixture) and singletons are reset
    # per-test in a per-worker process, so workers never share DB/engine state.
    ("pytest (fast suite)", ("-m", "pytest", "-q", "--no-cov", "-n", "auto")),
    # PR2 Task 9: labeled, scoped equivalence-harness step — see module
    # docstring step 6 for why this is scoped to one file with an explicit
    # marker override rather than a bare `-m slow`.
    (
        "pytest (equivalence harness, slow-marked)",
        (
            "-m",
            "pytest",
            "-q",
            "--no-cov",
            "-m",
            "slow",
            "tests/equivalence/test_engine_equivalence_harness.py",
        ),
    ),
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
