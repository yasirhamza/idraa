"""pip-audit policy gate (#555). FAIL on fixable+unsuppressed, WARN otherwise.

pip-audit's JSON has fixability but not severity; severity-aware gating is
dependency-review-action's job on the PR path. Suppressions: one GHSA/PYSEC id
per line, each immediately preceded by a comment stating the reason + a
review-by date — a bare id FAILS the gate (machine-enforced auditability).
Tool errors fail CLOSED with a pointer at the offline hatch
IDRAA_GATE_SKIP_AUDIT=1 (document the reason in the next commit).

Marker-agnostic (#182): pip-audit drops every requirement whose environment
marker does not match the RUNNING interpreter, so a lock fork such as
``anyio==4.15.1 ; python_full_version >= '3.15'`` (or a win32-only pin) was
never audited. The export is flattened to bare ``name==version`` pins with the
markers stripped and audited with ``--no-deps --disable-pip``; because
pip-audit rejects two pins of one package in a single file, forked pins are
spread across as many requirement files as the widest fork and the results
unioned.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

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


def partition_export(text: str) -> list[list[str]]:
    """Strip markers from a ``uv export`` file; one pin list per fork layer.

    Layer ``i`` holds the ``i``-th distinct version of every package that has
    more than ``i`` versions, so no layer pins one package twice. Local
    editable/path lines (``-e .``) and comments are skipped — they are
    first-party code, not index packages.
    """
    versions: dict[str, list[str]] = {}
    for raw in text.splitlines():
        line = raw.split(";", 1)[0].strip()
        if not line or line.startswith(("#", "-", ".", "/")):
            continue
        name = re.split(r"[\[=<>!~ ]", line, maxsplit=1)[0].lower().replace("_", "-")
        pins = versions.setdefault(name, [])
        if line not in pins:
            pins.append(line)
    depth = max((len(v) for v in versions.values()), default=0)
    return [[pins[i] for pins in versions.values() if len(pins) > i] for i in range(depth)]


def evaluate(deps: list[dict[str, Any]], suppressed: set[str]) -> tuple[list[str], list[str]]:
    failures, warnings = [], []
    for dep in deps:
        for v in dep.get("vulns", []):
            label = f"{dep['name']}: {v['id']} (fixes: {v.get('fix_versions') or 'none'})"
            if v["id"] in suppressed or not v.get("fix_versions"):
                warnings.append(label)
            else:
                failures.append(label)
    return list(dict.fromkeys(failures)), list(dict.fromkeys(warnings))


def _audit(pins: list[str]) -> subprocess.CompletedProcess[str]:
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as tf:
        tf.write("\n".join(pins) + "\n")
        req = tf.name
    try:
        return subprocess.run(  # noqa: S603 — direct module run, no nested uv resolve
            [
                sys.executable,
                "-m",
                "pip_audit",
                "-r",
                req,
                "--no-deps",
                "--disable-pip",
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


def main() -> int:
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as tf:
        exported = tf.name
    try:
        subprocess.run(  # noqa: S603 — fixed argv, no user input
            ["uv", "export", "--frozen", "--no-dev", "--no-hashes", "-q", "-o", exported],
            cwd=REPO_ROOT,
            check=True,
        )
        layers = partition_export(Path(exported).read_text())
    finally:
        os.unlink(exported)
    if not layers:
        print(f"sca_gate: empty dependency export — failing closed; {SKIP_HINT}")
        return 2
    deps: list[dict[str, Any]] = []
    for pins in layers:
        proc = _audit(pins)
        # pip-audit: 0 = clean, 1 = vulns found, anything else = tool/network error.
        if proc.returncode not in (0, 1):
            print(proc.stderr, file=sys.stderr)
            print(
                f"sca_gate: pip-audit errored (exit {proc.returncode}) — failing closed; {SKIP_HINT}"
            )
            return 2
        try:
            deps.extend(json.loads(proc.stdout)["dependencies"])  # KeyError = schema drift
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            print(f"sca_gate: unparseable pip-audit output ({exc}) — failing closed; {SKIP_HINT}")
            return 2
    try:
        failures, warnings = evaluate(deps, parse_suppressions(SUPPRESSIONS))
    except (KeyError, TypeError) as exc:
        print(f"sca_gate: unparseable pip-audit output ({exc}) — failing closed; {SKIP_HINT}")
        return 2
    audited = sum(len(p) for p in layers)
    print(f"sca_gate: audited {audited} pins across {len(layers)} marker-fork layer(s)")
    for w in warnings:
        print(f"sca_gate WARN: {w}")
    for f in failures:
        print(f"sca_gate FAIL (fixable, unsuppressed): {f}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
