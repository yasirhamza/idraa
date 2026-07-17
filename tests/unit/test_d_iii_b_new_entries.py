"""Epic D-iii-b: the 8 new attested vertical entries (envelope x share).

Per-entry reconstruction + curation invariants: each new entry's loss nodes
must rebuild EXACTLY from the sector envelope + its loss_form_profile shares
(PERT via the Milestone B mechanical conversion for capped entries; native
lognormal mu = mu_s + ln(Sum shares), sigma = sigma_s for catastrophic), all
validate through LibraryEntrySeed, carry loss_tier=paginated + the IRIS
envelope cite + an attestation cite, and keep Sum(shares) <= 1 with a
differentiated PERT TEF/vuln. Also pins the builder's IND2SEC to the
differentiation guard's copy (drift guard).
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pytest

_PROJ = Path(__file__).parent.parent.parent
for p in (_PROJ / "src", _PROJ / "scripts", _PROJ / "tests" / "integration"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from idraa.services.seed_library_loader import LibraryEntrySeed  # noqa: E402

_EXT = "data/seed_library_entries_extension.json"
_ENV = "data/loss_form_envelopes.json"
_ATTACK_FULL_DIIIB = "data/seed_attack_d_iii_b_full.json"
_ATTACK_CATALOG = "data/seed_attack_catalog.json"

# The 8 D-iii-b slugs and whether they carry a secondary loss.
_D_IIIB = {
    "physician-practice-clearinghouse-revenue-disruption": False,
    "law-enforcement-records-extortion-breach": True,
    "casino-ransomware-operational-disruption": True,
    "telecom-lawful-intercept-nationstate-compromise": True,
    "law-firm-privileged-data-ransomware-extortion": True,
    "k12-edtech-vendor-breach": True,
    "higher-ed-insider-ddos": False,
    "judiciary-court-system-ransomware": True,
}


def _load() -> dict[str, dict]:
    return {e["slug"]: e for e in json.loads(Path(_EXT).read_text(encoding="utf-8"))}


def _envelopes() -> dict[str, dict]:
    return {r["sector"]: r for r in json.loads(Path(_ENV).read_text(encoding="utf-8"))}


def _sector(e: dict, ind2sec: dict) -> str:
    ind = (e.get("calibration_anchor") or {}).get("industry")
    return ind2sec.get(ind) or ind2sec.get(
        (e.get("applicable_industries") or [None])[0], "technology_saas"
    )


@pytest.mark.parametrize("slug", sorted(_D_IIIB))
def test_new_entry_present_and_valid(slug: str) -> None:
    from tests._loss_shape_helpers import CATASTROPHIC_SLUGS

    e = _load().get(slug)
    assert e is not None, f"{slug} not authored into the extension file"
    assert e["loss_tier"] == "paginated"
    # Milestone B (#loss-pert-overhaul): capped entries are bounded PERT;
    # only the catastrophic shortlist keeps native lognormal.
    expected = "lognormal" if slug in CATASTROPHIC_SLUGS else "PERT"
    assert e["primary_loss"]["distribution"] == expected
    assert e["status"] == "published"
    LibraryEntrySeed.model_validate(e)  # raises on any schema violation


@pytest.mark.parametrize("slug", sorted(_D_IIIB))
def test_new_entry_reconstructs_from_envelope_and_shares(slug: str) -> None:
    from test_library_loss_differentiation import _IND2SEC  # type: ignore[import-not-found]

    from tests._loss_shape_helpers import CATASTROPHIC_SLUGS, expected_pert_from_lognormal

    e = _load()[slug]
    env = _envelopes()
    sec = _sector(e, _IND2SEC)
    mu_s, sigma_s = env[sec]["mean"], env[sec]["sigma"]
    prof = e["loss_form_profile"]
    sp = sum(f["share"] for f in prof if f["kind"] == "primary" and f.get("share") is not None)
    ss = sum(f["share"] for f in prof if f["kind"] == "secondary" and f.get("share") is not None)

    exp_pl_mu = round(mu_s + math.log(sp), 10)
    exp_sigma = round(sigma_s, 10)
    if slug in CATASTROPHIC_SLUGS:
        # Native lognormal (catastrophic): envelope x share params pinned directly.
        assert e["primary_loss"]["sigma"] == pytest.approx(exp_sigma, abs=1e-9)
        assert e["primary_loss"]["mean"] == pytest.approx(exp_pl_mu, abs=1e-9)
    else:
        # Capped: the Milestone B mechanical PERT conversion of the SAME
        # envelope x share lognormal -- citation chain preserved.
        pl = e["primary_loss"]
        exp_low, exp_high = expected_pert_from_lognormal(exp_pl_mu, exp_sigma)
        assert pl["low"] == pytest.approx(exp_low, rel=1e-9)
        assert pl["high"] == pytest.approx(exp_high, rel=1e-9)
        assert pl["mode"] == pl["low"]

    if _D_IIIB[slug]:
        sl = e["secondary_loss"]
        assert sl is not None
        exp_sl_mu = round(mu_s + math.log(ss), 10)
        if slug in CATASTROPHIC_SLUGS:
            assert sl["distribution"] == "lognormal"
            assert sl["sigma"] == pytest.approx(exp_sigma, abs=1e-9)
            assert sl["mean"] == pytest.approx(exp_sl_mu, abs=1e-9)
        else:
            assert sl["distribution"] == "PERT"
            exp_low, exp_high = expected_pert_from_lognormal(exp_sl_mu, exp_sigma)
            assert sl["low"] == pytest.approx(exp_low, rel=1e-9)
            assert sl["high"] == pytest.approx(exp_high, rel=1e-9)
            assert sl["mode"] == sl["low"]
    else:
        assert e.get("secondary_loss") is None
        assert ss == 0

    # joint coherence bound + share bounds
    total = sum(f["share"] for f in prof if f.get("share") is not None)
    assert total <= 1.0 + 1e-9, f"{slug}: Sum(shares)={total} > 1"
    for f in prof:
        if f.get("share") is not None:
            assert 0.0 < f["share"] <= 1.0


@pytest.mark.parametrize("slug", sorted(_D_IIIB))
def test_new_entry_citations_and_pert(slug: str) -> None:
    e = _load()[slug]
    cites = e.get("source_citations") or []
    assert cites and "IRIS 2025 Figure A3" in cites[0], "IRIS envelope cite must be first"
    assert len(cites) >= 2, "must carry an attestation cite alongside the envelope cite"
    # forbidden citations never appear
    blob = json.dumps(e)
    for bad in ("FBI PSA I-091019-PSA", "DOJ 15-1433", "CISA AA22-186A", "PREPA/AMI"):
        assert bad not in blob, f"{slug} cites forbidden {bad}"
    # TEF is bounded PERT again (#tef-pert-revert); vuln stays PERT ([0,1]).
    tef = e["threat_event_frequency"]
    assert tef["distribution"] == "PERT"
    assert tef["low"] < tef["mode"] < tef["high"]
    v = e["vulnerability"]
    assert v["distribution"] == "PERT"
    assert v["low"] < v["mode"] < v["high"]
    assert v["low"] > 0.0 and v["high"] < 1.0
    # vuln_posture guard-required
    assert (e.get("calibration_anchor") or {}).get("vuln_posture", "").strip()


def test_builder_ind2sec_matches_differentiation_guard() -> None:
    """Drift guard: the builder's copied IND2SEC must equal the differentiation guard's."""
    from build_d_iii_b_new_entries import IND2SEC as BUILDER_MAP  # type: ignore[import-not-found]
    from test_library_loss_differentiation import _IND2SEC  # type: ignore[import-not-found]

    assert BUILDER_MAP == _IND2SEC, "builder IND2SEC drifted from the differentiation guard's copy"


def test_new_entries_have_attack_mappings() -> None:
    """Task 2: every one of the 8 D-iii-b slugs has >=1 row in the SEPARATE
    seed_attack_d_iii_b_full.json full-mapping file, and each mapped
    technique_id exists in the ATT&CK catalog."""
    mappings = json.loads(Path(_ATTACK_FULL_DIIIB).read_text(encoding="utf-8"))["mappings"]
    catalog = json.loads(Path(_ATTACK_CATALOG).read_text(encoding="utf-8"))
    techniques = {(t["domain"], t["technique_id"]) for t in catalog["techniques"]}

    by_slug: dict[str, list[dict]] = {}
    for m in mappings:
        by_slug.setdefault(m["entry_slug"], []).append(m)

    for slug in _D_IIIB:
        rows = by_slug.get(slug, [])
        assert rows, f"{slug}: no ATT&CK full-mapping row in {_ATTACK_FULL_DIIIB}"
        for row in rows:
            key = (row["domain"], row["technique_id"])
            assert key in techniques, f"{slug}: unknown technique {key}"


def test_six_verticals_each_have_a_new_entry() -> None:
    """Design section 7: each under-represented vertical earns >=1 attested new entry."""
    d = _load()
    coverage = {
        "healthcare": "physician-practice-clearinghouse-revenue-disruption",
        "government_public": "law-enforcement-records-extortion-breach",
        "hospitality": "casino-ransomware-operational-disruption",
        "telecom": "telecom-lawful-intercept-nationstate-compromise",
        "professional_services": "law-firm-privileged-data-ransomware-extortion",
        "education": "k12-edtech-vendor-breach",
    }
    for vertical, slug in coverage.items():
        assert slug in d, f"{vertical} vertical missing its new entry {slug}"
    # telecom is identified by tag, not resolved envelope sector
    assert "telecom" in (d["telecom-lawful-intercept-nationstate-compromise"].get("tags") or [])


_TARGETS_FILE = "data/loss_form_targets.json"

# sub_sector -> new D-iii-b slug, the deterministic flip set from Task 4 Step 1.
_FLIPPED_SUB_SECTORS = {
    "physician_practice": "physician-practice-clearinghouse-revenue-disruption",
    "law_enforcement": "law-enforcement-records-extortion-breach",
    "casino": "casino-ransomware-operational-disruption",
    "wireless_carrier": "telecom-lawful-intercept-nationstate-compromise",
    "law_firm": "law-firm-privileged-data-ransomware-extortion",
    "k12": "k12-edtech-vendor-breach",
    "higher_education": "higher-ed-insider-ddos",
    "judiciary": "judiciary-court-system-ransomware",
}


def test_every_new_entry_target_is_attested() -> None:
    """Task 4 Step 1/4: the 8 flipped loss_form_targets.json archetypes have a
    non-null attestation + needs_fresh_research == False; the other 14 'new'
    gap-report sub-sectors remain needs_fresh_research == True (untouched)."""
    targets = json.loads(Path(_TARGETS_FILE).read_text(encoding="utf-8"))
    new_rows = {a["sub_sector"]: a for a in targets["archetypes"] if a["keep_or_new"] == "new"}

    for sub_sector, slug in _FLIPPED_SUB_SECTORS.items():
        row = new_rows.get(sub_sector)
        assert row is not None, f"sub_sector {sub_sector!r} not found among 'new' targets"
        assert row["needs_fresh_research"] is False, (
            f"{sub_sector}: expected needs_fresh_research=False after flip"
        )
        assert row.get("attestation"), f"{sub_sector}: expected non-null attestation after flip"
        assert row.get("existing_slug") == slug, (
            f"{sub_sector}: expected existing_slug={slug!r}, got {row.get('existing_slug')!r}"
        )

    untouched = set(new_rows) - set(_FLIPPED_SUB_SECTORS)
    assert len(untouched) == 14, f"expected 14 untouched 'new' sub-sectors, got {len(untouched)}"
    for sub_sector in untouched:
        assert new_rows[sub_sector]["needs_fresh_research"] is True, (
            f"{sub_sector}: expected needs_fresh_research to remain True"
        )
