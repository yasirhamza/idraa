"""Unit tests for scripts/runtime_smoke.py (idraa#260): the module-walk filter and exit paths.

The script itself is exercised against a real ``--no-dev`` install by the
``runtime-smoke`` CI job; these tests pin the pieces that must not silently
drift: which modules the walk skips (test packages, conftests), that a broken
non-test module/package IS reported, and that every failure path exits non-zero.
"""

from __future__ import annotations

import sys
import textwrap
from collections.abc import Iterator
from pathlib import Path

import pytest

from scripts import runtime_smoke as rs

_PKG = "runtime_smoke_fixture_pkg"


# --- is_test_module -----------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "fair_cam.tests",
        "fair_cam.tests.models",
        "fair_cam.tests.models.test_control",
        "fair_cam.tests.conftest",
        "idraa.conftest",
        "idraa.services.conftest",
        "tests",
    ],
)
def test_test_modules_are_skipped(name: str) -> None:
    assert rs.is_test_module(name)


@pytest.mark.parametrize(
    "name",
    [
        "idraa",
        "fair_cam",
        "idraa.services.loss_forms",
        "idraa.__main__",
        "idraa.tasks.__main__",
        "fair_cam.risk_engine",
        # Whole-component match only: a substring of "tests"/"conftest" in a real
        # runtime module name must NOT be skipped, or its import errors go unseen.
        "idraa.services.attests",
        "idraa.contests",
        "idraa.conftest_helpers",
        "idraa.testing",
    ],
)
def test_runtime_modules_are_not_skipped(name: str) -> None:
    assert not rs.is_test_module(name)


# --- the walk, against a synthetic package ------------------------------------


@pytest.fixture
def synthetic_pkg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """An importable throwaway package under tmp_path; purged from sys.modules afterwards."""
    root = tmp_path / _PKG
    root.mkdir()
    monkeypatch.syspath_prepend(str(tmp_path))
    yield root
    for name in [m for m in sys.modules if m == _PKG or m.startswith(f"{_PKG}.")]:
        del sys.modules[name]


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, body in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(body), encoding="utf-8")


_BREAKS = "import definitely_not_an_installed_package_xyz\n"


def test_walk_imports_runtime_modules_and_skips_test_modules(synthetic_pkg: Path) -> None:
    _write(
        synthetic_pkg,
        {
            "__init__.py": "",
            "good.py": "X = 1\n",
            "__main__.py": 'if __name__ == "__main__":\n    raise SystemExit(99)\n',
            "sub/__init__.py": "",
            "sub/leaf.py": "Y = 2\n",
            # Everything below is test-only and would fail if imported (or, for
            # the packages, if walk_packages' own recursion import failure were
            # counted): none of it may be reported or imported.
            "tests/__init__.py": _BREAKS,
            "tests/test_a.py": _BREAKS,
            "conftest.py": _BREAKS,
            "sub/tests/__init__.py": "",
            "sub/tests/test_b.py": _BREAKS,
        },
    )

    imported, failures = rs.import_all_modules([_PKG])

    assert failures == {}
    assert sorted(imported) == [
        _PKG,
        f"{_PKG}.__main__",
        f"{_PKG}.good",
        f"{_PKG}.sub",
        f"{_PKG}.sub.leaf",
    ]
    # Not merely unreported: never imported (each of these would have raised).
    for skipped in (f"{_PKG}.conftest", f"{_PKG}.tests.test_a", f"{_PKG}.sub.tests.test_b"):
        assert skipped not in sys.modules
        assert skipped not in imported


def test_walk_reports_a_broken_module_and_keeps_going(synthetic_pkg: Path) -> None:
    _write(
        synthetic_pkg,
        {
            "__init__.py": "",
            "aaa_good.py": "",
            "bad.py": _BREAKS,
            "zzz_good.py": "",
        },
    )

    imported, failures = rs.import_all_modules([_PKG])

    assert set(failures) == {f"{_PKG}.bad"}
    assert "definitely_not_an_installed_package_xyz" in failures[f"{_PKG}.bad"]
    # A failure does not stop the walk: modules sorted before AND after it import.
    assert f"{_PKG}.aaa_good" in imported
    assert f"{_PKG}.zzz_good" in imported
    assert f"{_PKG}.bad" not in imported


