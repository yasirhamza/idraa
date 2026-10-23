"""The clerk's project inputs: clerk.toml and the hand-written manifests (spec §3.1, §3.2, §3.4)."""

from __future__ import annotations

import ast
import fnmatch
import functools
import html
import json
import os
import re
import subprocess
import tomllib
from collections.abc import Collection, Iterable, Iterator, Sequence
from pathlib import Path
from typing import cast

from scripts.lint_org_scoped_lookups import ORG_SCOPED_MODELS

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
        "allow_external_judge": "per-run",
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
        "scripts/mypy_ratchet.py",
        "scripts/mypy_tests_baseline.json",
        "tests/scripts/test_mypy_ratchet.py",
        "tests/scripts/test_clerk_*.py",
        "tests/arch/**",
        ".gitleaks.toml",
        "scripts/sca_suppressions.txt",
        "scripts/check_canonical_remote.py",
        "pyproject.toml",
        "**/conftest.py",
        "docs/reference/fair-departures-register.md",
        "docs/security/**",
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
    for ratchet_file in (
        "scripts/mypy_ratchet.py",
        "scripts/mypy_tests_baseline.json",
        "tests/scripts/test_mypy_ratchet.py",
    ):
        assert ratchet_file in lanes["security"]["globs"], ratchet_file
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
    """Every methodology-lane file escalates; the lane's completeness is enforced by the membership rules M-A, M-B, M-B-JS and M-C below (spec 2026-10-10, #217)."""
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
    """Spec §3.1 coverage rule over the manifest-pinned citations: every line-cited tracked file in the threat model carries a tag (the fenced §1 diagram is unpinned by design, §13)."""
    entries = json.loads(
        (ROOT / ".clerk" / "manifests" / "citations.json").read_text(encoding="utf-8")
    )["entries"]
    cited = sorted({str(e["file"]) for e in entries if e["doc"] == "docs/security/threat-model.md"})
    assert cited, "the threat model cites product files"
    globs = _escalation_globs()
    uncovered = [f for f in cited if not any(glob_match(g, f) for g in globs)]
    assert uncovered == [], uncovered


# --------------------------------------------------------------------------------------------
# Lane completeness (spec 2026-10-10, #217 methodology, #218 security, #243): five membership
# rules. Each detector is a pure function, exercised on snippets by `test_detectors_on_snippets`,
# so a broken detector cannot pass a rule vacuously (the per-rule minimums back that up).
# --------------------------------------------------------------------------------------------

EXEMPTIONS = ROOT / ".clerk" / "manifests" / "lane_exemptions.json"
EXEMPTIONS_REL = ".clerk/manifests/lane_exemptions.json"
REGISTER = "docs/reference/fair-departures-register.md"

# Minimums, about 10% under the counts measured at the base (spec section 3).
MIN_M_A, MIN_M_B, MIN_M_B_JS, MIN_M_C, MIN_S_A = 100, 48, 2, 64, 66

SCOPE_PREFIXES = ("src/idraa/", "scripts/", "alembic/versions/")
VENDOR_PREFIX = "src/idraa/static/vendor/"
NUMERIC_MODULES = frozenset(
    {"numpy", "scipy", "math", "fair_cam", "statistics", "random", "pandas"}
)
# `decimal` is deliberately absent: it would pull in 21 money/model/schema files whose decimals
# are storage precision, not model math (spec section 3, M-B).
MATH_CALL = re.compile(r"\bMath\.(?:exp|expm1|log|log1p|log2|log10|pow|sqrt|cbrt)\s*\(")
# M-C: the project annotates every methodology finding at its fix site (spec section 3, M-C).
METH_FINDING = re.compile(
    r"Meth-?[A-Z0-9]|METH-[0-9]|METH[- ][A-Z]-?[0-9]|\b[Mm]eth [A-Z]-?[0-9]"
    r"|\b[Mm]ethodology [A-Z]-?[0-9]|methodology[- ](?:review|gate|finding)"
    r"|(?i:methodology[- ](?:audit|decision|check|owned))|[Mm]ethodology \+ [a-z]"
    r"|\n\s*Methodology: "
)

METHODOLOGY_EXEMPTION_KINDS = frozenset({"presentation", "input-validation", "dev-tool"})
SECURITY_EXEMPTION_KINDS = frozenset({"dev-tool"})
PINNED_METHODOLOGY_EXEMPTIONS = {
    "src/idraa/formatting.py": "presentation",
    "src/idraa/services/chart_svg.py": "presentation",
    "scripts/bench_mc_iterations.py": "dev-tool",
    "scripts/curation_check/report.py": "dev-tool",
    "scripts/curation_check/__main__.py": "dev-tool",
    "scripts/sweep_run_samples_finite.py": "dev-tool",
    "scripts/curation_check/README.md": "dev-tool",
    "scripts/curation_check/flags.py": "dev-tool",
    "scripts/curation_check/library.py": "dev-tool",
}
PINNED_SECURITY_EXEMPTIONS: dict[str, str] = {}

# Existing tags that must not be retagged (spec section 2): a retag would silently change which
# subject the judge instances over the file carry.
PINNED_TAGS = {
    "src/idraa/services/run_executor.py": "derivation",
    "src/idraa/services/qualitative_converter.py": "derivation",
    "src/idraa/services/attack_coverage.py": "derivation",
    "src/idraa/services/overlays.py": "calibration",
    "src/idraa/services/pdf_report.py": "import-export",
    "src/idraa/app.py": "templating",
    "src/idraa/services/loss_pinning.py": "calibration",
    "src/idraa/services/qualitative_bands.py": "calibration",
    "src/idraa/services/wizard_finalize.py": "derivation",
    "src/idraa/services/reports.py": "import-export",
    "src/idraa/help_content.py": "templating",
    # the existing exact key of every file the lane audit adds to a lane
    "src/idraa/config.py": "run-limits",
    "src/idraa/services/fair_cam_validation.py": "derivation",
    "src/idraa/services/runs.py": "run-limits",
    "src/idraa/services/scenarios.py": "org-scoping",
    "tests/routes/test_threat_community_pages.py": "templating",
}

