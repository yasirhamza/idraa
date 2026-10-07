"""The clerk's project inputs: clerk.toml and the hand-written manifests (spec §3.1, §3.2, §3.4)."""

from __future__ import annotations

import fnmatch
import functools
import html
import json
import os
import re
import subprocess
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
CLERK_TOML = ROOT / "clerk.toml"
LANES = ROOT / ".clerk" / "manifests" / "lanes.json"
SURFACES = ROOT / ".clerk" / "manifests" / "surfaces.json"

ESCALATION_SUBJECTS = [
    "fair-math",
    "calibration",
    "derivation",
    "auth",
    "csrf",
    "org-scoping",
    "migration",
    "import-export",
    "templating",
    "rbac",
    "run-limits",
    "audit",
    "secrets",
    "gate-integrity",
]
LANE_NAMES = {"methodology", "spec-compliance", "architect", "security"}
_SPACE = re.compile(r"\s+")


def normalise(text: str) -> str:
    """The clerk's surfaces normalisation: entities unescaped, whitespace collapsed, case kept."""
    return _SPACE.sub(" ", html.unescape(text)).strip()


def segment_glob(pattern: str) -> bool:
    """A well-formed segment glob: relative, no empty / `.` / `..` segment, `**` only whole."""
    if not pattern or pattern.startswith("/"):
        return False
    segments = pattern.split("/")
    if any(seg in ("", ".", "..") for seg in segments):
        return False
    return not any("**" in seg and seg != "**" for seg in segments)


def glob_match(pattern: str, rel: str) -> bool:
    """The clerk's `boundary.glob_match`: segment-wise, `**` spans whole segments, case-sensitive."""
    pat = tuple(pattern.split("/"))
    parts = tuple(rel.split("/"))

    @functools.cache
    def go(i: int, j: int) -> bool:
        if i == len(pat):
            return j == len(parts)
        if pat[i] == "**":
            return go(i + 1, j) or (j < len(parts) and go(i, j + 1))
        return j < len(parts) and fnmatch.fnmatchcase(parts[j], pat[i]) and go(i + 1, j + 1)

    return bool(rel) and go(0, 0)


def tracked() -> list[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    out = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True, env=env
    )
    return [p for p in out.stdout.decode("utf-8").split("\0") if p]


def _toml() -> dict[str, object]:
    return tomllib.loads(CLERK_TOML.read_text(encoding="utf-8"))


def _lanes() -> dict[str, dict[str, list[str]]]:
    return json.loads(LANES.read_text(encoding="utf-8"))["lanes"]


def _escalation_globs() -> list[str]:
    return list(_toml()["escalation"]["paths"])


def test_clerk_toml_parses_and_pins() -> None:
    cfg = _toml()
    assert set(cfg) == {"project", "judge", "trust", "escalation", "gates", "boundary"}
    assert cfg["project"] == "idraa"
    assert cfg["judge"] == {
        "backend": "jev",
        "model": "jev-1.13.0",
        "keychain_service": "jev-eval-typesafe-key",
        "allow_external_judge": True,
    }
    assert cfg["trust"] == {"confidence": 0.95, "min_actionable": 19}
    assert cfg["boundary"] == {
        "workspace_allow": ["docs/superpowers/**"],
        "allow_external_untracked": False,
    }
    assert cfg["gates"] == {
        "citations_manifest": ".clerk/manifests/citations.json",
        "surfaces_manifest": ".clerk/manifests/surfaces.json",
        "lanes_manifest": ".clerk/manifests/lanes.json",
        "scope_allowlist": ["uv.lock"],
    }


def test_escalation_subjects_and_paths() -> None:
    esc = _toml()["escalation"]
    assert set(esc) == {"subjects", "paths"}
    assert esc["subjects"] == ESCALATION_SUBJECTS
    paths = esc["paths"]
    assert set(paths.values()) == set(ESCALATION_SUBJECTS), (
        "every subject has a path and no path has a stray subject"
    )
    assert all(segment_glob(g) for g in paths), [g for g in paths if not segment_glob(g)]
    assert paths["fair_cam/**"] == "fair-math"
    assert paths["alembic/**"] == "migration"
    assert paths["src/idraa/middleware/csrf.py"] == "csrf"
    assert paths["src/idraa/repositories/**"] == "org-scoping"
    assert paths["src/idraa/templates/**"] == "templating"
    assert paths[".github/**"] == "gate-integrity"
    for own in (
        "clerk.toml",
        ".clerk/**",
        "scripts/run_local_gate.py",
        "tests/scripts/test_clerk_*.py",
        "tests/arch/**",
    ):
        assert paths[own] == "gate-integrity", (
            own
        )  # a retag would drop the per-task security dispatch
    assert paths["scripts/clerk_manifests.py"] == "gate-integrity"
    engine = {
        "src/idraa/services/run_executor.py": "derivation",
        "src/idraa/services/shapley.py": "derivation",
        "src/idraa/services/calibration.py": "calibration",
        "data/**": "calibration",
    }
    for glob, subject in engine.items():
        assert paths[glob] == subject, glob


