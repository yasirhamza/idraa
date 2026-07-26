"""Unit tests for routes.scenario_form_helpers per-node distribution parsing.

Epic B (#326) Task 5: TEF / Primary loss / Secondary loss each gain a
PERT|lognormal selector. ``dist_from_raw`` dispatches on ``{prefix}_dist``;
for "lognormal" it reads ``{prefix}_low``/``{prefix}_high`` as the p5/p95 pair
and stores native ``{mean, sigma}`` via the closed form in
``fair_cam.quantile_pooling.lognormal_from_quantiles``. Vulnerability is a
probability ∈ [0, 1] and stays PERT-only — no ``vuln_dist`` field is ever read.

These tests pin:
  - lognormal node stored as native {mean, sigma} (no low/mode/high leakage),
  - the PERT default path is unregressed,
  - vulnerability is never lognormal even if a ``vuln_dist`` field is injected,
  - the secondary-loss lognormal branch honours ``sl_dist``,
  - the ``dist_to_form`` round-trip emits ``{prefix}_dist`` + the right fields,
  - ``form_defaults`` carries the ``*_dist`` defaults so the select renders.
"""

from __future__ import annotations

import math
from types import SimpleNamespace

import pytest

from idraa.routes.scenario_form_helpers import (
    EFFECT_CHOICES,
    ScenarioFormValidationError,
    dist_from_raw,
    dist_to_form,
    form_defaults,
    form_from_scenario,
    parse_scenario_form,
)


def _base_raw(**over: object) -> dict[str, object]:
    raw: dict[str, object] = {
        "name": "S",
        "threat_category": "ransomware",
        "tef_dist": "pert",
        "tef_low": "1",
        "tef_mode": "2",
        "tef_high": "3",
        "vuln_low": "0.1",
        "vuln_mode": "0.2",
        "vuln_high": "0.3",
        "pl_dist": "pert",
        "pl_low": "100",
        "pl_mode": "200",
        "pl_high": "300",
    }
    raw.update(over)
    return raw


# ── parse_scenario_form dispatch ──────────────────────────────────────────


def test_lognormal_node_stored_as_native() -> None:
    raw = _base_raw(pl_dist="lognormal", pl_low="100", pl_high="10000")
    form = parse_scenario_form(raw)
    assert form.primary_loss["distribution"] == "lognormal"
    # ln(100)=4.60517, ln(10000)=9.21034; symmetric p5/p95 => mean = midpoint.
    assert form.primary_loss["mean"] == pytest.approx(6.907755, abs=1e-5)
    assert form.primary_loss["sigma"] > 0
    assert "low" not in form.primary_loss
    assert "mode" not in form.primary_loss
    assert "high" not in form.primary_loss


def test_pert_node_default_unregressed() -> None:
    form = parse_scenario_form(_base_raw())
    assert form.primary_loss == {
        "distribution": "PERT",
        "low": 100.0,
        "mode": 200.0,
        "high": 300.0,
    }


def test_tef_lognormal_node_stored_as_native() -> None:
    raw = _base_raw(tef_dist="lognormal", tef_low="0.1", tef_high="10")
    form = parse_scenario_form(raw)
    assert form.threat_event_frequency["distribution"] == "lognormal"
    # ln(0.1) + ln(10) = 0 => mean = 0 exactly.
    assert form.threat_event_frequency["mean"] == pytest.approx(0.0, abs=1e-9)
    assert "mode" not in form.threat_event_frequency


def test_vuln_never_lognormal() -> None:
    # No vuln_dist field is read; vuln is always PERT even if injected.
    raw = _base_raw(vuln_dist="lognormal")  # ignored
    form = parse_scenario_form(raw)
    assert form.vulnerability["distribution"] == "PERT"
    assert form.vulnerability == {
        "distribution": "PERT",
        "low": 0.1,
        "mode": 0.2,
        "high": 0.3,
    }


def test_secondary_loss_lognormal_honours_sl_dist() -> None:
    raw = _base_raw(sl_dist="lognormal", sl_low="1000", sl_high="100000")
    form = parse_scenario_form(raw)
    assert form.secondary_loss is not None
    assert form.secondary_loss["distribution"] == "lognormal"
    assert "mode" not in form.secondary_loss
    # ln(1000)+ln(100000) = 6.9077553 + 11.5129255 = 18.4206807 ; /2 = 9.2103403.
    assert form.secondary_loss["mean"] == pytest.approx(9.2103403, abs=1e-5)


