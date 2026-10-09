#!/usr/bin/env python3
"""Local verification gate — pre-push hook running the real quality tools.

On the public repo GitHub Actions is free and CI (.github/workflows/ci.yml)
re-runs this gate verbatim as the branch-protection merge authority; this
pre-push stage is the fast local mirror. Before this script existed the pre-push gate only checked test-count
regression + working-tree cleanliness (scripts/lint_branch_state.py) — it
never executed pytest, ruff, or mypy, so a broken push was caught only by
the developer remembering to run them. This gate makes that deterministic.

Steps (each via ``sys.executable -m`` so the venv that runs the hook is the
venv that runs the tools):

1. ruff check src tests scripts
2. ruff format --check src tests scripts
3. org-scoped lookups (scripts/lint_org_scoped_lookups.py --all) — flags a
   bare ``<expr>.get(Model, id)`` on an org-scoped model with no
   organization_id check anywhere in its enclosing function (IDOR guard,
   docs/security/threat-model.md §7). AST-based, full src/idraa/{routes,
   services,repositories} sweep; negligible runtime (pure-Python parse of
   a few hundred files).
4. mypy src/idraa (pyproject-configured, strict)
5. css staleness — ``python -m idraa.tasks.build_css --check`` (fails if
   the committed ``tailwind.css`` output is stale relative to its inputs)
6. pytest fast suite (default addopts markers: not e2e / not slow /
   not ci_only) with coverage disabled for speed — this collects
   ``fair_cam/tests`` too (``pyproject.toml``'s ``testpaths`` lists both
   ``tests`` and ``fair_cam/tests``; see
   ``tests/contracts/test_fair_cam_tests_collected.py`` for the tracked pin
   that guards the merge-path collection hole from reopening — PR2 Task 1b).
   Measured: ``fair_cam/tests`` alone runs in ~3.2-4.5s (605 tests), negligible
   against the ~3-4 min gate budget.
7. equivalence harness, LABELED (PR2 Task 9) — the native-engine equivalence
   goldens (``tests/equivalence/test_engine_equivalence_harness.py``) are
   ``@pytest.mark.slow``, so step 5's default ``not slow`` addopts deselect
   them — they would otherwise NEVER run in the merge path even though they
   are the regression anchor for the native FAIREngine (Epic A #324) that
   PR2's truncated-lognormal sampler builds directly on top of. Scoped to
   this ONE file with an explicit ``-m slow`` override (not a bare ``-m
   slow`` across the whole tree) because other ``@pytest.mark.slow`` tests
   exist elsewhere (``tests/smoke/test_notebooks.py``,
   ``fair_cam/tests/risk_engine/test_native_lognormal.py`` /
   ``test_truncation.py`` / ``test_mixture_sampling.py``) that are NOT part
   of this budget and would blow it up if swept in by accident. Measured:
   ~1.6-3.4s (8 passed, 3 skipped by design — the analytic-anchor layer only
   applies to no-control fixtures), negligible against the ~3-4 min budget —
   so, per the same hygiene Task 1b used (cite the rule, measure the
   runtime, land a LABELED step rather than a silent addition): no fallback
   hedge is needed here either.

Escape hatches:
- ``IDRAA_GATE_SKIP_TESTS=1`` skips step 6 only (lints + css check still
  run) — for emergency pushes; document the reason in the next commit.
- ``IDRAA_GATE_SKIP_CSS=1`` skips step 5 only (css staleness check) —
  for emergency pushes when the Tailwind binary is unavailable; document
  the reason in the next commit.
- ``git push --no-verify`` skips the whole pre-push stage (rare; document).
- ``IDRAA_GATE_SKIP_CLERK=1`` skips the clerk stage (see below).

Clerk stage (adoption design 2026-10-03, §4; plugin-install design 2026-10-09):
the gate runs the superpowers-clerk that the Claude Code plugin system installed
(``superpowers-clerk@superpowers-clerk``, user scope, read from
``<CLAUDE_CONFIG_DIR or ~/.claude>/plugins/installed_plugins.json``), or the
checkout ``IDRAA_CLERK_ROOT`` names when that is set (a development override
that wins over the plugin). It runs the clerk's deterministic gates as the
author's pre-flight against the merge-base with origin/main, both blocking:
``citations hygiene`` with the working-tree copy of the tracked citations
manifest, then ``surfaces`` with the tracked surfaces manifest. The stage
prints which clerk ran (``clerk at <root> (<version>; <source>)``; the version
comes from the plugin manifest, so no git is needed and the installed version
is the pin). With neither the plugin nor the variable it prints one skip line
— CI has neither and never runs the clerk; citation freshness in CI is
enforced by tests/scripts/test_clerk_manifests.py. Mode decision: clerk.toml at
the merge-base → normal; absent there but present at origin/main → refuse
(rebase); absent at both → bootstrap (hygiene only, ``--trust-worktree-config``,
no surfaces run). Runs before the ruff/mypy/pytest steps: it takes seconds and
its findings are the cheapest to act on. It runs the ``uv`` binary with GIT_*
and VIRTUAL_ENV removed, so like ``uv lock --check`` it sits outside GATE_STEPS.

Runtime: steps 1-4 ~30s; step 5 ~1s; step 6 (pytest) runs under
``pytest-xdist -n auto`` — wall-clock scales with core count (the ~5.9k-test
merge-path suite was the serial bottleneck; parallel it is a few minutes on a
multi-core machine and ~a quarter of that on CI's 4-core runner).
That cost is the point — it is the only automated gate this repo has.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent

# (label, argv-after-sys.executable) — order is cheap-to-expensive so the
# fast failures fire first.
GATE_STEPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("ruff check", ("-m", "ruff", "check", "src", "tests", "scripts", "fair_cam", "security")),
    (
        "ruff format --check",
        ("-m", "ruff", "format", "--check", "src", "tests", "scripts", "fair_cam", "security"),
    ),
    ("org-scoped lookups", ("-m", "scripts.lint_org_scoped_lookups", "--all")),
    # mypy scope is src/idraa + fair_cam SOURCE + security (the committed
    # DAST harness, first-class-gated per arch-I4). tests/ and fair_cam/tests/
    # are EXCLUDED: tests/ carries ~409 pre-existing errors (issue #359) and
    # fair_cam/tests/ is untyped (relaxed in pyproject). fair_cam source was
    # burned down to 0 errors when it became first-party; security has no
    # tests/ subtree of its own. Once tests/ burns down, drop the explicit
    # paths so the pyproject `files` key drives scope.
    (
        "mypy",
        (
            "-m",
            "mypy",
            "--config-file=pyproject.toml",
            "src/idraa",
            "fair_cam",
            "security",
            "--exclude",
            "fair_cam/tests",
        ),
    ),
    ("css staleness", ("-m", "idraa.tasks.build_css", "--check")),
    # `-n auto` (pytest-xdist) parallelizes across the machine's cores — the
    # merge-path suite is ~5.9k tests and was the gate's dominant wall-clock
    # cost when run serially. Safe here because every test gets its own tmp
    # sqlite DB (tests/conftest.py db_url fixture) and singletons are reset
    # per-test in a per-worker process, so workers never share DB/engine state.
    ("pytest (fast suite)", ("-m", "pytest", "-q", "--no-cov", "-n", "auto")),
    # PR2 Task 9: labeled, scoped equivalence-harness step — see module
    # docstring step 6 for why this is scoped to one file with an explicit
    # marker override rather than a bare `-m slow`.
    (
        "pytest (equivalence harness, slow-marked)",
        (
            "-m",
            "pytest",
            "-q",
            "--no-cov",
            "-m",
            "slow",
            "tests/equivalence/test_engine_equivalence_harness.py",
        ),
    ),
)

SKIP_TESTS_ENV = "IDRAA_GATE_SKIP_TESTS"
SKIP_CSS_ENV = "IDRAA_GATE_SKIP_CSS"


def steps_to_run(env: dict[str, str] | None = None) -> list[tuple[str, tuple[str, ...]]]:
    """Resolve the step list honoring the skip-tests and skip-css escape hatches."""
    env = os.environ if env is None else env  # type: ignore[assignment]
    steps = list(GATE_STEPS)
    if env.get(SKIP_TESTS_ENV) == "1":
        steps = [(label, argv) for label, argv in steps if not label.startswith("pytest")]
    if env.get(SKIP_CSS_ENV) == "1":
        steps = [(label, argv) for label, argv in steps if label != "css staleness"]
    return steps


def run_step(label: str, argv: tuple[str, ...]) -> int:
    print(f"== local gate: {label} ==", flush=True)
    proc = subprocess.run(  # noqa: S603 — argv is a module-constant list
        [sys.executable, *argv], cwd=REPO_ROOT, check=False
    )
    return proc.returncode


# --- clerk stage: begin (rollback: delete this block; see adoption design §7) ---
CLERK_ROOT_ENV = "IDRAA_CLERK_ROOT"
SKIP_CLERK_ENV = "IDRAA_GATE_SKIP_CLERK"
CLAUDE_CONFIG_DIR_ENV = "CLAUDE_CONFIG_DIR"
# The key Claude Code files the clerk under in installed_plugins.json (`<plugin>@<marketplace>`).
CLERK_PLUGIN_KEY = "superpowers-clerk@superpowers-clerk"
CLERK_SOURCE_OVERRIDE = f"{CLERK_ROOT_ENV} override"
CLERK_SOURCE_INSTALLED = "installed plugin"
# Fully qualified: a bare `origin/main` is ambiguous with a local tag or branch of that name.
CLERK_ORIGIN_REF = "refs/remotes/origin/main"
CLERK_PROJECT_FILES = ("pyproject.toml", "uv.lock")
CLERK_CONFIG = "clerk.toml"
CLERK_MANIFEST = ".clerk/manifests/citations.json"
CLERK_SURFACES_MANIFEST = ".clerk/manifests/surfaces.json"
CLERK_NOT_INSTALLED_REASON = (
    f"superpowers-clerk not installed (claude plugin install {CLERK_PLUGIN_KEY})"
    f" and {CLERK_ROOT_ENV} unset (CI always skips)"
)


def _installed_clerk_record(env: Mapping[str, str]) -> dict[str, Any] | None:
    """The first user-scope record of the clerk plugin in Claude Code's registry, or None; never raises.

    Reads `<CLAUDE_CONFIG_DIR or ~/.claude>/plugins/installed_plugins.json` and nothing else
    under the config directory. A missing or unreadable file, malformed JSON, a missing key,
    an empty list, no record of `scope == "user"`, or a first user-scope record without a string
    `installPath` all mean "not installed". The registry holds every scope's installs, and a
    project- or local-scope record made while working in another repository must not become the
    clerk this repository's gate runs, so only user scope counts (the first such record decides).
    """
    try:
        config_dir = Path(env.get(CLAUDE_CONFIG_DIR_ENV) or Path.home() / ".claude")
        registry = json.loads(
            (config_dir / "plugins" / "installed_plugins.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError, RuntimeError):  # RuntimeError: no home dir, JSON nesting depth
        return None
    plugins = registry.get("plugins") if isinstance(registry, dict) else None
    records = plugins.get(CLERK_PLUGIN_KEY) if isinstance(plugins, dict) else None
    if not isinstance(records, list) or not records:
        return None
    record = next((r for r in records if isinstance(r, dict) and r.get("scope") == "user"), None)
    if record is None:
        return None
    install_path = record.get("installPath")
    if not isinstance(install_path, str) or not install_path:
        return None
    return record


def clerk_root(env: Mapping[str, str]) -> tuple[str, str] | None:
    """`(root, source)` of the clerk to run, or None when there is none.

    `IDRAA_CLERK_ROOT` (a development checkout) wins; otherwise the installed plugin's
    `installPath`. Whether that path is absolute, a directory and a clerk is `run_clerk_gate`'s
    check (exit 2).
    """
    override = env.get(CLERK_ROOT_ENV)
    if override:
        return override, CLERK_SOURCE_OVERRIDE
    record = _installed_clerk_record(env)
    if record is None:
        return None
    return str(record["installPath"]), CLERK_SOURCE_INSTALLED


def clerk_installed_sha(env: Mapping[str, str]) -> str | None:
    """The installed record's `gitCommitSha` when it is a non-empty string, else None."""
    record = _installed_clerk_record(env)
    sha = record.get("gitCommitSha") if record is not None else None
    return sha if isinstance(sha, str) and sha else None