def test_every_glob_matches_a_tracked_file() -> None:
    """A renamed file would otherwise silently void a BLOCKER-class escalation path or a lane."""
    files = tracked()
    globs = set(_escalation_globs()) | {g for lane in _lanes().values() for g in lane["globs"]}
    dead = sorted(g for g in globs if not any(glob_match(g, f) for f in files))
    assert dead == [], dead


def test_lanes_manifest_four_lanes() -> None:
    lanes = _lanes()
    assert set(lanes) == LANE_NAMES
    for name, lane in lanes.items():
        assert set(lane) == {"globs", "subjects"}, name
        assert lane["globs"], name
        assert all(segment_glob(g) for g in lane["globs"]), (name, lane["globs"])
    assert lanes["spec-compliance"] == {"globs": ["**"], "subjects": []}
    for name in LANE_NAMES - {"spec-compliance"}:
        assert lanes[name]["subjects"], name
    assert "clerk.toml" in lanes["security"]["globs"]
    assert ".clerk/**" in lanes["security"]["globs"]
    assert "scripts/clerk_manifests.py" in lanes["security"]["globs"]
    assert "fair_cam/**" in lanes["methodology"]["globs"]


def test_lane_subjects_cover_escalation_subjects() -> None:
    covered = {s for lane in _lanes().values() for s in lane["subjects"]}
    assert set(ESCALATION_SUBJECTS) <= covered, set(ESCALATION_SUBJECTS) - covered


def test_security_lane_files_are_escalation_paths() -> None:
    """Spec §3.1: the clerk design's 'security-lane files always escalate', done by configuration."""
    globs = _escalation_globs()
    lane = _lanes()["security"]["globs"]
    uncovered = sorted(
        f
        for f in tracked()
        if any(glob_match(g, f) for g in lane) and not any(glob_match(g, f) for g in globs)
    )
    assert uncovered == [], uncovered


def test_methodology_lane_files_are_escalation_paths() -> None:
    """FAIR math, calibration and derivations always escalate (CLAUDE.md: the methodology reviewer is mandatory)."""
    globs = _escalation_globs()
    lane = _lanes()["methodology"]["globs"]
    uncovered = sorted(
        f
        for f in tracked()
        if any(glob_match(g, f) for g in lane) and not any(glob_match(g, f) for g in globs)
    )
    assert uncovered == [], uncovered


def test_gate_integrity_globs_are_security_lane_globs() -> None:
    """A gate-integrity path is always reported as touching the security lane by `lane_paths`."""
    lane = set(_lanes()["security"]["globs"])
    integrity = {g for g, s in _toml()["escalation"]["paths"].items() if s == "gate-integrity"}
    assert integrity <= lane, sorted(integrity - lane)


def test_surfaces_manifest_shape() -> None:
    sentences = json.loads(SURFACES.read_text(encoding="utf-8"))["sentences"]
    ids = [s["id"] for s in sentences]
    assert len(ids) == len(set(ids)) and len(ids) >= 5
    for s in sentences:
        assert set(s) == {"id", "text", "surfaces"}, s["id"]
        assert s["text"].strip() == s["text"] and s["text"], s["id"]
        assert len(s["surfaces"]) >= 2, s["id"]
        assert len(set(s["surfaces"])) == len(s["surfaces"]), s["id"]


def test_surfaces_manifest_sentences_present() -> None:
    sentences = json.loads(SURFACES.read_text(encoding="utf-8"))["sentences"]
    files = set(tracked())
    missing: list[tuple[str, str]] = []
    for s in sentences:
        for surface in s["surfaces"]:
            assert ".." not in surface.split("/") and surface in files, (s["id"], surface)
            if normalise(s["text"]) not in normalise((ROOT / surface).read_text(encoding="utf-8")):
                missing.append((s["id"], surface))
    assert missing == []


def test_threat_model_cited_files_are_escalation_paths() -> None:
    """Spec §3.1 coverage rule over the manifest-pinned citations: every line-cited src/idraa/ file in the threat model carries a tag (the fenced §1 diagram is unpinned by design, §13)."""
    entries = json.loads(
        (ROOT / ".clerk" / "manifests" / "citations.json").read_text(encoding="utf-8")
    )["entries"]
    cited = sorted(
        {
            str(e["file"])
            for e in entries
            if e["doc"] == "docs/security/threat-model.md"
            and str(e["file"]).startswith("src/idraa/")
        }
    )
    assert cited, "the threat model cites product files"
    globs = _escalation_globs()
    uncovered = [f for f in cited if not any(glob_match(g, f) for g in globs)]
    assert uncovered == [], uncovered