def test_secondary_loss_pert_default_unregressed() -> None:
    raw = _base_raw(sl_low="10", sl_mode="20", sl_high="30")
    form = parse_scenario_form(raw)
    assert form.secondary_loss == {
        "distribution": "PERT",
        "low": 10.0,
        "mode": 20.0,
        "high": 30.0,
    }


def test_secondary_loss_lognormal_blank_is_none() -> None:
    # sl_dist lognormal but no low/high => omitted (matches nullable column).
    raw = _base_raw(sl_dist="lognormal", sl_low="", sl_high="")
    form = parse_scenario_form(raw)
    assert form.secondary_loss is None


def test_lognormal_invalid_low_raises_valueerror() -> None:
    # low <= 0 is rejected by lognormal_from_quantiles (ValueError); the route's
    # existing except (..., ValueError) re-renders the form 422.
    raw = _base_raw(pl_dist="lognormal", pl_low="0", pl_high="100")
    with pytest.raises(ValueError):
        parse_scenario_form(raw)


# ── dist_from_raw directly ────────────────────────────────────────────────


def test_dist_from_raw_pert_default_when_no_dist_field() -> None:
    raw = {"pl_low": "1", "pl_mode": "2", "pl_high": "3"}
    out = dist_from_raw(raw, "pl")
    assert out == {"distribution": "PERT", "low": 1.0, "mode": 2.0, "high": 3.0}


def test_dist_from_raw_lognormal() -> None:
    raw = {"pl_dist": "lognormal", "pl_low": "100", "pl_high": "10000"}
    out = dist_from_raw(raw, "pl")
    assert out["distribution"] == "lognormal"
    assert set(out) == {"distribution", "mean", "sigma"}


# ── dist_to_form round-trip (edit / re-render) ────────────────────────────


def test_dist_to_form_lognormal_round_trips_quantiles() -> None:
    # Build native from quantiles, then re-derive the quantiles for the form.
    parsed = dist_from_raw({"pl_dist": "lognormal", "pl_low": "100", "pl_high": "10000"}, "pl")
    out = dist_to_form(parsed, "pl")
    assert out["pl_dist"] == "lognormal"
    assert out["pl_mode"] == ""  # no mode in lognormal mode
    assert float(out["pl_low"]) == pytest.approx(100.0, rel=1e-6)
    assert float(out["pl_high"]) == pytest.approx(10000.0, rel=1e-6)


def test_dist_to_form_pert_emits_dist_pert() -> None:
    out = dist_to_form({"distribution": "PERT", "low": 1.0, "mode": 2.0, "high": 3.0}, "tef")
    assert out["tef_dist"] == "pert"
    assert out["tef_low"] == "1.0"
    assert out["tef_mode"] == "2.0"
    assert out["tef_high"] == "3.0"


def test_dist_to_form_none_is_pert_blank() -> None:
    out = dist_to_form(None, "sl")
    assert out["sl_dist"] == "pert"
    assert out["sl_low"] == ""


# ── dist_to_form mixture flatten (#27 — edit form, informed replacement) ──

# The epic's worked pair: SME A (meanlog 8.06, σ 0.70) + B (15.77, 1.19),
# equal weights. Exact-identity hand-math anchor: with w_A = 0.5,
# 0.5·F_A(x) = 0.05 ⇔ F_A(x) = 0.10 (B's mass is negligible down there), so
# Q_mix(0.05) = Q_A(0.10) = exp(8.06 + 0.70·Φ⁻¹(0.10)) ≈ $1,290.666.
_MIXTURE_DIST = {
    "distribution": "lognormal_mixture",
    "components": [
        {"mean": 8.06, "sigma": 0.70, "weight": 0.5},
        {"mean": 15.77, "sigma": 1.19, "weight": 0.5},
    ],
}


def test_dist_to_form_mixture_flattens_to_true_mixture_quantiles() -> None:
    """#27: a stored mixture renders as lognormal p5/p95 of the TRUE mixture
    (fair_cam bisection oracle — same convention as the CSV export flatten),
    never blank PERT fields (the pre-fix fall-through)."""
    from fair_cam.quantile_pooling import (
        LogNormalTruncFit,
        LognormMixture,
        mixture_quantile_lognorm,
    )

    out = dist_to_form(_MIXTURE_DIST, "pl")
    assert out["pl_dist"] == "lognormal"
    assert out["pl_mode"] == ""  # no mode under the lognormal selector
    mix = LognormMixture(
        components=tuple(
            LogNormalTruncFit(
                meanlog=c["mean"], sdlog=c["sigma"], min_support=0.0, max_support=math.inf
            )
            for c in _MIXTURE_DIST["components"]
        ),
        weights=(0.5, 0.5),
    )
    # Side-by-side: fair_cam oracle AND the epic's hand-math identity pin.
    assert float(out["pl_low"]) == pytest.approx(mixture_quantile_lognorm(mix, 0.05), rel=1e-12)
    assert float(out["pl_low"]) == pytest.approx(1290.666, rel=1e-6)
    assert float(out["pl_high"]) == pytest.approx(mixture_quantile_lognorm(mix, 0.95), rel=1e-12)
    assert float(out["pl_high"]) == pytest.approx(32444657.93, rel=1e-6)


