"""Verified reference data from Cyentia IRIS 2025.

Sources:
- Cyentia Institute, "Information Risk Insights Study 2025: It's About Time"
  (June 2025). https://www.cyentia.com/wp-content/uploads/2025/06/IRIS-2025.pdf
- Cyentia Member Vault — IRIS 2025 Bonus Figures (additional sector frequency
  time series, sector loss quantile trends, multi-incident probability by
  revenue tier table).
- "IRIS incident frequency modeling improvements" — Cyentia Institute article
  (member-vault HTML).
- "From Headlines to Histograms" — Cyentia Institute article (Nov 2025).

Methodology summary (Section A1 of the public report + member-vault articles):
- Underlying corpus: Zywave (formerly Advisen) Cyber Loss Data, 150,000+
  public-record incidents spanning 2008-2024.
- Cyentia enrichment: classification models, NLP, ATT&CK mapping, manual tagging.
- Frequency model (IRIS 2025 upgrade): hierarchical random-effects negative
  binomial that pools data across the size x industry x event-type grid.
- Augmentation: Feedly real-time discovery for 2024-Q4 (Figure 18).
- All financial values inflation-adjusted to 2024 dollars.

Numbers in this module are the ones DIRECTLY extracted from labeled tables or
text-quoted figures. Constants estimated from a chart (rather than a labeled
table or text quote) are noted in the inline comment with the source figure.

This module is intentionally a flat collection of module-level constants. It
serves as a single auditable record of what IRIS 2025 actually says, decoupled
from any FAIR-translation logic. Downstream calibration code (e.g.
``fair_cam.parameters.industry_calibration.create_industry_calibrated_parameters``) is
expected to import these constants and document its translation assumptions
at the call site rather than mutating the values here.
"""

from __future__ import annotations

from typing import Final

# === Q1: Incident volume growth (Figure 1) ===
QUARTERLY_INCIDENT_COUNT_2008: Final[int] = 450  # Figure 1, ~450 quarterly publicly-reported
QUARTERLY_INCIDENT_COUNT_2024: Final[int] = 3000  # Figure 1, ~3,000 quarterly (Feedly-augmented Q4)
INCIDENT_GROWTH_15Y: Final[float] = 6.5  # 650% over 15 years (text Q1)

# === Q3: Annualized incident probability — typical org (Figure 6) ===
ANNUAL_INCIDENT_PROBABILITY_TYPICAL_2008: Final[float] = 0.025
ANNUAL_INCIDENT_PROBABILITY_TYPICAL_MID_2010S: Final[float] = 0.061
ANNUAL_INCIDENT_PROBABILITY_TYPICAL_2024: Final[float] = 0.093

# === Q3: Per-industry annualized incident probability ============================
# Public-report figures (Figure 8, IRIS 2025) — 8 sectors.
# Member-Vault BONUS figures (additional_sector_freq_time1/2.pdf) — 12 more.
ANNUAL_INCIDENT_PROBABILITY_BY_INDUSTRY_2024: Final[dict[str, float]] = {
    # Public-report figures (Figure 8, IRIS 2025) — 8 sectors
    "manufacturing": 0.112,  # Figure 8
    "public": 0.109,  # Figure 8
    "healthcare": 0.091,  # Figure 8
    "information": 0.084,  # Figure 8
    "retail": 0.073,  # Figure 8
    "financial": 0.068,  # Figure 8
    "utilities": 0.045,  # Figure 8
    "entertainment": 0.041,  # Figure 8
    # Member-Vault BONUS figures (additional_sector_freq_time1/2.pdf) — 12 more
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
    "agriculture": 0.001,  # BONUS freq_time1 (reported as "<0.1%"; encoded as 0.001 placeholder)
}

# === Q2: Relative incident frequency by revenue tier (Figure 4, 2024 latest) ===
# Multiplier vs population baseline (incidents per firm in tier / median across tiers).
RELATIVE_INCIDENT_FREQ_BY_REVENUE_TIER_2024: Final[dict[str, float]] = {
    "less_than_10m": 0.53,  # Figure 4
    "10m_to_100m": 2.1,  # Figure 4
    "100m_to_1b": 7.2,  # Figure 4
    "1b_to_10b": 32.0,  # Figure 4
    "10b_to_100b": 77.0,  # Figure 4
    "more_than_100b": 620.0,  # Figure 4
}

