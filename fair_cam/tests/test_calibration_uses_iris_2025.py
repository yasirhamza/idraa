"""``create_industry_calibrated_parameters`` consumes the IRIS 2025 reference.

These tests pin the calibration function to its IRIS 2025 grounding rather
than to specific output numbers. The values can shift if the documented
modeling assumptions (Poisson, vulnerability priors, per-industry magnitude
priors) are re-tuned, but the IRIS 2025 source must remain the calibration
target.

After PR β / Task B5 the public translation API lives in
``fair_cam.parameters.industry_calibration`` and takes
``(industry: IndustryType, revenue_tier: str, *, iris_year=None)``.
"""

from __future__ import annotations

import math

import pytest

from fair_cam.data import iris_2025
from fair_cam.parameters.industry_calibration import (
    IndustryType,
    create_industry_calibrated_parameters,
)


def test_calibration_covers_all_iris_2025_industries() -> None:
    """All 20 industries (Figure 8 + BONUS) published in IRIS 2025 must be calibrated."""
    for it in IndustryType:
        params = create_industry_calibrated_parameters(it, "100m_to_1b")
        assert params is not None
        assert params.threat_event_frequency is not None


def test_calibration_covers_member_vault_bonus_sectors() -> None:
    """Spot-check 5 of the 12 new BONUS sectors calibrate without raising."""
    for it in (
        IndustryType.CONSTRUCTION,
        IndustryType.TRADE,
        IndustryType.TRANSPORTATION,
        IndustryType.EDUCATION,
        IndustryType.AGRICULTURE,
    ):
        params = create_industry_calibrated_parameters(it, "100m_to_1b")
        assert params.threat_event_frequency is not None
        assert params.vulnerability is not None
        assert params.primary_loss is not None
        assert params.secondary_loss is not None


def test_healthcare_loss_anchored_on_magnitude_prior() -> None:
    """Healthcare primary_loss anchors on PER_INDUSTRY_MAGNITUDE_PRIORS_2025[HEALTHCARE].p50.

    After B5 the loss anchor is the per-industry magnitude prior, NOT the
    previous OVERALL_LOSS_MEDIAN * multiplier formula. After B-HLT-2
    (2026-06-10) the prior is $557K (IRIS 2025 Figure A3, p. 35 sector
    median), superseding the cross-contaminated ~$4.2M value.
    LOGNORMAL primary_loss "mean" is in log-space, so it equals log(prior_p50).
    """
    from fair_cam.parameters._iris_2025_calibration import (
        PER_INDUSTRY_MAGNITUDE_PRIORS_2025,
    )

    params = create_industry_calibrated_parameters(IndustryType.HEALTHCARE, "100m_to_1b")
    expected_log_mean = math.log(PER_INDUSTRY_MAGNITUDE_PRIORS_2025[IndustryType.HEALTHCARE].p50)
    actual_log_mean = params.primary_loss.parameters["mean"]
    assert abs(actual_log_mean - expected_log_mean) < 1e-9


def test_manufacturing_loss_anchored_on_magnitude_prior() -> None:
    """Manufacturing primary_loss anchors on the per-industry magnitude prior."""
    from fair_cam.parameters._iris_2025_calibration import (
        PER_INDUSTRY_MAGNITUDE_PRIORS_2025,
    )

    params = create_industry_calibrated_parameters(IndustryType.MANUFACTURING, "100m_to_1b")
    expected_log_mean = math.log(PER_INDUSTRY_MAGNITUDE_PRIORS_2025[IndustryType.MANUFACTURING].p50)
    actual_log_mean = params.primary_loss.parameters["mean"]
    assert abs(actual_log_mean - expected_log_mean) < 1e-9


def test_healthcare_tef_grounded_in_iris_2025() -> None:
    """Healthcare TEF must reflect IRIS 2025 grounding (Figure 8 + tier scaling).

    Translation:
        combined_p = p_industry * (p_tier / p_typical)  (clipped to 0.999)
        LEF = -ln(1 - combined_p)
        TEF = LEF / vulnerability_base

    For healthcare at 100m_to_1b: p_industry=0.091, p_tier=0.0999,
    p_typical=0.093 -> tier_scale ~ 1.074 -> combined ~ 0.0977 ->
    LEF ~ 0.103 -> TEF ~ 0.228 (vuln=0.45). Pin the actual computation,
    not magic constants.
    """
    params = create_industry_calibrated_parameters(IndustryType.HEALTHCARE, "100m_to_1b")
    tef_mode = params.threat_event_frequency.parameters["mode"]
    assert 0.05 <= tef_mode <= 1.0, (
        f"healthcare TEF mode {tef_mode} outside plausible IRIS 2025-grounded range"
    )

    # Sanity: re-derive expected TEF from public IRIS 2025 constants.
    p_industry = iris_2025.ANNUAL_INCIDENT_PROBABILITY_BY_INDUSTRY_2024["healthcare"]
    p_tier = iris_2025.ANNUAL_INCIDENT_PROBABILITY_BY_REVENUE_TIER_2024["100m_to_1b"]
    p_typical = iris_2025.ANNUAL_INCIDENT_PROBABILITY_TYPICAL_2024
    tier_scale = p_tier / p_typical
    combined = min(p_industry * tier_scale, 0.999)
    expected_lef = -math.log(1.0 - combined)
    expected_tef = expected_lef / 0.45  # healthcare vulnerability prior
    assert abs(tef_mode - expected_tef) < 1e-6


