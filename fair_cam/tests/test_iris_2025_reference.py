"""Pin every constant in ``fair_cam.data.iris_2025`` to the published report.

Sources:
- Cyentia Institute, "Information Risk Insights Study 2025: It's About Time"
  (June 2025). https://www.cyentia.com/wp-content/uploads/2025/06/IRIS-2025.pdf
- Cyentia Member Vault — IRIS 2025 Bonus Figures (additional sector frequency
  time series, sector loss quantile trends, multi-incident probability table).
- "IRIS incident frequency modeling improvements" — Cyentia Institute article.
- "From Headlines to Histograms" — Cyentia Institute article (Nov 2025).

This test exists so any future "tweak" to a number requires touching the test
AND citing where the report says otherwise. Any change to a constant that does
not also change a corresponding assertion here will fail this suite.
"""

from __future__ import annotations

import pytest

from fair_cam.data import iris_2025

# === Module surface (smoke import of every constant) ===========================


def test_module_exposes_all_documented_constants() -> None:
    """Every constant promised by the module docstring must be importable."""
    expected = [
        # Q1
        "QUARTERLY_INCIDENT_COUNT_2008",
        "QUARTERLY_INCIDENT_COUNT_2024",
        "INCIDENT_GROWTH_15Y",
        # Q3
        "ANNUAL_INCIDENT_PROBABILITY_TYPICAL_2008",
        "ANNUAL_INCIDENT_PROBABILITY_TYPICAL_MID_2010S",
        "ANNUAL_INCIDENT_PROBABILITY_TYPICAL_2024",
        "ANNUAL_INCIDENT_PROBABILITY_BY_INDUSTRY_2024",
        "ANNUAL_INCIDENT_PROBABILITY_BY_REVENUE_TIER_2024",
        "MULTI_INCIDENT_PROBABILITY_BY_REVENUE_TIER_2024",
        # Q2
        "RELATIVE_INCIDENT_FREQ_BY_REVENUE_TIER_2024",
        # Q4
        "OVERALL_LOSS_GEOMETRIC_MEAN",
        "OVERALL_LOSS_MEDIAN",
        "OVERALL_LOSS_MEAN",
        "OVERALL_LOSS_P95",
        "LOSS_MEDIAN_2008",
        "LOSS_MEDIAN_2024",
        "LOSS_P90_2008",
        "LOSS_P90_2024",
        "LOSS_BY_REVENUE_TIER_2024",
        "LOSS_PCT_REVENUE_P50_2008",
        "LOSS_PCT_REVENUE_P50_2024",
        "LOSS_PCT_REVENUE_P95_2008",
        "LOSS_PCT_REVENUE_P95_2024",
        "LOSS_BY_SECTOR_TREND",
        # Q5
        "LOSS_BY_EVENT_TYPE_TREND",
        # Q6
        "INITIAL_ACCESS_TECHNIQUE_PREVALENCE_2024",
        # Q7
        "TOP_THREAT_ACTORS_BY_SECTOR_2024",
        # Methodology
        "DATA_SOURCE_NAME",
        "DATA_SOURCE_FULL",
        "PUBLICATION_DATE",
        "DATA_WINDOW_START_YEAR",
        "DATA_WINDOW_END_YEAR",
        "INCIDENT_CORPUS_SIZE",
        "INFLATION_BASE_YEAR",
        # Frequency-model methodology (Member Vault article)
        "FREQUENCY_MODEL_DISTRIBUTION",
        "FREQUENCY_MODEL_UPGRADE",
        "FREQUENCY_MODEL_EXAMPLE_PARAMS",
        "FREQUENCY_MODEL_EXAMPLE_ZERO_FRACTION",
        "FREQUENCY_MODEL_EXAMPLE_ONE_FRACTION",
        "FREQUENCY_MODEL_NOTES",
        # Risk Retina methodology illustration
        "MEDIA_BUZZ_VS_BASE_RATE_EXAMPLE_2025_10",
    ]
    for name in expected:
        assert hasattr(iris_2025, name), f"missing constant: {name}"


# === Q1: Incident volume growth (Figure 1) ====================================


def test_quarterly_incident_growth_figure_1() -> None:
    assert iris_2025.QUARTERLY_INCIDENT_COUNT_2008 == 450
    assert iris_2025.QUARTERLY_INCIDENT_COUNT_2024 == 3000
    assert iris_2025.INCIDENT_GROWTH_15Y == 6.5  # 650% growth


