"""Boundary for Excel-library imports in runtime code (``src/idraa`` +
``alembic/versions``):

- **xlsxwriter** is the runtime Excel WRITER (the LET dynamic-array workbook). It is
  confined to the one sanctioned, auditable builder module so the writer surface
  stays small.
- **openpyxl** is a DEV-ONLY dependency (moved out of ``[project].dependencies`` in
  the spill redesign — it is only a test-time READER for the injection/doc tests).
  It must NOT be imported from runtime ``src`` at all: a runtime openpyxl import
  would be a production ``ImportError`` (the package isn't installed in the prod
  image), which this boundary catches at test time.

Tests may import either library freely."""

import pathlib
import re

# The ONLY runtime module permitted to import the Excel writer (xlsxwriter). One
# entry, justified above. The test compares the FULL relative path
# (str(py.relative_to(root))), NOT the basename.
_XLSXWRITER_RUNTIME_ALLOWLIST = {"src/idraa/services/verification_workbook.py"}


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


def test_openpyxl_not_imported_at_runtime():
    # openpyxl is dev-only; ANY runtime src import would ImportError in prod.
    root = pathlib.Path(__file__).resolve().parent.parent.parent
    bad = re.compile(r"^\s*(import|from)\s+openpyxl\b", re.M)
    offenders = [
        py.relative_to(root).as_posix()
        for py in _runtime_py_files(root)
        if bad.search(py.read_text(encoding="utf-8"))
    ]
    assert offenders == [], (
        f"openpyxl is dev-only — runtime import would ImportError in prod: {offenders}"
    )


def test_allowlist_is_exactly_the_workbook_builder():
    # Guard against the xlsxwriter allowlist quietly growing.
    assert {"src/idraa/services/verification_workbook.py"} == _XLSXWRITER_RUNTIME_ALLOWLIST
