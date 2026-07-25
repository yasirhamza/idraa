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
import re
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
    # Round-3 additions. Every symbol the plan names must appear here — round 3
    # named these three WITHOUT map verification, and the first one was asserted
    # (wrongly) not to exist at all.
    ("src/idraa/errors.py", "FAIRCAMValidationError"),
    ("fair_cam/quantile_pooling/_lognormal.py", "_qlnormtrunc"),
    ("src/idraa/services/run_executor.py", "execute_run"),
    # Round-4: the multi-currency entry path. `max` inputs land inside the
    # currency-scoped block whose siblings are converted to USD before parsing,
    # so this surface is load-bearing on the cap's units.
    ("src/idraa/services/scenario_currency.py", "convert_loss_inputs_to_usd"),
    ("tests/migrations/conftest.py", "alembic_config"),
    # Round-5: the DISPLAY consumers. The Threading table stopped at surfaces that
    # populate/persist/scale/validate the dict and never enumerated the ones that
    # RENDER it, so post-PR2 these would state a mean and p95 from a distribution
    # the engine no longer samples -- on screen and in the circulated PDF.
    ("src/idraa/app.py", "lognormal_display_rows"),
    ("src/idraa/app.py", "lognormal_mixture_display_rows"),
    ("src/idraa/services/pdf_report.py", "_lognormal_input_percentiles"),
    ("src/idraa/services/pdf_report.py", "_lognormal_mixture_percentiles"),
]

