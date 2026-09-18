# tests/unit/test_build_secondary_response_reclass.py
"""Unit tests for scripts/build_secondary_response_reclass.py (issue #175).

Synthetic entries only: the real-data assertions live in
tests/integration/test_loss_form_stakeholder_test.py (Task 2) and the
reconstruction guard. The script is imported by path (scripts/ is not a
package) -- the tests/unit/test_sweep_run_samples_finite.py idiom. No
population count, scenario name or loss figure from any deployment appears in
this file; every fixture value is synthetic or derived from the seed library.
"""

from __future__ import annotations

import copy
import importlib.util
import math
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "build_secondary_response_reclass.py"
_spec = importlib.util.spec_from_file_location("build_secondary_response_reclass", _SCRIPT)
assert _spec is not None and _spec.loader is not None
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)

_BUILDER = Path(__file__).resolve().parents[2] / "scripts" / "build_d_iii_a_recalibration.py"
_bspec = importlib.util.spec_from_file_location("build_d_iii_a_recalibration", _BUILDER)
assert _bspec is not None and _bspec.loader is not None
builder = importlib.util.module_from_spec(_bspec)
_bspec.loader.exec_module(builder)

Z = 1.6448536269514722
ENV = {"healthcare": {"sector": "healthcare", "mean": 13.2303205189, "sigma": 1.9602032156}}


def _entry(
    slug: str, ttype: str, shares: list[tuple[str, str, float]], shape: str = "capped"
) -> dict:
    prof = [
        {
            "form": f,
            "kind": k,
            "magnitude_basis": "envelope-share (analyst-judged, vulnerability-grade; per loss-form-share-rubric.md)",
            "citations": [],
            "verified": False,
            "composition_role": "contributing",
            "share": s,
        }
        for f, k, s in shares
    ]
    dom = max(s for _, _, s in shares)
    for p in prof:
        if p["share"] == dom:
            p["composition_role"] = "dominant"
    sp = sum(s for _, k, s in shares if k == "primary")
    ss = sum(s for _, k, s in shares if k == "secondary")
    mu_p = round(ENV["healthcare"]["mean"] + math.log(sp), 10)
    mu_s = round(ENV["healthcare"]["mean"] + math.log(ss), 10) if ss > 0 else None

    def node(mu: float | None) -> dict | None:
        if mu is None:
            return None
        if shape == "catastrophic":
            return {"distribution": "lognormal", "mean": mu, "sigma": 1.7}
        low = round(math.exp(mu - Z * 1.7), 10)
        return {
            "distribution": "PERT",
            "low": low,
            "mode": low,
            "high": round(math.exp(mu + Z * 1.7), 10),
        }

    return {
        "slug": slug,
        "threat_event_type": ttype,
        "applicable_industries": ["healthcare"],
        "calibration_anchor": {"industry": "healthcare"},
        "loss_tier": "paginated",
        "loss_shape": shape,
        "primary_loss": node(mu_p),
        "secondary_loss": node(mu_s),
        "loss_form_profile": prof,
    }


def _share(e: dict, form: str, kind: str) -> float:
    return sum(
        (p["share"] or 0) for p in e["loss_form_profile"] if p["form"] == form and p["kind"] == kind
    )


def test_ind2sec_matches_builder_map() -> None:
    assert mod.IND2SEC == builder.IND2SEC
    assert {"utilities", "hospitality"} <= set(mod.IND2SEC)