# === Q3: Annualized incident probability by revenue tier (Member Vault table) ===
# Replaced with verified Member Vault table values (was chart-estimated in commit 1).
# Source: IRIS 2025 Member Vault BONUS Figure "one_plus_breach_freq_by_revenue_tbl.pdf"
# titled "Likelihood of at least N incidents in the next year". This dict pins the
# at-least-1 column; see MULTI_INCIDENT_PROBABILITY_BY_REVENUE_TIER_2024 for the
# at-least-2 / at-least-3 columns.
ANNUAL_INCIDENT_PROBABILITY_BY_REVENUE_TIER_2024: Final[dict[str, float]] = {
    "less_than_10m": 0.0748,  # 7.48%
    "10m_to_100m": 0.0838,  # 8.38%
    "100m_to_1b": 0.0999,  # 9.99%
    "1b_to_10b": 0.1513,  # 15.13%
    "10b_to_100b": 0.3126,  # 31.26%
    "more_than_100b": 0.4343,  # 43.43%
}

# === Multi-incident probability by revenue tier (Member Vault BONUS table) ======
# Likelihood of at least N incidents in the next year per revenue tier.
# Source: IRIS 2025 Member Vault BONUS Figure "one_plus_breach_freq_by_revenue_tbl.pdf".
MULTI_INCIDENT_PROBABILITY_BY_REVENUE_TIER_2024: Final[dict[str, dict[str, float]]] = {
    "less_than_10m": {"at_least_1": 0.0748, "at_least_2": 0.0083, "at_least_3": 0.0010},
    "10m_to_100m": {"at_least_1": 0.0838, "at_least_2": 0.0125, "at_least_3": 0.0021},
    "100m_to_1b": {"at_least_1": 0.0999, "at_least_2": 0.0194, "at_least_3": 0.0043},
    "1b_to_10b": {"at_least_1": 0.1513, "at_least_2": 0.0454, "at_least_3": 0.0156},
    "10b_to_100b": {"at_least_1": 0.3126, "at_least_2": 0.1244, "at_least_3": 0.0529},
    "more_than_100b": {"at_least_1": 0.4343, "at_least_2": 0.2045, "at_least_3": 0.0989},
}

# === Q4: Overall loss magnitude distribution (Figure 9, 2015-2024) ===
# All values 2024 dollars (inflation-adjusted).
OVERALL_LOSS_GEOMETRIC_MEAN: Final[int] = 464_000  # Figure 9
OVERALL_LOSS_MEDIAN: Final[int] = 603_000  # Figure 9
OVERALL_LOSS_MEAN: Final[int] = 14_000_000  # Figure 9
OVERALL_LOSS_P95: Final[int] = 32_000_000  # Figure 9

# === Q4: Loss magnitude trend over 15 years (Figure 10) ===
LOSS_MEDIAN_2008: Final[int] = 189_900  # Figure 10
LOSS_MEDIAN_2024: Final[int] = 2_900_000  # Figure 10 (15.20x growth)
LOSS_P90_2008: Final[int] = 6_000_000  # Figure 10
LOSS_P90_2024: Final[int] = 28_500_000  # Figure 10 (4.75x growth)

# === Q4: Loss by revenue tier (Table 1, latest data, 2024 dollars) ===
LOSS_BY_REVENUE_TIER_2024: Final[dict[str, dict[str, int]]] = {
    "more_than_10b": {"p50": 2_200_000, "p95": 266_200_000},  # Table 1
    "1b_to_10b": {"p50": 1_800_000, "p95": 61_800_000},  # Table 1
    "100m_to_1b": {"p50": 466_700, "p95": 12_300_000},  # Table 1
    "less_than_100m": {"p50": 357_000, "p95": 9_100_000},  # Table 1
}

# === Q4: Loss as percent of revenue (Figure 11) ===
LOSS_PCT_REVENUE_P50_2008: Final[float] = 0.0008  # 0.08% (Figure 11)
LOSS_PCT_REVENUE_P50_2024: Final[float] = 0.0065  # 0.65% (Figure 11), 8x growth
LOSS_PCT_REVENUE_P95_2008: Final[float] = 1.0227  # 102.27% (Figure 11)
LOSS_PCT_REVENUE_P95_2024: Final[float] = 1.1723  # 117.23% (Figure 11)

