#!/usr/bin/env python3
# scripts/lint_org_scoped_lookups.py
"""Lint rule flagging bare PK lookups on org-scoped models with no org check.

The IDOR guard convention across this codebase (docs/security/threat-model.md
§7) is: repos expose ``*_for_org(organization_id, ...)`` methods, and any raw
``db.get(Model, id)`` on an org-scoped model is expected to be followed (or
preceded, or delegated) by an explicit ``organization_id`` check — cross-org
ids 404, never leak another org's row. That discipline was convention-
enforced only (comments, review), not automated. This script closes that gap
for the single riskiest shape found during the threat-model sweep: a bare
``<expr>.get(Model, id)`` call on a model with no organization_id guard
anywhere in its enclosing function.

Two ways a call clears the check, searched anywhere in the enclosing
function body (order-independent — a check before OR after the call, or in
an early-return guard, all count):
- An inline ``Compare`` touching an ``.organization_id`` attribute (the
  ``if x.organization_id != org.id: raise ...`` shape).
- A call to a helper whose name contains ``for_org`` (the delegated-safety
  shape: ``RunRepo(db).get_for_org_or_raise(org_id, id)`` resolves ownership
  before an exempt bare fetch — services/runs.py's documented pattern).

Opt-out: ``# org-scope: ok — <reason>`` on the same line as the flagged
call. Reason text is required (non-empty after the em-dash).

The org-scoped model set (models inheriting ``OrgMixin``, plus the two models
that declare ``organization_id`` directly rather than via the mixin) is a
maintained allowlist below — update it when a new org-scoped model is added
(``models/mixins.py:OrgMixin`` or a direct ``organization_id`` column).
Deliberately EXCLUDED: ``Organization`` (is the org, not FK'd to one) and the
user-scoped-not-org-scoped tables (``AuthSession``, ``WebAuthnCredential``,
``UserTotp``, ``RecoveryCode``, ``LoginAttempt``).

Usage:
- ``python scripts/lint_org_scoped_lookups.py file1.py file2.py`` — scan
  named files.
- ``python scripts/lint_org_scoped_lookups.py --all`` — walk
  src/idraa/{routes,services,repositories} for every ``.py`` file. A
  directory walk (not a registered file list) so a newly added route/service
  file is covered automatically, with no separate registry to keep in sync.

Exit code: 0 on no violations; 1 on any violation. Each violation prints a
single-line message ``<file>:<line>: <call> has no organization_id check in
this function — IDOR risk...``.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

# ---- org-scoped model allowlist ----

ORG_SCOPED_MODELS: frozenset[str] = frozenset(
    {
        # OrgMixin-inheriting models (models/*.py, verified 2026-08-05).
        "CSVImportPreview",
        "Control",
        "ScenarioAttackMapping",
        "ControlFunctionAssignment",
        "RegisterBindingProfile",
        "OverlayDefinition",
        "FxRate",
        "RunSamples",
        "SystemState",
        "ScenarioSMEEstimate",
        "RiskAnalysisRun",
        "User",
        "QualitativeMappingOrgBand",
        "Scenario",
        "ScenarioLibraryOverride",
        "SecuritySettings",
        "SubjectMatterExpert",
        # Declare organization_id directly, not via OrgMixin.
        "AuditLog",
        "WizardDraft",
    }
)

# Directories walked by --all. Repositories are included because a new
# repo-method implementation is exactly where a fresh bare .get() would be
# introduced under the guise of "internal, trusted" access.
_SCAN_DIRS = ("src/idraa/routes", "src/idraa/services", "src/idraa/repositories")

# `# org-scope: ok — <reason>` — em-dash (—) OR two-or-more hyphens (--)
# followed by non-empty reason, mirroring lint_adapter_iter.py's convention.
_OPT_OUT_RE = re.compile(
    r"#\s*org-scope:\s*ok\s+(?:—|-{2,})\s+\S+",
    flags=re.UNICODE,
)


# ---- detection ----


def _is_flagged_get_call(node: ast.AST) -> str | None:
    """Return the flagged model name if ``node`` is ``<expr>.get(Model, id, ...)``."""
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    if not (isinstance(func, ast.Attribute) and func.attr == "get"):
        return None
    if not node.args:
        return None
    first = node.args[0]
    if not isinstance(first, ast.Name):
        return None
    if first.id not in ORG_SCOPED_MODELS:
        return None
    # Require a 2nd arg (the id) — a bare `.get(Model)` isn't a PK lookup shape.
    if len(node.args) < 2:
        return None
    return first.id


def _mentions_organization_id_check(node: ast.AST) -> bool:
    """True if ``node`` is a Compare touching an .organization_id attribute."""
    if not isinstance(node, ast.Compare):
        return False
    operands = [node.left, *node.comparators]
    return any(isinstance(o, ast.Attribute) and o.attr == "organization_id" for o in operands)


def _mentions_for_org_helper_call(node: ast.AST) -> bool:
    """True if ``node`` calls a helper whose name contains 'for_org' (delegated safety)."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    name = func.attr if isinstance(func, ast.Attribute) else None
    if name is None and isinstance(func, ast.Name):
        name = func.id
    return name is not None and "for_org" in name


