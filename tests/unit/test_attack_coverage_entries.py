"""Attack-coverage gap-fill epic (#529) Task 1: the 9 new cross-cutting-vector
entries (envelope x share).

Per-entry reconstruction + curation invariants: each new entry's loss nodes
must rebuild EXACTLY from the sector envelope + its loss_form_profile shares
(PERT via the Milestone B mechanical conversion for the 8 capped entries;
native lognormal mean = mu_s + ln(Sum shares), sigma = sigma_s for W1, the
lone catastrophic entry), all validate through LibraryEntrySeed, carry
loss_tier=paginated + the IRIS envelope cite + an attestation cite, and keep
Sum(shares) <= 1 with a differentiated PERT TEF/vuln. Also pins the builder's
IND2SEC to the differentiation guard's copy (drift guard) and confirms A1's
primary_loss is byte-identical to ransomware-on-fileshare's (the deliberate
_PL_ALLOWLIST pair).

The authoritative acceptance for the whole batch is that
tests/integration/test_library_loss_differentiation.py stays fully green
after these entries land -- this file checks correctness of THIS batch only.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pytest

_PROJ = Path(__file__).resolve().parent.parent.parent
for p in (_PROJ / "src", _PROJ / "scripts", _PROJ / "tests" / "integration"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from idraa.services.seed_library_loader import LibraryEntrySeed  # noqa: E402

_BASE = "data/seed_library_entries.json"
_EXT = "data/seed_library_entries_extension.json"
_ENV = "data/loss_form_envelopes.json"

# The 9 new slugs and whether they carry a secondary loss (per the Milestone-B
# amendment: null for B1/D1/D2 regardless of shape).
_NEW_SLUGS = {
    "edge-ransomware-perimeter-gateway": True,
    "edge-espionage-nationstate": True,
    "edge-device-orb-foothold": False,
    "transient-cyber-asset-ot-intrusion": True,
    "browser-zeroday-driveby": True,
    "email-client-zeroclick-espionage": True,
    "removable-media-airgap-ot": False,
    "ot-wireless-field-network-compromise": False,
    "destructive-wiper-nationstate": True,
}

_A1_SLUG = "edge-ransomware-perimeter-gateway"
_W1_SLUG = "destructive-wiper-nationstate"


def _load_base() -> list[dict]:
    return json.loads(Path(_BASE).read_text(encoding="utf-8"))


def _load_ext() -> list[dict]:
    return json.loads(Path(_EXT).read_text(encoding="utf-8"))


def _load_all() -> list[dict]:
    return _load_base() + _load_ext()


def _by_slug() -> dict[str, dict]:
    return {e["slug"]: e for e in _load_all()}


def _envelopes() -> dict[str, dict]:
    return {r["sector"]: r for r in json.loads(Path(_ENV).read_text(encoding="utf-8"))}


def _sector(e: dict, ind2sec: dict) -> str:
    ind = (e.get("calibration_anchor") or {}).get("industry")
    return ind2sec.get(ind) or ind2sec.get(
        (e.get("applicable_industries") or [None])[0], "technology_saas"
    )


@pytest.mark.parametrize("slug", sorted(_NEW_SLUGS))
def test_new_entry_present_and_valid(slug: str) -> None:
    from tests._loss_shape_helpers import CATASTROPHIC_SLUGS

    e = _by_slug().get(slug)
    assert e is not None, f"{slug} not authored into the extension file"
    assert e["status"] == "published"
    assert e["loss_tier"] == "paginated"
    expected_shape = "catastrophic" if slug in CATASTROPHIC_SLUGS else "capped"
    assert e["loss_shape"] == expected_shape
    expected_dist = "lognormal" if expected_shape == "catastrophic" else "PERT"
    assert e["primary_loss"]["distribution"] == expected_dist
    LibraryEntrySeed.model_validate(e)  # raises on any schema violation


@pytest.mark.parametrize("slug", sorted(_NEW_SLUGS))
def test_new_entry_reconstructs_from_envelope_and_shares(slug: str) -> None:
    from test_library_loss_differentiation import _IND2SEC  # type: ignore[import-not-found]

    from tests._loss_shape_helpers import CATASTROPHIC_SLUGS, expected_pert_from_lognormal

    e = _by_slug()[slug]
    env = _envelopes()
    sector = _sector(e, _IND2SEC)
    mu_s, sigma_s = env[sector]["mean"], env[sector]["sigma"]
    prof = e["loss_form_profile"]
    sp = sum(f["share"] for f in prof if f["kind"] == "primary" and f.get("share") is not None)
    ss = sum(f["share"] for f in prof if f["kind"] == "secondary" and f.get("share") is not None)

    exp_pl_mean = round(mu_s + math.log(sp), 10)
    exp_sigma = round(sigma_s, 10)
    is_catastrophic = slug in CATASTROPHIC_SLUGS

    pl = e["primary_loss"]
    if is_catastrophic:
        assert pl["distribution"] == "lognormal"
        assert pl["sigma"] == pytest.approx(exp_sigma, abs=1e-9)
        assert pl["mean"] == pytest.approx(exp_pl_mean, abs=1e-9)
    else:
        assert pl["distribution"] == "PERT"
        exp_low, exp_high = expected_pert_from_lognormal(exp_pl_mean, exp_sigma)
        assert pl["low"] == pytest.approx(exp_low, rel=1e-9)
        assert pl["high"] == pytest.approx(exp_high, rel=1e-9)
        assert pl["mode"] == pl["low"]

    if _NEW_SLUGS[slug]:
        sl = e["secondary_loss"]
        assert sl is not None
        exp_sl_mean = round(mu_s + math.log(ss), 10)
        if is_catastrophic:
            assert sl["distribution"] == "lognormal"
            assert sl["sigma"] == pytest.approx(exp_sigma, abs=1e-9)
            assert sl["mean"] == pytest.approx(exp_sl_mean, abs=1e-9)
        else:
            assert sl["distribution"] == "PERT"
            exp_low, exp_high = expected_pert_from_lognormal(exp_sl_mean, exp_sigma)
            assert sl["low"] == pytest.approx(exp_low, rel=1e-9)
            assert sl["high"] == pytest.approx(exp_high, rel=1e-9)
            assert sl["mode"] == sl["low"]
    else:
        assert e.get("secondary_loss") is None
        assert ss == 0

    # Coherence bound + share bounds (Amendment A1).
    total = sum(f["share"] for f in prof if f.get("share") is not None)
    assert total <= 1.0 + 1e-9, f"{slug}: Sum(shares)={total} > 1"
    for f in prof:
        if f.get("share") is not None:
            assert 0.0 < f["share"] <= 1.0


@pytest.mark.parametrize("slug", sorted(_NEW_SLUGS))
def test_new_entry_tef_and_vuln_bounds(slug: str) -> None:
    e = _by_slug()[slug]
    tef = e["threat_event_frequency"]
    assert tef["distribution"] == "PERT"
    assert tef["low"] < tef["mode"] < tef["high"]
    v = e["vulnerability"]
    assert v["distribution"] == "PERT"
    assert v["low"] < v["mode"] < v["high"]
    assert v["low"] > 0.0 and v["high"] < 1.0


@pytest.mark.parametrize("slug", sorted(_NEW_SLUGS))
def test_new_entry_citations(slug: str) -> None:
    e = _by_slug()[slug]
    cites = e.get("source_citations") or []
    assert cites and "IRIS 2025 Figure A3" in cites[0], "IRIS envelope cite must be first"
    assert len(cites) >= 2, "must carry an attestation cite alongside the envelope cite"
    assert (e.get("calibration_anchor") or {}).get("vuln_posture", "").strip()


def test_builder_ind2sec_matches_differentiation_guard() -> None:
    """Drift guard: the builder's copied IND2SEC must equal the differentiation
    guard's (tests/integration/test_library_loss_differentiation._IND2SEC)."""
    from build_attack_coverage_entries import (
        IND2SEC as BUILDER_MAP,  # type: ignore[import-not-found]
    )
    from test_library_loss_differentiation import _IND2SEC  # type: ignore[import-not-found]

    assert BUILDER_MAP == _IND2SEC, "builder IND2SEC drifted from the differentiation guard's copy"


def test_a1_primary_loss_matches_ransomware_on_fileshare_byte_for_byte() -> None:
    """A1 (edge-ransomware-perimeter-gateway) is deliberately loss-effect-identical
    to ransomware-on-fileshare (both professional_services, Sum(primary)=0.70) --
    the genuine tie added to _PL_ALLOWLIST. Both are capped/PERT post-Milestone-B."""
    d = _by_slug()
    a1 = d[_A1_SLUG]
    baseline = d["ransomware-on-fileshare"]
    assert a1["primary_loss"] == baseline["primary_loss"]


def test_total_published_entries_is_102() -> None:
    base = _load_base()
    ext = _load_ext()
    assert len(base) == 31
    assert len(ext) == 71
    published = [e for e in (base + ext) if e["status"] == "published"]
    assert len(published) == 102


def test_w1_in_catastrophic_slugs() -> None:
    from tests._loss_shape_helpers import CATASTROPHIC_SLUGS

    assert _W1_SLUG in CATASTROPHIC_SLUGS
    assert len(CATASTROPHIC_SLUGS) == 11
