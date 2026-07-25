"""Machine-extracted surface map for the PR2 capacity-bound plan.

WHY THIS EXISTS
---------------
The PR2 plan-gate ran two rounds. Round 1 returned 13 blockers, round 2 returned
11 -- and virtually every round-2 blocker lived inside round-1's *fixes*. They
were not 24 independent mistakes; they were one, repeated: hand-written claims
about the codebase that were never executed. A representative sample:

    form_from_raw                 -> symbol does not exist
    dist_from_raw at 388          -> def is at 369
    secondary_loss via dist_from_raw -> SL is built inline, elsewhere
    _invcdf(dist, "u")            -> raises without u_sel
    fair_cam/tests/               -> 0 collected (testpaths = ["tests"])
    build_scenario_payload x2     -> exactly 1 call site in src/

This is the same failure the sigma phase hit one layer up, where ~240
hand-maintained derived NUMBERS would not converge under review. That was solved
by generating the figures and quoting the output. This script applies the same
move to code-surface FACTS: the plan quotes generated output instead of asserting
line numbers, signatures, and call-site counts from memory.

The script FAILS LOUD when a named symbol is missing, so a fabricated function
name is caught at generation time rather than by a reviewer -- or, worse, by an
implementer three tasks in.

Usage:
    uv run python scripts/surface_map.py > docs/superpowers/specs/surface-map.generated.txt
"""

from __future__ import annotations

import ast
import subprocess  # fixed first-party argv lists; never shell=True
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# (module path, symbol) pairs the PR2 plan depends on. A missing symbol is a
# generation-time failure: this is the check that would have caught form_from_raw.
REQUIRED_SYMBOLS: list[tuple[str, str]] = [
    ("src/idraa/routes/scenario_form_helpers.py", "dist_from_raw"),
    ("src/idraa/routes/scenario_form_helpers.py", "dist_to_form"),
    ("src/idraa/routes/scenario_form_helpers.py", "parse_scenario_form"),
    ("src/idraa/routes/scenario_form_helpers.py", "form_from_scenario"),
    ("src/idraa/routes/scenario_form_helpers.py", "form_defaults"),
    ("src/idraa/routes/scenario_form_helpers.py", "render_scenario_form"),
    ("src/idraa/services/run_executor.py", "_dict_to_fair_distribution"),
    ("src/idraa/services/fair_cam_validation.py", "_validate_finite"),
    ("src/idraa/services/fair_cam_validation.py", "validate_fair_distributions"),
    ("src/idraa/services/wizard_finalize.py", "build_scenario_payload"),
    ("src/idraa/services/scenario_import.py", "_structural_dist_problem"),
    ("src/idraa/services/scenario_import.py", "_validate_rows"),
    ("src/idraa/services/scenario_export.py", "_normalize_dist"),
    ("src/idraa/services/scenario_export.py", "_dist_cells"),
    ("src/idraa/services/verification_workbook_let.py", "_invcdf"),
    ("src/idraa/services/verification_workbook_let.py", "scaled_params"),
    ("src/idraa/services/verification_workbook_let.py", "_assert_numeric_dist"),
    ("src/idraa/services/loss_capacity.py", "capacity_max_for_org"),
    ("fair_cam/risk_engine/fair_core.py", "_scale_distribution"),
    # rev-3 additions: every symbol the thin plan names must be verified here.
    ("src/idraa/services/scenarios.py", "_apply_form_fields"),
    ("fair_cam/risk_engine/fair_core.py", "sample"),
    ("fair_cam/risk_engine/fair_core.py", "calculate_risk"),
    ("src/idraa/app.py", "_money_filter"),
]

# Class attributes the plan names (models are AnnAssigns, not defs). Same
# fail-loud contract: a missing attribute is a fabricated claim.
REQUIRED_ATTRS: list[tuple[str, str, str]] = [
    ("src/idraa/models/risk_analysis_run.py", "RiskAnalysisRun", "scenario_inputs_snapshot"),
]