# Class attributes the plan names (models are AnnAssigns, not defs). Same
# fail-loud contract: a missing attribute is a fabricated claim.
REQUIRED_ATTRS: list[tuple[str, str, str]] = [
    ("src/idraa/models/risk_analysis_run.py", "RiskAnalysisRun", "scenario_inputs_snapshot"),
    # Sole justification for Task 1's mandatory Decimal test input.
    ("src/idraa/models/organization.py", "Organization", "annual_revenue"),
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
# (defining module | None, symbol). A module scope is REQUIRED whenever the name
# is not unique repo-wide: `_validate_rows` is defined in BOTH scenario_import and
# overlays_importer, so an unscoped census would report 4 call sites for a symbol
# that has 2 — the same inflation the lookbehind fix removed, in another dimension.
CALL_SITE_SYMBOLS: list[tuple[str | None, str]] = [
    (None, "validate_fair_distributions"),
    (None, "build_scenario_payload"),
    (None, "dist_from_raw"),
    (None, "dist_to_form"),
    (None, "capacity_max_for_org"),
    (None, "form_defaults"),
    ("src/idraa/services/scenario_import.py", "_structural_dist_problem"),
    ("src/idraa/services/scenario_import.py", "_validate_rows"),
    ("src/idraa/services/scenario_currency.py", "convert_loss_inputs_to_usd"),
    ("tests/migrations/conftest.py", "alembic_config"),
]


def _defs(path: Path) -> dict[str, tuple[int, str]]:
    """Map every function AND CLASS name to (lineno, signature).

    CLASSES ARE INCLUDED DELIBERATELY. Walking only FunctionDef made this tool
    structurally blind to classes, so probing it for a class name returned
    "SYMBOL NOT FOUND" — indistinguishable from "does not exist in the tree".
    That misreading produced a false claim in the rev-3 plan (that
    FAIRCAMValidationError, which is defined in errors.py and re-exported by the
    very module the plan edits, did not exist) — i.e. this tool CAUSED an
    instance of the defect it exists to prevent. A blind spot in a fail-loud
    verifier is worse than no verifier, because its silence reads as evidence.

    Returns ALL definitions per name. Ambiguity is reported to the caller rather
    than silently last-wins: a name-keyed map cannot distinguish a method from a
    module-level function of the same name, and would pin a real symbol at the
    wrong location. Only ambiguity in a name the PLAN references is fatal
    (ordinary dunders collide constantly and are nobody's claim).
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return {}
    out: dict[str, list[tuple[int, str]]] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            prefix = "async def " if isinstance(node, ast.AsyncFunctionDef) else "def "
            # Return annotation included: the plan makes claims about RETURN shapes
            # (e.g. "_dist_cells is a fixed 4-tuple, so CSV cannot carry max"), and
            # an args-only signature cannot support them.
            ret = f" -> {ast.unparse(node.returns)}" if node.returns is not None else ""
            sig = f"{prefix}{node.name}({ast.unparse(node.args)}){ret}"
        elif isinstance(node, ast.ClassDef):
            bases = ", ".join(ast.unparse(b) for b in node.bases)
            sig = f"class {node.name}({bases})" if bases else f"class {node.name}"
        else:
            continue
        out.setdefault(node.name, []).append((node.lineno, sig))
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
        if len(defs[name]) > 1:
            # Fatal only for a name the plan actually references.
            missing.append(
                f"{rel}::{name} (AMBIGUOUS — defined at "
                f"{', '.join(str(ln) for ln, _ in defs[name])})"
            )
            lines.append(f"  {rel}::{name}")
            lines.append("      *** AMBIGUOUS — multiple definitions ***")
            continue
        lineno, sig = defs[name][0]
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
    for scope, name in CALL_SITE_SYMBOLS:
        # The leading boundary is LOAD-BEARING: a plain substring search for
        # `dist_from_raw(` also matches inside `pert_dist_from_raw(`, which
        # inflated that census from 2 to 5 and dragged a `def pert_...` line past
        # the "excl. defs" filter. A plan quoting an inflated count inherits a
        # wrong claim from the very artifact declared authoritative.
        # -P with a negative lookbehind, NOT -w: `-w` is unreliable when the
        # pattern ends in a non-word character like `(`.
        proc = subprocess.run(  # noqa: S603
            ["git", "grep", "-nP", "-e", rf"(?<![A-Za-z0-9_]){name}\("],
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
            # Scope disambiguates WHICH definition the name refers to — it does
            # NOT restrict where callers may live. A hit counts if it is in the
            # defining module itself, OR in a module that does not define its own
            # symbol of that name (so it must be importing the scoped one).
            # Filtering to `== scope` instead would have dropped
            # library_bundle_import's real call to scenario_import's chokepoint.
            and (scope is None or ln.split(":")[0] == scope or not _defines(ln.split(":")[0], name))
        ]
        # Separate real calls from definitions and prose mentions in comments.
        calls = [h for h in hits if f"def {name}(" not in h and not _is_comment(h)]
        label = name if scope is None else f"{scope}::{name}"
        lines.append(
            f"  {label}: {len(calls)} call site(s) under src/ + fair_cam/ (excl. defs, comments)"
        )
        for h in calls:
            path, lineno, text = h.split(":", 2)
            lines.append(f"      {path}:{lineno}  {text.strip()[:88]}")
    return lines


def _defines(rel: str, name: str) -> bool:
    """True if `rel` contains its own definition of `name` (so a call there is local)."""
    return name in _defs(ROOT / rel)


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
        # Strip pytest's wall-clock suffix: it churns the committed artifact on
        # every regeneration, so a real drift is hidden among timing-only diffs.
        lines.append(f"  {label}")
        lines.append(f"      {re.sub(r' in [0-9.]+s$', '', tail[0].strip())[:96]}")
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


# Operator-local (gitignored) planning documents. Absent in a fresh clone and in
# CI, where the coherence check simply does not run -- it is a authoring-time gate,
# not a merge gate.
DESIGN_DOC = ROOT / "docs/superpowers/specs/2026-07-25-capacity-bound-design.md"
PLAN_DOC = ROOT / "docs/superpowers/plans/2026-07-25-capacity-bound-pr2.md"

# Files the design's Threading table names that NO task needs to modify, with the
# reason. Anything not listed here MUST appear in a plan TASK: that is the check.
# (Empty today: `library_bundle_import.py` was here as "no independent change", but
# Task 4b now names it explicitly for the library-vs-scenario flag, so the entry
# was inert and is removed. Keep the mechanism for a genuine future exemption.)
THREADING_EXEMPT: dict[str, str] = {}

# Store / table names the Threading table names as bare identifiers (no file
# extension), so the file-path regex below cannot see them. Checked by name
# against the plan's task region. (All FOUR the design's Stores row names — round 7
# found `wizard_drafts` had been omitted, so a store the design names went
# unenforced while the check reported "pass".)
THREADING_STORES = (
    "scenarios",
    "scenario_library_entries",
    "scenario_library_overrides",
    "wizard_drafts",
)

# Symbols the map verifies as supporting CONTEXT rather than because a task names
# them. Every other REQUIRED_SYMBOLS entry must be reachable from some task, so a
# dropped task cannot leave a silently-unused verification behind.
CONTEXT_ONLY_SYMBOLS = {
    "render_scenario_form": "signature quoted to show the pre-fill has no rate in scope",
    "form_defaults": "census establishes the expert form's default-construction path",
    "_assert_numeric_dist": "LET-path caveat, carried as a deferred nice-to-have",
    "execute_run": "adapter-raise behaviour (run flips FAILED) cited by Task 3's guard",
    "_qlnormtrunc": "Task 2's empirical-agreement reference",
    "alembic_config": "fixture location claim for Task 5",
    "calculate_risk": "secondary_loss_subtractor ordering pin in Task 2",
    "sample": "the branch Task 2 modifies, named via fair_core.py::sample",
}


def coherence_section() -> list[str]:
    """[DESIGN<->PLAN COHERENCE] the check no mechanism performed before round 6.

    WHY THIS EXISTS. Rounds 1-3's blocker class was "claims about the codebase
    written from memory"; the sections above killed it, and round 5 returned zero
    blockers of that class. What replaced it was a DIFFERENT class: a fix applied
    to ONE of the two coupled planning documents. Round 4 fixed the Task-3b hoist
    in the plan but left three stale pointers; round 5 found the entry-currency
    surface added to the design's Threading table with NO task, no file, and no
    criterion -- caught by two reviewers independently, three rounds after the same
    signature first appeared.

    A design is not executable. A Threading row with no task ships as a cap whose
    units depend on the analyst's entry currency. So the coupling is checked here,
    mechanically: the design names the surfaces, the plan's TASK region must name
    them back.

    WHAT THIS CATCHES (round 6 hardened all three): a surface named in a
    non-`.py`/`.html` file (goldens `.json`, config `.toml`) via a wider extension
    set; a store/table named as a bare identifier (THREADING_STORES); a surface
    named only in the plan's PROSE and not in a task (the plan is sliced to the
    task region); and a same-basename collision (`routes/scenarios.py` vs
    `services/scenarios.py`) — when a basename is ambiguous repo-wide the DIR-
    QUALIFIED path the design wrote is required, not just the basename.

    WHAT IT STILL CANNOT CATCH, stated so nobody mistakes it for complete: a
    surface the design describes in PROSE outside the `## Threading` section; a
    name that appears in a task body as incidental prose rather than a real
    `**Files:**`/criterion line (it checks presence in the task region, not role
    within it); and — asymmetrically with the file dimension — a STORE that is
    newly named in the design's Stores row. File/glob surfaces are EXTRACTED from
    the design text, so a new one auto-forces a task; but THREADING_STORES is a
    HAND-MAINTAINED constant (store names cannot be regex-extracted from the Stores
    prose, which also carries `sa.JSON`/`state_json`/`JSONB`), so a store added to
    the design without also being added to that tuple passes silently. The store
    set is frozen for this epic (D14), so this is a latent gap, not a live one; a
    future store-adding PR must extend THREADING_STORES, or mechanize it against
    `Base.metadata.tables`. Report's line-by-line classification and human review
    remain the backstop for all of the above.
    """
    lines = ["", "[DESIGN<->PLAN COHERENCE] every design surface must be named by a task", ""]
    if not (DESIGN_DOC.exists() and PLAN_DOC.exists()):
        lines.append("  design and/or plan not present (fresh clone or CI) — check SKIPPED")
        lines.append("      these documents are operator-local; this is an authoring-time gate")
        return lines
    design = DESIGN_DOC.read_text(encoding="utf-8")
    plan = PLAN_DOC.read_text(encoding="utf-8")
    # Slice the plan to its TASK region: a surface mentioned only in the intro
    # prose is NOT "named by a task". The first "### Task" heading starts it.
    task_split = re.search(r"\n### Task\b", plan)
    plan_tasks = plan[task_split.start() :] if task_split else plan

    m = re.search(r"\n## Threading\b(.*?)(?=\n## )", design, re.S)
    if not m:
        raise SystemExit(
            "SURFACE MAP FAILED: could not locate the design's '## Threading' section. "
            "If it was renamed, update this check -- do not let the coherence gate "
            "silently pass by finding nothing."
        )
    threading = m.group(1)

    def _ambiguous(basename: str) -> bool:
        """True if >1 tracked file under src/ or fair_cam/ shares this basename."""
        hits = list((ROOT / "src").rglob(basename)) + list((ROOT / "fair_cam").rglob(basename))
        return len(hits) > 1

    # Full path tokens as the design WROTE them (strip a trailing ::symbol), over a
    # wider extension set than py/html. A GLOB path (`tests/equivalence/golden/*.json`)
    # is not a literal token — the `*` breaks it — so glob dirs are extracted
    # separately and checked as bare identifiers (their directory must be named by a
    # task). Round 7 found the bare extension-widening was inert without this.
    tokens = set(re.findall(r"[\w./_-]+\.(?:py|html|json|toml|js|css|sql)", threading))
    tokens |= {str(Path(g).parent) for g in re.findall(r"[\w./_-]+/\*\.\w+", threading)}
    tokens = sorted(tokens)
    missing: list[str] = []
    for tok in tokens:
        base = Path(tok).name
        # For an ambiguous basename, require the dir-qualified path the design used
        # (so routes/scenarios.py cannot be satisfied by services/scenarios.py); for
        # an unambiguous one, the basename in the plan's task region is enough.
        needle = tok if ("/" in tok and _ambiguous(base)) else base
        present = needle in plan_tasks
        exempt = THREADING_EXEMPT.get(base)
        status = (
            "EXEMPT" if exempt else ("in a task" if present else "*** NOT NAMED BY ANY TASK ***")
        )
        lines.append(f"  {needle:<44} {status}{'  — ' + exempt if exempt else ''}")
        if not present and not exempt:
            missing.append(needle)
    for store in THREADING_STORES:
        present = bool(re.search(rf"\b{re.escape(store)}\b", plan_tasks))
        lines.append(f"  {store:<44} {'in a task' if present else '*** STORE NOT NAMED ***'}")
        if not present:
            missing.append(store)
    if missing:
        raise SystemExit(
            "SURFACE MAP FAILED: the design's Threading table names surfaces the plan's "
            "TASK region never mentions:\n  "
            + "\n  ".join(missing)
            + "\nThis is the round-5 entry-currency blocker's signature: a fix applied to the "
            "design only. Add a task (or add the file to THREADING_EXEMPT with a reason)."
        )

    # Reverse direction: a verified symbol no task names is either a stale
    # allowlist entry or a dropped task. Both are worth a conscious decision.
    orphans = [
        name
        for _, name in REQUIRED_SYMBOLS
        if name not in plan and name not in CONTEXT_ONLY_SYMBOLS
    ]
    lines.append("")
    lines.append(f"  REQUIRED_SYMBOLS not named by any task: {len(orphans)}")
    if orphans:
        raise SystemExit(
            "SURFACE MAP FAILED: symbols are verified here but named by no task:\n  "
            + "\n  ".join(orphans)
            + "\nEither a task was dropped, or the entry is context-only — in which case add "
            "it to CONTEXT_ONLY_SYMBOLS with the reason it is verified."
        )
    lines.append(f"      ({len(CONTEXT_ONLY_SYMBOLS)} allowlisted as context-only, with reasons)")
    return lines


def main() -> None:
    print("=" * 78)
    print("SURFACE MAP — machine-extracted; the plan QUOTES this, never hand-writes it")
    print("=" * 78)
    print()
    print("Regenerate:  uv run python scripts/surface_map.py")
    print("Fails loud if a symbol the plan names does not exist in the tree.")
    print()
    for section in (
        symbols_section(),
        attrs_section(),
        call_sites_section(),
        collection_section(),
        coherence_section(),
    ):
        print("\n".join(section))


if __name__ == "__main__":
    main()