# === Q4: Per-sector loss trends ==================================================
# Public-report sectors (Figure 12) — 3 sectors.
# Member-Vault BONUS sectors (additional_sector_loss_quantile_trend1/2.pdf) — 8 more.
# Total: 11 sectors. There is no overlap between the public Figure 12 panels and
# the bonus PDFs, so the merge is clean.
LOSS_BY_SECTOR_TREND: Final[dict[str, dict[str, int]]] = {
    # Public-report sectors (Figure 12) — 3 sectors
    "education": {  # Figure 12, "Education" panel
        "p50_2008": 229_400,
        "p50_2024": 243_400,  # 1.06x
        "p90_2008": 3_500_000,
        "p90_2024": 5_400_000,  # 1.53x
    },
    "professional_services": {  # Figure 12, "Professional" panel
        "p50_2008": 58_500,
        "p50_2024": 1_500_000,  # 25.71x growth
        "p90_2008": 5_700_000,
        "p90_2024": 21_100_000,  # 3.72x
    },
    "retail": {  # Figure 12, "Retail" panel
        "p50_2008": 6_500_000,
        "p50_2024": 142_300,  # 0.02x DECREASE
        "p90_2008": 147_100_000,
        "p90_2024": 6_000_000,  # 0.04x DECREASE
    },
    # Member-Vault BONUS sectors (additional_sector_loss_quantile_trend1/2.pdf) — 8 sectors
    "administrative": {  # BONUS trend1
        "p50_2008": 185_400,
        "p50_2024": 525_600,  # 2.84x
        "p90_2008": 1_300_000,
        "p90_2024": 19_500_000,  # 15.13x
    },
    "financial": {  # BONUS trend1
        "p50_2008": 449_600,
        "p50_2024": 1_200_000,  # 2.73x
        "p90_2008": 49_800_000,
        "p90_2024": 110_100_000,  # 2.21x
    },
    "healthcare": {  # BONUS trend1
        "p50_2008": 159_100,
        "p50_2024": 748_400,  # 4.70x
        "p90_2008": 4_300_000,
        "p90_2024": 18_200_000,  # 4.20x
    },
    "information": {  # BONUS trend1
        "p50_2008": 3_800_000,
        "p50_2024": 297_200,  # 0.08x DECLINE
        "p90_2008": 136_300_000,
        "p90_2024": 30_600_000,  # 0.22x DECLINE
    },
    "management": {  # BONUS trend2
        "p50_2008": 2_400_000,
        "p50_2024": 43_800,  # 0.02x DECLINE
        "p90_2008": 167_400_000,
        "p90_2024": 1_100_000,  # 0.01x DECLINE
    },
    "manufacturing": {  # BONUS trend2
        "p50_2008": 382_200,
        "p50_2024": 1_800_000,  # 4.66x
        "p90_2008": 18_300_000,
        "p90_2024": 29_400_000,  # 1.61x
    },
    "public": {  # BONUS trend2
        "p50_2008": 176_800,
        "p50_2024": 285_600,  # 1.62x
        "p90_2008": 1_700_000,
        "p90_2024": 15_300_000,  # 9.02x
    },
    "other": {  # BONUS trend2
        "p50_2008": 293_100,
        "p50_2024": 1_300_000,  # 4.35x
        "p90_2008": 25_700_000,
        "p90_2024": 37_900_000,  # 1.48x
    },
}

# === Q5: Loss by incident pattern (Figure 15, 2008 → 2024) ===
LOSS_BY_EVENT_TYPE_TREND: Final[dict[str, dict[str, int]]] = {
    "accidental_disclosure_insider_misuse": {  # Figure 15, combined panel
        "p50_2008": 150_200,
        "p50_2024": 6_900,  # 0.05x
        "p90_2008": 2_300_000,
        "p90_2024": 1_600_000,  # 0.68x
    },
    "system_intrusion": {  # Figure 15
        "p50_2008": 645_000,
        "p50_2024": 1_300_000,  # 1.97x
        "p90_2008": 221_300_000,
        "p90_2024": 7_400_000,  # 0.03x DECREASE at top end
    },
    "ransomware": {  # Figure 15
        "p50_2008": 155_500,
        "p50_2024": 3_200_000,  # 20.49x
        "p90_2008": 5_000_000,
        "p90_2024": 27_600_000,  # 5.52x
    },
}

