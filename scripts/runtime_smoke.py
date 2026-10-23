"""Smoke test for the ``--no-dev`` runtime install (idraa#260).

CI installs every other job with ``--extra dev``, so a runtime dependency that
only a dev package happens to pull in transitively is invisible there. The
production image installs with ``uv sync --frozen --no-dev`` (``Dockerfile``),
which is the environment this script is meant to run in. Precedent: the
SQLAlchemy 2.1 bump (#230) moved ``greenlet`` into the ``[asyncio]`` extra and
only ``playwright`` (dev) still pulled it in, so every async DB call failed on a
``--no-dev`` install while CI and the local gate stayed green.

What it does, in order:

1. Refuses to run in an environment that still has dev-only packages
   installed (a vacuous pass would defeat the point); ``--allow-dev-env`` skips
   that guard for local dry runs.
2. Imports EVERY module of the ``idraa`` and ``fair_cam`` packages via
   ``pkgutil.walk_packages`` — not just ``import idraa``, because a dev-only
   package can hide behind a lazy or deep import. Test packages
   (``fair_cam.tests``) and ``conftest`` modules are skipped: they are
   dev-only by design. ``__main__`` modules ARE imported: both
   (``idraa.__main__``, ``idraa.tasks.__main__``) guard their work behind
   ``if __name__ == "__main__"``, so importing them has no side effects.
3. Runs one async SQLAlchemy round-trip (``select 1`` on
   ``sqlite+aiosqlite:///:memory:``), which exercises the ``greenlet`` /
   ``aiosqlite`` runtime path the #230 regression broke.

Exit status is non-zero, with the failing module name(s) on stderr, on any
import failure or if the round-trip fails.

Run it as ``uv run --no-sync python scripts/runtime_smoke.py`` after
``uv sync --frozen --no-dev`` (``--no-sync`` guarantees ``uv run`` cannot put
the dev extras back).

Environment: importing ``idraa.app`` and the route modules instantiates
``idraa.config.Settings``, which refuses the default ``SESSION_SECRET`` outside
``ENVIRONMENT=test``. The CI job therefore sets ``ENVIRONMENT=test`` (the same
value ``security/dast/run.py`` and ``tests/conftest.py`` use; no secret-shaped
value is needed) and ``DATABASE_URL=sqlite+aiosqlite:///:memory:`` so nothing
the import graph does can touch an on-disk database. The script sets neither
itself: a missing value must surface as an import failure, not be papered over.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import importlib.util
import pkgutil
import sys
from collections.abc import Callable, Iterable, Iterator

PACKAGES: tuple[str, ...] = ("idraa", "fair_cam")

# Dotted-name components that mark a module as test-only. A module is skipped
# when ANY component of its dotted name is in this set (so everything under
# ``fair_cam.tests`` is skipped, as is a ``conftest`` anywhere).
TEST_COMPONENTS: frozenset[str] = frozenset({"tests", "conftest"})

# Packages that must NOT be importable in a ``--no-dev`` environment. If any is
# present the smoke would pass vacuously, so the run is refused.
DEV_ONLY_MARKERS: tuple[str, ...] = ("pytest", "playwright", "papermill", "schemathesis")

_SELECT_ONE_URL = "sqlite+aiosqlite:///:memory:"


def is_test_module(name: str) -> bool:
    """True if the dotted module name is a test module/package or a conftest."""
    return any(part in TEST_COMPONENTS for part in name.split("."))


def _import_all(
    package: str,
    failures: dict[str, str],
    on_import: Callable[[str], None],
) -> None:
    """Import ``package`` and every non-test module beneath it.

    ``pkgutil.walk_packages`` itself imports each subpackage to recurse into
    it; a subpackage that fails to import is reported to ``onerror`` and its
    children are not walked, so the failure is recorded here and the run fails.
    """

    def onerror(name: str) -> None:
        # Called from inside walk_packages' ``except`` block, so the active
        # exception is available. Test packages never count as failures.
        if is_test_module(name):
            return
        exc = sys.exception()
        failures.setdefault(name, f"{type(exc).__name__}: {exc}")

    try:
        root = importlib.import_module(package)
    except Exception as exc:
        failures[package] = f"{type(exc).__name__}: {exc}"
        return
    on_import(package)

    for info in pkgutil.walk_packages(root.__path__, prefix=f"{package}.", onerror=onerror):
        name = info.name
        if is_test_module(name) or name in failures:
            continue
        try:
            importlib.import_module(name)
        except Exception as exc:
            failures[name] = f"{type(exc).__name__}: {exc}"
        else:
            on_import(name)


def import_all_modules(packages: Iterable[str] = PACKAGES) -> tuple[list[str], dict[str, str]]:
    """Import every non-test module of ``packages``.

    Returns ``(imported module names, {failing module name: error text})``.
    """
    imported: list[str] = []
    failures: dict[str, str] = {}
    for package in packages:
        _import_all(package, failures, imported.append)
    return imported, failures


def present_dev_markers(markers: Iterable[str] = DEV_ONLY_MARKERS) -> Iterator[str]:
    """Yield each dev-only marker package that IS importable in this environment."""
    for marker in markers:
        if importlib.util.find_spec(marker) is not None:
            yield marker


async def select_one(url: str = _SELECT_ONE_URL) -> int:
    """One async SQLAlchemy round-trip; returns the scalar ``select 1`` result."""
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:
            result = await conn.execute(text("select 1"))
            return int(result.scalar_one())
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else None)
    parser.add_argument(
        "--allow-dev-env",
        action="store_true",
        help="skip the refusal to run when dev-only packages are installed (local dry runs)",
    )
    args = parser.parse_args(argv)

    if not args.allow_dev_env:
        present = list(present_dev_markers())
        if present:
            print(
                "runtime-smoke: refusing to run — dev-only package(s) importable: "
                + ", ".join(present)
                + ". This is not a --no-dev install, so a pass would be vacuous. "
                "Use `uv sync --frozen --no-dev` then `uv run --no-sync python "
                "scripts/runtime_smoke.py` (or --allow-dev-env for a local dry run).",
                file=sys.stderr,
            )
            return 2

    imported, failures = import_all_modules()
    print(f"runtime-smoke: imported {len(imported)} modules from {', '.join(PACKAGES)}")
    for name, error in sorted(failures.items()):
        print(f"runtime-smoke: IMPORT FAILED: {name}: {error}", file=sys.stderr)
    if failures:
        print(f"runtime-smoke: FAIL — {len(failures)} module(s) did not import", file=sys.stderr)
        return 1

    try:
        value = asyncio.run(select_one())
    except Exception as exc:
        print(
            f"runtime-smoke: FAIL — async `select 1` round-trip: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 1
    if value != 1:
        print(f"runtime-smoke: FAIL — `select 1` returned {value!r}", file=sys.stderr)
        return 1
    print("runtime-smoke: async `select 1` round-trip OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