def test_revenue_tier_scaling_monotonic_in_tef() -> None:
    """Higher revenue tiers must produce higher TEF (per IRIS 2025 Figure 4).

    The new revenue-tier vocabulary uses
    ANNUAL_INCIDENT_PROBABILITY_BY_REVENUE_TIER_2024, where larger orgs have
    monotonically higher annual incident probability (7.5% at <$10M up to
    43.4% at >$100B). The translation API must preserve that ordering.
    """
    medium = create_industry_calibrated_parameters(IndustryType.MANUFACTURING, "100m_to_1b")
    large = create_industry_calibrated_parameters(IndustryType.MANUFACTURING, "1b_to_10b")
    medium_tef = medium.threat_event_frequency.parameters["mode"]
    large_tef = large.threat_event_frequency.parameters["mode"]
    assert large_tef > medium_tef, (
        f"1b_to_10b TEF {large_tef} not greater than 100m_to_1b TEF {medium_tef} — "
        f"violates IRIS 2025 Figure 4 monotonicity"
    )


def test_returns_FAIRParameters_with_required_distributions() -> None:
    params = create_industry_calibrated_parameters(IndustryType.MANUFACTURING, "100m_to_1b")
    assert params.threat_event_frequency is not None
    assert params.vulnerability is not None
    assert params.primary_loss is not None
    assert params.secondary_loss is not None


def test_unknown_revenue_tier_raises() -> None:
    """Unknown revenue_tier strings raise ValueError (no silent fallback).

    The new API is type-safe on industry (enum), and explicit on revenue_tier
    (raises rather than masking spec drift between IRIS revisions).
    """
    with pytest.raises(ValueError, match="revenue_tier"):
        create_industry_calibrated_parameters(IndustryType.MANUFACTURING, "not_a_real_tier")


def test_industry_calibration_data_source_strings_reference_iris_2025() -> None:
    """The industry_calibration.py BenchmarkData entries must surface IRIS 2025."""
    from fair_cam.parameters.industry_calibration import (
        IndustryParameterLibrary,
        IndustryType,
        OrganizationSize,
    )

    lib = IndustryParameterLibrary()
    hc = lib.get_benchmark(
        "threat_event_frequency",
        IndustryType.HEALTHCARE,
        OrganizationSize.MEDIUM,
    )
    assert hc is not None
    assert "IRIS 2025" in hc.data_source
    # last_updated must match IRIS 2025's actual publication date, not the
    # pre-publication "2025-01" placeholder that was there before.
    assert hc.last_updated == iris_2025.PUBLICATION_DATE
    assert hc.last_updated == "2025-06"


def test_industry_calibration_tef_median_translated_from_iris_2025() -> None:
    """Healthcare TEF p50 in IndustryParameterLibrary must equal the
    Poisson-translated value, not the prior 18.2/yr estimate."""
    from fair_cam.parameters.industry_calibration import (
        IndustryParameterLibrary,
        IndustryType,
        OrganizationSize,
    )

    lib = IndustryParameterLibrary()
    hc = lib.get_benchmark(
        "threat_event_frequency",
        IndustryType.HEALTHCARE,
        OrganizationSize.MEDIUM,
    )
    assert hc is not None

    expected_lef = -math.log(
        1.0 - iris_2025.ANNUAL_INCIDENT_PROBABILITY_BY_INDUSTRY_2024["healthcare"]
    )
    expected_tef = expected_lef / 0.45  # documented vulnerability prior
    assert abs(hc.percentile_50 - expected_tef) < 1e-6
    # And it must be FAR below the prior unfounded 18.2/yr.
    assert hc.percentile_50 < 1.0


def test_latest_iris_year_constant_exists():
    """LATEST_IRIS_YEAR must be exposed from fair_cam.data and equal 2025
    for the framework's "latest-default" semantics."""
    from fair_cam.data import LATEST_IRIS_YEAR

    assert LATEST_IRIS_YEAR == 2025


def test_industry_type_enum_has_20_naics2_sectors():
    """IndustryType must enumerate all 20 NAICS-2 sectors that IRIS 2025 covers.

    The keys must exactly match the keys in
    iris_2025.ANNUAL_INCIDENT_PROBABILITY_BY_INDUSTRY_2024.
    """
    from fair_cam.data import iris_2025
    from fair_cam.parameters.industry_calibration import IndustryType

    expected_keys = set(iris_2025.ANNUAL_INCIDENT_PROBABILITY_BY_INDUSTRY_2024.keys())
    enum_values = {it.value for it in IndustryType}

    assert enum_values == expected_keys, (
        f"IndustryType enum values do not match IRIS sector keys.\n"
        f"In IRIS but not enum: {expected_keys - enum_values}\n"
        f"In enum but not IRIS: {enum_values - expected_keys}"
    )
    assert len(enum_values) == 20


def test_iris_2025_calibration_module_exists():
    """The per-year helper module must exist and expose
    PER_INDUSTRY_MAGNITUDE_PRIORS_2025 and IndustryMagnitudePrior."""
    from fair_cam.parameters import _iris_2025_calibration as mod

    assert hasattr(mod, "PER_INDUSTRY_MAGNITUDE_PRIORS_2025")
    assert hasattr(mod, "IndustryMagnitudePrior")
    assert hasattr(mod, "build_from_iris_2025")


def test_industry_magnitude_prior_required_fields():
    """IndustryMagnitudePrior must be a frozen dataclass with p50, p95,
    and notes fields. notes must be non-empty for every entry."""
    from fair_cam.parameters._iris_2025_calibration import (
        PER_INDUSTRY_MAGNITUDE_PRIORS_2025,
        IndustryMagnitudePrior,
    )

    sample = next(iter(PER_INDUSTRY_MAGNITUDE_PRIORS_2025.values()))
    assert isinstance(sample, IndustryMagnitudePrior)
    assert sample.p50 > 0
    assert sample.p95 > sample.p50
    assert sample.notes.strip(), "notes must be non-empty for every entry"