# Every path the lane audit adds to a lane (spec sections 4.1 and 4.3), in table order.
METHODOLOGY_ADDITIONS = (
    "src/idraa/routes/scenario_form_helpers.py",
    "src/idraa/routes/scenario_loss_pin.py",
    "src/idraa/routes/scenario_wizard_seeding.py",
    "src/idraa/routes/scenarios.py",
    "src/idraa/app.py",
    "src/idraa/services/pdf_report.py",
    "src/idraa/services/reports.py",
    "src/idraa/services/reporting_currency.py",
    "src/idraa/services/scenario_export.py",
    "src/idraa/services/scenario_import_parsers.py",
    "src/idraa/services/controls_importer.py",
    "src/idraa/schemas/threat_community.py",
    "src/idraa/schemas/overlay.py",
    "scripts/capacity_bound_figures.py",
    "scripts/sigma_recal_figures.py",
    "alembic/versions/b3f8a2d94c1e_d12_tef_pert_collapse.py",
    "alembic/versions/e3a1c4f7b2d9_repair_divergent_lognorm_fits.py",
    "alembic/versions/ffed7c509563_capacity_max_backfill.py",
    "src/idraa/templates/runs/detail.html",
    "src/idraa/templates/scenarios/form.html",
    "src/idraa/templates/scenarios/wizard/_fair_params_form_inner.html",
    "tests/services/test_wizard_questions.py",
    "tests/unit/test_threat_community_seed.py",
    "tests/integration/test_library_loss_differentiation.py",
    "tests/integration/test_loss_form_stakeholder_test.py",
    "src/idraa/static/js/loss_preview.js",
    "tests/unit/test_loss_preview_parity.py",
    "src/idraa/models/enums.py",
    "src/idraa/services/capacity_bound_copy.py",
    "src/idraa/templates/help/figures/factor_tree.html",
    "src/idraa/templates/help/figures/lec_anatomy.html",
    "src/idraa/templates/scenarios/wizard/step_4_impact.html",
    "src/idraa/templates/scenarios/_loss_readout.html",
    "src/idraa/schemas/run_snapshot.py",
    "src/idraa/routes/controls.py",
    "src/idraa/help_content.py",
    "scripts/regen_r_oracle.R",
    "scripts/audit_library_vulnerability.py",
    "scripts/issue_131_audit_log_migration.py",
    "notebooks/fair_cam_example.ipynb",
    "src/idraa/services/seed_library_loader.py",
    "src/idraa/static/js/charts.js",
    "tests/contracts/test_weight_robustness_covariation.py",
    "tests/migrations/test_threat_communities_migration.py",
    "alembic/versions/c4e4d441087c_sigma_recalibration.py",
    "alembic/versions/291038b726fd_backfill_scenario_attack_mappings_from_.py",
    "alembic/versions/6108f3899a78_attack_catalog_and_mappings.py",
    "alembic/versions/f9330f3b7208_rerun_attack_backfill_post_curation.py",
    "scripts/issue_129_audit_log_migration.py",
    "src/idraa/config.py",
    "src/idraa/routes/register_import.py",
    "src/idraa/schemas/attack_catalog.py",
    "src/idraa/services/library_bundle_export.py",
    "src/idraa/services/register_import.py",
    "src/idraa/services/scenario_import.py",
    "src/idraa/services/scenarios.py",
    "src/idraa/services/wizard_state.py",
    "src/idraa/templates/scenarios/view.html",
    "src/idraa/templates/macros/chart.html",
    "src/idraa/templates/controls/_clear_capability_modal.html",
    "src/idraa/templates/macros/unit_aware_inputs.html",
    "src/idraa/templates/controls/import.html",
    "src/idraa/templates/controls/_assignment_row.html",
    "src/idraa/templates/library/threat_community_detail.html",
    "tests/routes/test_threat_community_pages.py",
    "src/idraa/templates/runs/_results_panel.html",
    "src/idraa/templates/runs/components/verdict_strip.html",
    "src/idraa/templates/runs/components/control_ledger.html",
    "src/idraa/templates/scenarios/attack_coverage.html",
    "tests/services/test_weight_robustness_ensemble.py",
    "tests/services/test_shapley_453_regression.py",
    "scripts/sweep_library_secondary_response.py",
    "src/idraa/models/overlay.py",
    "src/idraa/templates/scenarios/confirm_loss_refresh.html",
    "src/idraa/schemas/control_library.py",
    "src/idraa/schemas/crosswalk.py",
    "src/idraa/services/library_bundle_import.py",
    "src/idraa/services/scenario_library.py",
    "src/idraa/templates/library/threat_communities.html",
    "alembic/versions/a7c3f9b21e60_recurate_pilot_entries.py",
    "alembic/versions/d4fc657eb424_recurate_rollout_t1_entries.py",
    "alembic/versions/b8d3f6a1c4e7_recurate_rollout_t2_entries.py",
    "alembic/versions/a3f7c1e9b2d4_recurate_rollout_t3_entries.py",
    "alembic/versions/e5f1a9c3d7b2_epic_f_library_curation.py",
    "src/idraa/services/sample_export.py",
    "src/idraa/schemas/control.py",
    "src/idraa/templates/overlays/edit.html",
    "src/idraa/templates/organization/form.html",
    "alembic/versions/f1a2b3c4d5e6_crosswalk_ext_patch_resistance.py",
    "alembic/versions/e7b1c9d4a2f8_crosswalk_ext_provenance.py",
    "alembic/versions/63cfe62ef5a7_insert_attack_coverage_new_entries.py",
    "alembic/versions/380ccba92ebd_reclassify_two_library_entries_to_third_party_revenue.py",
    "src/idraa/models/fx_rate.py",
    "src/idraa/services/crosswalk_reconciliation.py",
    "src/idraa/templates/register_import/preview.html",
    "tests/integration/test_seed_library_lognormal.py",
    "tests/unit/test_loss_anchor_tables.py",
    "tests/unit/test_loss_form_targets.py",
    "tests/integration/test_fair_cam_v3_unit_type_parity.py",
    "tests/unit/test_seed_balance.py",
    "tests/unit/test_attack_vector_coverage.py",
    "tests/unit/test_library_taxonomy_coverage.py",
    "tests/unit/test_netdiligence_sigma_check.py",
    "src/idraa/models/_methodology.py",
    "src/idraa/routes/library_overrides.py",
    "src/idraa/templates/library/overrides/form.html",
    "src/idraa/templates/register_import/report.html",
    "src/idraa/templates/register_import/upload.html",
    "src/idraa/services/runs.py",
    "src/idraa/services/attack_mappings.py",
    "src/idraa/models/scenario.py",
    "tests/unit/test_sigma_calibration_constant.py",
    "tests/migrations/test_attack_full_mappings_seed.py",
    "tests/test_anti_regression.py",
    "src/idraa/services/dashboard.py",
    "tests/unit/test_attack_rationale_quotes_entry.py",
)
SECURITY_ADDITIONS = (
    "src/idraa/__main__.py",
    "src/idraa/services/attack_coverage.py",
    "src/idraa/services/attack_mappings.py",
    "src/idraa/services/control_library.py",
    "src/idraa/services/control_resync.py",
    "src/idraa/services/controls_maintenance.py",
    "src/idraa/services/dashboard.py",
    "src/idraa/services/fx_rates.py",
    "src/idraa/services/loss_pinning.py",
    "src/idraa/services/overlays.py",
    "src/idraa/services/qualitative_bands.py",
    "src/idraa/services/qualitative_converter.py",
    "src/idraa/services/run_executor.py",
    "src/idraa/services/scenario_control_recommendations.py",
    "src/idraa/services/scenario_library.py",
    "src/idraa/services/sme_directory.py",
    "src/idraa/services/wizard_finalize.py",
    "src/idraa/services/wizard_state.py",
    "src/idraa/services/seed_library_loader.py",
    "src/idraa/schemas/_csv.py",
    "src/idraa/schemas/scenario.py",
    "src/idraa/schemas/overlay.py",
    "src/idraa/schemas/control.py",
    "src/idraa/schemas/organization.py",
    "src/idraa/services/fair_cam_validation.py",
    "src/idraa/schemas/wizard_step3.py",
    "src/idraa/schemas/sme.py",
)


