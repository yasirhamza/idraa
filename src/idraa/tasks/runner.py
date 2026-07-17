"""Task runner — canonical local CI interface.

Each task is a small Python function that shells out to a tool. The ``ci`` task
composes the others in order. This mirrors what the dormant GHA workflow does
remotely, so ``python -m idraa.tasks ci`` locally = the same steps the CI
would run if GHA were available.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class Task:
    name: str
    description: str
    fn: Callable[[], int]


def _run(cmd: list[str]) -> int:
    """Run a shell command and return its exit code, streaming output."""
    print(f"> {' '.join(cmd)}", flush=True)
    # S603: `cmd` is built from hardcoded developer-tool invocations (ruff, mypy,
    # pytest, docker) — no user input reaches this call site.
    result = subprocess.run(cmd, check=False)  # noqa: S603
    return result.returncode


def _lint() -> int:
    rc1 = _run(["ruff", "check", "."])
    rc2 = _run(["ruff", "format", "--check", "."])
    return rc1 or rc2


def _typecheck() -> int:
    return _run(["mypy"])


def _test() -> int:
    return _run(["pytest"])


def _notebook_smoke() -> int:
    return _run(["pytest", "-m", "notebook", "tests/smoke"])


def _docker_build() -> int:
    return _run(["docker", "build", "-t", "idraa:ci", "."])


def _e2e() -> int:
    return _run(["pytest", "tests/e2e", "-m", "e2e"])


def _build_css() -> int:
    from idraa.tasks import build_css

    return build_css.main([])


def _vendor_sync() -> int:
    from idraa.tasks import vendor_sync

    return vendor_sync.main([])


def _ci() -> int:
    """Full CI pipeline — matches .github/workflows/ci.yml."""
    for step in (_lint, _typecheck, _test, _notebook_smoke, _docker_build):
        rc = step()
        if rc != 0:
            return rc
    return 0


_TASKS: dict[str, Task] = {
    t.name: t
    for t in [
        Task("lint", "Run ruff check + format --check", _lint),
        Task("typecheck", "Run mypy (strict)", _typecheck),
        Task("test", "Run pytest with coverage", _test),
        Task("notebook-smoke", "Run papermill notebook smoke tests", _notebook_smoke),
        Task("docker-build", "Build the production Docker image", _docker_build),
        Task("e2e", "Run Playwright E2E tests", _e2e),
        Task("build-css", "Build the purged static tailwind.css (standalone CLI)", _build_css),
        Task("vendor-sync", "Re-vendor front-end assets to package.json versions", _vendor_sync),
        Task("ci", "Full CI pipeline (lint -> type -> test -> notebook -> docker)", _ci),
    ]
}


def list_tasks() -> list[Task]:
    """Return all registered tasks."""
    return list(_TASKS.values())


def run_task(name: str) -> int:
    """Run a task by name and return its exit code."""
    task = _TASKS.get(name)
    if task is None:
        raise KeyError(f"unknown task: {name!r}. Known: {sorted(_TASKS)}")
    return task.fn()


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m idraa.tasks",
        description="Idraa local CI task runner",
    )
    subparsers = parser.add_subparsers(dest="task", required=False)
    for task in list_tasks():
        subparsers.add_parser(task.name, help=task.description)

    args = parser.parse_args(argv)
    if args.task is None:
        parser.print_help()
        print("\nAvailable tasks:")
        for task in list_tasks():
            print(f"  {task.name:20s} {task.description}")
        return 0
    return run_task(args.task)


if __name__ == "__main__":
    sys.exit(main())