def _function_is_org_verified(func_node: ast.AST) -> bool:
    for child in ast.walk(func_node):
        if _mentions_organization_id_check(child) or _mentions_for_org_helper_call(child):
            return True
    return False


class _Violation:
    __slots__ = ("file", "line", "model")

    def __init__(self, file: Path, line: int, model: str) -> None:
        self.file = file
        self.line = line
        self.model = model


def _span_lines(source_lines: list[str], start: int, end: int) -> list[str]:
    """Lines ``start..end`` (1-indexed, inclusive), clamped to the file's bounds.

    A flagged call's own line span, not just its AST ``lineno`` — ruff may
    wrap a long call across multiple lines, landing a trailing suppression
    comment on the closing-paren line rather than the line the call itself
    starts on (real example: routes/step_up.py after a ruff format pass).
    """
    start = max(start, 1)
    end = min(end, len(source_lines))
    if start > end:
        return []
    return source_lines[start - 1 : end]


def _line_has_opt_out(source_lines: list[str], start: int, end: int) -> bool:
    return any(_OPT_OUT_RE.search(line) for line in _span_lines(source_lines, start, end))


def _line_has_unjustified_opt_out(source_lines: list[str], start: int, end: int) -> bool:
    span = _span_lines(source_lines, start, end)
    if not any("org-scope:" in line.lower() for line in span):
        return False
    return not any(_OPT_OUT_RE.search(line) for line in span)


_FUNC_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef)


def scan_file(path: Path) -> list[_Violation]:
    source = path.read_text(encoding="utf-8")
    source_lines = source.splitlines()
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as e:
        raise RuntimeError(f"failed to parse {path}: {e}") from e

    # Map every node to its nearest enclosing function, so a flagged call's
    # "is this function org-verified?" search covers exactly that function's
    # body (not a sibling function, not module scope). Also map every node to
    # its nearest enclosing STATEMENT, so the opt-out comment search covers
    # the whole statement's line span, not just the Call node's own lineno —
    # ruff may wrap a long call across lines, landing the trailing comment on
    # the enclosing statement's closing-paren line instead (real example:
    # routes/step_up.py after a format pass).
    enclosing_func: dict[int, ast.AST] = {}
    for func_node in ast.walk(tree):
        if isinstance(func_node, _FUNC_TYPES):
            for child in ast.walk(func_node):
                enclosing_func[id(child)] = func_node

    # Plain (overwriting) assignment, not setdefault: ast.walk is breadth-
    # first, so an outer statement is always yielded before its nested
    # children — a later assignment for the same descendant id therefore
    # always comes from a deeper (more specific) enclosing statement, which
    # is what we want. Getting this backwards (first-wins) would let the
    # OUTERMOST statement's span win, over-widening the opt-out search and
    # risking one suppression comment silently covering a DIFFERENT flagged
    # call elsewhere in the same outer block (an if/elif with two .get()
    # calls, only one of which is meant to be suppressed).
    enclosing_stmt: dict[int, ast.stmt] = {}
    for stmt_node in ast.walk(tree):
        if isinstance(stmt_node, ast.stmt):
            for child in ast.walk(stmt_node):
                enclosing_stmt[id(child)] = stmt_node

    violations: list[_Violation] = []
    for node in ast.walk(tree):
        model = _is_flagged_get_call(node)
        if model is None:
            continue
        lineno = getattr(node, "lineno", 0)
        stmt_node = enclosing_stmt.get(id(node))
        span_end = getattr(stmt_node, "end_lineno", lineno) if stmt_node is not None else lineno
        span_start = getattr(stmt_node, "lineno", lineno) if stmt_node is not None else lineno
        if _line_has_unjustified_opt_out(source_lines, span_start, span_end):
            violations.append(_Violation(path, lineno, f"{model} (opt-out missing reason)"))
            continue
        if _line_has_opt_out(source_lines, span_start, span_end):
            continue
        func_node = enclosing_func.get(id(node))
        if func_node is not None and _function_is_org_verified(func_node):
            continue
        violations.append(_Violation(path, lineno, model))
    return violations


def _walk_scan_dirs() -> list[Path]:
    here = Path(__file__).resolve()
    repo_root = here.parent.parent
    files: list[Path] = []
    for rel_dir in _SCAN_DIRS:
        d = repo_root / rel_dir
        if d.is_dir():
            files.extend(sorted(d.rglob("*.py")))
    return files


def main(argv: list[str]) -> int:
    if "--all" in argv:
        files = _walk_scan_dirs()
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
            f"{v.file}:{v.line}: `.get({v.model}, ...)` has no organization_id check in this "
            f"function — IDOR risk (a cross-org id would return another org's row). Opt out "
            f"with `# org-scope: ok — <reason>` if genuinely safe."
        )
        print(msg)

    return 1 if all_violations else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
