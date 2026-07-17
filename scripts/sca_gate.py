"""pip-audit policy gate (#555). FAIL on fixable+unsuppressed, WARN otherwise.

pip-audit's JSON has fixability but not severity; severity-aware gating is
dependency-review-action's job on the PR path. Suppressions: one GHSA/PYSEC id
per line, each immediately preceded by a comment stating the reason + a
review-by date — a bare id FAILS the gate (machine-enforced auditability).
Tool errors fail CLOSED with a pointer at the offline hatch
IDRAA_GATE_SKIP_AUDIT=1 (document the reason in the next commit).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SUPPRESSIONS = REPO_ROOT / "scripts" / "sca_suppressions.txt"
SKIP_HINT = "offline? set IDRAA_GATE_SKIP_AUDIT=1 and document why in the next commit"


def parse_suppressions(path: Path) -> set[str]:
    """Ids must be preceded by a reason comment; a bare id raises."""
    if not path.exists():
        return set()
    ids: set[str] = set()
    prev_comment = False
    for line in path.read_text().splitlines():
        s = line.strip()
        if not s:
            prev_comment = False
        elif s.startswith("#"):
            prev_comment = True
        else:
            if not prev_comment:
                raise ValueError(f"suppression {s!r} lacks a reason comment above it")
            ids.add(s)
            prev_comment = False
    return ids


def evaluate(deps: list[dict], suppressed: set[str]) -> tuple[list[str], list[str]]:
    failures, warnings = [], []
    for dep in deps:
        for v in dep.get("vulns", []):
            label = f"{dep['name']}: {v['id']} (fixes: {v.get('fix_versions') or 'none'})"
            if v["id"] in suppressed or not v.get("fix_versions"):
                warnings.append(label)
            else:
                failures.append(label)
    return failures, warnings


def main() -> int:
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as tf:
        req = tf.name
    try:
        subprocess.run(  # noqa: S603 — fixed argv, no user input
            ["uv", "export", "--frozen", "--no-dev", "--no-hashes", "-o", req],
            cwd=REPO_ROOT,
            check=True,
        )
        proc = subprocess.run(  # noqa: S603 — direct module run, no nested uv resolve
            [
                sys.executable,
                "-m",
                "pip_audit",
                "-r",
                req,
                "--format",
                "json",
                "--progress-spinner",
                "off",
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        os.unlink(req)
    # pip-audit: 0 = clean, 1 = vulns found, anything else = tool/network error.
    if proc.returncode not in (0, 1):
        print(proc.stderr, file=sys.stderr)
        print(f"sca_gate: pip-audit errored (exit {proc.returncode}) — failing closed; {SKIP_HINT}")
        return 2
    try:
        deps = json.loads(proc.stdout)["dependencies"]  # KeyError = schema drift
        failures, warnings = evaluate(deps, parse_suppressions(SUPPRESSIONS))
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        print(f"sca_gate: unparseable pip-audit output ({exc}) — failing closed; {SKIP_HINT}")
        return 2
    for w in warnings:
        print(f"sca_gate WARN: {w}")
    for f in failures:
        print(f"sca_gate FAIL (fixable, unsuppressed): {f}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