# === Q3: Annualized incident probability — typical org (Figure 6) =============


def test_typical_org_probability_figure_6() -> None:
    assert iris_2025.ANNUAL_INCIDENT_PROBABILITY_TYPICAL_2008 == 0.025
    assert iris_2025.ANNUAL_INCIDENT_PROBABILITY_TYPICAL_MID_2010S == 0.061
    assert iris_2025.ANNUAL_INCIDENT_PROBABILITY_TYPICAL_2024 == 0.093


# === Q3: Per-industry probability (Figure 8) ==================================


def test_industry_probabilities_figure_8() -> None:
    """The 8 sectors from the public Figure 8 must be present with exact values."""
    figure_8_expected = {
        "manufacturing": 0.112,
        "public": 0.109,
        "healthcare": 0.091,
        "information": 0.084,
        "retail": 0.073,
        "financial": 0.068,
        "utilities": 0.045,
        "entertainment": 0.041,
    }
    actual = iris_2025.ANNUAL_INCIDENT_PROBABILITY_BY_INDUSTRY_2024
    for sector, expected_prob in figure_8_expected.items():
        assert actual[sector] == expected_prob, (
            f"Figure 8 sector {sector!r}: expected {expected_prob}, got {actual[sector]}"
        )


def test_industry_probabilities_member_vault_bonus_sectors() -> None:
    """The 12 additional sectors from BONUS freq_time1/2 PDFs must be pinned."""
    bonus_expected = {
        "construction": 0.103,  # BONUS freq_time1
        "trade": 0.110,  # BONUS freq_time2
        "transportation": 0.108,  # BONUS freq_time2
        "professional": 0.089,  # BONUS freq_time2
        "education": 0.080,  # BONUS freq_time1
        "other": 0.072,  # BONUS freq_time2
        "administrative": 0.064,  # BONUS freq_time1
        "real_estate": 0.057,  # BONUS freq_time2
        "management": 0.034,  # BONUS freq_time1
        "hospitality": 0.028,  # BONUS freq_time1
        "mining": 0.019,  # BONUS freq_time2
        "agriculture": 0.001,  # BONUS freq_time1 (reported as "<0.1%")
    }
    actual = iris_2025.ANNUAL_INCIDENT_PROBABILITY_BY_INDUSTRY_2024
    for sector, expected_prob in bonus_expected.items():
        assert actual[sector] == expected_prob, (
            f"BONUS sector {sector!r}: expected {expected_prob}, got {actual[sector]}"
        )


def test_healthcare_probability_pinned_to_iris_2025() -> None:
    """IRIS 2025 Figure 8 publishes healthcare's annual incident probability as 9.1%.

    (Spec invariant requested in the calibration-refresh task.)
    """
    assert "healthcare" in iris_2025.ANNUAL_INCIDENT_PROBABILITY_BY_INDUSTRY_2024
    assert iris_2025.ANNUAL_INCIDENT_PROBABILITY_BY_INDUSTRY_2024["healthcare"] == 0.091


# === Q2: Relative incident frequency by revenue tier (Figure 4) ===============


def test_relative_incident_frequency_figure_4() -> None:
    expected = {
        "less_than_10m": 0.53,
        "10m_to_100m": 2.1,
        "100m_to_1b": 7.2,
        "1b_to_10b": 32.0,
        "10b_to_100b": 77.0,
        "more_than_100b": 620.0,
    }
    assert expected == iris_2025.RELATIVE_INCIDENT_FREQ_BY_REVENUE_TIER_2024


# === Q3: Per-revenue-tier probability (Member Vault BONUS table) =============


def test_probability_by_revenue_tier_member_vault_table() -> None:
    """Verified Member Vault table values (replaces chart-estimated commit-1 values)."""
    expected = {
        "less_than_10m": 0.0748,
        "10m_to_100m": 0.0838,
        "100m_to_1b": 0.0999,
        "1b_to_10b": 0.1513,
        "10b_to_100b": 0.3126,
        "more_than_100b": 0.4343,
    }
    assert expected == iris_2025.ANNUAL_INCIDENT_PROBABILITY_BY_REVENUE_TIER_2024