# Files this PR CREATES. Only these may be absent; any other missing path is a
# typo'd claim and must fail loud. Without this allowlist a wrong path renders as
# "created by this PR" -- the same silent-skip that let bad claims through twice.
EXPECTED_NEW_FILES = {
    "src/idraa/services/loss_capacity.py",
    "fair_cam/risk_engine/_truncation.py",
}

# Symbols whose CALL SITES the plan makes claims about. Hand-counted censuses were
# wrong twice (validate_fair_distributions, build_scenario_payload).
CALL_SITE_SYMBOLS = [
    "validate_fair_distributions",
    "build_scenario_payload",
    "dist_from_raw",
    "dist_to_form",
    "capacity_max_for_org",
    "form_defaults",
]


def _defs(path: Path) -> dict[str, tuple[int, str]]:
    """Map every top-level and nested function name to (lineno, signature)."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return {}
    out: dict[str, tuple[int, str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = ast.unparse(node.args)
            prefix = "async def " if isinstance(node, ast.AsyncFunctionDef) else "def "
            out[node.name] = (node.lineno, f"{prefix}{node.name}({args})")
    return out


def symbols_section() -> list[str]:
    lines = ["[SYMBOLS] definition line + exact signature, extracted from the AST", ""]
    missing: list[str] = []
    if not (ROOT / "pyproject.toml").exists():
        # Guards against running a COPY of this script from outside the repo, which
        # silently rebases ROOT and makes every path miss. Found the hard way.
        raise SystemExit(f"SURFACE MAP FAILED: ROOT={ROOT} is not the repo root")
    for rel, name in REQUIRED_SYMBOLS:
        path = ROOT / rel
        if not path.exists():
            if rel in EXPECTED_NEW_FILES:
                lines.append(f"  {rel}::{name}")
                lines.append("      not yet created — this PR adds it (allowlisted)")
                continue
            missing.append(f"{rel} (file missing, and not in EXPECTED_NEW_FILES)")
            lines.append(f"  {rel}::{name}")
            lines.append("      *** FILE NOT FOUND ***")
            continue
        defs = _defs(path)
        if name not in defs:
            missing.append(f"{rel}::{name}")
            lines.append(f"  {rel}::{name}")
            lines.append("      *** SYMBOL NOT FOUND ***")
            continue
        lineno, sig = defs[name]
        lines.append(f"  {rel}:{lineno}")
        lines.append(f"      {sig}")
    if missing:
        # This is the check that catches a fabricated name like `form_from_raw`.
        raise SystemExit(
            "SURFACE MAP FAILED: symbols named by the plan do not exist in the tree:\n  "
            + "\n  ".join(missing)
            + "\nFix the plan (or the REQUIRED_SYMBOLS list) -- do not publish a map "
            "that asserts a symbol the tree does not have."
        )
    return lines


def attrs_section() -> list[str]:
    """Class attributes: AnnAssign/Assign targets inside the named class body."""
    lines = ["", "[ATTRS] class attributes the plan names, extracted from the AST", ""]
    missing: list[str] = []
    for rel, cls, attr in REQUIRED_ATTRS:
        path = ROOT / rel
        found: tuple[int, str] | None = None
        if path.exists():
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if not (isinstance(node, ast.ClassDef) and node.name == cls):
                    continue
                for stmt in node.body:
                    if (
                        isinstance(stmt, ast.AnnAssign)
                        and isinstance(stmt.target, ast.Name)
                        and stmt.target.id == attr
                    ):
                        found = (stmt.lineno, ast.unparse(stmt.annotation))
                    elif isinstance(stmt, ast.Assign) and any(
                        isinstance(t, ast.Name) and t.id == attr for t in stmt.targets
                    ):
                        found = (stmt.lineno, "<untyped assign>")
        if found is None:
            missing.append(f"{rel}::{cls}.{attr}")
            lines.append(f"  {rel}::{cls}.{attr}")
            lines.append("      *** ATTRIBUTE NOT FOUND ***")
        else:
            lines.append(f"  {rel}:{found[0]}")
            lines.append(f"      {cls}.{attr}: {found[1]}")
    if missing:
        raise SystemExit(
            "SURFACE MAP FAILED: attributes named by the plan do not exist in the tree:\n  "
            + "\n  ".join(missing)
        )
    return lines


def call_sites_section() -> list[str]:
    lines = ["", "[CALL SITES] every reference under src/ and fair_cam/, by symbol", ""]
    for name in CALL_SITE_SYMBOLS:
        proc = subprocess.run(  # noqa: S603
            ["git", "grep", "-n", f"{name}("],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        hits = [
            ln
            for ln in proc.stdout.splitlines()
            if (ln.startswith("src/") or ln.startswith("fair_cam/"))
            and "test" not in ln.split(":")[0]
        ]
        # Separate real calls from definitions and prose mentions in comments.
        calls = [h for h in hits if f"def {name}(" not in h and not _is_comment(h)]
        lines.append(
            f"  {name}: {len(calls)} call site(s) under src/ + fair_cam/ (excl. defs, comments)"
        )
        for h in calls:
            path, lineno, text = h.split(":", 2)
            lines.append(f"      {path}:{lineno}  {text.strip()[:88]}")
    return lines


def _is_comment(grep_line: str) -> bool:
    body = grep_line.split(":", 2)[-1].lstrip()
    return body.startswith("#") or body.startswith('"') or body.startswith("*")


def collection_section() -> list[str]:
    """Which test directories actually run in the default selection.

    Round 2's B-Arch-4 fix moved determinism tests into fair_cam/tests/ -- which
    `testpaths = ["tests"]` excludes just as thoroughly as the `slow` marker
    excluded tests/equivalence. A plan that places a test must state where the
    test actually runs.
    """
    lines = ["", "[TEST COLLECTION] what the DEFAULT selection actually collects", ""]
    py = sys.executable
    for label, argv in (
        (
            "default (no path arg -- governed by testpaths)",
            [py, "-m", "pytest", "-q", "--no-cov", "--collect-only"],
        ),
        (
            "tests/equivalence (default markers)",
            [py, "-m", "pytest", "tests/equivalence", "-q", "--no-cov", "--collect-only"],
        ),
        (
            'tests/equivalence -m ""',
            [py, "-m", "pytest", "tests/equivalence", "-m", "", "-q", "--no-cov", "--collect-only"],
        ),
        (
            "fair_cam/tests (explicit path)",
            [py, "-m", "pytest", "fair_cam/tests", "-q", "--no-cov", "--collect-only"],
        ),
    ):
        proc = subprocess.run(  # noqa: S603
            argv, cwd=ROOT, capture_output=True, text=True, check=False
        )
        tail = [ln for ln in proc.stdout.splitlines() if ln.strip()][-1:] or ["<no output>"]
        lines.append(f"  {label}")
        lines.append(f"      {tail[0].strip()[:96]}")
    fair_cam_default = subprocess.run(  # noqa: S603
        [py, "-m", "pytest", "-q", "--no-cov", "--collect-only"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    n_fc = sum(1 for ln in fair_cam_default.splitlines() if ln.startswith("fair_cam/tests"))
    lines.append("")
    lines.append(f"  fair_cam/tests collected by the DEFAULT selection: {n_fc}")
    lines.append("      -> a test placed there does NOT run in the merge path unless the")
    lines.append("         gate is given an explicit path. Verify placement before relying on it.")
    return lines


def main() -> None:
    print("=" * 78)
    print("SURFACE MAP — machine-extracted; the plan QUOTES this, never hand-writes it")
    print("=" * 78)
    print()
    print("Regenerate:  uv run python scripts/surface_map.py")
    print("Fails loud if a symbol the plan names does not exist in the tree.")
    print()
    for section in (symbols_section(), attrs_section(), call_sites_section(), collection_section()):
        print("\n".join(section))


if __name__ == "__main__":
    main()