@functools.cache
def _tracked_files() -> tuple[str, ...]:
    return tuple(tracked())


def _exemptions() -> dict[str, dict[str, list[str]]]:
    data: dict[str, dict[str, list[str]]] = json.loads(EXEMPTIONS.read_text(encoding="utf-8"))
    return data


def _lane_gaps(lane: str, files: Iterable[str], *, exemptable: bool = True) -> list[str]:
    """One message per file that is not in `lane` AND tagged, and not exempt; each names the remedy."""
    globs = _lanes()[lane]["globs"]
    escalation = _escalation_globs()
    exempt = set(_exemptions()[lane]) if exemptable else set()
    remedy = (
        f"add `{{rel}}` to the {lane} lane in `.clerk/manifests/lanes.json` and give it an "
        "escalation tag"
    )
    remedy += (
        ", or list it in `.clerk/manifests/lane_exemptions.json` with a reason"
        if exemptable
        else " (a file the register names cannot be exempted)"
    )
    gaps = []
    for rel in sorted(set(files) - exempt):
        in_lane = any(glob_match(g, rel) for g in globs)
        tagged = any(glob_match(g, rel) for g in escalation)
        if in_lane and tagged:
            continue
        why = "not in the lane" if not in_lane else "in the lane but matches no escalation path"
        gaps.append(f"{rel}: {why}; " + remedy.format(rel=rel))
    return gaps


# ---- M-A: the departures register's Where / Evaluate text -----------------------------------

_HEADING = re.compile(r"^#{1,6} ")
_BULLET = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s")
_WHERE_EVALUATE_BULLET = re.compile(r"^\s*-\s+\*\*(?:Where|Evaluate)\.\*\*")
_SPAN = re.compile(r"`([^`]+)`")
_REGISTER_PREFIXES = ("", "src/idraa/", "fair_cam/", "docs/reference/")


def _continuation(lines: list[str], start: int) -> str:
    """Lines `start`... joined, up to the next bullet, heading, blank line or blockquote line."""
    taken = []
    for line in lines[start:]:
        if (
            not line.strip()
            or _HEADING.match(line)
            or _BULLET.match(line)
            or line.lstrip().startswith(">")
        ):
            break
        taken.append(line.strip())
    return " ".join(taken)


def register_segments(text: str) -> list[str]:
    """The register's Where / Evaluate text, in the four forms the register uses.

    - `- **Where.**` / `- **Evaluate.**` bullets with their continuation lines;
    - the blockquote `Where:` (from `Where:` to the end of that blockquote);
    - inline `Evaluate:` inside a note (from `Evaluate:` to the end of that paragraph);
    - the `Where` column of a table.
    """
    lines = text.splitlines()
    segments: list[str] = []
    for i, line in enumerate(lines):
        if _WHERE_EVALUATE_BULLET.match(line):
            first = _WHERE_EVALUATE_BULLET.sub("", line, count=1).strip()
            segments.append(f"{first} {_continuation(lines, i + 1)}")
        elif not line.lstrip().startswith(">") and "Evaluate:" in line:
            rest = line.split("Evaluate:", 1)[1].strip()
            segments.append(f"{rest} {_continuation(lines, i + 1)}")
    i = 0
    while i < len(lines):  # blockquotes: maximal runs of `>` lines
        if not lines[i].lstrip().startswith(">"):
            i += 1
            continue
        j = i
        while j < len(lines) and lines[j].lstrip().startswith(">"):
            j += 1
        block = " ".join(ln.lstrip()[1:].strip() for ln in lines[i:j])
        if "Where:" in block:
            segments.append(block.split("Where:", 1)[1])
        i = j
    i = 0
    while i < len(lines):  # tables with a `Where` column
        header = [c.strip() for c in lines[i].strip().strip("|").split("|")]
        if lines[i].lstrip().startswith("|") and "Where" in header and i + 1 < len(lines):
            col = header.index("Where")
            j = i + 2  # skip the separator row
            while j < len(lines) and lines[j].lstrip().startswith("|"):
                cells = [c.strip() for c in lines[j].strip().strip("|").split("|")]
                assert len(cells) == len(header), f"register table row misparsed: {lines[j][:80]}"
                segments.append(cells[col])
                j += 1
            i = j
        else:
            i += 1
    return segments


