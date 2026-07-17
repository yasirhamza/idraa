#!/usr/bin/env python3
# scripts/lint_adapter_iter.py
"""Lint rule banning κ-class index-on-list patterns in conversion-layer code.

Scans Python files for patterns that take a single element from a list-typed
value (where iteration is more likely to be intended), and refuses the build
unless an opt-out comment with a reason is present on the same line.

Patterns flagged:
- xs[0]
- xs[-1]
- next(iter(xs))
- list(xs)[0]
- xs.pop()
- xs.pop(0)
- xs.pop(-1)

Opt-out: ``# adapter-iter: ok — <reason>`` on the same line as the flagged
statement. Reason text is required (non-empty after the em-dash).

Usage:
- ``python scripts/lint_adapter_iter.py file1.py file2.py`` — scan named files.
- ``python scripts/lint_adapter_iter.py --all`` — scan all files configured in
  pyproject.toml's ``[tool.idraa.contracts.lint] scoped_files``.

Exit code: 0 on no violations; 1 on any violation. Each violation prints a
single-line message ``<file>:<line>: <pattern> may silently drop list data —
opt out with `# adapter-iter: ok — <reason>` if intentional.``
"""

from __future__ import annotations

import ast
import re
import sys
import tomllib
from pathlib import Path

# ---- pattern matchers ----

# `# adapter-iter: ok — <reason>` — em-dash (—) OR two-or-more hyphens (--)
# followed by non-empty reason. Whitespace required around the separator.
_OPT_OUT_RE = re.compile(
    r"#\s*adapter-iter:\s*ok\s+(?:—|-{2,})\s+\S+",
    flags=re.UNICODE,
)


def _is_index_zero(node: ast.AST) -> bool:
    """xs[0]"""
    if not isinstance(node, ast.Subscript):
        return False
    slice_value = node.slice
    return isinstance(slice_value, ast.Constant) and slice_value.value == 0


def _is_index_minus_one(node: ast.AST) -> bool:
    """xs[-1]"""
    if not isinstance(node, ast.Subscript):
        return False
    slice_value = node.slice
    if isinstance(slice_value, ast.UnaryOp) and isinstance(slice_value.op, ast.USub):
        operand = slice_value.operand
        return isinstance(operand, ast.Constant) and operand.value == 1
    return False


def _is_next_iter(node: ast.AST) -> bool:
    """next(iter(xs))"""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if not (isinstance(func, ast.Name) and func.id == "next"):
        return False
    if not node.args:
        return False
    inner = node.args[0]
    if not isinstance(inner, ast.Call):
        return False
    inner_func = inner.func
    return isinstance(inner_func, ast.Name) and inner_func.id == "iter"


def _is_list_paren_zero(node: ast.AST) -> bool:
    """list(xs)[0]"""
    if not isinstance(node, ast.Subscript):
        return False
    if not (isinstance(node.slice, ast.Constant) and node.slice.value == 0):
        return False
    inner = node.value
    if not isinstance(inner, ast.Call):
        return False
    return isinstance(inner.func, ast.Name) and inner.func.id == "list"


def _is_pop_call(node: ast.AST) -> bool:
    """xs.pop() or xs.pop(0) or xs.pop(-1)"""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if not (isinstance(func, ast.Attribute) and func.attr == "pop"):
        return False
    if not node.args:
        return True
    arg = node.args[0]
    if isinstance(arg, ast.Constant) and arg.value in (0, -1):
        return True
    if isinstance(arg, ast.UnaryOp) and isinstance(arg.op, ast.USub):
        operand = arg.operand
        if isinstance(operand, ast.Constant) and operand.value == 1:
            return True
    return False


# Order matters: most-specific detectors first so list(xs)[0] is reported
# as "list(...)[0]" not as plain "[0]".
_PATTERN_DETECTORS = [
    ("list(...)[0]", _is_list_paren_zero),
    ("[0]", _is_index_zero),
    ("[-1]", _is_index_minus_one),
    ("next(iter(...))", _is_next_iter),
    (".pop()", _is_pop_call),
]


# ---- scanner ----


class _Violation:
    __slots__ = ("file", "line", "pattern")

    def __init__(self, file: Path, line: int, pattern: str) -> None:
        self.file = file
        self.line = line
        self.pattern = pattern


def _line_has_opt_out(source_lines: list[str], lineno: int) -> bool:
    if lineno < 1 or lineno > len(source_lines):
        return False
    return bool(_OPT_OUT_RE.search(source_lines[lineno - 1]))


def _line_has_unjustified_opt_out(source_lines: list[str], lineno: int) -> bool:
    if lineno < 1 or lineno > len(source_lines):
        return False
    line = source_lines[lineno - 1]
    if "adapter-iter:" not in line.lower():
        return False
    return not _OPT_OUT_RE.search(line)


def scan_file(path: Path) -> list[_Violation]:
    source = path.read_text(encoding="utf-8")
    source_lines = source.splitlines()
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as e:
        raise RuntimeError(f"failed to parse {path}: {e}") from e

    violations: list[_Violation] = []
    for node in ast.walk(tree):
        for pattern_label, detector in _PATTERN_DETECTORS:
            if detector(node):
                lineno = getattr(node, "lineno", 0)
                if _line_has_unjustified_opt_out(source_lines, lineno):
                    violations.append(
                        _Violation(path, lineno, f"{pattern_label} (opt-out missing reason)")
                    )
                elif not _line_has_opt_out(source_lines, lineno):
                    violations.append(_Violation(path, lineno, pattern_label))
                break
    return violations


def _load_scoped_files() -> list[Path]:
    here = Path(__file__).resolve()
    for parent in [here, *here.parents]:
        candidate = parent / "pyproject.toml"
        if candidate.is_file():
            with candidate.open("rb") as f:
                config = tomllib.load(f)
            files = (
                config.get("tool", {})
                .get("idraa", {})
                .get("contracts", {})
                .get("lint", {})
                .get("scoped_files", [])
            )
            project_root = candidate.parent
            return [project_root / f for f in files]
    raise RuntimeError("pyproject.toml not found")


def main(argv: list[str]) -> int:
    if "--all" in argv:
        files = _load_scoped_files()
    else:
        files = [Path(a) for a in argv if a and not a.startswith("-")]

    if not files:
        return 0

    all_violations: list[_Violation] = []
    for file in files:
        if not file.is_file():
            print(f"{file}: file not found", file=sys.stderr)
            return 2
        all_violations.extend(scan_file(file))

    for v in all_violations:
        msg = (
            f"{v.file}:{v.line}: `{v.pattern}` may silently drop list data — "
            f"opt out with `# adapter-iter: ok — <reason>` if intentional."
        )
        print(msg)

    return 1 if all_violations else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