def clerk_skip_reason(env: Mapping[str, str]) -> str | None:
    """Why the clerk stage is skipped, or None when it runs."""
    if env.get(SKIP_CLERK_ENV) == "1":
        return f"{SKIP_CLERK_ENV}=1 (escape hatch; document the reason in the next commit)"
    if clerk_root(env) is None:
        return CLERK_NOT_INSTALLED_REASON
    return None


def clerk_mode(*, at_base: bool, at_origin_main: bool) -> str:
    """normal: config at the merge-base; refuse: only origin/main has it (rebase); bootstrap: neither."""
    if at_base:
        return "normal"
    return "refuse" if at_origin_main else "bootstrap"


def clerk_gate_command(root: str, base_sha: str, *, mode: str) -> list[str]:
    prefix = ["uv", "run", "--frozen", "--project", root, "clerk", "gate"]
    common = ["--config", CLERK_CONFIG, "--base", base_sha]
    if mode == "normal":
        return [
            *prefix,
            "citations",
            "hygiene",
            *common,
            "--manifest",
            CLERK_MANIFEST,
            "--range",
            f"{base_sha}..HEAD",
        ]
    if mode == "surfaces":
        return [*prefix, "surfaces", *common, "--manifest", CLERK_SURFACES_MANIFEST]
    if mode == "bootstrap":
        return [
            *prefix,
            "hygiene",
            *common,
            "--range",
            f"{base_sha}..HEAD",
            "--trust-worktree-config",
        ]
    raise ValueError(f"no clerk command for mode {mode!r}")