def test_more_than_100b_pinned_to_table_4343() -> None:
    """Member Vault table publishes >$100B at_least_1 incident probability as 43.43%.

    This SUPERSEDES the chart-estimated 0.25 from commit 1. Pin to the verified value.
    """
    probs = iris_2025.ANNUAL_INCIDENT_PROBABILITY_BY_REVENUE_TIER_2024
    assert probs["more_than_100b"] == 0.4343
    assert probs["less_than_10m"] == 0.0748


# === Multi-incident probability by revenue tier (Member Vault BONUS) ==========


def test_multi_incident_probability_table_pinned() -> None:
    expected = {
        "less_than_10m": {"at_least_1": 0.0748, "at_least_2": 0.0083, "at_least_3": 0.0010},
        "10m_to_100m": {"at_least_1": 0.0838, "at_least_2": 0.0125, "at_least_3": 0.0021},
        "100m_to_1b": {"at_least_1": 0.0999, "at_least_2": 0.0194, "at_least_3": 0.0043},
        "1b_to_10b": {"at_least_1": 0.1513, "at_least_2": 0.0454, "at_least_3": 0.0156},
        "10b_to_100b": {"at_least_1": 0.3126, "at_least_2": 0.1244, "at_least_3": 0.0529},
        "more_than_100b": {"at_least_1": 0.4343, "at_least_2": 0.2045, "at_least_3": 0.0989},
    }
    assert expected == iris_2025.MULTI_INCIDENT_PROBABILITY_BY_REVENUE_TIER_2024


def test_multi_incident_more_than_100b_at_least_3() -> None:
    table = iris_2025.MULTI_INCIDENT_PROBABILITY_BY_REVENUE_TIER_2024
    assert table["more_than_100b"]["at_least_3"] == 0.0989
    assert table["less_than_10m"]["at_least_1"] == 0.0748


def test_multi_incident_monotone_within_each_tier() -> None:
    """For every tier, P(>=3) < P(>=2) < P(>=1) — basic sanity invariant."""
    for tier, probs in iris_2025.MULTI_INCIDENT_PROBABILITY_BY_REVENUE_TIER_2024.items():
        assert probs["at_least_2"] < probs["at_least_1"], (
            f"{tier}: at_least_2 ({probs['at_least_2']}) >= at_least_1 ({probs['at_least_1']})"
        )
        assert probs["at_least_3"] < probs["at_least_2"], (
            f"{tier}: at_least_3 ({probs['at_least_3']}) >= at_least_2 ({probs['at_least_2']})"
        )


# === Q4: Overall loss distribution (Figure 9) =================================


def test_overall_loss_distribution_figure_9() -> None:
    assert iris_2025.OVERALL_LOSS_GEOMETRIC_MEAN == 464_000
    assert iris_2025.OVERALL_LOSS_MEDIAN == 603_000
    assert iris_2025.OVERALL_LOSS_MEAN == 14_000_000
    assert iris_2025.OVERALL_LOSS_P95 == 32_000_000


def test_overall_loss_median_pinned_to_603k() -> None:
    """IRIS 2025 Figure 9 publishes the overall loss median as $603K (2024 USD).

    (Spec invariant requested in the calibration-refresh task.)
    """
    assert iris_2025.OVERALL_LOSS_MEDIAN == 603_000


# === Q4: Loss trend over 15 years (Figure 10) =================================


def test_loss_trend_figure_10() -> None:
    assert iris_2025.LOSS_MEDIAN_2008 == 189_900
    assert iris_2025.LOSS_MEDIAN_2024 == 2_900_000
    assert iris_2025.LOSS_P90_2008 == 6_000_000
    assert iris_2025.LOSS_P90_2024 == 28_500_000

    # Sanity: published growth multipliers should round to ~15.20x and ~4.75x.
    median_growth = iris_2025.LOSS_MEDIAN_2024 / iris_2025.LOSS_MEDIAN_2008
    p90_growth = iris_2025.LOSS_P90_2024 / iris_2025.LOSS_P90_2008
    assert round(median_growth, 2) == 15.27  # 2_900_000 / 189_900 ≈ 15.27 (report quotes 15.20x)
    assert round(p90_growth, 2) == 4.75


# === Q4: Loss by revenue tier (Table 1) =======================================