def test_initial_three_priors_present():
    """The first wave of priors covers MANUFACTURING, HEALTHCARE, FINANCIAL.
    Remaining 17 land in task B4."""
    from fair_cam.parameters._iris_2025_calibration import (
        PER_INDUSTRY_MAGNITUDE_PRIORS_2025,
    )
    from fair_cam.parameters.industry_calibration import IndustryType

    for it in (IndustryType.MANUFACTURING, IndustryType.HEALTHCARE, IndustryType.FINANCIAL):
        assert it in PER_INDUSTRY_MAGNITUDE_PRIORS_2025, (
            f"{it.value} prior missing in initial 3-entry set"
        )


def test_trio_invariant_every_industry_has_freq_and_magnitude_prior():
    """Trio invariant: every IndustryType enum value must have BOTH
    an entry in IRIS 2025's ANNUAL_INCIDENT_PROBABILITY_BY_INDUSTRY_2024
    AND an entry in PER_INDUSTRY_MAGNITUDE_PRIORS_2025.
    """
    from fair_cam.data import iris_2025
    from fair_cam.parameters._iris_2025_calibration import (
        PER_INDUSTRY_MAGNITUDE_PRIORS_2025,
    )
    from fair_cam.parameters.industry_calibration import IndustryType

    iris_keys = set(iris_2025.ANNUAL_INCIDENT_PROBABILITY_BY_INDUSTRY_2024.keys())
    enum_values = {it.value for it in IndustryType}
    prior_industries = {it.value for it in PER_INDUSTRY_MAGNITUDE_PRIORS_2025}

    # Three sets must be identical
    assert iris_keys == enum_values == prior_industries, (
        f"Trio invariant violated.\n"
        f"IRIS keys ({len(iris_keys)}): {sorted(iris_keys)}\n"
        f"Enum values ({len(enum_values)}): {sorted(enum_values)}\n"
        f"Priors ({len(prior_industries)}): {sorted(prior_industries)}\n"
        f"Missing from priors: {iris_keys - prior_industries}\n"
        f"Missing from enum: {iris_keys - enum_values}"
    )


def test_every_prior_has_non_empty_notes():
    """Every magnitude prior entry MUST document its anchor source/methodology."""
    from fair_cam.parameters._iris_2025_calibration import (
        PER_INDUSTRY_MAGNITUDE_PRIORS_2025,
    )

    for industry, prior in PER_INDUSTRY_MAGNITUDE_PRIORS_2025.items():
        assert prior.notes.strip(), f"prior for {industry.value} has empty notes"
        assert len(prior.notes.strip()) >= 50, (
            f"prior for {industry.value} has trivially short notes "
            f"(less than 50 chars) — must include anchor source + methodology"
        )


# === B5: year-aware translation API =================================================


def test_create_industry_calibrated_parameters_accepts_iris_year_keyword():
    """The translation API must accept iris_year as a keyword argument
    and default to LATEST_IRIS_YEAR when None."""
    from fair_cam.parameters.industry_calibration import (
        IndustryType,
        create_industry_calibrated_parameters,
    )

    p1 = create_industry_calibrated_parameters(
        industry=IndustryType.MANUFACTURING,
        revenue_tier="1b_to_10b",
        iris_year=None,  # explicit None = latest
    )
    p2 = create_industry_calibrated_parameters(
        industry=IndustryType.MANUFACTURING,
        revenue_tier="1b_to_10b",
        iris_year=2025,
    )
    # Two calls with equivalent year selection should produce identical FAIRParameters.
    # Compare key parameter dicts (dataclass equality may not work for nested dicts).
    assert p1.threat_event_frequency.parameters == p2.threat_event_frequency.parameters
    assert p1.primary_loss.parameters == p2.primary_loss.parameters


def test_unsupported_iris_year_raises_value_error():
    from fair_cam.parameters.industry_calibration import (
        IndustryType,
        create_industry_calibrated_parameters,
    )

    with pytest.raises(ValueError, match="not supported"):
        create_industry_calibrated_parameters(
            industry=IndustryType.MANUFACTURING,
            revenue_tier="1b_to_10b",
            iris_year=99999,
        )


def test_all_20_sectors_calibrate_without_error():
    """Every sector × at least one revenue tier must produce non-zero
    FAIRParameters via the new translation API."""
    from fair_cam.parameters.industry_calibration import (
        IndustryType,
        create_industry_calibrated_parameters,
    )

    for it in IndustryType:
        params = create_industry_calibrated_parameters(
            industry=it,
            revenue_tier="100m_to_1b",
            iris_year=2025,
        )
        assert params is not None
        # TEF and primary_loss distributions must be present and non-trivial
        assert params.threat_event_frequency is not None
        assert params.primary_loss is not None
        assert params.threat_event_frequency.parameters["mode"] > 0


def test_revenue_tier_scaling_loss_invariant():
    """Architectural assertion: tier scaling does NOT affect loss params.
    Loss is anchored on the per-industry magnitude prior; tier-driven loss
    scaling is deferred to overlays (B6/B7).
    """
    from fair_cam.parameters.industry_calibration import (
        IndustryType,
        create_industry_calibrated_parameters,
    )

    medium = create_industry_calibrated_parameters(IndustryType.MANUFACTURING, "100m_to_1b")
    large = create_industry_calibrated_parameters(IndustryType.MANUFACTURING, "1b_to_10b")
    # Loss params should be identical regardless of tier
    assert medium.primary_loss.parameters == large.primary_loss.parameters
    assert medium.secondary_loss.parameters == large.secondary_loss.parameters


def test_unsupported_year_2024_also_raises():
    """Boundary test: a plausibly-unsupported year (2024) should raise
    the same way as a wildly-unsupported year (99999).
    """
    import pytest

    from fair_cam.parameters.industry_calibration import (
        IndustryType,
        create_industry_calibrated_parameters,
    )

    with pytest.raises(ValueError, match="not supported"):
        create_industry_calibrated_parameters(
            industry=IndustryType.MANUFACTURING,
            revenue_tier="1b_to_10b",
            iris_year=2024,
        )