def _clean_env() -> dict[str, str]:
    """GIT_* leaks from the pre-push hook; VIRTUAL_ENV is Idraa's venv and makes the nested uv warn."""
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_") and k != "VIRTUAL_ENV"}


def _git(*args: str, cwd: Path | str = REPO_ROOT) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 — argv is built from constants and a recorded sha
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=False, env=_clean_env()
    )


def _run(argv: list[str]) -> int:
    return subprocess.run(  # noqa: S603 — see clerk_gate_command
        argv, cwd=REPO_ROOT, check=False, env=_clean_env()
    ).returncode


def clerk_base_sha() -> str | None:
    """The full merge-base with origin/main, or None (after saying why) when there is none."""
    proc = _git("merge-base", "HEAD", CLERK_ORIGIN_REF)
    sha = proc.stdout.strip()
    if proc.returncode != 0:
        first = (proc.stderr.strip().splitlines() or [""])[0]
        print(f"local gate: git merge-base exit {proc.returncode}: {first}")
        return None
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        print(f"local gate: unexpected merge-base output {sha[:80]!r}")
        return None
    return sha


def _has_clerk_config(rev: str) -> bool | None:
    """True/False when git answered; None when git failed (never read an error as "absent")."""
    proc = _git("ls-tree", "-z", rev, "--", CLERK_CONFIG)
    if proc.returncode != 0:
        return None
    return bool(proc.stdout)