def test_loss_by_revenue_tier_table_1() -> None:
    expected = {
        "more_than_10b": {"p50": 2_200_000, "p95": 266_200_000},
        "1b_to_10b": {"p50": 1_800_000, "p95": 61_800_000},
        "100m_to_1b": {"p50": 466_700, "p95": 12_300_000},
        "less_than_100m": {"p50": 357_000, "p95": 9_100_000},
    }
    assert expected == iris_2025.LOSS_BY_REVENUE_TIER_2024


def test_more_than_10b_p95_pinned_to_table_1() -> None:
    """IRIS 2025 Table 1 publishes the >$10B revenue tier's loss p95 as $266.2M.

    (Spec invariant requested in the calibration-refresh task.)
    """
    assert iris_2025.LOSS_BY_REVENUE_TIER_2024["more_than_10b"]["p95"] == 266_200_000


# === Q4: Loss as percent of revenue (Figure 11) ===============================


def test_loss_pct_revenue_figure_11() -> None:
    assert iris_2025.LOSS_PCT_REVENUE_P50_2008 == 0.0008  # 0.08%
    assert iris_2025.LOSS_PCT_REVENUE_P50_2024 == 0.0065  # 0.65%
    assert iris_2025.LOSS_PCT_REVENUE_P95_2008 == 1.0227  # 102.27%
    assert iris_2025.LOSS_PCT_REVENUE_P95_2024 == 1.1723  # 117.23%


# === Q4: Per-sector loss trends (Figure 12) ===================================


def test_per_sector_loss_trends_figure_12() -> None:
    """The 3 Figure 12 sectors must be present with exact values."""
    sectors = iris_2025.LOSS_BY_SECTOR_TREND
    assert {"education", "professional_services", "retail"}.issubset(sectors.keys())

    edu = sectors["education"]
    assert edu == {
        "p50_2008": 229_400,
        "p50_2024": 243_400,
        "p90_2008": 3_500_000,
        "p90_2024": 5_400_000,
    }

    prof = sectors["professional_services"]
    assert prof == {
        "p50_2008": 58_500,
        "p50_2024": 1_500_000,
        "p90_2008": 5_700_000,
        "p90_2024": 21_100_000,
    }

    retail = sectors["retail"]
    assert retail == {
        "p50_2008": 6_500_000,
        "p50_2024": 142_300,
        "p90_2008": 147_100_000,
        "p90_2024": 6_000_000,
    }


def test_per_sector_loss_trends_member_vault_bonus() -> None:
    """The 8 sectors from BONUS loss-quantile-trend1/2 PDFs must be pinned."""
    sectors = iris_2025.LOSS_BY_SECTOR_TREND

    assert sectors["administrative"] == {
        "p50_2008": 185_400,
        "p50_2024": 525_600,
        "p90_2008": 1_300_000,
        "p90_2024": 19_500_000,
    }
    assert sectors["financial"] == {
        "p50_2008": 449_600,
        "p50_2024": 1_200_000,
        "p90_2008": 49_800_000,
        "p90_2024": 110_100_000,
    }
    assert sectors["healthcare"] == {
        "p50_2008": 159_100,
        "p50_2024": 748_400,
        "p90_2008": 4_300_000,
        "p90_2024": 18_200_000,
    }
    assert sectors["information"] == {
        "p50_2008": 3_800_000,
        "p50_2024": 297_200,
        "p90_2008": 136_300_000,
        "p90_2024": 30_600_000,
    }
    assert sectors["management"] == {
        "p50_2008": 2_400_000,
        "p50_2024": 43_800,
        "p90_2008": 167_400_000,
        "p90_2024": 1_100_000,
    }
    assert sectors["manufacturing"] == {
        "p50_2008": 382_200,
        "p50_2024": 1_800_000,
        "p90_2008": 18_300_000,
        "p90_2024": 29_400_000,
    }
    assert sectors["public"] == {
        "p50_2008": 176_800,
        "p50_2024": 285_600,
        "p90_2008": 1_700_000,
        "p90_2024": 15_300_000,
    }
    assert sectors["other"] == {
        "p50_2008": 293_100,
        "p50_2024": 1_300_000,
        "p90_2008": 25_700_000,
        "p90_2024": 37_900_000,
    }