# === Epic B #326: cited-σ derivation, scoped to figure-cited priors ==========


def test_manufacturing_lognormal_sigma_is_cited_derivation() -> None:
    """MANUFACTURING loss σ derives from its BOTH-cited (p50, p95) magnitude
    prior, not a hardcoded constant (re-gate Methodology BLOCKER-2).

    Epic C-iii-a re-anchor (2026-06-11): BOTH legs now from IRIS 2025 Figure A3,
    p. 35 (pure-paginated). Supersedes the prior mixed-source anchor:
      BEFORE: p50=$2.8M (NetDiligence 2024), p95=$23M (conservative estimate);
              σ ≈ 1.2803
      AFTER:  p50=$1M (Figure A3 sector median), p95=$42M (Figure A3 sector 95th);
              σ ≈ 2.2723

    σ = (ln p95 − ln p50) / Z_0_95
      = ln(42e6 / 1e6) / 1.6448536269514722
      ≈ 2.2723
    """
    from fair_cam.parameters._iris_2025_calibration import build_from_iris_2025
    from fair_cam.quantile_pooling import Z_0_95

    params = build_from_iris_2025(IndustryType.MANUFACTURING, "100m_to_1b")
    assert params.primary_loss.distribution_type.value == "lognormal"
    expected_sigma = (math.log(42_000_000) - math.log(1_000_000)) / Z_0_95
    assert expected_sigma == pytest.approx(2.2723, abs=1e-4)
    assert params.primary_loss.parameters["sigma"] == pytest.approx(expected_sigma, abs=1e-12)
    # Median (mean = ln p50) anchors on the cited p50.
    assert params.primary_loss.parameters["mean"] == pytest.approx(math.log(1_000_000), abs=1e-12)
    # Secondary loss shares the SAME cited σ (same magnitude spread).
    assert params.secondary_loss.distribution_type.value == "lognormal"
    assert params.secondary_loss.parameters["sigma"] == pytest.approx(expected_sigma, abs=1e-12)


def test_healthcare_lognormal_sigma_is_cited_derivation() -> None:
    """HEALTHCARE loss σ = ln(14e6/557e3)/Z_0_95 ≈ 1.9602.

    BOTH legs from IRIS 2025 Figure A3, p. 35 (Appendix — Loss magnitude
    statistics by sector): Healthcare Median $557K; 95th percentile $14M.
    Corrected by B-HLT-2 (2026-06-10): prior values p50=$4.2M/p95=$42M were
    cross-contaminated (DBIR 2024 publishes no per-sector dollar median; $42M
    is Manufacturing's Figure-A3 tail).
    """
    from fair_cam.parameters._iris_2025_calibration import build_from_iris_2025
    from fair_cam.quantile_pooling import Z_0_95

    params = build_from_iris_2025(IndustryType.HEALTHCARE, "100m_to_1b")
    expected_sigma = (math.log(14_000_000) - math.log(557_000)) / Z_0_95
    assert expected_sigma == pytest.approx(1.9602, abs=1e-4)
    assert params.primary_loss.distribution_type.value == "lognormal"
    assert params.primary_loss.parameters["sigma"] == pytest.approx(expected_sigma, abs=1e-12)


def test_non_cited_prior_yields_no_lognormal_node() -> None:
    """A non-figure-cited prior (AGRICULTURE) must NOT emit a lognormal loss
    node — its p95 is anecdotal (JBS 2021), so σ would be uncited. It produces
    PERT loss nodes instead (re-gate Methodology BLOCKER-2)."""
    from fair_cam.parameters._iris_2025_calibration import (
        _SECTOR_TABLE_CITED,
        build_from_iris_2025,
    )

    assert IndustryType.AGRICULTURE not in _SECTOR_TABLE_CITED
    params = build_from_iris_2025(IndustryType.AGRICULTURE, "100m_to_1b")
    assert params.primary_loss.distribution_type.value != "lognormal"
    assert params.primary_loss.distribution_type.value == "pert"
    assert params.secondary_loss.distribution_type.value == "pert"


def test_financial_promoted_to_lognormal_both_legs_figure_a3() -> None:
    """FINANCIAL cites IRIS 2025 Figure A3 for both legs now; it IS in
    _SECTOR_TABLE_CITED and produces a LOGNORMAL, NOT PERT
    (Epic C-iii-a re-anchor: p50=$1M, p95=$194M, both paginated)."""
    from fair_cam.parameters._iris_2025_calibration import (
        _SECTOR_TABLE_CITED,
        build_from_iris_2025,
    )

    assert IndustryType.FINANCIAL in _SECTOR_TABLE_CITED
    params = build_from_iris_2025(IndustryType.FINANCIAL, "100m_to_1b")
    assert params.primary_loss.distribution_type.value == "lognormal"


# === Epic C-iii-a: re-anchor ALL mappable industries to IRIS 2025 Figure A3 ===


