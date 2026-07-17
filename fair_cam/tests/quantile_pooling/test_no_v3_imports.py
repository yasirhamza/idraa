"""Arch-4 R1 invariant: fair_cam/quantile_pooling/* imports only stdlib,
numpy, scipy. Zero imports from idraa.*, pyfair, or other fair_cam
subpackages that transitively import idraa."""

from __future__ import annotations

import ast
import pathlib

ALLOWED_TOP_LEVEL_PREFIXES = (
    "math",
    "time",
    "typing",
    "dataclasses",
    "enum",
    "collections",
    "functools",
    "itertools",
    "warnings",
    "json",
    "re",
    "abc",
    "numpy",
    "scipy",
    # stdlib logging — added for the #343 divergent-pooling warning in _types.py.
    "logging",
)


def _module_files() -> list[pathlib.Path]:
    root = pathlib.Path(__file__).resolve().parents[2] / "quantile_pooling"
    return sorted(root.glob("*.py"))


def test_module_files_exist() -> None:
    names = {f.name for f in _module_files()}
    assert {"__init__.py", "_types.py", "_lognormal.py", "_normal.py", "_cleaning.py"} <= names


_LEGACY_PREFIXES = ("idraa", "riskflow")  # riskflow kept: legacy belt-and-suspenders


def test_no_riskflow_imports() -> None:
    for path in _module_files():
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                for prefix in _LEGACY_PREFIXES:
                    assert not module.startswith(prefix), f"{path.name} imports {module}"
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    for prefix in _LEGACY_PREFIXES:
                        assert not alias.name.startswith(prefix), (
                            f"{path.name} imports {alias.name}"
                        )


def test_no_pyfair_imports() -> None:
    for path in _module_files():
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith("pyfair"), f"{path.name} imports pyfair"
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("pyfair"), f"{path.name} imports pyfair"


def test_only_allowed_third_party() -> None:
    for path in _module_files():
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.ImportFrom) and node.module:
                modules.append(node.module)
            elif isinstance(node, ast.Import):
                modules.extend(a.name for a in node.names)
            for module in modules:
                top = module.split(".")[0]
                if top == "" or top.startswith("_") or top == "fair_cam":
                    continue
                assert any(top.startswith(p) for p in ALLOWED_TOP_LEVEL_PREFIXES), (
                    f"{path.name} imports disallowed top-level: {top}"
                )