def _resolve_file(name: str, files: Sequence[str]) -> list[str]:
    for prefix in _REGISTER_PREFIXES:
        if prefix + name in files:
            return [prefix + name]
    return [f for f in files if f.endswith("/" + name)]


def resolve_register_span(span: str, files: Sequence[str]) -> tuple[list[str], str | None]:
    """(tracked files the span names, error). A non-path span resolves to nothing, no error."""
    span = span.split("::", 1)[0]
    if not span or re.search(r"\s", span):
        return [], None
    extensions = {Path(f).suffix for f in files if Path(f).suffix}
    if "/" not in span and Path(span).suffix not in extensions:
        return [], None
    if span.endswith("/") or "*" in span:
        for prefix in _REGISTER_PREFIXES:
            if span.endswith("/"):
                hits = [f for f in files if f.startswith(prefix + span)]
            else:
                hits = [f for f in files if glob_match(prefix + span, f)]
            if hits:
                return hits, None
        return [], f"`{span}` matches no tracked file"
    hits = _resolve_file(span, files)
    if not hits and "." in span and Path(span).suffix not in extensions:
        hits = _resolve_file(span.rsplit(".", 1)[0] + ".py", files)  # `module.symbol` -> its module
    if not hits:
        return [], f"`{span}` matches no tracked file"
    if len(hits) > 1:
        return [], f"`{span}` matches several tracked files: {sorted(hits)}"
    return hits, None


def register_files(text: str, files: Sequence[str]) -> tuple[set[str], list[str]]:
    """(the tracked files the register's Where / Evaluate text names, resolution errors)."""
    named: set[str] = set()
    errors: list[str] = []
    for segment in register_segments(text):
        for span in _SPAN.findall(segment):
            hits, error = resolve_register_span(span, files)
            named.update(hits)
            if error:
                errors.append(error)
    return named, sorted(set(errors))


# ---- M-B / M-B-JS / M-C: numeric code and recorded methodology findings ----------------------


def imports_numeric(source: str) -> bool:
    """Any absolute import of a numeric top-level package, at any depth (`ast.walk`)."""
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import) and any(
            a.name.split(".")[0] in NUMERIC_MODULES for a in node.names
        ):
            return True
        if (
            isinstance(node, ast.ImportFrom)
            and node.level == 0
            and node.module is not None
            and node.module.split(".")[0] in NUMERIC_MODULES
        ):
            return True
    return False


def calls_math(source: str) -> bool:
    return MATH_CALL.search(source) is not None