def test_dist_to_form_mixture_sets_from_mixture_flag_with_component_count() -> None:
    """The {prefix}_from_mixture flag drives the template's informed-replacement
    warning; it carries the component count for the warning copy."""
    out = dist_to_form(_MIXTURE_DIST, "sl")
    assert out["sl_from_mixture"] == "2"


def test_dist_to_form_non_mixture_branches_carry_no_from_mixture_flag() -> None:
    """Absence contract: plain lognormal / PERT / None never emit the flag —
    a stray truthy value would render the replacement warning spuriously."""
    plain = dist_from_raw({"pl_dist": "lognormal", "pl_low": "100", "pl_high": "10000"}, "pl")
    for dist, prefix in (
        (plain, "pl"),
        ({"distribution": "PERT", "low": 1.0, "mode": 2.0, "high": 3.0}, "tef"),
        (None, "sl"),
    ):
        out = dist_to_form(dist, prefix)
        assert f"{prefix}_from_mixture" not in out


# ── form_defaults carries the *_dist selectors ────────────────────────────


def test_form_defaults_has_dist_selectors() -> None:
    d = form_defaults()
    assert d["tef_dist"] == "pert"
    assert d["pl_dist"] == "pert"
    assert d["sl_dist"] == "pert"
    # vuln has no selector (PERT-only).
    assert "vuln_dist" not in d


# Cross-check the helper's mean against the closed form so a future change to
# lognormal_from_quantiles surfaces here, not only in fair_cam's own suite.
def test_lognormal_mean_matches_closed_form() -> None:
    out = dist_from_raw({"pl_dist": "lognormal", "pl_low": "100", "pl_high": "10000"}, "pl")
    assert out["mean"] == pytest.approx((math.log(100) + math.log(10000)) / 2, abs=1e-9)


# ── ScenarioEffect (CIA) ──────────────────────────────────────────────────


def test_effect_choices_are_cia() -> None:
    assert EFFECT_CHOICES == [
        ("confidentiality", "Confidentiality"),
        ("integrity", "Integrity"),
        ("availability", "Availability"),
    ]


def test_parse_scenario_form_reads_effect() -> None:
    raw = _base_raw(effect="availability")
    form = parse_scenario_form(raw)
    assert form.effect == "availability"


def test_parse_scenario_form_effect_empty_is_none() -> None:
    raw = _base_raw(effect="")
    assert parse_scenario_form(raw).effect is None


def test_form_defaults_and_from_scenario_effect_blank() -> None:
    assert form_defaults()["effect"] == ""


# ── PR2 D17 (Task 4c): authorable capacity cap on the expert form ─────────


def test_form_defaults_has_blank_capacity_fields() -> None:
    """The cap field is BLANK on fresh create — never a pre-filled value
    (round-6-fixed decision 2)."""
    d = form_defaults()
    assert d["pl_max"] == ""
    assert d["sl_max"] == ""


def test_dist_from_raw_pl_blank_max_mints_from_capacity() -> None:
    """Blank pl_max -> the caller's minted capacity_max."""
    raw = {"pl_dist": "lognormal", "pl_low": "100", "pl_high": "10000"}
    out = dist_from_raw(raw, "pl", capacity_max=1_000_000.0)
    assert out["max"] == pytest.approx(1_000_000.0)


def test_dist_from_raw_pl_blank_max_no_capacity_omits_max_key() -> None:
    """Blank pl_max + unknown revenue (capacity_max=None, D14) -> no "max" key."""
    raw = {"pl_dist": "lognormal", "pl_low": "100", "pl_high": "10000"}
    out = dist_from_raw(raw, "pl", capacity_max=None)
    assert "max" not in out


def test_dist_from_raw_pl_typed_max_within_bound_used_as_is() -> None:
    raw = {"pl_dist": "lognormal", "pl_low": "100", "pl_high": "10000", "pl_max": "500000"}
    out = dist_from_raw(raw, "pl", capacity_max=1_000_000.0)
    assert out["max"] == pytest.approx(500_000.0)


