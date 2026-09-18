# tests/integration/test_loss_form_stakeholder_test.py
"""Stakeholder-test conformance guards for the seed library (issue #175),
regulator-and-judgment sub-case.

FAIR: Secondary Loss stems from the reactions of secondary stakeholders. A
``fines`` share IS such a reaction (regulator, court or counterparty), and that
reaction forces response cost -- so every fines-firing entry must carry a
``response/secondary`` share. data_disclosure entries without one must be
allowlisted with an entry-specific rationale. See
docs/reference/loss-form-share-rubric.md section 4 and fair-departures-register.md B5.
The customer-reaction sub-case (reputation without fines) is NOT guarded here
(tracked in #181).

The checks are pure functions over entry dicts so they are shown RED on a
synthetic violating entry (revert-to-red discipline), then run on the real seed.
"""

from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "build_secondary_response_reclass.py"
_spec = importlib.util.spec_from_file_location("build_secondary_response_reclass", _SCRIPT)
assert _spec is not None and _spec.loader is not None
reclass = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(reclass)

# A fines-firing entry that legitimately carries NO response/secondary share must be
# listed here with a rationale. Empty today; the mechanism exists so a future
# exception is loud and explained, never silent.
_FINES_WITHOUT_RESPONSE_ALLOWLIST: dict[str, str] = {}

# ±0.04 band for the 7 legacy authored data_disclosure splits only (the 24 rule-derived
# entries are checked exactly by exact_split_violations). The band is tight only against
# moving AWAY from the rule; a legacy entry may sit up to 2c on the rule-ward side.
# Binding today: hospitality-pos-card-skimming, retail-pos-card-skimming,
# s3-misconfiguration-data-exposure at 0.14/0.36 = 0.3889 vs 3/7 (distance 0.0397,
# headroom 0.0003).
_FRACTION_TOLERANCE = 0.04

_BASES = {reclass.SECONDARY_RESPONSE_BASIS_DATA, reclass.SECONDARY_RESPONSE_BASIS_PROCEEDING}

# Pre-#175 response/primary total per renumbered slug (== p + s after the split; the
# rule preserves the budget). Lets exact_split_violations check the 24 rule-derived
# splits exactly instead of within a tolerance band.
_RESPONSE_BUDGET: dict[str, float] = {
    "ransomware-on-ehr": 0.25,
    "ransomware-on-historian": 0.25,
    "tolling-plant-ransomware-customer-liability": 0.25,
    "judiciary-court-system-ransomware": 0.25,
    "edge-ransomware-perimeter-gateway": 0.25,
    "ransomware-healthcare-small-practice": 0.22,
    "law-firm-privileged-data-ransomware-extortion": 0.30,
    "safety-system-bypass": 0.15,
    "chemical-process-safety-attack": 0.15,
    "unauthorized-plc-modification": 0.16,
    "insider-data-theft-financial": 0.20,
    "education-student-records-insider": 0.20,
    "accidental-insider-exposure": 0.18,
    "healthcare-staff-credential-phish": 0.26,
    "food-recall-data-tampering": 0.20,
    "financial-transaction-tampering": 0.22,
    "healthcare-record-alteration": 0.22,
    "energy-settlement-platform-tampering-offtaker-liability": 0.18,
    "pipeline-nomination-scada-curtailment-shipper-penalty": 0.10,
    "k12-edtech-vendor-breach": 0.35,
    "data-breach-notification-regulatory-tail": 0.20,
    "generative-ai-prompt-injection": 0.21,
    "law-enforcement-records-extortion-breach": 0.30,
    "telecom-lawful-intercept-nationstate-compromise": 0.30,
}


def _load() -> list[dict]:
    entries: list[dict] = []
    for name in ("seed_library_entries.json", "seed_library_entries_extension.json"):
        entries.extend(json.loads(Path("data", name).read_text(encoding="utf-8")))
    return entries


def _share(e: dict, form: str, kind: str) -> float:
    return sum(
        (p.get("share") or 0.0)
        for p in e["loss_form_profile"]
        if p["form"] == form and p["kind"] == kind
    )


def fines_without_secondary_response(entries: list[dict]) -> list[str]:
    return sorted(
        e["slug"]
        for e in entries
        if _share(e, "fines", "secondary") > 0
        and _share(e, "response", "secondary") == 0
        and e["slug"] not in _FINES_WITHOUT_RESPONSE_ALLOWLIST
    )


def data_disclosure_without_secondary_response(entries: list[dict]) -> list[str]:
    return sorted(
        e["slug"]
        for e in entries
        if e["threat_event_type"] == "data_disclosure"
        and _share(e, "response", "secondary") == 0
        and e["slug"] not in reclass.NO_SECONDARY_RESPONSE_ALLOWLIST
    )