def test_walk_reports_a_broken_subpackage(synthetic_pkg: Path) -> None:
    """walk_packages itself imports subpackages; its onerror path must surface the failure."""
    _write(
        synthetic_pkg,
        {
            "__init__.py": "",
            "broken/__init__.py": _BREAKS,
            "broken/child.py": "",
            "fine.py": "",
        },
    )

    imported, failures = rs.import_all_modules([_PKG])

    assert set(failures) == {f"{_PKG}.broken"}
    assert f"{_PKG}.fine" in imported


def test_walk_reports_a_broken_top_level_package(synthetic_pkg: Path) -> None:
    _write(synthetic_pkg, {"__init__.py": _BREAKS, "child.py": ""})

    imported, failures = rs.import_all_modules([_PKG])

    assert set(failures) == {_PKG}
    assert imported == []


def test_walk_failure_in_one_package_does_not_hide_another(synthetic_pkg: Path) -> None:
    _write(synthetic_pkg, {"__init__.py": _BREAKS})

    imported, failures = rs.import_all_modules([_PKG, "json"])

    assert set(failures) == {_PKG}
    assert "json" in imported


# --- the real packages --------------------------------------------------------


def test_default_packages_cover_idraa_and_fair_cam() -> None:
    assert rs.PACKAGES == ("idraa", "fair_cam")


# --- dev-env guard + round-trip + main() exit paths ----------------------------


def test_present_dev_markers_reports_only_installed_packages() -> None:
    # pytest is, by construction, importable inside this test run.
    assert list(rs.present_dev_markers(["pytest", "definitely_not_an_installed_package_xyz"])) == [
        "pytest"
    ]


def test_select_one_round_trip() -> None:
    import asyncio

    assert asyncio.run(rs.select_one()) == 1


def test_main_refuses_a_dev_environment(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:

    def walked() -> tuple[list[str], dict[str, str]]:
        raise AssertionError("the dev-env guard must trip BEFORE any import walk")

    monkeypatch.setattr(rs, "present_dev_markers", lambda: iter(["pytest"]))
    monkeypatch.setattr(rs, "import_all_modules", walked)

    assert rs.main([]) == 2
    assert "refusing to run" in capsys.readouterr().err


def test_main_exits_nonzero_and_names_the_failing_module(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(rs, "present_dev_markers", lambda: iter(()))
    monkeypatch.setattr(
        rs,
        "import_all_modules",
        lambda: (["idraa"], {"idraa.services.loss_forms": "ModuleNotFoundError: pytest"}),
    )

    assert rs.main([]) == 1
    assert "idraa.services.loss_forms" in capsys.readouterr().err


def test_main_exits_nonzero_when_the_round_trip_fails(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def broken_select_one() -> int:
        raise ImportError("The SQLAlchemy asyncio module requires that the Python 'greenlet' ...")

    monkeypatch.setattr(rs, "present_dev_markers", lambda: iter(()))
    monkeypatch.setattr(rs, "import_all_modules", lambda: (["idraa"], {}))
    monkeypatch.setattr(rs, "select_one", broken_select_one)

    assert rs.main([]) == 1
    assert "greenlet" in capsys.readouterr().err


def test_main_exits_zero_on_a_clean_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rs, "present_dev_markers", lambda: iter(()))
    monkeypatch.setattr(rs, "import_all_modules", lambda: (["idraa"], {}))

    assert rs.main([]) == 0


def test_allow_dev_env_skips_the_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rs, "present_dev_markers", lambda: iter(["pytest"]))
    monkeypatch.setattr(rs, "import_all_modules", lambda: (["idraa"], {}))

    assert rs.main(["--allow-dev-env"]) == 0