# === Q6: ATT&CK Initial Access technique prevalence (Figure 16, 2024 latest) ===
INITIAL_ACCESS_TECHNIQUE_PREVALENCE_2024: Final[dict[str, dict[str, float | str]]] = {
    "T1078": {"name": "Valid Accounts", "share": 0.46},  # Figure 16
    "T1190": {"name": "Exploit Public-Facing Application", "share": 0.34},  # Figure 16
    "T1566": {"name": "Phishing", "share": 0.28},  # Figure 16
    "T1199": {"name": "Trusted Relationship", "share": 0.12},  # Figure 16
    "T1200": {"name": "Hardware Additions", "share": 0.10},  # Figure 16
    "T1133": {"name": "External Remote Services", "share": 0.10},  # Figure 16
    "T1091": {"name": "Replication Through Removable Media", "share": 0.09},  # Figure 16
    "T1189": {"name": "Drive-by Compromise", "share": 0.01},  # Figure 16
    "T1195": {"name": "Supply Chain Compromise", "share": 0.005},  # Figure 16 (<1%)
}

# === Q7: Top threat actors observed in 2024 by sector (Table 2, via Feedly) ===
TOP_THREAT_ACTORS_BY_SECTOR_2024: Final[dict[str, list[str]]] = {
    "finance": ["Lazarus Group", "Shiny Hunters", "RansomHub"],
    "healthcare": ["Alpha Spider", "RansomHub", "Vanilla Tempest"],
    "public": ["GhostEmperor", "Volt Typhoon", "Flax Typhoon"],
}

# === Methodology metadata (Section A1) ===
DATA_SOURCE_NAME: Final[str] = "Cyentia IRIS 2025"
DATA_SOURCE_FULL: Final[str] = (
    "Information Risk Insights Study 2025: It's About Time, Cyentia Institute"
)
PUBLICATION_DATE: Final[str] = "2025-06"
DATA_WINDOW_START_YEAR: Final[int] = 2008
DATA_WINDOW_END_YEAR: Final[int] = 2024
INCIDENT_CORPUS_SIZE: Final[int] = 150_000  # Zywave/Advisen base dataset
INFLATION_BASE_YEAR: Final[int] = 2024

# === Cyentia frequency-model methodology =========================================
# Source: "IRIS incident frequency modeling improvements" — Cyentia Institute
# article (member-vault HTML, retrieved Apr 2026).
FREQUENCY_MODEL_DISTRIBUTION: Final[str] = "Negative Binomial"
FREQUENCY_MODEL_UPGRADE: Final[str] = "Hierarchical random effects (IRIS 2025)"

# Example empirical fit (single distribution across all firms in dataset).
# Documented as illustrative — NOT a universal calibration parameter set.
FREQUENCY_MODEL_EXAMPLE_PARAMS: Final[dict[str, float]] = {
    "size": 0.262,  # negative-binomial dispersion parameter
    "mu": 0.088,  # negative-binomial mean
}

# Empirical zero-inflation example from the article (single all-firms distribution).
# Illustrative, not a universal IRIS 2025 calibration value.
FREQUENCY_MODEL_EXAMPLE_ZERO_FRACTION: Final[float] = 0.926  # 92.6% of firm-years had zero events
FREQUENCY_MODEL_EXAMPLE_ONE_FRACTION: Final[float] = 0.066  # 6.6% had exactly one

FREQUENCY_MODEL_NOTES: Final[str] = (
    "IRIS 2025 introduces a hierarchical random-effects model that pools "
    "data across the size x industry x event-type grid to produce stable "
    "estimates even for sparse cells (e.g., >$100B Healthcare firms). "
    "Earlier IRIS editions fit per-slice negative binomial / Poisson / zero-"
    "inflated distributions independently per sector x size combination. "
    "The new model still emits negative-binomial parameters compatible "
    "with downstream FAIR-style modeling. Loss-magnitude distributions are "
    "modeled via the same hierarchical structure with a continuous response."
)

# === Risk Retina monthly methodology illustration ===============================
# Source: "From Headlines to Histograms" — Cyentia Institute article (Nov 2025).
# Illustrates the "Base rate vs Recent events vs Recent buzz" comparison Cyentia
# uses for monthly Risk Retina deliverables. NOT calibration data — just an
# anchor for the methodology. October 2025 (latest available): Manufacturing
# had 16% of incidents but 56% of media coverage — a 3.5x media-buzz/base-rate
# ratio.
MEDIA_BUZZ_VS_BASE_RATE_EXAMPLE_2025_10: Final[dict[str, dict[str, float | None]]] = {
    "manufacturing": {
        "incidents_share_in_month": 0.16,  # 16% of October 2025 incidents
        "media_coverage_share": 0.56,  # 56% of October 2025 cyber-incident headlines
        "five_year_base_rate": None,  # Article notes the base rate but doesn't quote a number
    },
}
