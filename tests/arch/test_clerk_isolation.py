"""Idraa takes no Python dependency on the clerk, and the adoption's footprint is fixed
(adoption design §2, §6, §7): the rollback procedure removes or edits exactly these files."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent

_IMPORT = re.compile(r"^\s*(?:from|import)\s+superpowers_clerk\b", re.MULTILINE)
# Any of these in a tracked file marks it as part of the adoption.
_TOKEN = re.compile(
    r"superpowers[-_]clerk|clerk\.toml|IDRAA_CLERK|\.clerk/manifests|clerk_manifests"
)
_SCANNED_TREES = {"src", "fair_cam", "scripts", "security", "tests"}

FOOTPRINT = frozenset(
    {
        "clerk.toml",
        ".clerk/README.md",
        ".clerk/manifests/lanes.json",
        ".clerk/manifests/surfaces.json",
        "scripts/clerk_manifests.py",
        "scripts/run_local_gate.py",
        "tests/scripts/test_clerk_config.py",
        "tests/scripts/test_clerk_manifests.py",
        "tests/scripts/test_run_local_gate_clerk.py",
        "tests/arch/test_clerk_isolation.py",
        "docs/security/threat-model.md",  # its sections 12 and 13 name clerk.toml and the generator (Task 3)
    }
)
GENERATED = (
    ".clerk/manifests/citations.json"  # excluded from the scan: its entries cite product files
)


def _tracked() -> list[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    out = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True, env=env
    )
    return [p for p in out.stdout.decode("utf-8").split("\0") if p]


def _text(rel: str) -> str:
    try:
        return (ROOT / rel).read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return ""


def test_nothing_imports_the_clerk() -> None:
    offenders = [
        p
        for p in _tracked()
        if p.endswith(".py") and p.split("/")[0] in _SCANNED_TREES and _IMPORT.search(_text(p))
    ]
    assert offenders == []
    assert "superpowers-clerk" not in _text("pyproject.toml")
    assert "superpowers-clerk" not in _text("uv.lock")


def test_adoption_footprint_is_the_allowlist() -> None:
    tracked = _tracked()
    touched = {p for p in tracked if p != GENERATED and _TOKEN.search(_text(p))}
    assert touched == FOOTPRINT, {"unexpected": touched - FOOTPRINT, "missing": FOOTPRINT - touched}
    assert GENERATED in tracked and (ROOT / GENERATED).is_file()