def test_sector_table_cited_allowlist_has_all_mappable_industries() -> None:
    """_SECTOR_TABLE_CITED must contain all 18 industries that map to a verified
    Figure A3 row (excludes AGRICULTURE and MINING — near-point-mass exclusions).
    """
    from fair_cam.parameters._iris_2025_calibration import _SECTOR_TABLE_CITED

    expected = frozenset(
        {
            IndustryType.HEALTHCARE,
            IndustryType.UTILITIES,
            IndustryType.EDUCATION,
            IndustryType.INFORMATION,
            IndustryType.MANUFACTURING,
            IndustryType.FINANCIAL,
            IndustryType.RETAIL,
            IndustryType.TRANSPORTATION,
            IndustryType.PROFESSIONAL,
            IndustryType.PUBLIC,
            IndustryType.HOSPITALITY,
            IndustryType.ADMINISTRATIVE,
            IndustryType.CONSTRUCTION,
            IndustryType.ENTERTAINMENT,
            IndustryType.MANAGEMENT,
            IndustryType.OTHER,
            IndustryType.REAL_ESTATE,
            IndustryType.TRADE,
        }
    )
    assert expected == _SECTOR_TABLE_CITED, (
        f"_SECTOR_TABLE_CITED mismatch.\n"
        f"Extra:   {_SECTOR_TABLE_CITED - expected}\n"
        f"Missing: {expected - _SECTOR_TABLE_CITED}"
    )


def test_unmappable_industries_not_in_sector_table_cited() -> None:
    """AGRICULTURE and MINING are excluded (near-point-mass σ); must NOT appear
    in _SECTOR_TABLE_CITED and must NOT produce lognormal loss nodes."""
    from fair_cam.parameters._iris_2025_calibration import (
        _SECTOR_TABLE_CITED,
        build_from_iris_2025,
    )

    for it in (IndustryType.AGRICULTURE, IndustryType.MINING):
        assert it not in _SECTOR_TABLE_CITED, (
            f"{it.value} is excluded from Figure A3 (near-point-mass) but "
            "is present in _SECTOR_TABLE_CITED"
        )
        params = build_from_iris_2025(it, "100m_to_1b")
        assert params.primary_loss.distribution_type.value == "pert", (
            f"{it.value} must produce PERT, got {params.primary_loss.distribution_type.value}"
        )


def test_unmappable_industries_keep_prior_values() -> None:
    """AGRICULTURE and MINING p50/p95 must stay byte-identical to their
    pre-C-iii-a anecdotal anchors (no Figure-A3 row; near-point-mass rows
    excluded).  Pins the constants so a future edit that accidentally replaces
    the retained anchors with the excluded Figure-A3 rows is caught immediately.
    """
    from fair_cam.parameters._iris_2025_calibration import (
        PER_INDUSTRY_MAGNITUDE_PRIORS_2025,
    )

    prior_ag = PER_INDUSTRY_MAGNITUDE_PRIORS_2025[IndustryType.AGRICULTURE]
    assert prior_ag.p50 == 380_000
    assert prior_ag.p95 == 3_800_000

    prior_mn = PER_INDUSTRY_MAGNITUDE_PRIORS_2025[IndustryType.MINING]
    assert prior_mn.p50 == 850_000
    assert prior_mn.p95 == 8_000_000


def test_unmappable_notes_mention_no_figure_a3_row() -> None:
    """AGRICULTURE and MINING notes must mention that no Figure-A3 row maps
    to this industry and that the anecdotal anchor is retained."""
    from fair_cam.parameters._iris_2025_calibration import PER_INDUSTRY_MAGNITUDE_PRIORS_2025

    for it in (IndustryType.AGRICULTURE, IndustryType.MINING):
        notes = PER_INDUSTRY_MAGNITUDE_PRIORS_2025[it].notes
        assert "Figure-A3" in notes or "Figure A3" in notes, (
            f"{it.value} notes must reference Figure A3 exclusion, got: {notes!r}"
        )
        assert "anecdotal" in notes.lower(), (
            f"{it.value} notes must mention anecdotal anchor retained, got: {notes!r}"
        )


def test_all_sector_table_cited_priors_have_figure_a3_in_notes() -> None:
    """Every re-anchored industry's notes must contain 'Figure A3'."""
    from fair_cam.parameters._iris_2025_calibration import (
        _SECTOR_TABLE_CITED,
        PER_INDUSTRY_MAGNITUDE_PRIORS_2025,
    )

    for it in _SECTOR_TABLE_CITED:
        notes = PER_INDUSTRY_MAGNITUDE_PRIORS_2025[it].notes
        assert "Figure A3" in notes, (
            f"{it.value} is in _SECTOR_TABLE_CITED but notes do not contain 'Figure A3': {notes!r}"
        )


# Per-industry sigma pin tests: σ = (ln p95 − ln p50) / Z_0_95


def test_utilities_lognormal_sigma_is_cited_derivation() -> None:
    """UTILITIES: p50=$146K, p95=$3M — both IRIS 2025 Figure A3, p. 35.
    σ = ln(3e6/146e3)/Z_0_95 ≈ 1.8377."""
    from fair_cam.parameters._iris_2025_calibration import build_from_iris_2025
    from fair_cam.quantile_pooling import Z_0_95

    params = build_from_iris_2025(IndustryType.UTILITIES, "100m_to_1b")
    assert params.primary_loss.distribution_type.value == "lognormal"
    expected_sigma = (math.log(3_000_000) - math.log(146_000)) / Z_0_95
    assert expected_sigma == pytest.approx(1.8377, abs=1e-4)
    assert params.primary_loss.parameters["sigma"] == pytest.approx(expected_sigma, abs=1e-12)
    assert params.primary_loss.parameters["mean"] == pytest.approx(math.log(146_000), abs=1e-12)


def test_education_lognormal_sigma_is_cited_derivation() -> None:
    """EDUCATION: p50=$249K, p95=$6M — both IRIS 2025 Figure A3, p. 35.
    σ = ln(6e6/249e3)/Z_0_95 ≈ 1.9346."""
    from fair_cam.parameters._iris_2025_calibration import build_from_iris_2025
    from fair_cam.quantile_pooling import Z_0_95

    params = build_from_iris_2025(IndustryType.EDUCATION, "100m_to_1b")
    assert params.primary_loss.distribution_type.value == "lognormal"
    expected_sigma = (math.log(6_000_000) - math.log(249_000)) / Z_0_95
    assert expected_sigma == pytest.approx(1.9346, abs=1e-4)
    assert params.primary_loss.parameters["sigma"] == pytest.approx(expected_sigma, abs=1e-12)
    assert params.primary_loss.parameters["mean"] == pytest.approx(math.log(249_000), abs=1e-12)