def records_methodology_finding(data: bytes) -> bool:
    """A file whose own text records a methodology-review finding; undecodable files are skipped."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return METH_FINDING.search(text) is not None


def _in_scope(rel: str) -> bool:
    return rel.startswith(SCOPE_PREFIXES) and not rel.startswith(VENDOR_PREFIX)


def numeric_python_files(files: Iterable[str]) -> list[str]:
    return sorted(
        rel
        for rel in files
        if rel.endswith(".py")
        and _in_scope(rel)
        and imports_numeric((ROOT / rel).read_text(encoding="utf-8"))
    )


def numeric_js_files(files: Iterable[str]) -> list[str]:
    return sorted(
        rel
        for rel in files
        if glob_match("src/idraa/static/js/*.js", rel)
        and calls_math((ROOT / rel).read_text(encoding="utf-8"))
    )


def recorded_finding_files(files: Iterable[str]) -> list[str]:
    return sorted(
        rel
        for rel in files
        if _in_scope(rel) and records_methodology_finding((ROOT / rel).read_bytes())
    )


# ---- S-A: security signals in the syntax tree ------------------------------------------------

_MODELS = "idraa.models"
_REPOSITORIES = "idraa.repositories"
_COOKIE_CALLS = frozenset({"set_cookie", "delete_cookie"})


def _is_type_checking(test: ast.expr) -> bool:
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
        isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
    )


def _runtime_nodes(tree: ast.AST) -> Iterator[ast.AST]:
    """Every node that runs: not an `if TYPE_CHECKING:` body (its `else:` is scanned), not an annotation.

    Only S-A's org-scoped-model clause reads this view (spec section 3); every other S-A signal is
    read over the whole tree.
    """
    stack: list[ast.AST] = [tree]
    while stack:
        node = stack.pop()
        yield node
        if isinstance(node, ast.If) and _is_type_checking(node.test):
            stack.extend(node.orelse)
            continue
        for name, value in ast.iter_fields(node):
            if name == "annotation" and isinstance(node, ast.arg | ast.AnnAssign):
                continue
            if name == "returns" and isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            if isinstance(value, ast.AST):
                stack.append(value)
            elif isinstance(value, list):
                stack.extend(v for v in value if isinstance(v, ast.AST))


def _from_module(node: ast.ImportFrom, package: str) -> str | None:
    """The absolute module an `ImportFrom` names; relative forms resolve against `package`."""
    if node.level == 0:
        return node.module
    parts = package.split(".")
    if node.level - 1 >= len(parts):  # leaves the package, however far
        return None
    base = parts[: len(parts) - (node.level - 1)]
    return ".".join([*base, *([node.module] if node.module else [])])


def _dotted(node: ast.expr) -> list[str] | None:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        return [node.id, *reversed(parts)]
    return None


def security_signals(source: str, package: str, org_scoped: Collection[str]) -> set[str]:
    """The security signals (spec section 3, S-A) a module's syntax tree shows.

    `package` is the dotted package containing the module, against which relative imports resolve.
    Only the org-scoped-model clause skips `if TYPE_CHECKING:` bodies and annotations (spec
    section 3); every other signal, `idraa.repositories` imports included, is read over the whole
    tree, so a repository typed only under `TYPE_CHECKING` still puts the file in the lane.
    """
    tree = ast.parse(source)
    signals: set[str] = set()

    # Org-scoped model clause: runtime nodes only.
    model_names: set[str] = set()  # local names bound to an org-scoped model
    module_aliases: dict[str, str] = {}  # local name -> the dotted module it stands for
    runtime = list(_runtime_nodes(tree))
    for node in runtime:
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    module_aliases[alias.asname] = alias.name
                else:
                    module_aliases[alias.name.split(".")[0]] = alias.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom):
            module = _from_module(node, package)
            if module is None:
                continue
            for alias in node.names:
                bound = alias.asname or alias.name
                if module == _MODELS or module.startswith(_MODELS + "."):
                    if alias.name in org_scoped:
                        model_names.add(bound)
                    elif module == _MODELS:
                        module_aliases[bound] = f"{_MODELS}.{alias.name}"  # a submodule
                elif module == "idraa" and alias.name == "models":
                    module_aliases[bound] = _MODELS
    for node in runtime:
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            if node.id in model_names:
                signals.add("org-scoped model")
        elif isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
            chain = _dotted(node)
            if chain and chain[0] in module_aliases:
                full = [*module_aliases[chain[0]].split("."), *chain[1:]]
                models = _MODELS.split(".")
                if full[: len(models)] == models and any(
                    c in org_scoped for c in full[len(models) :]
                ):
                    signals.add("org-scoped model")

    # Every other signal: the whole tree.
    everything = list(ast.walk(tree))
    markup_names = {"Markup"}
    audit_names = {"AuditWriter"}
    for node in everything:
        if isinstance(node, ast.Import):
            if any(
                a.name == _REPOSITORIES or a.name.startswith(_REPOSITORIES + ".")
                for a in node.names
            ):
                signals.add("repositories")
        elif isinstance(node, ast.ImportFrom):
            module = _from_module(node, package)
            if module is None:
                continue
            for alias in node.names:
                if alias.name == "Markup":
                    markup_names.add(alias.asname or alias.name)
                elif alias.name == "AuditWriter":
                    audit_names.add(alias.asname or alias.name)
            if (
                module == _REPOSITORIES
                or module.startswith(_REPOSITORIES + ".")
                or (module == "idraa" and any(a.name == "repositories" for a in node.names))
            ):
                signals.add("repositories")
    for node in everything:
        if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
            if node.attr == "cookies":
                signals.add("cookies")
            if node.attr == "session":
                base = node.value
                if (isinstance(base, ast.Name) and base.id == "request") or (
                    isinstance(base, ast.Attribute) and base.attr == "request"
                ):
                    signals.add("request.session")
        elif isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            if not isinstance(name, str):
                continue
            if name.endswith("_for_org"):
                signals.add("*_for_org call")
            elif name == "text":
                signals.add("text()")
            elif name in _COOKIE_CALLS:
                signals.add(name)
            elif (isinstance(func, ast.Name) and name in audit_names) or name == "AuditWriter":
                signals.add("AuditWriter")
            elif (isinstance(func, ast.Name) and name in markup_names) or name == "Markup":
                signals.add("Markup")
    return signals


def _package_of(rel: str) -> str:
    """`src/idraa/routes/x.py` -> `idraa.routes`; `src/idraa/routes/__init__.py` likewise."""
    return ".".join(Path(rel).with_suffix("").parts[1:-1])


def security_signal_files(files: Iterable[str]) -> list[str]:
    return sorted(
        rel
        for rel in files
        if rel.startswith("src/idraa/")
        and rel.endswith(".py")
        and security_signals(
            (ROOT / rel).read_text(encoding="utf-8"), _package_of(rel), ORG_SCOPED_MODELS
        )
    )


# ---- the rules --------------------------------------------------------------------------------


def _report(gaps: list[str]) -> str:
    return f"{len(gaps)} file(s) outside the lane:\n" + "\n".join(gaps)


def _too_few(rule: str, found: int, minimum: int, const: str, note: str = "") -> str:
    return (
        f"{rule} found {found} files, minimum {minimum}{note}: detector broken, or, if files were "
        f"legitimately removed, lower `{const}` with a stated reason"
    )


def register_gaps(named: Iterable[str]) -> list[str]:
    """M-A's lane check. It honours no exemption: a file the register names is the site of a
    departure, or the test that pins what the departure secures (spec section 3)."""
    return _lane_gaps("methodology", named, exemptable=False)


def test_register_where_files_are_methodology_lane() -> None:
    """M-A: every file the departures register's Where / Evaluate text names is a methodology file."""
    named, errors = register_files((ROOT / REGISTER).read_text(encoding="utf-8"), _tracked_files())
    assert errors == [], (
        "register Where / Evaluate spans that resolve to no single tracked file (fix the register "
        f"text, or the resolver if the form is new): {errors}"
    )
    assert len(named) >= MIN_M_A, _too_few("M-A", len(named), MIN_M_A, "MIN_M_A")
    gaps = register_gaps(named)
    assert gaps == [], _report(gaps)


def test_numeric_python_is_methodology_lane_or_exempt() -> None:
    """M-B: every numeric module (numpy, scipy, math, fair_cam, statistics, random, pandas)."""
    found = numeric_python_files(_tracked_files())
    assert len(found) >= MIN_M_B, _too_few("M-B", len(found), MIN_M_B, "MIN_M_B")
    gaps = _lane_gaps("methodology", found)
    assert gaps == [], _report(gaps)