def test_dist_from_raw_pl_typed_max_no_capacity_accepted_as_is() -> None:
    """Typed pl_max with revenue unset (capacity_max=None) has no ceiling to
    check against — the expert form is the D18 escape hatch."""
    raw = {"pl_dist": "lognormal", "pl_low": "100", "pl_high": "10000", "pl_max": "9999999999"}
    out = dist_from_raw(raw, "pl", capacity_max=None)
    assert out["max"] == pytest.approx(9_999_999_999.0)


def test_dist_from_raw_pl_typed_max_exceeds_capacity_raises() -> None:
    """D13: an explicit override may TIGHTEN below capacity, never LOOSEN
    above it."""
    raw = {"pl_dist": "lognormal", "pl_low": "100", "pl_high": "10000", "pl_max": "2000000"}
    with pytest.raises(ScenarioFormValidationError, match="pl_max"):
        dist_from_raw(raw, "pl", capacity_max=1_000_000.0)


def test_dist_from_raw_pl_typed_max_equal_to_capacity_accepted() -> None:
    """Boundary: max == capacity is accepted (only > capacity is rejected)."""
    raw = {"pl_dist": "lognormal", "pl_low": "100", "pl_high": "10000", "pl_max": "1000000"}
    out = dist_from_raw(raw, "pl", capacity_max=1_000_000.0)
    assert out["max"] == pytest.approx(1_000_000.0)


@pytest.mark.parametrize("bad_val", ["abc", "0", "-5", "inf", "nan"])
def test_dist_from_raw_pl_max_malformed_raises(bad_val: str) -> None:
    raw = {"pl_dist": "lognormal", "pl_low": "100", "pl_high": "10000", "pl_max": bad_val}
    with pytest.raises(ScenarioFormValidationError, match="pl_max"):
        dist_from_raw(raw, "pl", capacity_max=1_000_000.0)


def test_dist_from_raw_tef_never_gets_max_even_if_present_in_raw() -> None:
    """D12/D19 scope: the capacity ceiling applies to loss fields only —
    dist_from_raw only resolves it for prefix == "pl"."""
    raw = {"tef_dist": "lognormal", "tef_low": "0.1", "tef_high": "10", "tef_max": "5"}
    out = dist_from_raw(raw, "tef", capacity_max=1_000_000.0)
    assert "max" not in out


def test_dist_from_raw_pert_kind_unaffected_by_capacity_max() -> None:
    """PERT losses are never capped (D12/D19 govern lognormal only)."""
    raw = {"pl_low": "1", "pl_mode": "2", "pl_high": "3"}
    out = dist_from_raw(raw, "pl", capacity_max=1_000_000.0)
    assert out == {"distribution": "PERT", "low": 1.0, "mode": 2.0, "high": 3.0}


# ── SL max — the INLINE construction site in parse_scenario_form ──────────


def test_secondary_loss_blank_max_mints_from_capacity() -> None:
    raw = _base_raw(sl_dist="lognormal", sl_low="1000", sl_high="100000")
    form = parse_scenario_form(raw, capacity_max=2_000_000.0)
    assert form.secondary_loss is not None
    assert form.secondary_loss["max"] == pytest.approx(2_000_000.0)


def test_secondary_loss_typed_max_within_bound_used_as_is() -> None:
    raw = _base_raw(sl_dist="lognormal", sl_low="1000", sl_high="100000", sl_max="750000")
    form = parse_scenario_form(raw, capacity_max=2_000_000.0)
    assert form.secondary_loss is not None
    assert form.secondary_loss["max"] == pytest.approx(750_000.0)


def test_secondary_loss_typed_max_exceeds_capacity_raises() -> None:
    raw = _base_raw(sl_dist="lognormal", sl_low="1000", sl_high="100000", sl_max="9000000")
    with pytest.raises(ScenarioFormValidationError, match="sl_max"):
        parse_scenario_form(raw, capacity_max=2_000_000.0)


def test_secondary_loss_pert_kind_never_gets_max() -> None:
    """PERT secondary loss is never capped, regardless of a stray sl_max."""
    raw = _base_raw(sl_low="10", sl_mode="20", sl_high="30", sl_max="500")
    form = parse_scenario_form(raw, capacity_max=1_000_000.0)
    assert form.secondary_loss == {
        "distribution": "PERT",
        "low": 10.0,
        "mode": 20.0,
        "high": 30.0,
    }