def test_information_lognormal_sigma_is_cited_derivation() -> None:
    """INFORMATION: p50=$718K, p95=$217M — both IRIS 2025 Figure A3, p. 35.
    σ = ln(217e6/718e3)/Z_0_95 ≈ 3.4722."""
    from fair_cam.parameters._iris_2025_calibration import build_from_iris_2025
    from fair_cam.quantile_pooling import Z_0_95

    params = build_from_iris_2025(IndustryType.INFORMATION, "100m_to_1b")
    assert params.primary_loss.distribution_type.value == "lognormal"
    expected_sigma = (math.log(217_000_000) - math.log(718_000)) / Z_0_95
    assert expected_sigma == pytest.approx(3.4722, abs=1e-4)
    assert params.primary_loss.parameters["sigma"] == pytest.approx(expected_sigma, abs=1e-12)
    assert params.primary_loss.parameters["mean"] == pytest.approx(math.log(718_000), abs=1e-12)


def test_manufacturing_lognormal_sigma_after_reanchor() -> None:
    """MANUFACTURING: p50=$1M, p95=$42M — BOTH from IRIS 2025 Figure A3, p. 35.
    σ = ln(42e6/1e6)/Z_0_95 ≈ 2.2723.
    BEFORE: p50=$2.8M (NetDiligence), p95=$23M; σ ≈ 1.2803.
    AFTER:  p50=$1M (Figure A3), p95=$42M (Figure A3); σ ≈ 2.2723.
    """
    from fair_cam.parameters._iris_2025_calibration import build_from_iris_2025
    from fair_cam.quantile_pooling import Z_0_95

    params = build_from_iris_2025(IndustryType.MANUFACTURING, "100m_to_1b")
    assert params.primary_loss.distribution_type.value == "lognormal"
    expected_sigma = (math.log(42_000_000) - math.log(1_000_000)) / Z_0_95
    assert expected_sigma == pytest.approx(2.2723, abs=1e-4)
    assert params.primary_loss.parameters["sigma"] == pytest.approx(expected_sigma, abs=1e-12)
    assert params.primary_loss.parameters["mean"] == pytest.approx(math.log(1_000_000), abs=1e-12)
    assert params.secondary_loss.distribution_type.value == "lognormal"
    assert params.secondary_loss.parameters["sigma"] == pytest.approx(expected_sigma, abs=1e-12)


def test_financial_lognormal_sigma_is_cited_derivation() -> None:
    """FINANCIAL: p50=$1M, p95=$194M — both IRIS 2025 Figure A3, p. 35.
    σ = ln(194e6/1e6)/Z_0_95 ≈ 3.2026.
    Prior was PERT (FFIEC p50 uncited); now both legs paginated."""
    from fair_cam.parameters._iris_2025_calibration import build_from_iris_2025
    from fair_cam.quantile_pooling import Z_0_95

    params = build_from_iris_2025(IndustryType.FINANCIAL, "100m_to_1b")
    assert params.primary_loss.distribution_type.value == "lognormal"
    expected_sigma = (math.log(194_000_000) - math.log(1_000_000)) / Z_0_95
    assert expected_sigma == pytest.approx(3.2026, abs=1e-4)
    assert params.primary_loss.parameters["sigma"] == pytest.approx(expected_sigma, abs=1e-12)
    assert params.primary_loss.parameters["mean"] == pytest.approx(math.log(1_000_000), abs=1e-12)


def test_retail_lognormal_sigma_is_cited_derivation() -> None:
    """RETAIL: p50=$746K, p95=$45M — both IRIS 2025 Figure A3, p. 35.
    σ = ln(45e6/746e3)/Z_0_95 ≈ 2.4924."""
    from fair_cam.parameters._iris_2025_calibration import build_from_iris_2025
    from fair_cam.quantile_pooling import Z_0_95

    params = build_from_iris_2025(IndustryType.RETAIL, "100m_to_1b")
    assert params.primary_loss.distribution_type.value == "lognormal"
    expected_sigma = (math.log(45_000_000) - math.log(746_000)) / Z_0_95
    assert expected_sigma == pytest.approx(2.4924, abs=1e-4)
    assert params.primary_loss.parameters["sigma"] == pytest.approx(expected_sigma, abs=1e-12)
    assert params.primary_loss.parameters["mean"] == pytest.approx(math.log(746_000), abs=1e-12)


def test_transportation_lognormal_sigma_is_cited_derivation() -> None:
    """TRANSPORTATION: p50=$490K, p95=$23M — both IRIS 2025 Figure A3, p. 35.
    σ = ln(23e6/490e3)/Z_0_95 ≈ 2.3399."""
    from fair_cam.parameters._iris_2025_calibration import build_from_iris_2025
    from fair_cam.quantile_pooling import Z_0_95

    params = build_from_iris_2025(IndustryType.TRANSPORTATION, "100m_to_1b")
    assert params.primary_loss.distribution_type.value == "lognormal"
    expected_sigma = (math.log(23_000_000) - math.log(490_000)) / Z_0_95
    assert expected_sigma == pytest.approx(2.3399, abs=1e-4)
    assert params.primary_loss.parameters["sigma"] == pytest.approx(expected_sigma, abs=1e-12)
    assert params.primary_loss.parameters["mean"] == pytest.approx(math.log(490_000), abs=1e-12)