def _is_rule_derived(e: dict) -> bool:
    return any(
        p["form"] == "response" and p["kind"] == "secondary" and p.get("magnitude_basis") in _BASES
        for p in e["loss_form_profile"]
    )


def fraction_violations(entries: list[dict]) -> list[str]:
    """±_FRACTION_TOLERANCE band, restricted to the 7 legacy authored splits.
    Rule-derived entries (a _BASES magnitude_basis on their response/secondary row) are
    checked exactly by exact_split_violations instead and are skipped here."""
    out = []
    for e in entries:
        if _is_rule_derived(e):
            continue
        p, s = _share(e, "response", "primary"), _share(e, "response", "secondary")
        if s <= 0:
            continue
        rule = reclass.SECONDARY_FRACTION.get(
            e["threat_event_type"], reclass.DEFAULT_SECONDARY_FRACTION
        )
        total = p + s
        frac = s / total
        if abs(frac - rule) > _FRACTION_TOLERANCE:
            out.append(
                f"{e['slug']}: s/(p+s)={frac:.3f} vs rule {rule:.3f} "
                f"(distance {abs(frac - rule):.4f} > tolerance {_FRACTION_TOLERANCE}) "
                f"type={e['threat_event_type']} p={p:.2f} s={s:.2f} total={total:.2f} "
                f"rule-prescribed s={round(total * rule, 2):.2f}"
            )
    return sorted(out)


def exact_split_violations(entries: list[dict]) -> list[str]:
    """The 24 rule-derived entries (a _BASES magnitude_basis on their response/secondary
    row) must split their pre-#175 response budget (_RESPONSE_BUDGET) EXACTLY per the
    rule -- no tolerance band. Reports slug, type, p, s, budget and the rule-prescribed s."""
    out = []
    for e in entries:
        if not _is_rule_derived(e):
            continue
        slug = e["slug"]
        p, s = _share(e, "response", "primary"), _share(e, "response", "secondary")
        rule = reclass.SECONDARY_FRACTION.get(
            e["threat_event_type"], reclass.DEFAULT_SECONDARY_FRACTION
        )
        budget = _RESPONSE_BUDGET[slug]
        exp_s = round(budget * rule, 2)
        exp_p = round(budget - exp_s, 2)
        if abs(p + s - budget) >= 1e-9 or s != exp_s or p != exp_p:
            out.append(
                f"{slug}: type={e['threat_event_type']} p={p} s={s} budget={budget} "
                f"rule-prescribed s={exp_s}"
            )
    return sorted(out)


def _synthetic(slug: str, ttype: str, shares: list[tuple[str, str, float]]) -> dict:
    return {
        "slug": slug,
        "threat_event_type": ttype,
        "loss_form_profile": [{"form": f, "kind": k, "share": s} for f, k, s in shares],
    }


def test_guard_bites_on_fines_without_secondary_response() -> None:
    bad = _synthetic(
        "bad", "ransomware", [("response", "primary", 0.25), ("fines", "secondary", 0.10)]
    )
    good = _synthetic(
        "good",
        "ransomware",
        [
            ("response", "primary", 0.17),
            ("response", "secondary", 0.08),
            ("fines", "secondary", 0.10),
        ],
    )
    clean = _synthetic("clean", "ransomware", [("response", "primary", 0.25)])
    assert fines_without_secondary_response([bad, good, clean]) == ["bad"]


def test_guard_bites_on_unlisted_data_disclosure() -> None:
    bad = _synthetic(
        "bad-dd",
        "data_disclosure",
        [("response", "primary", 0.18), ("reputation", "secondary", 0.15)],
    )
    listed = _synthetic(
        "edge-espionage-nationstate", "data_disclosure", [("response", "primary", 0.28)]
    )
    non_dd_clean = _synthetic("non-dd-clean", "ransomware", [("response", "primary", 0.20)])
    assert data_disclosure_without_secondary_response([bad, listed, non_dd_clean]) == ["bad-dd"]