def test_secondary_loss_none_when_blank_never_mints_max() -> None:
    """No secondary loss at all (blank low/high) -> None, regardless of
    capacity_max — there's no dict to attach a cap to."""
    raw = _base_raw(sl_dist="lognormal", sl_low="", sl_high="")
    form = parse_scenario_form(raw, capacity_max=2_000_000.0)
    assert form.secondary_loss is None


def test_primary_and_secondary_loss_both_capped_independently() -> None:
    """Round-trip: BOTH pl_max and sl_max thread through the SAME create call
    — pl via dist_from_raw, sl via its inline site."""
    raw = _base_raw(
        pl_dist="lognormal",
        pl_low="100",
        pl_high="10000",
        sl_dist="lognormal",
        sl_low="1000",
        sl_high="100000",
    )
    form = parse_scenario_form(raw, capacity_max=5_000_000.0)
    assert form.primary_loss["max"] == pytest.approx(5_000_000.0)
    assert form.secondary_loss is not None
    assert form.secondary_loss["max"] == pytest.approx(5_000_000.0)


# ── dist_to_form round-trips the cap (silent-strip guard) ─────────────────


def test_dist_to_form_lognormal_round_trips_existing_max() -> None:
    dist = {"distribution": "lognormal", "mean": 10.0, "sigma": 1.0, "max": 500_000.0}
    out = dist_to_form(dist, "pl")
    assert out["pl_max"] == "500000.0"


def test_dist_to_form_lognormal_no_max_is_blank() -> None:
    dist = {"distribution": "lognormal", "mean": 10.0, "sigma": 1.0}
    out = dist_to_form(dist, "pl")
    assert out["pl_max"] == ""


def test_dist_to_form_mixture_round_trips_shared_top_level_max() -> None:
    dist = {**_MIXTURE_DIST, "max": 40_000_000.0}
    out = dist_to_form(dist, "pl")
    assert out["pl_max"] == "40000000.0"


def test_dist_to_form_pert_max_is_always_blank() -> None:
    out = dist_to_form({"distribution": "PERT", "low": 1.0, "mode": 2.0, "high": 3.0}, "pl")
    assert out["pl_max"] == ""


def test_dist_to_form_none_max_is_blank() -> None:
    out = dist_to_form(None, "sl")
    assert out["sl_max"] == ""


def test_form_from_scenario_sl_absent_emits_blank_max() -> None:
    """form_from_scenario's explicit no-SL branch also blanks sl_max (not
    just sl_low/mode/high) — a template macro reads it unconditionally."""
    scenario = SimpleNamespace(
        name="S",
        description="",
        threat_category="ransomware",
        threat_actor_type="",
        attack_vector="",
        asset_class="",
        effect="",
        mitigating_controls=[],
        threat_event_frequency={"distribution": "PERT", "low": 0.1, "mode": 0.2, "high": 0.3},
        vulnerability={"distribution": "PERT", "low": 0.1, "mode": 0.2, "high": 0.3},
        primary_loss={"distribution": "PERT", "low": 1.0, "mode": 2.0, "high": 3.0},
        secondary_loss=None,
    )
    out = form_from_scenario(scenario)  # type: ignore[arg-type]
    assert out["sl_max"] == ""


def test_form_from_scenario_preserves_pl_max_for_unchanged_resubmit() -> None:
    """Silent-strip regression (unit level): form_from_scenario emits the
    EXISTING pl.max, and re-parsing that echoed value (no new capacity_max —
    mirrors an edit where revenue hasn't changed) reproduces it exactly.
    Without round-tripping the field here, parse_scenario_form would rebuild
    primary_loss from scratch with no "max" key at all."""
    scenario = SimpleNamespace(
        name="S",
        description="",
        threat_category="ransomware",
        threat_actor_type="",
        attack_vector="",
        asset_class="",
        effect="",
        mitigating_controls=[],
        threat_event_frequency={"distribution": "PERT", "low": 0.1, "mode": 0.2, "high": 0.3},
        vulnerability={"distribution": "PERT", "low": 0.1, "mode": 0.2, "high": 0.3},
        primary_loss={"distribution": "lognormal", "mean": 10.0, "sigma": 1.0, "max": 500_000.0},
        secondary_loss=None,
    )
    form_dict = form_from_scenario(scenario)  # type: ignore[arg-type]
    assert form_dict["pl_max"] == "500000.0"

    raw: dict[str, object] = dict(form_dict)
    raw["name"] = "S"
    raw["threat_category"] = "ransomware"
    form = parse_scenario_form(raw, capacity_max=500_000.0)  # unchanged org revenue
    assert form.primary_loss["max"] == pytest.approx(500_000.0)