def test_professional_lognormal_sigma_is_cited_derivation() -> None:
    """PROFESSIONAL: p50=$736K, p95=$17M — both IRIS 2025 Figure A3, p. 35.
    σ = ln(17e6/736e3)/Z_0_95 ≈ 1.9088."""
    from fair_cam.parameters._iris_2025_calibration import build_from_iris_2025
    from fair_cam.quantile_pooling import Z_0_95

    params = build_from_iris_2025(IndustryType.PROFESSIONAL, "100m_to_1b")
    assert params.primary_loss.distribution_type.value == "lognormal"
    expected_sigma = (math.log(17_000_000) - math.log(736_000)) / Z_0_95
    assert expected_sigma == pytest.approx(1.9088, abs=1e-4)
    assert params.primary_loss.parameters["sigma"] == pytest.approx(expected_sigma, abs=1e-12)
    assert params.primary_loss.parameters["mean"] == pytest.approx(math.log(736_000), abs=1e-12)


def test_public_lognormal_sigma_is_cited_derivation() -> None:
    """PUBLIC: p50=$214K, p95=$18M — both IRIS 2025 Figure A3, p. 35.
    σ = ln(18e6/214e3)/Z_0_95 ≈ 2.6946."""
    from fair_cam.parameters._iris_2025_calibration import build_from_iris_2025
    from fair_cam.quantile_pooling import Z_0_95

    params = build_from_iris_2025(IndustryType.PUBLIC, "100m_to_1b")
    assert params.primary_loss.distribution_type.value == "lognormal"
    expected_sigma = (math.log(18_000_000) - math.log(214_000)) / Z_0_95
    assert expected_sigma == pytest.approx(2.6946, abs=1e-4)
    assert params.primary_loss.parameters["sigma"] == pytest.approx(expected_sigma, abs=1e-12)
    assert params.primary_loss.parameters["mean"] == pytest.approx(math.log(214_000), abs=1e-12)


def test_hospitality_lognormal_sigma_is_cited_derivation() -> None:
    """HOSPITALITY: p50=$600K, p95=$62M — both IRIS 2025 Figure A3, p. 35.
    σ = ln(62e6/600e3)/Z_0_95 ≈ 2.8197."""
    from fair_cam.parameters._iris_2025_calibration import build_from_iris_2025
    from fair_cam.quantile_pooling import Z_0_95

    params = build_from_iris_2025(IndustryType.HOSPITALITY, "100m_to_1b")
    assert params.primary_loss.distribution_type.value == "lognormal"
    expected_sigma = (math.log(62_000_000) - math.log(600_000)) / Z_0_95
    assert expected_sigma == pytest.approx(2.8197, abs=1e-4)
    assert params.primary_loss.parameters["sigma"] == pytest.approx(expected_sigma, abs=1e-12)
    assert params.primary_loss.parameters["mean"] == pytest.approx(math.log(600_000), abs=1e-12)


def test_administrative_lognormal_sigma_is_cited_derivation() -> None:
    """ADMINISTRATIVE: p50=$529K, p95=$31M — both IRIS 2025 Figure A3, p. 35.
    σ = ln(31e6/529e3)/Z_0_95 ≈ 2.4748."""
    from fair_cam.parameters._iris_2025_calibration import build_from_iris_2025
    from fair_cam.quantile_pooling import Z_0_95

    params = build_from_iris_2025(IndustryType.ADMINISTRATIVE, "100m_to_1b")
    assert params.primary_loss.distribution_type.value == "lognormal"
    expected_sigma = (math.log(31_000_000) - math.log(529_000)) / Z_0_95
    assert expected_sigma == pytest.approx(2.4748, abs=1e-4)
    assert params.primary_loss.parameters["sigma"] == pytest.approx(expected_sigma, abs=1e-12)
    assert params.primary_loss.parameters["mean"] == pytest.approx(math.log(529_000), abs=1e-12)


def test_construction_lognormal_sigma_is_cited_derivation() -> None:
    """CONSTRUCTION: p50=$189K, p95=$5M — both IRIS 2025 Figure A3, p. 35.
    σ = ln(5e6/189e3)/Z_0_95 ≈ 1.9913."""
    from fair_cam.parameters._iris_2025_calibration import build_from_iris_2025
    from fair_cam.quantile_pooling import Z_0_95

    params = build_from_iris_2025(IndustryType.CONSTRUCTION, "100m_to_1b")
    assert params.primary_loss.distribution_type.value == "lognormal"
    expected_sigma = (math.log(5_000_000) - math.log(189_000)) / Z_0_95
    assert expected_sigma == pytest.approx(1.9913, abs=1e-4)
    assert params.primary_loss.parameters["sigma"] == pytest.approx(expected_sigma, abs=1e-12)
    assert params.primary_loss.parameters["mean"] == pytest.approx(math.log(189_000), abs=1e-12)


def test_entertainment_lognormal_sigma_is_cited_derivation() -> None:
    """ENTERTAINMENT: p50=$282K, p95=$12M — both IRIS 2025 Figure A3, p. 35.
    σ = ln(12e6/282e3)/Z_0_95 ≈ 2.2803."""
    from fair_cam.parameters._iris_2025_calibration import build_from_iris_2025
    from fair_cam.quantile_pooling import Z_0_95

    params = build_from_iris_2025(IndustryType.ENTERTAINMENT, "100m_to_1b")
    assert params.primary_loss.distribution_type.value == "lognormal"
    expected_sigma = (math.log(12_000_000) - math.log(282_000)) / Z_0_95
    assert expected_sigma == pytest.approx(2.2803, abs=1e-4)
    assert params.primary_loss.parameters["sigma"] == pytest.approx(expected_sigma, abs=1e-12)
    assert params.primary_loss.parameters["mean"] == pytest.approx(math.log(282_000), abs=1e-12)