def clerk_version(root: str) -> str:
    """`version` from the plugin manifest; else git describe (a checkout); else "unknown".

    The installed plugin copy has no `.git`, so git alone cannot report its version.
    """
    try:
        manifest = json.loads(
            (Path(root) / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError, RuntimeError):
        manifest = None
    version = manifest.get("version") if isinstance(manifest, dict) else None
    if isinstance(version, str) and version.strip():
        return version.strip()
    return _git("describe", "--tags", "--dirty", "--always", cwd=root).stdout.strip() or "unknown"


def run_clerk_gate(root: str, source: str = CLERK_SOURCE_OVERRIDE, sha: str | None = None) -> int:
    installed = source == CLERK_SOURCE_INSTALLED
    repair = f" — repair it with `claude plugin update {CLERK_PLUGIN_KEY}`" if installed else ""
    subject = (
        f"the installed {CLERK_PLUGIN_KEY} record points at {root!r}, which"
        if installed
        else f"{CLERK_ROOT_ENV}={root!r}"
    )
    # A relative record would resolve under the repository being reviewed; refuse before is_dir().
    if installed and not Path(root).is_absolute():
        print(f"local gate: {subject} is not an absolute path{repair}")
        return 2
    if not Path(root).is_dir():
        print(f"local gate: {subject} is not a directory{repair}")
        return 2
    # `uv run --frozen --project <root>` needs both; check before any git or clerk invocation.
    if not all((Path(root) / name).is_file() for name in CLERK_PROJECT_FILES):
        print(
            f"local gate: {root!r} has no {'/'.join(CLERK_PROJECT_FILES)}"
            f" — not a superpowers-clerk checkout{repair}"
        )
        return 2
    version = clerk_version(root)
    if sha:
        version = f"{version} {sha[:12]}"
    print(f"local gate: clerk at {root} ({version}; {source})")
    base = clerk_base_sha()
    if base is None:
        print("local gate: SKIPPING clerk gate — no merge-base with origin/main")
        return 0
    at_base = _has_clerk_config(base)
    if at_base is None:
        print(f"local gate: git could not read {CLERK_CONFIG} at base — input error")
        return 2
    at_origin = _has_clerk_config(CLERK_ORIGIN_REF)
    if at_origin is None:
        print(f"local gate: git could not read {CLERK_CONFIG} at origin/main — input error")
        return 2
    mode = clerk_mode(at_base=at_base, at_origin_main=at_origin)
    if mode == "refuse":
        print(
            "local gate: branch predates the clerk adoption — rebase with"
            " `git rebase --onto origin/main <old adoption tip>`"
            " (a plain rebase replays the squashed adoption commits)"
        )
        return 2
    if mode == "bootstrap":
        print(
            f"local gate: {CLERK_CONFIG} absent at the merge-base and at origin/main"
            " — bootstrap run (--trust-worktree-config)"
        )
    print("== local gate: clerk gate ==", flush=True)
    rc = _run(clerk_gate_command(root, base, mode=mode))
    if rc != 0 or mode == "bootstrap":
        return rc
    print("== local gate: clerk gate surfaces ==", flush=True)
    return _run(clerk_gate_command(root, base, mode="surfaces"))


def clerk_stage(env: Mapping[str, str]) -> int:
    reason = clerk_skip_reason(env)
    resolved = clerk_root(env) if reason is None else None
    if resolved is None:
        print(f"local gate: SKIPPING clerk gate — {reason or CLERK_NOT_INSTALLED_REASON}")
        return 0
    root, source = resolved
    sha = clerk_installed_sha(env) if source == CLERK_SOURCE_INSTALLED else None
    rc = run_clerk_gate(root, source, sha)
    if rc != 0:
        print(
            f"local gate: FAILED at clerk gate (exit {rc}: 1 = findings, 2 = input error or stale base)"
            f"; bypass only this stage with {SKIP_CLERK_ENV}=1 (document why)"
        )
    return rc


# --- clerk stage: end ---


def main() -> int:
    skipped_tests = os.environ.get(SKIP_TESTS_ENV) == "1"
    if skipped_tests:
        print(f"local gate: {SKIP_TESTS_ENV}=1 — SKIPPING pytest (lints still run)")
    skipped_css = os.environ.get(SKIP_CSS_ENV) == "1"
    if skipped_css:
        print(f"local gate: {SKIP_CSS_ENV}=1 — SKIPPING css staleness check")

    # Dev-path lockfile freshness — matches Docker's `uv sync --frozen`.
    # Runs the uv BINARY (not python -m), so it sits outside GATE_STEPS.
    print("local gate: uv lock --check")
    # Args are a fully-literal list — ruff's S603 doesn't fire on this shape
    # (unlike the sys.executable/*argv call above), so no noqa is needed.
    lock = subprocess.run(["uv", "lock", "--check"], cwd=REPO_ROOT, check=False)
    if lock.returncode != 0:
        print("local gate: FAILED at uv lock --check (pyproject/uv.lock drift)")
        return lock.returncode

    if os.environ.get("IDRAA_GATE_SKIP_AUDIT") == "1":
        print("local gate: IDRAA_GATE_SKIP_AUDIT=1 — SKIPPING pip-audit")
    else:
        print("local gate: pip-audit (fixable-vuln policy)")
        # Fully-literal list, same shape as the uv lock --check call above —
        # ruff's S603 doesn't fire on this shape, so no noqa is needed.
        audit = subprocess.run([sys.executable, "scripts/sca_gate.py"], cwd=REPO_ROOT, check=False)
        if audit.returncode != 0:
            print("local gate: FAILED at pip-audit — fix, or suppress with rationale")
            return audit.returncode

    rc = clerk_stage(os.environ)
    if rc != 0:
        return rc

    for label, argv in steps_to_run():
        rc = run_step(label, argv)
        if rc != 0:
            print()
            print(f"local gate FAILED at: {label} (exit {rc})")
            print("Fix and re-push, or bypass with `git push --no-verify` (document why).")
            return rc
    print("local gate: all steps passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