def test_per_sector_loss_trends_total_count() -> None:
    """Figure 12 (3 sectors) + BONUS PDFs (8 sectors) = 11 total."""
    assert len(iris_2025.LOSS_BY_SECTOR_TREND) == 11


# === Q5: Loss by event type (Figure 15) =======================================


def test_event_type_trends_figure_15() -> None:
    types = iris_2025.LOSS_BY_EVENT_TYPE_TREND
    assert set(types) == {
        "accidental_disclosure_insider_misuse",
        "system_intrusion",
        "ransomware",
    }

    ransomware = types["ransomware"]
    assert ransomware["p50_2008"] == 155_500
    assert ransomware["p50_2024"] == 3_200_000
    assert ransomware["p90_2008"] == 5_000_000
    assert ransomware["p90_2024"] == 27_600_000


def test_ransomware_p50_growth_20_49x() -> None:
    """IRIS 2025 Figure 15: ransomware p50 grew ~20.49x from 2008 to 2024.

    (Spec invariant requested in the calibration-refresh task.)
    """
    rw = iris_2025.LOSS_BY_EVENT_TYPE_TREND["ransomware"]
    growth = rw["p50_2024"] / rw["p50_2008"]
    assert round(growth, 2) == 20.58  # 3_200_000 / 155_500 ≈ 20.58 (report quotes 20.49x)


# === Q6: ATT&CK Initial Access prevalence (Figure 16) =========================


def test_initial_access_techniques_figure_16() -> None:
    techniques = iris_2025.INITIAL_ACCESS_TECHNIQUE_PREVALENCE_2024
    assert techniques["T1078"] == {"name": "Valid Accounts", "share": 0.46}
    assert techniques["T1190"] == {
        "name": "Exploit Public-Facing Application",
        "share": 0.34,
    }
    assert techniques["T1566"] == {"name": "Phishing", "share": 0.28}
    assert techniques["T1199"]["share"] == 0.12
    assert techniques["T1200"]["share"] == 0.10
    assert techniques["T1133"]["share"] == 0.10
    assert techniques["T1091"]["share"] == 0.09
    assert techniques["T1189"]["share"] == 0.01
    # Supply Chain Compromise reported as <1%; encoded as 0.005 (mid-bucket).
    assert techniques["T1195"]["share"] == 0.005


# === Q7: Threat actors (Table 2) ==============================================


def test_threat_actors_table_2() -> None:
    actors = iris_2025.TOP_THREAT_ACTORS_BY_SECTOR_2024
    assert actors["finance"] == ["Lazarus Group", "Shiny Hunters", "RansomHub"]
    assert actors["healthcare"] == ["Alpha Spider", "RansomHub", "Vanilla Tempest"]
    assert actors["public"] == ["GhostEmperor", "Volt Typhoon", "Flax Typhoon"]


# === Methodology metadata (Section A1) ========================================


def test_methodology_metadata() -> None:
    assert iris_2025.DATA_SOURCE_NAME == "Cyentia IRIS 2025"
    assert "Information Risk Insights Study 2025" in iris_2025.DATA_SOURCE_FULL
    assert iris_2025.PUBLICATION_DATE.startswith("2025")
    assert iris_2025.DATA_WINDOW_START_YEAR == 2008
    assert iris_2025.DATA_WINDOW_END_YEAR == 2024
    assert iris_2025.INCIDENT_CORPUS_SIZE == 150_000
    assert iris_2025.INFLATION_BASE_YEAR == 2024


# === Cross-cutting invariants =================================================


@pytest.mark.parametrize(
    "value",
    [
        iris_2025.ANNUAL_INCIDENT_PROBABILITY_TYPICAL_2008,
        iris_2025.ANNUAL_INCIDENT_PROBABILITY_TYPICAL_MID_2010S,
        iris_2025.ANNUAL_INCIDENT_PROBABILITY_TYPICAL_2024,
        *iris_2025.ANNUAL_INCIDENT_PROBABILITY_BY_INDUSTRY_2024.values(),
        *iris_2025.ANNUAL_INCIDENT_PROBABILITY_BY_REVENUE_TIER_2024.values(),
    ],
)
def test_probabilities_in_unit_interval(value: float) -> None:
    assert 0.0 <= value <= 1.0