def test_ransomware_style_entry_splits_one_third_and_preserves_total() -> None:
    e = _entry(
        "x-rans",
        "ransomware",
        [
            ("productivity", "primary", 0.42),
            ("response", "primary", 0.25),
            ("replacement", "primary", 0.05),
            ("reputation", "secondary", 0.15),
            ("fines", "secondary", 0.10),
        ],
    )
    before = copy.deepcopy(e)
    assert mod.reclassify([e], ENV) == ["x-rans"]
    assert _share(e, "response", "primary") == 0.17
    assert _share(e, "response", "secondary") == 0.08
    total_before = sum(p["share"] for p in before["loss_form_profile"])
    total_after = sum(p["share"] for p in e["loss_form_profile"])
    assert abs(total_after - total_before) < 1e-9
    # rounded-mu chain (spec section 4 anchor A1): mu = round(13.2303205189 + ln 0.64, 10)
    assert e["primary_loss"]["low"] == e["primary_loss"]["mode"] == 21758.9892608694
    assert e["primary_loss"]["high"] == 5840252.452964859
    assert e["secondary_loss"]["low"] == 11219.4788375594
    assert e["secondary_loss"]["high"] == 3011380.171039504
    rows = [p for p in e["loss_form_profile"] if p["form"] == "response"]
    assert {p["kind"] for p in rows} == {"primary", "secondary"}
    sec = next(p for p in rows if p["kind"] == "secondary")
    assert (
        sec["magnitude_basis"] == mod.SECONDARY_RESPONSE_BASIS_DATA
    )  # x-rans not in PROCEEDING set
    assert (
        sec["composition_role"] == "contributing"
        and sec["citations"] == []
        and sec["verified"] is False
    )
    assert [p["form"] for p in e["loss_form_profile"]][:5] == [
        "productivity",
        "response",
        "replacement",
        "reputation",
        "fines",
    ]


def test_proceeding_variant_selected_by_slug() -> None:
    slug = next(iter(mod.PROCEEDING_BASIS_SLUGS))
    e = _entry(
        slug,
        "ransomware",
        [
            ("productivity", "primary", 0.42),
            ("response", "primary", 0.25),
            ("fines", "secondary", 0.10),
        ],
    )
    mod.reclassify([e], ENV)
    sec = next(
        p for p in e["loss_form_profile"] if p["form"] == "response" and p["kind"] == "secondary"
    )
    assert sec["magnitude_basis"] == mod.SECONDARY_RESPONSE_BASIS_PROCEEDING
    assert (
        len(mod.PROCEEDING_BASIS_SLUGS) == 11 and mod.PROCEEDING_BASIS_SLUGS <= mod.RENUMBERED_SLUGS
    )


def test_social_engineering_phishing_uses_three_sevenths() -> None:
    e = _entry(
        "x-se",
        "social_engineering",
        [
            ("response", "primary", 0.26),
            ("reputation", "secondary", 0.15),
            ("fines", "secondary", 0.12),
        ],
    )
    mod.reclassify([e], ENV)
    assert _share(e, "response", "primary") == 0.15
    assert _share(e, "response", "secondary") == 0.11


def test_data_disclosure_uses_three_sevenths() -> None:
    e = _entry(
        "x-dd",
        "data_disclosure",
        [
            ("response", "primary", 0.20),
            ("reputation", "secondary", 0.22),
            ("fines", "secondary", 0.20),
        ],
    )
    mod.reclassify([e], ENV)
    assert _share(e, "response", "primary") == 0.11
    assert _share(e, "response", "secondary") == 0.09


def test_extra_renumbered_slug_is_split_without_a_fines_share() -> None:
    slug = next(iter(mod.EXTRA_RENUMBERED))
    e = _entry(
        slug,
        "data_disclosure",
        [
            ("response", "primary", 0.30),
            ("replacement", "primary", 0.15),
            ("reputation", "secondary", 0.12),
        ],
        shape="catastrophic",
    )
    assert mod.reclassify([e], ENV) == [slug]
    assert _share(e, "response", "primary") == 0.17
    assert _share(e, "response", "secondary") == 0.13
    assert e["secondary_loss"] == {
        "distribution": "lognormal",
        "mean": round(ENV["healthcare"]["mean"] + math.log(0.25), 10),
        "sigma": 1.7,
    }


def test_catastrophic_entry_keeps_native_lognormal() -> None:
    e = _entry(
        "x-ot",
        "ot_safety_tampering",
        [
            ("productivity", "primary", 0.45),
            ("replacement", "primary", 0.25),
            ("response", "primary", 0.15),
            ("fines", "secondary", 0.09),
        ],
        shape="catastrophic",
    )
    mod.reclassify([e], ENV)
    assert e["primary_loss"] == {
        "distribution": "lognormal",
        "mean": round(ENV["healthcare"]["mean"] + math.log(0.80), 10),
        "sigma": 1.7,
    }
    assert e["secondary_loss"] == {
        "distribution": "lognormal",
        "mean": round(ENV["healthcare"]["mean"] + math.log(0.14), 10),
        "sigma": 1.7,
    }