def test_numeric_js_is_methodology_lane_or_exempt() -> None:
    """M-B-JS: shipped JavaScript calling Math.exp / log / pow / sqrt and kin."""
    found = numeric_js_files(_tracked_files())
    assert len(found) >= MIN_M_B_JS, _too_few(
        "M-B-JS",
        len(found),
        MIN_M_B_JS,
        "MIN_M_B_JS",
        f" ({MIN_M_B_JS} is the count measured when the rule landed, so there is no headroom)",
    )
    gaps = _lane_gaps("methodology", found)
    assert gaps == [], _report(gaps)


def test_recorded_methodology_findings_are_methodology_lane_or_exempt() -> None:
    """M-C: a file whose own text records a methodology-review finding is a methodology file."""
    found = recorded_finding_files(_tracked_files())
    assert len(found) >= MIN_M_C, _too_few("M-C", len(found), MIN_M_C, "MIN_M_C")
    gaps = _lane_gaps("methodology", found)
    assert gaps == [], _report(gaps)


def test_security_signal_code_is_security_lane_or_exempt() -> None:
    """S-A: org-scoped model loads, repositories, *_for_org, text(), cookies, Markup, AuditWriter."""
    found = security_signal_files(_tracked_files())
    assert len(found) >= MIN_S_A, _too_few("S-A", len(found), MIN_S_A, "MIN_S_A")
    gaps = _lane_gaps("security", found)
    assert gaps == [], _report(gaps)


def test_lane_exemptions_are_live_and_pinned() -> None:
    """Every exemption is a pinned, still-triggering entry; any change is a visible diff here."""
    files = _tracked_files()
    exemptions = _exemptions()
    assert set(exemptions) == {"methodology", "security"}
    for lane, kinds, pinned in (
        ("methodology", METHODOLOGY_EXEMPTION_KINDS, PINNED_METHODOLOGY_EXEMPTIONS),
        ("security", SECURITY_EXEMPTION_KINDS, PINNED_SECURITY_EXEMPTIONS),
    ):
        for path, entry in exemptions[lane].items():
            assert path in files, f"{lane} exemption {path} is not a tracked file"
            assert len(entry) == 2, f"{lane} exemption {path} must be [kind, reason]"
            kind, reason = entry
            assert kind in kinds, f"{lane} exemption {path}: kind {kind!r} not in {sorted(kinds)}"
            assert reason.strip(), f"{lane} exemption {path} needs a reason"
        assert {p: e[0] for p, e in exemptions[lane].items()} == pinned, (
            f"the {lane} exemptions changed: update PINNED_*_EXEMPTIONS in this test with the "
            "reason, and dispatch the lane's reviewer"
        )
    live = (
        set(numeric_python_files(files))
        | set(numeric_js_files(files))
        | set(recorded_finding_files(files))
    )
    stale = sorted(set(exemptions["methodology"]) - live)
    assert stale == [], f"methodology exemptions that no rule triggers any more: {stale}"
    stale = sorted(set(exemptions["security"]) - set(security_signal_files(files)))
    assert stale == [], f"security exemptions that no rule triggers any more: {stale}"


def test_register_rule_honours_no_exemptions() -> None:
    """M-A is the one rule without "or is exempt": an exempt file the register names still fails it."""
    lane = _lanes()["methodology"]["globs"]
    escalation = _escalation_globs()
    outside = sorted(
        f
        for f in _exemptions()["methodology"]
        if not (any(glob_match(g, f) for g in lane) and any(glob_match(g, f) for g in escalation))
    )
    assert outside, "no exempt file is outside the lane, so this test would pin nothing"
    assert _lane_gaps("methodology", outside) == []  # the other rules honour the exemption ...
    assert [g.split(":")[0] for g in register_gaps(outside)] == outside  # ... M-A does not
    assert all("cannot be exempted" in g for g in register_gaps(outside))


def test_lane_additions_stay() -> None:
    """Every path the lane audit added stays: covers the judgement rows that no rule sees."""
    lanes = _lanes()
    missing = []
    for lane, additions in (
        ("methodology", METHODOLOGY_ADDITIONS),
        ("security", SECURITY_ADDITIONS),
    ):
        assert len(set(additions)) == len(additions), f"duplicate {lane} addition"
        have = set(lanes[lane]["globs"])
        missing += [
            f"{path}: missing from the {lane} lane in .clerk/manifests/lanes.json"
            for path in (*additions, EXEMPTIONS_REL)
            if path not in have
        ]
    assert missing == [], _report(missing)


def test_existing_tags_not_retagged() -> None:
    """A retag silently changes the subject the judge instances over a file carry."""
    paths = cast("dict[str, str]", cast("dict[str, object]", _toml()["escalation"])["paths"])
    wrong = [
        f"{path}: tagged {paths.get(path)!r}, pinned {subject!r}"
        for path, subject in PINNED_TAGS.items()
        if paths.get(path) != subject
    ]
    assert wrong == [], wrong


# ---- detector self-tests ----------------------------------------------------------------------

_SNIPPET_FILES = [
    "src/idraa/services/loss_capacity.py",
    "src/idraa/services/reports.py",
    "src/idraa/services/table_only.py",
    "src/idraa/templates/a/dup.html",
    "src/idraa/templates/b/dup.html",
    "fair_cam/composition.py",
    "fair_cam/risk_engine/fair_core.py",
    "fair_cam/quantile_pooling/a.py",
    "fair_cam/quantile_pooling/b.py",
    "docs/reference/loss-representation.md",
    "tests/integration/test_x.py",
    "tests/unit/test_only_node.py",
    "data/seed.json",
    "data/other.json",
    "notebooks/n.ipynb",
]
_SNIPPET_REGISTER = """\
### A1. Something

- **Where.** `services/loss_capacity.py`, `fair_core.py` (the
  `loss_magnitude = primary + secondary` line); `quantile_pooling/`
  and `data/seed*.json`.
- **Evaluate.** `tests/integration/test_x.py::test_node` and
  `services/reports.build_groups`; `P(secondary)` and `a/b c` are not paths.
- **Rationale.** `src/idraa/templates/a/dup.html` is prose, not read.

> A prose span `notebooks/n.ipynb` is not read. Where: `loss-representation.md` and
> `data/other.json` (tripwires).

- **Note (x).** Prose. Evaluate: the invariants in
  `fair_cam/composition.py`.

Another paragraph: `src/idraa/templates/b/dup.html` is not read.

| Item | What | Where |
|---|---|---|
| D1 | `notebooks/n.ipynb` is not read | `services/table_only.py` (no math) |
| D2 | same | same |
"""