def test_management_lognormal_sigma_is_cited_derivation() -> None:
    """MANAGEMENT: p50=$332K, p95=$140M — both IRIS 2025 Figure A3, p. 35.
    σ = ln(140e6/332e3)/Z_0_95 ≈ 3.6747."""
    from fair_cam.parameters._iris_2025_calibration import build_from_iris_2025
    from fair_cam.quantile_pooling import Z_0_95

    params = build_from_iris_2025(IndustryType.MANAGEMENT, "100m_to_1b")
    assert params.primary_loss.distribution_type.value == "lognormal"
    expected_sigma = (math.log(140_000_000) - math.log(332_000)) / Z_0_95
    assert expected_sigma == pytest.approx(3.6747, abs=1e-4)
    assert params.primary_loss.parameters["sigma"] == pytest.approx(expected_sigma, abs=1e-12)
    assert params.primary_loss.parameters["mean"] == pytest.approx(math.log(332_000), abs=1e-12)


def test_other_lognormal_sigma_is_cited_derivation() -> None:
    """OTHER (Other services): p50=$348K, p95=$41M — both IRIS 2025 Figure A3, p. 35.
    σ = ln(41e6/348e3)/Z_0_95 ≈ 2.8994."""
    from fair_cam.parameters._iris_2025_calibration import build_from_iris_2025
    from fair_cam.quantile_pooling import Z_0_95

    params = build_from_iris_2025(IndustryType.OTHER, "100m_to_1b")
    assert params.primary_loss.distribution_type.value == "lognormal"
    expected_sigma = (math.log(41_000_000) - math.log(348_000)) / Z_0_95
    assert expected_sigma == pytest.approx(2.8994, abs=1e-4)
    assert params.primary_loss.parameters["sigma"] == pytest.approx(expected_sigma, abs=1e-12)
    assert params.primary_loss.parameters["mean"] == pytest.approx(math.log(348_000), abs=1e-12)


def test_real_estate_lognormal_sigma_is_cited_derivation() -> None:
    """REAL_ESTATE: p50=$236K, p95=$2M — IRIS 2025 Figure A3, p. 35.
    σ = ln(2e6/236e3)/Z_0_95 ≈ 1.2992.
    Note: lower-confidence — sector sits below-median in IRIS Figure A1 event
    frequency (0.93x); σ=1.299 clears the near-point-mass floor."""
    from fair_cam.parameters._iris_2025_calibration import (
        PER_INDUSTRY_MAGNITUDE_PRIORS_2025,
        build_from_iris_2025,
    )
    from fair_cam.quantile_pooling import Z_0_95

    params = build_from_iris_2025(IndustryType.REAL_ESTATE, "100m_to_1b")
    assert params.primary_loss.distribution_type.value == "lognormal"
    expected_sigma = (math.log(2_000_000) - math.log(236_000)) / Z_0_95
    assert expected_sigma == pytest.approx(1.2992, abs=1e-4)
    assert params.primary_loss.parameters["sigma"] == pytest.approx(expected_sigma, abs=1e-12)
    assert params.primary_loss.parameters["mean"] == pytest.approx(math.log(236_000), abs=1e-12)
    # The notes must include the lower-confidence caveat
    notes = PER_INDUSTRY_MAGNITUDE_PRIORS_2025[IndustryType.REAL_ESTATE].notes
    assert "lower-confidence" in notes.lower(), (
        f"REAL_ESTATE notes must include 'lower-confidence' caveat; got: {notes!r}"
    )


def test_trade_lognormal_sigma_is_cited_derivation() -> None:
    """TRADE: p50=$1M, p95=$23M — both IRIS 2025 Figure A3, p. 35.
    σ = ln(23e6/1e6)/Z_0_95 ≈ 1.9062."""
    from fair_cam.parameters._iris_2025_calibration import build_from_iris_2025
    from fair_cam.quantile_pooling import Z_0_95

    params = build_from_iris_2025(IndustryType.TRADE, "100m_to_1b")
    assert params.primary_loss.distribution_type.value == "lognormal"
    expected_sigma = (math.log(23_000_000) - math.log(1_000_000)) / Z_0_95
    assert expected_sigma == pytest.approx(1.9062, abs=1e-4)
    assert params.primary_loss.parameters["sigma"] == pytest.approx(expected_sigma, abs=1e-12)
    assert params.primary_loss.parameters["mean"] == pytest.approx(math.log(1_000_000), abs=1e-12)


def test_manufacturing_sigma_changed_from_old_values() -> None:
    """Regression: manufacturing must NOT use the old NetDiligence-mixed values
    (p50=$2.8M, p95=$23M, σ≈1.2803). After re-anchor to Figure A3 pure-paginated
    values (p50=$1M, p95=$42M), σ≈2.2723 and mean=ln(1e6)."""
    from fair_cam.parameters._iris_2025_calibration import (
        PER_INDUSTRY_MAGNITUDE_PRIORS_2025,
        build_from_iris_2025,
    )

    prior = PER_INDUSTRY_MAGNITUDE_PRIORS_2025[IndustryType.MANUFACTURING]
    assert prior.p50 == 1_000_000, (
        f"Manufacturing p50 must be $1M (Figure A3 sector median); got {prior.p50}"
    )
    assert prior.p95 == 42_000_000, (
        f"Manufacturing p95 must be $42M (Figure A3 sector 95th); got {prior.p95}"
    )
    params = build_from_iris_2025(IndustryType.MANUFACTURING, "100m_to_1b")
    old_sigma = (math.log(23_000_000) - math.log(2_800_000)) / 1.6448536269514722
    assert params.primary_loss.parameters["sigma"] != pytest.approx(old_sigma, abs=1e-4), (
        "Manufacturing sigma must not equal the old mixed-source value"
    )