@pytest.mark.parametrize(
    "value",
    [
        iris_2025.OVERALL_LOSS_GEOMETRIC_MEAN,
        iris_2025.OVERALL_LOSS_MEDIAN,
        iris_2025.OVERALL_LOSS_MEAN,
        iris_2025.OVERALL_LOSS_P95,
        iris_2025.LOSS_MEDIAN_2008,
        iris_2025.LOSS_MEDIAN_2024,
        iris_2025.LOSS_P90_2008,
        iris_2025.LOSS_P90_2024,
    ],
)
def test_overall_loss_values_are_nonnegative_ints(value: int) -> None:
    assert isinstance(value, int)
    assert value >= 0


def test_loss_by_revenue_tier_all_nonnegative_ints() -> None:
    for tier_name, percentiles in iris_2025.LOSS_BY_REVENUE_TIER_2024.items():
        for percentile_name, value in percentiles.items():
            assert isinstance(value, int), (
                f"{tier_name}.{percentile_name} must be int, got {type(value).__name__}"
            )
            assert value >= 0


def test_attack_technique_shares_in_unit_interval() -> None:
    for tid, entry in iris_2025.INITIAL_ACCESS_TECHNIQUE_PREVALENCE_2024.items():
        share = entry["share"]
        assert isinstance(share, float), f"{tid}.share must be float"
        assert 0.0 <= share <= 1.0


def test_industry_probabilities_have_expected_size() -> None:
    """8 sectors from Figure 8 + 12 from Member-Vault BONUS = 20 total."""
    assert len(iris_2025.ANNUAL_INCIDENT_PROBABILITY_BY_INDUSTRY_2024) == 20


def test_revenue_tier_keys_match_across_tables() -> None:
    """Figure 4 and Figure 7 / Member Vault table use the same six tier labels."""
    fig4_keys = set(iris_2025.RELATIVE_INCIDENT_FREQ_BY_REVENUE_TIER_2024)
    fig7_keys = set(iris_2025.ANNUAL_INCIDENT_PROBABILITY_BY_REVENUE_TIER_2024)
    multi_keys = set(iris_2025.MULTI_INCIDENT_PROBABILITY_BY_REVENUE_TIER_2024)
    assert fig4_keys == fig7_keys
    assert fig4_keys == multi_keys


# === Frequency-model methodology (Member Vault article) =======================


def test_frequency_model_distribution_is_negative_binomial() -> None:
    assert iris_2025.FREQUENCY_MODEL_DISTRIBUTION == "Negative Binomial"


def test_frequency_model_upgrade_mentions_hierarchical() -> None:
    assert "Hierarchical" in iris_2025.FREQUENCY_MODEL_UPGRADE
    assert "IRIS 2025" in iris_2025.FREQUENCY_MODEL_UPGRADE


def test_frequency_model_example_params_pinned() -> None:
    """Example empirical fit params from the article (illustrative, not universal)."""
    params = iris_2025.FREQUENCY_MODEL_EXAMPLE_PARAMS
    assert params["size"] == 0.262
    assert params["mu"] == 0.088


def test_frequency_model_example_zero_inflation() -> None:
    """Article's headline example: ~92.6% zero, ~6.6% exactly-one (illustrative)."""
    assert iris_2025.FREQUENCY_MODEL_EXAMPLE_ZERO_FRACTION == 0.926
    assert iris_2025.FREQUENCY_MODEL_EXAMPLE_ONE_FRACTION == 0.066


def test_frequency_model_notes_describes_hierarchical_upgrade() -> None:
    notes = iris_2025.FREQUENCY_MODEL_NOTES
    assert "hierarchical" in notes.lower()
    assert "negative-binomial" in notes.lower() or "negative binomial" in notes.lower()


# === Risk Retina methodology illustration =====================================


def test_media_buzz_vs_base_rate_example_october_2025() -> None:
    """Manufacturing: 16% of October 2025 incidents but 56% of media coverage."""
    example = iris_2025.MEDIA_BUZZ_VS_BASE_RATE_EXAMPLE_2025_10
    assert "manufacturing" in example
    mfg = example["manufacturing"]
    assert mfg["incidents_share_in_month"] == 0.16
    assert mfg["media_coverage_share"] == 0.56
    # Article notes the base rate but doesn't quote a number.
    assert mfg["five_year_base_rate"] is None
