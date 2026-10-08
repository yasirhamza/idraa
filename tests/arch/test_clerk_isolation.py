"""Idraa takes no Python dependency on the clerk, and the adoption's footprint is fixed
(adoption design §2, §6, §7): the rollback procedure removes or edits exactly these files."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
SELF = Path(__file__).resolve().relative_to(ROOT).as_posix()

# Any use of the module name in Python source: import statements in every form, importlib,
# pytest.importorskip, find_spec. This file is the one exception (its patterns name the module).
_MODULE = re.compile(rb"\bsuperpowers_clerk\b")
# Every PEP 503 spelling of the distribution name: runs of separators collapse, so `superpowers__clerk`
# and `superpowers-.-clerk` name the same distribution.
_DIST = re.compile(rb"superpowers[-_.]+clerk", re.IGNORECASE)
# Any of these in a tracked file marks it as part of the adoption.
_TOKEN = re.compile(
    rb"superpowers[-_.]+clerk|clerk\.toml|IDRAA_CLERK|IDRAA_GATE_SKIP_CLERK|\.clerk(?:/|[\"'])|clerk_manifests"
)

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
        # Its sections 12 and 13 name clerk.toml and the generator (Task 3).
        "docs/security/threat-model.md",
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


def _bytes(rel: str) -> bytes:
    """Raw bytes, as the rollback check's grep reads them: an undecodable file still scans."""
    try:
        return (ROOT / rel).read_bytes()
    except OSError:
        return b""


def test_nothing_imports_the_clerk() -> None:
    py = [p for p in _tracked() if p.endswith((".py", ".pyi", ".ipynb"))]
    assert SELF in py and "scripts/run_local_gate.py" in py  # the scan is not vacuous
    assert [p for p in py if p != SELF and _MODULE.search(_bytes(p))] == []
    assert not _DIST.search(_bytes("pyproject.toml"))
    assert not _DIST.search(_bytes("uv.lock"))


def test_adoption_footprint_is_the_allowlist() -> None:
    tracked = _tracked()
    touched = {p for p in tracked if p != GENERATED and _TOKEN.search(_bytes(p))}
    assert touched == FOOTPRINT, {"unexpected": touched - FOOTPRINT, "missing": FOOTPRINT - touched}
    assert GENERATED in tracked, f"{GENERATED} is not tracked"
    assert (ROOT / GENERATED).is_file(), f"{GENERATED} is missing from the working tree"
