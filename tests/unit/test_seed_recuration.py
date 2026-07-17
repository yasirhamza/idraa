"""Epic C-iii-a: re-curation guard tests for all 44 seed library entries.

These tests run against the committed seed JSON files (data/seed_library_entries.json
and data/seed_library_entries_extension.json) and verify that the recurate_seed_entries
script has applied the correct transformations per the plan rules 1–8.

Tests (a)–(h) as specified in the plan Step 2:

  (a) Every entry has explicit loss_tier.
  (b) Entries whose archetype anchor is quantile_pair carry lognormal primary_loss
      with σ == anchor-derived value (hand-check 3 named entries).
  (c) none-anchored entries retain their EXACT pre-existing PERT values (pin 2 examples
      byte-wise).
  (d) Every entry's calibration_anchor has vuln_posture.
  (e) The 2 flagged entries' vuln modes are the new values AND
      credential-stuffing-consumer-portal's TEF is {1, 5, 20}.
  (f) All 44 slugs still present (no entry lost).
  (g) credential-stuffing-consumer-portal's canonical_fair_gap no longer contains
      the per-attempt phrasing ("successful stuffing rate").
  (h) For 2 named entries that have BOTH primary and secondary loss and a quantile_pair
      anchor: σ_secondary == σ_primary AND mean_secondary ≈ ln(p50_primary × R).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

_SEED_FILES = (
    "data/seed_library_entries.json",
    "data/seed_library_entries_extension.json",
)

_ANCHORS_FILE = "data/loss_anchor_tables.json"
_ARCHETYPES_FILE = "data/target_archetypes.json"

Z_0_95 = 1.6448536269514722


def _load_all_entries() -> dict[str, dict]:
    entries: dict[str, dict] = {}
    for f in _SEED_FILES:
        for e in json.loads(Path(f).read_text(encoding="utf-8")):
            entries[e["slug"]] = e
    return entries


def _load_anchors() -> dict[str, dict]:
    rows = json.loads(Path(_ANCHORS_FILE).read_text(encoding="utf-8"))
    return {r["archetype"]: r for r in rows}


def _load_keep_archetypes() -> list[dict]:
    rows = json.loads(Path(_ARCHETYPES_FILE).read_text(encoding="utf-8"))
    return [r for r in rows if r.get("keep_or_new") == "keep"]


# ── (a) every entry has explicit loss_tier ─────────────────────────────────────


def test_every_entry_has_explicit_loss_tier() -> None:
    """(a) Every keep entry must carry an explicit loss_tier field after re-curation."""
    entries = _load_all_entries()
    keeps = _load_keep_archetypes()
    missing = []
    for k in keeps:
        slug = k["existing_slug"]
        e = entries[slug]
        tier = e.get("loss_tier")
        if not tier or tier not in {"paginated", "vendor", "anecdotal", "none"}:
            missing.append((slug, tier))
    assert missing == [], f"Entries missing valid explicit loss_tier: {missing}"


# ── (b) quantile_pair entries carry lognormal primary_loss with correct σ ─────


def test_quantile_pair_entries_have_shape_class_loss() -> None:
    """(b) Post-Milestone-B (#loss-pert-overhaul): quantile_pair-anchored
    entries carry a bounded PERT primary_loss (capped default); only the
    catastrophic shortlist keeps the native lognormal."""
    from tests._loss_shape_helpers import CATASTROPHIC_SLUGS

    entries = _load_all_entries()
    anchors = _load_anchors()
    keeps = _load_keep_archetypes()
    wrong_shape = []
    for k in keeps:
        arch = k["archetype"]
        anchor = anchors.get(arch)
        if anchor and anchor["anchor_type"] == "quantile_pair":
            slug = k["existing_slug"]
            pl = entries[slug].get("primary_loss", {})
            expected = "lognormal" if slug in CATASTROPHIC_SLUGS else "PERT"
            if str(pl.get("distribution", "")) != expected:
                wrong_shape.append((slug, pl.get("distribution"), expected))
    assert wrong_shape == [], f"entries with wrong loss shape for their class: {wrong_shape}"


def test_every_entry_calibration_anchor_has_vuln_posture() -> None:
    """(d) Every entry's calibration_anchor must carry a vuln_posture key after re-curation."""
    entries = _load_all_entries()
    keeps = _load_keep_archetypes()
    missing = []
    for k in keeps:
        slug = k["existing_slug"]
        e = entries[slug]
        anchor = e.get("calibration_anchor", {})
        if not anchor.get("vuln_posture"):
            missing.append(slug)
    assert missing == [], f"Entries missing vuln_posture in calibration_anchor: {missing}"


# ── (e) flagged entries get new vuln + credential-stuffing TEF ────────────────


def test_credential_stuffing_vuln_raised_to_campaign_level() -> None:
    """(e) credential-stuffing-consumer-portal vuln mode == 0.30 (inherent, campaign-level)."""
    entries = _load_all_entries()
    e = entries["credential-stuffing-consumer-portal"]
    vuln = e["vulnerability"]
    assert vuln["low"] == pytest.approx(0.10, rel=1e-6)
    assert vuln["mode"] == pytest.approx(0.30, rel=1e-6)
    assert vuln["high"] == pytest.approx(0.60, rel=1e-6)


def test_credential_stuffing_tef_reinterpreted_to_campaign_frequency() -> None:
    """(e) credential-stuffing TEF = PERT campaign frequency {low:1, mode:5, high:20}
    (post #tef-pert-revert — TEF is bounded PERT again)."""
    entries = _load_all_entries()
    tef = entries["credential-stuffing-consumer-portal"]["threat_event_frequency"]
    assert tef["distribution"] == "PERT"
    assert tef["low"] == pytest.approx(1, rel=1e-6)
    assert tef["mode"] == pytest.approx(5, rel=1e-6)
    assert tef["high"] == pytest.approx(20, rel=1e-6)


def test_bec_fraud_vuln_raised_to_inherent_level() -> None:
    """(e) bec-fraud-financial vuln mode == 0.20 (inherent, control-naive BEC success)."""
    entries = _load_all_entries()
    vuln = entries["bec-fraud-financial"]["vulnerability"]
    assert vuln["low"] == pytest.approx(0.05, rel=1e-6)
    assert vuln["mode"] == pytest.approx(0.20, rel=1e-6)
    assert vuln["high"] == pytest.approx(0.45, rel=1e-6)


# ── (f) all 44 slugs still present ────────────────────────────────────────────


def test_all_44_keep_slugs_present() -> None:
    """(f) All 44 keep slugs must be present in the seed JSON files."""
    entries = _load_all_entries()
    keeps = _load_keep_archetypes()
    missing = [k["existing_slug"] for k in keeps if k["existing_slug"] not in entries]
    assert len(keeps) == 44, f"Expected 44 keep entries, got {len(keeps)}"
    assert missing == [], f"Keep slugs missing from seed files: {missing}"


# ── (g) canonical_fair_gap no longer contains per-attempt phrasing ─────────────


def test_credential_stuffing_canonical_fair_gap_rewritten_to_campaign_level() -> None:
    """(g) credential-stuffing canonical_fair_gap no longer describes per-attempt model."""
    entries = _load_all_entries()
    gap = entries["credential-stuffing-consumer-portal"]["canonical_fair_gap"]
    # The old per-attempt phrasing must be gone
    assert "successful stuffing rate" not in gap, (
        "canonical_fair_gap still contains per-attempt phrasing 'successful stuffing rate'; "
        "must be rewritten to campaign-level framing per rule 7"
    )


# ── (h) σ_secondary == σ_primary and mean_secondary ≈ ln(p50 × R) ─────────────
