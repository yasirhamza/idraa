#!/usr/bin/env python3
"""Branch-state lint — pre-push gate against scope-creep failure modes.

Two checks, both deterministic and cheap, both run external to the agent's
context (cannot be skipped by reasoning under context pressure):

1. Test count regression. Counts net test removals on this branch vs the
   base ref (main). Fails if net is negative. Catches "I deleted a test
   to make CI green" — a documented agent-under-pressure failure mode
   (see obra/superpowers issue #528 + the Autonomous Coding Toolkit gate
   pattern).

2. Working tree clean. Verifies `git status --porcelain` is empty before
   push. Catches "I'll commit it later" — ensures every uncommitted change
   is either landed or explicitly gitignored before pushing a branch.

Bypass: `git push --no-verify`. Document the reason in the next commit.

Inspired by the Autonomous Coding Toolkit's deterministic-gate pattern
discussed in the obra/superpowers#528 thread on review-skipping failure.
Spec philosophy: external gates beat in-context instructions.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Refs to try in order when looking for the merge-base of this branch.
# Falls back through alternatives so the script is portable across
# repos that name their default branch differently.
BASE_REF_CANDIDATES: tuple[str, ...] = ("main", "origin/main", "master", "origin/master")


def run(cmd: list[str]) -> tuple[int, str, str]:
    """Run cmd in repo root; return (rc, stdout, stderr). Never raises.

    cmd is a hard-coded list of git arguments built from constants in this
    module; no user input reaches subprocess. shell=False (default).
    """
    proc = subprocess.run(  # noqa: S603 — args are list literals, no shell
        cmd, capture_output=True, text=True, cwd=REPO_ROOT, check=False
    )
    return proc.returncode, proc.stdout, proc.stderr


def find_base_ref() -> str | None:
    """Return the first ref in BASE_REF_CANDIDATES that exists, or None."""
    for ref in BASE_REF_CANDIDATES:
        rc, _, _ = run(["git", "rev-parse", "--verify", "--quiet", ref])
        if rc == 0:
            return ref
    return None


def check_test_count_regression(base: str) -> list[str]:
    """Compare net `def test_*` deltas in tests/ on HEAD vs base. Returns errors."""
    rc, diff, err = run(["git", "diff", f"{base}..HEAD", "--", "tests/"])
    if rc != 0:
        # Diff itself failed (uncommon — bad ref, etc.); surface as a soft error.
        return [f"Test-count gate: cannot diff tests/ vs {base}: {err.strip()}"]

    added = len(re.findall(r"^\+\s*def test_", diff, re.MULTILINE))
    removed = len(re.findall(r"^-\s*def test_", diff, re.MULTILINE))
    net = added - removed

    if net < 0:
        return [
            (
                f"Test count regression: {removed} test(s) removed, {added} added "
                f"(net: {net}) on this branch vs {base}."
            ),
            (
                "Tests should only go up. If a removal is intentional (e.g., a "
                "deprecation), bypass with `git push --no-verify` and document "
                "the justification in the next commit."
            ),
        ]
    return []


def check_working_tree_clean() -> list[str]:
    """Fail if `git status --porcelain` is non-empty. Returns errors."""
    rc, out, _err = run(["git", "status", "--porcelain"])
    if rc != 0:
        return ["Working-tree gate: `git status` failed."]

    porcelain = out.strip()
    if not porcelain:
        return []

    lines = porcelain.splitlines()
    truncated = lines[:10]
    overflow = max(0, len(lines) - 10)
    return [
        f"Working tree is not clean ({len(lines)} entries):",
        *[f"  {line}" for line in truncated],
        *([f"  …(+{overflow} more)"] if overflow else []),
        "Either commit, gitignore, or stash these before pushing.",
        "Bypass: `git push --no-verify` (rare; document why in next commit).",
    ]


def main(argv: list[str]) -> int:
    base = argv[1] if len(argv) > 1 else (find_base_ref() or "")
    if not base:
        print(
            "Branch-state lint: no base ref found "
            f"(tried {', '.join(BASE_REF_CANDIDATES)}). Skipping."
        )
        return 0

    errors: list[str] = []
    errors.extend(check_test_count_regression(base))
    errors.extend(check_working_tree_clean())

    if errors:
        print("Branch-state lint failed (pre-push gate):")
        print()
        for line in errors:
            print(f"  {line}")
        print()
        print(
            "These gates are deterministic — they run external to agent "
            "reasoning and cannot be skipped under context pressure.\n"
            "See feedback_scope_creep_killed_v1_v2.md."
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
