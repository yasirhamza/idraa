"""Boundary for Excel-library imports in runtime code (``src/idraa`` +
``alembic/versions``):

- **xlsxwriter** is the runtime Excel WRITER (the LET dynamic-array workbook). It is
  confined to the one sanctioned, auditable builder module so the writer surface
  stays small.
- **openpyxl** is the runtime Excel READER for register import (epic #34 P1c
  Task 1: promoted from the dev extra to a genuine ``[project]`` dependency —
  see the pyproject.toml comment on that line — because ``register_import``
  needs to read arbitrary operator-uploaded ``.xlsx`` registers). It is
  confined to the one sanctioned, hardened parser module (zip-bomb guard +
  ``defusedxml`` entity-expansion protection — see
  ``register_import_parsers.py``'s module docstring) so the untrusted-input
  reader surface stays as small and auditable as the writer surface above.

Tests may import either library freely."""

import pathlib
import re

# The ONLY runtime module permitted to import the Excel writer (xlsxwriter). One
# entry, justified above. The test compares the FULL relative path
# (str(py.relative_to(root))), NOT the basename.
_XLSXWRITER_RUNTIME_ALLOWLIST = {"src/idraa/services/verification_workbook.py"}

# The ONLY runtime module permitted to import the Excel reader (openpyxl). One
# entry, justified above — every other runtime module reads registers only
# through register_import_parsers's structural API, never openpyxl directly.
_OPENPYXL_RUNTIME_ALLOWLIST = {"src/idraa/services/register_import_parsers.py"}


def _runtime_py_files(root: pathlib.Path):
    for base in ["src/idraa", "alembic/versions"]:
        yield from (root / base).rglob("*.py")


def test_xlsxwriter_runtime_imports_confined_to_allowlist():
    root = pathlib.Path(__file__).resolve().parent.parent.parent
    bad = re.compile(r"^\s*(import|from)\s+xlsxwriter\b", re.M)
    offenders = [
        rel
        for py in _runtime_py_files(root)
        if bad.search(py.read_text())
        and (rel := py.relative_to(root).as_posix())
        not in _XLSXWRITER_RUNTIME_ALLOWLIST  # as_posix: str() yields backslashes on Windows
    ]
    assert offenders == [], f"xlsxwriter imported outside the allowlist: {offenders}"


def test_openpyxl_runtime_imports_confined_to_allowlist():
    root = pathlib.Path(__file__).resolve().parent.parent.parent
    bad = re.compile(r"^\s*(import|from)\s+openpyxl\b", re.M)
    offenders = [
        rel
        for py in _runtime_py_files(root)
        if bad.search(py.read_text(encoding="utf-8"))
        and (rel := py.relative_to(root).as_posix()) not in _OPENPYXL_RUNTIME_ALLOWLIST
    ]
    assert offenders == [], f"openpyxl imported outside the allowlist: {offenders}"


def test_allowlist_is_exactly_the_workbook_builder():
    # Guard against the xlsxwriter allowlist quietly growing.
    assert {"src/idraa/services/verification_workbook.py"} == _XLSXWRITER_RUNTIME_ALLOWLIST


def test_openpyxl_allowlist_is_exactly_the_register_parser():
    # Guard against the openpyxl allowlist quietly growing.
    assert {"src/idraa/services/register_import_parsers.py"} == _OPENPYXL_RUNTIME_ALLOWLIST