def test_guard_bites_on_off_rule_fraction() -> None:
    bad = _synthetic(
        "bad", "ransomware", [("response", "primary", 0.10), ("response", "secondary", 0.15)]
    )
    on_rule_dd = _synthetic(
        "on-rule-dd",
        "data_disclosure",
        [("response", "primary", 0.12), ("response", "secondary", 0.09)],
    )
    off_rule_dd = _synthetic(
        "off-rule-dd",
        "data_disclosure",
        [("response", "primary", 0.18), ("response", "secondary", 0.06)],
    )
    violations = fraction_violations([bad, on_rule_dd, off_rule_dd])
    assert [v.split(":")[0] for v in violations] == ["bad", "off-rule-dd"]
    (msg,) = [v for v in violations if v.startswith("bad:")]
    assert msg == (
        "bad: s/(p+s)=0.600 vs rule 0.333 (distance 0.2667 > tolerance 0.04) "
        "type=ransomware p=0.10 s=0.15 total=0.25 rule-prescribed s=0.08"
    )


def test_guard_bites_on_inexact_rule_derived_split() -> None:
    bad = _synthetic(
        "ransomware-on-ehr",
        "ransomware",
        [("response", "primary", 0.17), ("response", "secondary", 0.09)],
    )
    for row in bad["loss_form_profile"]:
        if row["form"] == "response" and row["kind"] == "secondary":
            row["magnitude_basis"] = reclass.SECONDARY_RESPONSE_BASIS_DATA
    assert exact_split_violations([bad]) == [
        "ransomware-on-ehr: type=ransomware p=0.17 s=0.09 budget=0.25 rule-prescribed s=0.08"
    ]
    # budget-preserving mis-split: only the s != rule-prescribed clause can catch this one
    shifted = _synthetic(
        "ransomware-on-ehr",
        "ransomware",
        [("response", "primary", 0.16), ("response", "secondary", 0.09)],
    )
    for row in shifted["loss_form_profile"]:
        if row["form"] == "response" and row["kind"] == "secondary":
            row["magnitude_basis"] = reclass.SECONDARY_RESPONSE_BASIS_DATA
    assert exact_split_violations([shifted]) == [
        "ransomware-on-ehr: type=ransomware p=0.16 s=0.09 budget=0.25 rule-prescribed s=0.08"
    ]


def test_fines_secondary_implies_response_secondary() -> None:
    entries = _load()
    assert fines_without_secondary_response(entries) == []
    by_slug = {e["slug"]: e for e in entries}
    for slug, rationale in _FINES_WITHOUT_RESPONSE_ALLOWLIST.items():
        assert slug in by_slug, f"dead allowlist row: {slug}"
        e = by_slug[slug]
        assert _share(e, "fines", "secondary") > 0, f"{slug} no longer fires fines"
        assert _share(e, "response", "secondary") == 0, (
            f"{slug} now carries a secondary response; drop it from the allowlist"
        )
        assert len(rationale.strip()) > 40, f"{slug}: rationale must be entry-specific"


def test_data_disclosure_without_secondary_response_is_allowlisted() -> None:
    entries = _load()
    assert data_disclosure_without_secondary_response(entries) == []
    by_slug = {e["slug"]: e for e in entries}
    for slug, rationale in reclass.NO_SECONDARY_RESPONSE_ALLOWLIST.items():
        assert slug in by_slug, f"dead allowlist row: {slug}"
        e = by_slug[slug]
        assert e["threat_event_type"] == "data_disclosure", slug
        assert _share(e, "fines", "secondary") == 0, (
            f"{slug} fires fines; it must be renumbered, not allowlisted"
        )
        assert _share(e, "response", "secondary") == 0, (
            f"{slug} now carries a secondary response; drop it from the allowlist"
        )
        assert len(rationale.strip()) > 40, f"{slug}: rationale must be entry-specific"
    assert len(reclass.NO_SECONDARY_RESPONSE_ALLOWLIST) == 4


def test_secondary_response_fraction_follows_rule() -> None:
    assert fraction_violations(_load()) == []


def test_rule_derived_splits_are_exact() -> None:
    assert exact_split_violations(_load()) == []


def test_response_budget_covers_renumbered_slugs() -> None:
    assert set(_RESPONSE_BUDGET) == set(reclass.RENUMBERED_SLUGS)


def test_renumbered_set_matches_script_constant() -> None:
    """The 24 slugs the script names are exactly those carrying one of the two new basis strings."""
    carrying = {
        e["slug"]
        for e in _load()
        if any(
            p["form"] == "response" and p["kind"] == "secondary" and p["magnitude_basis"] in _BASES
            for p in e["loss_form_profile"]
        )
    }
    assert carrying == set(reclass.RENUMBERED_SLUGS)
    assert len(carrying) == 24


def _side_sums(entry: dict) -> tuple[float, float]:
    sp = sum(p["share"] for p in entry["loss_form_profile"] if p["kind"] == "primary")
    ss = sum(p["share"] for p in entry["loss_form_profile"] if p["kind"] == "secondary")
    return sp, ss