def test_no_fines_entry_untouched_and_idempotent() -> None:
    espionage = _entry(
        "x-esp",
        "data_disclosure",
        [("response", "primary", 0.18), ("reputation", "secondary", 0.15)],
    )
    rans = _entry(
        "x-rans",
        "ransomware",
        [
            ("productivity", "primary", 0.42),
            ("response", "primary", 0.25),
            ("fines", "secondary", 0.10),
        ],
    )
    snapshot = copy.deepcopy(espionage)
    assert mod.reclassify([espionage, rans], ENV) == ["x-rans"]
    assert espionage == snapshot
    again = copy.deepcopy([espionage, rans])
    assert mod.reclassify(again, ENV) == []
    assert again == [espionage, rans]


def test_dominant_role_recomputed_after_split() -> None:
    e = _entry(
        "x-law",
        "ransomware",
        [
            ("productivity", "primary", 0.25),
            ("response", "primary", 0.30),
            ("fines", "secondary", 0.08),
        ],
    )
    mod.reclassify([e], ENV)
    roles = {(p["form"], p["kind"]): p["composition_role"] for p in e["loss_form_profile"]}
    assert roles[("productivity", "primary")] == "dominant"
    assert roles[("response", "primary")] == "contributing"


def test_rounding_tie_is_refused() -> None:
    e = _entry(
        "x-tie", "ransomware", [("response", "primary", 0.015), ("fines", "secondary", 0.10)]
    )
    with pytest.raises(mod.ReclassError, match="tie"):
        mod.reclassify([e], ENV)


def test_fines_without_primary_response_row_is_a_named_assertion() -> None:
    e = _entry(
        "x-nores", "ransomware", [("productivity", "primary", 0.40), ("fines", "secondary", 0.10)]
    )
    with pytest.raises(mod.ReclassError, match="x-nores"):
        mod.reclassify([e], ENV)


def test_competitive_advantage_justification_appended_once() -> None:
    e = _entry(
        "insider-ip-theft-manufacturing",
        "insider_misuse",
        [("response", "primary", 0.20), ("reputation", "secondary", 0.15)],
    )
    e["loss_form_profile"].append(
        {
            "form": "competitive_advantage",
            "kind": "primary",
            "magnitude_basis": "UNMODELED -- dominant IP/trade-secret loss (sec6 waiver)",
            "citations": [],
            "verified": False,
            "composition_role": "provenance_only",
            "share": None,
        }
    )
    assert mod.reclassify([e], ENV) == ["insider-ip-theft-manufacturing"]
    ca = next(p for p in e["loss_form_profile"] if p["form"] == "competitive_advantage")
    assert ca["magnitude_basis"].endswith(mod.CA_JUSTIFICATION["insider-ip-theft-manufacturing"])
    assert ca["kind"] == "primary" and ca["share"] is None
    assert len(ca["magnitude_basis"]) <= 512
    assert mod.reclassify([e], ENV) == []


def test_seeded_pair_matches_wizard_helper() -> None:
    from idraa.services.run_executor import _dict_to_fair_distribution
    from idraa.services.wizard_helpers import _quantile_pair

    capped = {"distribution": "PERT", "low": 1000.0, "mode": 1000.0, "high": 250000.0}
    cat = {"distribution": "lognormal", "mean": 12.0, "sigma": 1.7}
    for node in (capped, cat):
        ref = _quantile_pair(_dict_to_fair_distribution(node))
        assert mod.seeded_pair(node) == (round(ref["low"], 10), round(ref["high"], 10))
    # the seeded pair is NOT the PERT bounds
    assert mod.seeded_pair(capped) != (1000.0, 250000.0)


def test_old_new_pairs_reports_seeded_pairs_for_both_fieldsets() -> None:
    e = _entry(
        "x-rans",
        "ransomware",
        [
            ("productivity", "primary", 0.42),
            ("response", "primary", 0.25),
            ("fines", "secondary", 0.10),
        ],
    )
    before = copy.deepcopy([e])
    mod.reclassify([e], ENV)
    pairs = mod.old_new_pairs(before, [e])
    (old_pl, new_pl) = pairs["x-rans"]["pl"]
    assert old_pl == mod.seeded_pair(before[0]["primary_loss"])
    assert new_pl == mod.seeded_pair(e["primary_loss"])
    assert pairs["x-rans"]["sl"][1][1] > pairs["x-rans"]["sl"][0][1]