def test_detectors_on_snippets() -> None:
    # M-A: the four forms, token resolution, and what is NOT read.
    named, errors = register_files(_SNIPPET_REGISTER, _SNIPPET_FILES)
    assert errors == []
    assert named == {
        "src/idraa/services/loss_capacity.py",
        "src/idraa/services/reports.py",
        "src/idraa/services/table_only.py",  # only the table's Where column names it
        "fair_cam/composition.py",
        "fair_cam/risk_engine/fair_core.py",
        "fair_cam/quantile_pooling/a.py",
        "fair_cam/quantile_pooling/b.py",
        "docs/reference/loss-representation.md",
        "tests/integration/test_x.py",
        "data/seed.json",
        "data/other.json",
    }
    stops = (
        "- **Where.** `data/other.json`\n  continued `data/seed.json`\n"
        "- **Rationale.** `notebooks/n.ipynb`\n"
    )
    assert register_files(stops, _SNIPPET_FILES)[0] == {"data/other.json", "data/seed.json"}
    heading = "- **Where.** `data/other.json`\n## H `notebooks/n.ipynb`\n"
    assert register_files(heading, _SNIPPET_FILES)[0] == {"data/other.json"}
    blank = "- **Evaluate.** `data/other.json`\n\n  `notebooks/n.ipynb`\n"
    assert register_files(blank, _SNIPPET_FILES)[0] == {"data/other.json"}
    quote = "- **Where.** `data/other.json`\n> `notebooks/n.ipynb`\n"
    assert register_files(quote, _SNIPPET_FILES)[0] == {"data/other.json"}
    nodes = "- **Where.** `tests/integration/test_x.py::test_a`, `docs/reference/loss-representation.md`"
    assert len(register_files(nodes, _SNIPPET_FILES)[0]) == 2
    # a `::node` span without a slash is a path only once the suffix is stripped
    slashless = "- **Evaluate.** `test_only_node.py::test_case`"
    assert register_files(slashless, _SNIPPET_FILES) == ({"tests/unit/test_only_node.py"}, [])
    bad = "- **Where.** `dup.html`, `missing.py`, `nowhere/`, `data/none*.json`"
    assert register_files(bad, _SNIPPET_FILES) == (
        set(),
        sorted(
            {
                "`dup.html` matches several tracked files: "
                "['src/idraa/templates/a/dup.html', 'src/idraa/templates/b/dup.html']",
                "`missing.py` matches no tracked file",
                "`nowhere/` matches no tracked file",
                "`data/none*.json` matches no tracked file",
            }
        ),
    )

    # M-B: every import form, at any depth; relative imports, decimal, comments and strings do not count.
    for hit in (
        "import numpy as np",
        "import os\nimport sys, math\n",
        "from scipy.stats import (\n    norm,\n    lognorm,\n)\n",
        "from fair_cam.risk_engine import FAIREngine",
        "def f():\n    import statistics\n",
        "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    import pandas\n",
        "import random",
    ):
        assert imports_numeric(hit), hit
    for miss in (
        "from . import math\nfrom .random import x\n",
        "import decimal\nfrom decimal import Decimal\n",
        "# import numpy\nx = 'import math'\n'''from scipy import stats'''\n",
        "import mathematics\nimport numpyish\n",
    ):
        assert not imports_numeric(miss), miss

    # M-B-JS: calls only; each of the nine functions the spec names in its own snippet, so
    # dropping any one from the rule fails, and a function the spec does not name does not match.
    for fn in ("exp", "expm1", "log", "log1p", "log2", "log10", "pow", "sqrt", "cbrt"):
        assert calls_math(f"const y = Math.{fn}(x);"), fn
    for fn in ("floor", "ceil", "round", "abs", "max", "min", "sin", "tanh", "hypot", "random"):
        assert not calls_math(f"const y = Math.{fn}(x);"), fn
    for hit in ("a = Math.log10 (v)", "Math.expm1(\n x)"):
        assert calls_math(hit), hit
    for miss in (
        "const f = Math.exp;",
        "xMath.exp(1)",
        "Math.logx(1)",
    ):
        assert not calls_math(miss), miss

    # M-C: each annotation form (case-sensitive, one scoped case-insensitive group), UTF-8 skip.
    for hit in (
        "# Meth-I1: PERT only",
        "# Meth2-I2 restated",
        "# MethB1",
        "# METH-3",
        "# METH I-3 basis label",
        "# METH B1",
        "# meth B1 note",
        "# Meth N-2 disclosure",
        "# methodology N1, do not fix",
        "# Methodology B-1",
        "# methodology review pinned this",
        "# methodology-gate",
        "# methodology finding",
        "# Methodology-Audit",
        "# methodology DECISION",
        "# methodology check",
        "# methodology-owned copy",
        "# methodology + architect finding",
        "x = 1\n    Methodology: the owner decided\n",
    ):
        assert records_methodology_finding(hit.encode()), hit
    for miss in (
        "# Method B1",
        "# METHOD-1 and Methods and Method2",
        "# the methodology is documented",
        "# Methodology: mid-line, not at the start of a line",
        "# Methodology Review",
    ):
        assert not records_methodology_finding(miss.encode()), miss
    assert records_methodology_finding("# σ is fixed (Meth-I1) é".encode())
    assert not records_methodology_finding(b"\x89PNG\r\n\x1a\n\xff\xfe Meth-I1")

    # S-A: syntax-tree signals; comments and string contents never count.
    org = {"Scenario", "Control"}

    def sig(src: str, package: str = "idraa.routes") -> set[str]:
        return security_signals(src, package, org)

    model = {"org-scoped model"}
    for src, package in (
        ("from idraa.models import Scenario\nx = Scenario\n", "idraa.routes"),
        ("from idraa.models.scenario import Scenario as S\nS.query()\n", "idraa.routes"),
        ("import idraa.models\nx = idraa.models.Scenario\n", "idraa.routes"),
        ("import idraa.models as m\nx = m.Control\n", "idraa.routes"),
        ("from idraa import models\nx = models.Scenario\n", "idraa.routes"),
        ("from . import models\nx = models.Scenario\n", "idraa"),
        ("from .. import models\nx = models.Scenario\n", "idraa.routes"),
        ("from ..models import Scenario\nx = Scenario\n", "idraa.routes"),
        ("from .models import Control\nx = Control\n", "idraa"),
        ("from .scenario import Scenario\nx = [Scenario]\n", "idraa.models"),
        ("from idraa.models import scenario\nx = scenario.Scenario\n", "idraa.routes"),
        (
            "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    pass\nelse:\n"
            "    from idraa.models import Scenario\n    y = Scenario\n",
            "idraa.routes",
        ),
        ("from idraa.models import Scenario\nf'{Scenario}'\n", "idraa.routes"),
    ):
        assert sig(src, package) == model, src
    for src, package in (
        ("from idraa.models import Scenario\n", "idraa.routes"),  # imported, never loaded
        (
            "from idraa.models import Scenario\ndef f(s: Scenario) -> Scenario: ...\n",
            "idraa.routes",
        ),
        ("from idraa.models import Scenario\nx: Scenario = 1\n", "idraa.routes"),
        (
            "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n"
            "    from idraa.models import Scenario\n    y = Scenario\n",
            "idraa.routes",
        ),
        ("from ...models import Scenario\nx = Scenario\n", "idraa.routes"),  # leaves the package
        ("from ....models import Scenario\nx = Scenario\n", "idraa.routes"),  # ... however far
        ("from .....models import Scenario\nx = Scenario\n", "idraa.routes"),
        ("import idraa.models\nx = idraa.models.RunStatus\n", "idraa.routes"),
        ("from idraa.models import RunStatus\nx = RunStatus.A\n", "idraa.routes"),
        ("from other.models import Scenario\nx = Scenario\n", "idraa.routes"),
        (
            "# from idraa.repositories import X\nx = 'AuditWriter()' + 'idraa.models.Scenario'\n",
            "idraa",
        ),
    ):
        assert sig(src, package) == set(), src
    assert sig("from idraa.repositories import ScenarioRepo\n") == {"repositories"}
    assert sig("from idraa.repositories.scenario import X\n") == {"repositories"}
    assert sig("import idraa.repositories.scenario\n") == {"repositories"}
    assert sig("from idraa import repositories\n") == {"repositories"}
    assert sig("from .. import repositories\n") == {"repositories"}
    assert sig("from ..repositories.x import Y\n") == {"repositories"}
    assert sig("from ...repositories import Y\n", "idraa.routes") == set()  # leaves the package
    assert sig("from ....repositories import Y\n", "idraa.routes") == set()  # ... however far
    assert sig("from .... import repositories\n", "idraa.services") == set()
    # the TYPE_CHECKING / annotation exclusion belongs to the model clause alone (spec section 3):
    # a repository imported only for typing still puts the file in the security lane
    tc = "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n"
    assert sig(tc + "    from idraa.repositories.x import R\n") == {"repositories"}
    assert sig(tc + "    from idraa.repositories import R\n") == {"repositories"}
    assert sig(tc + "    import idraa.repositories.x\n") == {"repositories"}
    assert sig(tc + "    from idraa import repositories\n") == {"repositories"}
    assert sig(tc + "    from ..repositories.x import R\n") == {"repositories"}
    assert sig(tc + "    pass\nelse:\n    from idraa.repositories import R\n") == {"repositories"}
    assert sig(tc + "    x = text('a')\n") == {"text()"}  # the other signals are scanned there too
    assert sig("x = get_scenario_for_org(1)\n") == {"*_for_org call"}
    assert sig("repo.list_for_org(\n    1,\n)\n") == {"*_for_org call"}
    assert sig("x = f'{get_x_for_org(1)}'\n") == {"*_for_org call"}
    assert sig("x = get_for_org\n") == set()
    assert sig("from sqlalchemy import text\nt = text(\n    'select 1'\n    'x'\n)\n") == {"text()"}
    assert sig("import sqlalchemy as sa\nsa.text('x')\n") == {"text()"}
    assert sig("x = request.cookies.get('a')\n") == {"cookies"}
    assert sig("request.cookies = {}\n") == set()
    assert sig("x = request.session['a']\n") == {"request.session"}
    assert sig("x = self.request.session\n") == {"request.session"}
    assert sig("x = self.session\n") == set()
    assert sig("response.set_cookie('a', 'b')\nresponse.delete_cookie('a')\n") == {
        "set_cookie",
        "delete_cookie",
    }
    assert sig("from markupsafe import Markup\nx = Markup('<b>')\n") == {"Markup"}
    assert sig("from markupsafe import Markup as M\nx = M('<b>')\n") == {"Markup"}
    assert sig("import markupsafe\nx = markupsafe.Markup('<b>')\n") == {"Markup"}
    assert sig("from markupsafe import Markup\nx = Markup\n") == set()
    assert sig("w = AuditWriter(db)\n") == {"AuditWriter"}
    assert sig("from idraa.services.audit import AuditWriter as AW\nw = AW(db)\n") == {
        "AuditWriter"
    }
    assert sig("from idraa.services.audit import AuditWriter as AW\nw = AW\n") == set()
    assert sig("w = AW(db)\n") == set()
    assert sig("w = audit.AuditWriter(db)\n") == {"AuditWriter"}
    assert sig("w = AuditWriterFactory(db)\n") == set()