def test_register_a5_b5_descriptive_statistics_hold() -> None:
    """Tripwire for the register's A5/B5 consequence figures (T5c-Meth N-4): re-tuning a
    renumbered entry's shares must fail here, not silently invalidate the register."""
    by_slug = {e["slug"]: e for e in _load()}
    telecom = _side_sums(by_slug["telecom-lawful-intercept-nationstate-compromise"])
    assert tuple(round(v, 9) for v in telecom) == (0.32, 0.25)  # register A5: 0.668 -> 0.508
    near_floor = 0
    for slug in reclass.RENUMBERED_SLUGS:
        sp, ss = _side_sums(by_slug[slug])
        ratio = (sp**2 + ss**2) / (sp + ss) ** 2  # A5 variance ratio, floor 0.5 at sp == ss
        near_floor += ratio < 0.51
    assert near_floor == 5  # register A5: "five entries now sit within 0.01 of the 0.5 floor"


def _bands() -> dict[str, tuple[float, float]]:
    """Register B5/A1 consequence bands recomputed from the shipped seed: the transferred
    share s is the response/secondary share; Σp₀ = Σp + s and Σs₀ = Σs − s recover the
    pre-change sides (the sweep test uses the same identity)."""
    by_slug = {e["slug"]: e for e in _load()}
    residual: dict[float, list[float]] = {0.3: [], 0.9: []}
    roi: list[float] = []
    slef: list[float] = []
    narrows = widens = 0
    for slug in reclass.RENUMBERED_SLUGS:
        e = by_slug[slug]
        sp, ss = _side_sums(e)
        s = next(
            p["share"]
            for p in e["loss_form_profile"]
            if p["form"] == "response" and p["kind"] == "secondary"
        )
        sp0, ss0 = sp + s, ss - s
        for eff in residual:
            residual[eff].append(0.3 * eff * s / (sp0 * (1 - 0.2 * eff) + ss0 * (1 - 0.5 * eff)))
        roi.append(0.3 * s / (0.2 * sp0 + 0.5 * ss0))
        slef.append(s * 0.5 / (sp0 + 0.5 * ss0))
        # Independent PL+SL at a fixed total mean: the sum's variance is smallest at
        # balance, so moving |Σp − Σs| toward zero narrows the per-event tail (A5).
        if abs(sp - ss) < abs(sp0 - ss0):
            narrows += 1
        else:
            widens += 1
    return {
        "residual": (min(residual[0.3]), max(residual[0.9])),
        "roi": (min(roi), max(roi)),
        "slef": (min(slef), max(slef)),
        "tails": (narrows, widens),
    }


def test_register_b5_consequence_bands_hold() -> None:
    """Tripwire for the register's descriptive bands (PRG-Meth N-4): re-tuning any share on a
    renumbered entry must fail here rather than silently stale B5/A1."""
    b = _bands()
    assert (f"{100 * b['residual'][0]:.2f}", f"{100 * b['residual'][1]:.1f}") == ("0.37", "8.9")
    assert (f"{100 * b['roi'][0]:.0f}", f"{100 * b['roi'][1]:.0f}") == ("4", "26")
    assert (f"{100 * b['slef'][0]:.1f}", f"{100 * b['slef'][1]:.1f}") == ("2.2", "14.9")
    assert b["tails"] == (17, 7)


def test_composition_role_dominant_is_the_global_argmax() -> None:
    """Corpus-wide invariant behind the 7 role flips (PRG-Arch N-4): `dominant` marks exactly
    the rows whose share equals the entry's maximum share across both kinds."""
    for e in _load():
        rows = [p for p in e["loss_form_profile"] if p.get("share") is not None]
        if not rows:
            continue
        top = max(p["share"] for p in rows)
        expected = {(p["form"], p["kind"]) for p in rows if p["share"] == top}
        actual = {(p["form"], p["kind"]) for p in rows if p.get("composition_role") == "dominant"}
        assert actual == expected, e["slug"]


def test_ind2sec_matches_guard_copy() -> None:
    # tests/ and tests/integration/ are packages, so the repo root is on sys.path.
    from tests.integration.test_library_loss_differentiation import _IND2SEC

    assert reclass.IND2SEC == _IND2SEC


def test_reclass_script_is_idempotent_on_shipped_data() -> None:
    entries = _load()
    env = {
        r["sector"]: r
        for r in json.loads(Path("data", "loss_form_envelopes.json").read_text(encoding="utf-8"))
    }
    snapshot = copy.deepcopy(entries)
    assert reclass.reclassify(entries, env) == []
    assert entries == snapshot
