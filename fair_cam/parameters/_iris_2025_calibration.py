"""IRIS 2025-specific FAIR-translation helpers.

Per-industry magnitude priors for all 20 IndustryType sectors. Each entry
MUST have non-empty ``notes`` documenting the prior's anchor source and
reasoning.

This module is the spec for v3's per-industry magnitude priors. To add
a new sector entry: see the framework spec at
``docs/superpowers/specs/2026-04-25-calibration-data-framework-design.md``
section 6.3.

Epic C-iii-a re-anchor (2026-06-11)
-------------------------------------
All 18 *mappable* IndustryType members are now anchored to the IRIS 2025
Appendix sector loss table at Figure A3, p. 35 ("Losses observed per
sector"), replacing the prior anecdotal / non-paginated anchors.

IndustryType → Figure A3 row mapping
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
UTILITIES       → Utilities      p50=$146K  p95=$3M
CONSTRUCTION    → Construction   p50=$189K  p95=$5M
MANUFACTURING   → Manufacturing  p50=$1M    p95=$42M
TRADE           → Trade          p50=$1M    p95=$23M
RETAIL          → Retail         p50=$746K  p95=$45M
TRANSPORTATION  → Transportation p50=$490K  p95=$23M
INFORMATION     → Information    p50=$718K  p95=$217M
FINANCIAL       → Financial      p50=$1M    p95=$194M
REAL_ESTATE     → Real Estate    p50=$236K  p95=$2M
PROFESSIONAL    → Professional   p50=$736K  p95=$17M
MANAGEMENT      → Management     p50=$332K  p95=$140M
ADMINISTRATIVE  → Administrative p50=$529K  p95=$31M
EDUCATION       → Education      p50=$249K  p95=$6M
HEALTHCARE      → Healthcare     p50=$557K  p95=$14M
ENTERTAINMENT   → Entertainment  p50=$282K  p95=$12M
HOSPITALITY     → Hospitality    p50=$600K  p95=$62M
OTHER           → Other services p50=$348K  p95=$41M
PUBLIC          → Public         p50=$214K  p95=$18M

UNMAPPABLE industries (no verified row, or excluded)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
AGRICULTURE — excluded. Agriculture row: p50=$2M, p95=$3M → σ≈0.247,
  near-point-mass. IRIS Figure A1 does not show an event-frequency baseline
  for Agriculture in the public report; sample too thin to anchor a lognormal.
  Anecdotal anchor retained.

MINING — excluded. Mining row: p50=$1M, p95=$2M → σ≈0.421, near-point-mass.
  IRIS Figure A1 shows relative event frequency 0.76x (thinnest non-Utilities
  sector sample). Same disqualification rationale as Agriculture.
  Anecdotal anchor retained.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Final

from fair_cam.data import iris_2025
from fair_cam.parameters.industry_calibration import (
    IndustryType,
    _annual_prob_to_lef,
)
from fair_cam.quantile_pooling import Z_0_95
from fair_cam.risk_engine.fair_core import (
    DistributionType,
    FAIRDistribution,
    FAIRParameters,
)

# === Cited-σ allowlist (Epic C-iii-a re-anchor 2026-06-11) ====================
#
# `build_from_iris_2025` emits a LOGNORMAL loss node only for industries whose
# BOTH (p50, p95) anchor legs trace to a paginated primary citation:
#
#     sigma = (ln(p95) − ln(p50)) / Z_0_95
#
# reads p50 and p95 with equal-and-opposite sensitivity — a TWO-source
# derivation (re-gate Meth-RG2-1). After Epic C-iii-a, all 18 mappable
# IndustryType members satisfy this criterion using IRIS 2025 Figure A3, p. 35
# (Appendix — "Losses observed per sector") for BOTH legs.
#
# The two EXCLUDED industries (AGRICULTURE, MINING) have near-point-mass sector
# rows (σ ≈ 0.247 and 0.421 respectively) — see module docstring for rationale.
# They remain PERT with anecdotal anchors.
#
# Historical note: this constant was previously named `_FIGURE12_CITED` and
# contained only {MANUFACTURING, HEALTHCARE}. The rename to `_SECTOR_TABLE_CITED`
# reflects the authoritative location (Figure A3 sector table, not Figure 12).
# ALL FIVE references in this module have been updated; downstream test imports
# in `test_calibration_uses_iris_2025.py` have likewise been updated.
_SECTOR_TABLE_CITED: frozenset[IndustryType] = frozenset(
    {
        IndustryType.UTILITIES,
        IndustryType.CONSTRUCTION,
        IndustryType.MANUFACTURING,
        IndustryType.TRADE,
        IndustryType.RETAIL,
        IndustryType.TRANSPORTATION,
        IndustryType.INFORMATION,
        IndustryType.FINANCIAL,
        IndustryType.REAL_ESTATE,
        IndustryType.PROFESSIONAL,
        IndustryType.MANAGEMENT,
        IndustryType.ADMINISTRATIVE,
        IndustryType.EDUCATION,
        IndustryType.HEALTHCARE,
        IndustryType.ENTERTAINMENT,
        IndustryType.HOSPITALITY,
        IndustryType.OTHER,
        IndustryType.PUBLIC,
    }
)


@dataclass(frozen=True)
class PrimaryCitation:
    """Machine-readable primary citation per the project's primary-cited
    gate (page/figure for paginated sources). Lets a completeness test —
    and any future lint — verify citation coverage without parsing the
    prose ``notes`` narratives."""

    source: str  # e.g. "IRIS 2025"
    page: int
    figure: str  # e.g. "A3"


# All 18 mapped sector rows share the single Figure-A3 anchor (both p50 and
# p95 legs). One constant, assigned per entry, so a future per-entry source
# change is an explicit per-entry edit.
_FIGURE_A3_CITATION = PrimaryCitation(source="IRIS 2025", page=35, figure="A3")


@dataclass(frozen=True)
class IndustryMagnitudePrior:
    """Per-industry loss-magnitude prior used when IRIS does not publish
    per-industry loss medians (true for IRIS 2025 public report).

    ``citation`` is the machine-readable counterpart of the prose ``notes``:
    non-None for every ``_SECTOR_TABLE_CITED`` industry (paginated primary
    source for BOTH anchor legs), None for the anecdotal-anchored excluded
    industries (AGRICULTURE, MINING — see module docstring). Completeness is
    pinned by fair_cam/tests/test_magnitude_prior_citations.py."""

    p50: float
    p95: float
    notes: str  # narrative: anchor source, methodology, assumptions
    citation: PrimaryCitation | None = None


PER_INDUSTRY_MAGNITUDE_PRIORS_2025: Final[dict[IndustryType, IndustryMagnitudePrior]] = {
    IndustryType.AGRICULTURE: IndustryMagnitudePrior(
        p50=380_000,
        p95=3_800_000,
        notes=(
            "UNMAPPABLE — no Figure-A3 row maps to this industry (Agriculture sector "
            "row p50=$2M/p95=$3M has σ≈0.247, near-point-mass; excluded per Epic "
            "C-iii-a methodology gate). Anecdotal anchor retained (see C-ii-b "
            "catalogue). FAIR prior: low-tech sector with limited cyber attack "
            "surface; small-org skew. Anchored to small-business cyber-incident "
            "median ($350K-$400K, Verizon DBIR 2024 SMB section); p95 reflects "
            "rare ransomware-on-large-agribusiness scenarios (e.g., JBS 2021)."
        ),
    ),
    IndustryType.MINING: IndustryMagnitudePrior(
        p50=850_000,
        p95=8_000_000,
        notes=(
            "UNMAPPABLE — no Figure-A3 row maps to this industry (Mining sector row "
            "p50=$1M/p95=$2M has σ≈0.421, near-point-mass; IRIS Figure A1 shows "
            "0.76x relative event frequency, the thinnest non-Utilities sector "
            "sample — same disqualification rationale as Agriculture; excluded per "
            "Epic C-iii-a methodology gate). Anecdotal anchor retained (see C-ii-b "
            "catalogue). FAIR prior: extraction-industry IT systems mostly "
            "OT-centric; cyber events rare per IRIS but high impact when they hit "
            "(Colonial Pipeline 2021 reference point). Anchored to NetDiligence "
            "energy-sector claims median; p95 from CISA OT advisories."
        ),
    ),
    IndustryType.UTILITIES: IndustryMagnitudePrior(
        p50=146_000,
        p95=3_000_000,
        notes=(
            "IRIS 2025, Figure A3, p. 35 (Appendix — Loss magnitude statistics by "
            "sector): Utilities Median $146K; 95th percentile $3M. "
            "sigma = ln(3e6/146e3)/Z_0_95 ≈ 1.838. "
            "Supersedes the prior mixed cite (NetDiligence Energy Vertical Report "
            "2024 p50 + CISA YIR 2024 p95 — both anecdotal/non-paginated)."
        ),
        citation=_FIGURE_A3_CITATION,
    ),
    IndustryType.CONSTRUCTION: IndustryMagnitudePrior(
        p50=189_000,
        p95=5_000_000,
        notes=(
            "IRIS 2025, Figure A3, p. 35 (Appendix — Loss magnitude statistics by "
            "sector): Construction Median $189K; 95th percentile $5M. "
            "sigma = ln(5e6/189e3)/Z_0_95 ≈ 1.991. "
            "Supersedes the prior mixed cite (NetDiligence 2024 construction "
            "median p50 — anecdotal/non-paginated)."
        ),
        citation=_FIGURE_A3_CITATION,
    ),
    IndustryType.MANUFACTURING: IndustryMagnitudePrior(
        p50=1_000_000,
        p95=42_000_000,
        notes=(
            "IRIS 2025, Figure A3, p. 35 (Appendix — Loss magnitude statistics by "
            "sector): Manufacturing Median $1M; 95th percentile $42M. "
            "sigma = ln(42e6/1e6)/Z_0_95 ≈ 2.272. "
            "Supersedes the prior mixed-source anchor (p50 = $2.8M NetDiligence "
            "Cyber Claims Study 2024 manufacturing vertical median; p95 = $23M "
            "conservative within-sector estimate — replaced by the pure-paginated "
            "Figure A3 pair; BEFORE: p50=$2.8M/p95=$23M/sigma≈1.281)."
        ),
        citation=_FIGURE_A3_CITATION,
    ),
    IndustryType.TRADE: IndustryMagnitudePrior(
        p50=1_000_000,
        p95=23_000_000,
        notes=(
            "IRIS 2025, Figure A3, p. 35 (Appendix — Loss magnitude statistics by "
            "sector): Trade Median $1M; 95th percentile $23M. "
            "sigma = ln(23e6/1e6)/Z_0_95 ≈ 1.906. "
            "Supersedes the prior mixed cite (NetDiligence retail/wholesale split "
            "median — anecdotal/non-paginated)."
        ),
        citation=_FIGURE_A3_CITATION,
    ),
    IndustryType.RETAIL: IndustryMagnitudePrior(
        p50=746_000,
        p95=45_000_000,
        notes=(
            "IRIS 2025, Figure A3, p. 35 (Appendix — Loss magnitude statistics by "
            "sector): Retail Median $746K; 95th percentile $45M. "
            "sigma = ln(45e6/746e3)/Z_0_95 ≈ 2.492. "
            "Supersedes the prior mixed cite (NetDiligence 2024 retail claims "
            "median p50; publicized large-retailer breach p95 — anecdotal)."
        ),
        citation=_FIGURE_A3_CITATION,
    ),
    IndustryType.TRANSPORTATION: IndustryMagnitudePrior(
        p50=490_000,
        p95=23_000_000,
        notes=(
            "IRIS 2025, Figure A3, p. 35 (Appendix — Loss magnitude statistics by "
            "sector): Transportation Median $490K; 95th percentile $23M. "
            "sigma = ln(23e6/490e3)/Z_0_95 ≈ 2.340. "
            "Supersedes the prior mixed cite (NetDiligence transportation claims "
            "median; Maersk-NotPetya p95 — anecdotal)."
        ),
        citation=_FIGURE_A3_CITATION,
    ),
    IndustryType.INFORMATION: IndustryMagnitudePrior(
        p50=718_000,
        p95=217_000_000,
        notes=(
            "IRIS 2025, Figure A3, p. 35 (Appendix — Loss magnitude statistics by "
            "sector): Information Median $718K; 95th percentile $217M. "
            "sigma = ln(217e6/718e3)/Z_0_95 ≈ 3.472. "
            "Supersedes the prior mixed cite (NetDiligence Tech Sector Report 2024 "
            "median; non-paginated SaaS/telecom breach p95 — anecdotal)."
        ),
        citation=_FIGURE_A3_CITATION,
    ),
    IndustryType.FINANCIAL: IndustryMagnitudePrior(
        p50=1_000_000,
        p95=194_000_000,
        notes=(
            "IRIS 2025, Figure A3, p. 35 (Appendix — Loss magnitude statistics by "
            "sector): Financial Median $1M; 95th percentile $194M. "
            "sigma = ln(194e6/1e6)/Z_0_95 ≈ 3.203. "
            "Supersedes the prior mixed cite (FFIEC reporting p50 — bare string, "
            "no report/year/table, failed primary-cited gate; IRIS 2025 Figure 12 "
            "financial trend panel p95 — replaced by Figure A3 pure-paginated pair)."
        ),
        citation=_FIGURE_A3_CITATION,
    ),
    IndustryType.REAL_ESTATE: IndustryMagnitudePrior(
        p50=236_000,
        p95=2_000_000,
        notes=(
            "IRIS 2025, Figure A3, p. 35 (Appendix — Loss magnitude statistics by "
            "sector): Real Estate Median $236K; 95th percentile $2M. "
            "sigma = ln(2e6/236e3)/Z_0_95 ≈ 1.299. "
            "lower-confidence: sector sits below-median in IRIS Figure A1 event "
            "frequency (0.93x); sigma=1.299 clears the near-point-mass floor. "
            "Supersedes the prior mixed cite (FBI IC3 2025 real-estate wire fraud "
            "median — anecdotal/non-paginated)."
        ),
        citation=_FIGURE_A3_CITATION,
    ),
    IndustryType.PROFESSIONAL: IndustryMagnitudePrior(
        p50=736_000,
        p95=17_000_000,
        notes=(
            "IRIS 2025, Figure A3, p. 35 (Appendix — Loss magnitude statistics by "
            "sector): Professional Median $736K; 95th percentile $17M. "
            "sigma = ln(17e6/736e3)/Z_0_95 ≈ 1.909. "
            "Supersedes the prior mixed cite (NetDiligence professional-services "
            "median; high-profile law-firm-breach p95 — anecdotal)."
        ),
        citation=_FIGURE_A3_CITATION,
    ),
    IndustryType.MANAGEMENT: IndustryMagnitudePrior(
        p50=332_000,
        p95=140_000_000,
        notes=(
            "IRIS 2025, Figure A3, p. 35 (Appendix — Loss magnitude statistics by "
            "sector): Management Median $332K; 95th percentile $140M. "
            "sigma = ln(140e6/332e3)/Z_0_95 ≈ 3.675. "
            "Supersedes the prior mixed cite (NAICS 55 claims median; small-N "
            "caveat documented in IRIS 2025 BONUS notes — anecdotal)."
        ),
        citation=_FIGURE_A3_CITATION,
    ),
    IndustryType.ADMINISTRATIVE: IndustryMagnitudePrior(
        p50=529_000,
        p95=31_000_000,
        notes=(
            "IRIS 2025, Figure A3, p. 35 (Appendix — Loss magnitude statistics by "
            "sector): Administrative Median $529K; 95th percentile $31M. "
            "sigma = ln(31e6/529e3)/Z_0_95 ≈ 2.475. "
            "Supersedes the prior mixed cite (NetDiligence NAICS 56 median — "
            "anecdotal/non-paginated)."
        ),
        citation=_FIGURE_A3_CITATION,
    ),
    IndustryType.EDUCATION: IndustryMagnitudePrior(
        p50=249_000,
        p95=6_000_000,
        notes=(
            "IRIS 2025, Figure A3, p. 35 (Appendix — Loss magnitude statistics by "
            "sector): Education Median $249K; 95th percentile $6M. "
            "sigma = ln(6e6/249e3)/Z_0_95 ≈ 1.935. "
            "Supersedes the prior mixed cite (NetDiligence education claims + "
            "K12SIX incident reporting — anecdotal/non-paginated)."
        ),
        citation=_FIGURE_A3_CITATION,
    ),
    IndustryType.HEALTHCARE: IndustryMagnitudePrior(
        p50=557_000,
        p95=14_000_000,
        notes=(
            "IRIS 2025, Figure A3, p. 35 (Appendix — Loss magnitude statistics by "
            "sector): Healthcare Median $557K; 95th percentile $14M. "
            "sigma = ln(14e6/557e3)/Z_0_95 ≈ 1.960. "
            "Supersedes the prior mixed cite (DBIR 2024 publishes no per-sector "
            "dollar median; Figure 12 covers only Education/Professional/Retail "
            "trend panels — the healthcare p95=$42M previously here was "
            "Manufacturing's Figure-A3 value, cross-contaminated; corrected "
            "B-HLT-2 2026-06-10)."
        ),
        citation=_FIGURE_A3_CITATION,
    ),
    IndustryType.ENTERTAINMENT: IndustryMagnitudePrior(
        p50=282_000,
        p95=12_000_000,
        notes=(
            "IRIS 2025, Figure A3, p. 35 (Appendix — Loss magnitude statistics by "
            "sector): Entertainment Median $282K; 95th percentile $12M. "
            "sigma = ln(12e6/282e3)/Z_0_95 ≈ 2.280. "
            "Supersedes the prior mixed cite (NetDiligence entertainment claims; "
            "high-profile studio-breach p95 — anecdotal)."
        ),
        citation=_FIGURE_A3_CITATION,
    ),
    IndustryType.HOSPITALITY: IndustryMagnitudePrior(
        p50=600_000,
        p95=62_000_000,
        notes=(
            "IRIS 2025, Figure A3, p. 35 (Appendix — Loss magnitude statistics by "
            "sector): Hospitality Median $600K; 95th percentile $62M. "
            "sigma = ln(62e6/600e3)/Z_0_95 ≈ 2.820. "
            "Supersedes the prior mixed cite (NetDiligence hospitality vertical "
            "median; Marriott 2018 p95 — anecdotal)."
        ),
        citation=_FIGURE_A3_CITATION,
    ),
    IndustryType.OTHER: IndustryMagnitudePrior(
        p50=348_000,
        p95=41_000_000,
        notes=(
            "IRIS 2025, Figure A3, p. 35 (Appendix — Loss magnitude statistics by "
            "sector): Other services Median $348K; 95th percentile $41M. "
            "sigma = ln(41e6/348e3)/Z_0_95 ≈ 2.899. "
            "Supersedes the prior mixed cite (NetDiligence small-business segment "
            "median — anecdotal/non-paginated)."
        ),
        citation=_FIGURE_A3_CITATION,
    ),
    IndustryType.PUBLIC: IndustryMagnitudePrior(
        p50=214_000,
        p95=18_000_000,
        notes=(
            "IRIS 2025, Figure A3, p. 35 (Appendix — Loss magnitude statistics by "
            "sector): Public Median $214K; 95th percentile $18M. "
            "sigma = ln(18e6/214e3)/Z_0_95 ≈ 2.695. "
            "Supersedes the prior mixed cite (NetDiligence public-sector claims "
            "median — anecdotal/non-paginated)."
        ),
        citation=_FIGURE_A3_CITATION,
    ),
}


# === FAIR-translation helpers (year-specific because vulnerability/incident
#     priors are anchored to IRIS 2025 evidence) =====================

# Note: ``_annual_prob_to_lef`` is the generic FAIR Poisson translation
# (LEF = -ln(1 - p_annual)) and lives in ``industry_calibration``. We import
# it above rather than duplicate it here.


# Per-industry FAIR vulnerability priors. These values are FAIR-modeled
# judgment calls inspired by IRIS 2025 Q6 commentary (Figure 16 initial-access
# shares: valid accounts 46%, phishing 28% — both human/identity-driven
# failure modes), NOT measured 2025 data points; IRIS 2025 does not publish
# per-industry vulnerability distributions in the public report. A median
# FAIR vulnerability around 0.30-0.45 across sectors is the working
# calibration target derived from that commentary; org-specific telemetry
# should override these priors when available. Year-scoped here because
# IRIS 2026 may revise the anchor reasoning.
_INDUSTRY_VULNERABILITY_BASE_2025: dict[str, float] = {
    # Public-report sectors (Figure 8)
    "manufacturing": 0.40,
    "healthcare": 0.45,  # higher — IRIS 2025 lists healthcare elevated
    "financial": 0.30,  # lower — Figure 8 financial declining due to investment
    "retail": 0.35,
    "information": 0.40,
    "public": 0.45,
    "utilities": 0.40,
    "entertainment": 0.35,
    # Member-Vault BONUS sectors — FAIR-modeled estimates documented as priors
    "construction": 0.40,  # similar to manufacturing
    "trade": 0.35,  # similar to retail
    "transportation": 0.40,  # similar to manufacturing/utilities — supply-chain target
    "professional": 0.40,  # NAICS 54 — professional, scientific, technical services
    "education": 0.40,  # federally-regulated; similar to public but lower
    "other": 0.40,  # default
    "administrative": 0.35,
    "real_estate": 0.35,
    "management": 0.30,  # high security investment, similar to financial
    "hospitality": 0.40,
    "mining": 0.40,
    "agriculture": 0.40,  # low data; default
}


def build_from_iris_2025(
    industry: IndustryType,
    revenue_tier: str,
) -> FAIRParameters:
    """Mechanical assembly: IRIS 2025 frequency × revenue-tier scaling × prior magnitude.

    Combines three IRIS 2025 inputs into a calibrated ``FAIRParameters``:

    1. ``ANNUAL_INCIDENT_PROBABILITY_BY_INDUSTRY_2024[industry]`` — per-industry
       annualized P(>=1 incident).
    2. ``ANNUAL_INCIDENT_PROBABILITY_BY_REVENUE_TIER_2024[revenue_tier]`` /
       ``ANNUAL_INCIDENT_PROBABILITY_TYPICAL_2024`` — revenue-tier scaling
       factor against the typical-org baseline (9.3%).
    3. ``PER_INDUSTRY_MAGNITUDE_PRIORS_2025[industry].p50`` — per-industry
       loss-magnitude prior anchored to IRIS 2025 Figure A3, p. 35 for the 18
       mappable industries; anecdotal anchor for AGRICULTURE and MINING.

    Revenue-tier vocabulary
    -----------------------
    Uses the ``ANNUAL_INCIDENT_PROBABILITY_BY_REVENUE_TIER_2024`` family of
    revenue-tier labels as the canonical vocabulary for v3. Valid values:

        ``less_than_10m``, ``10m_to_100m``, ``100m_to_1b``, ``1b_to_10b``,
        ``10b_to_100b``, ``more_than_100b``.

    A separate family of labels appears in ``LOSS_BY_REVENUE_TIER_2024``
    (``less_than_100m`` / ``more_than_10b`` etc., from Table 1) — this
    builder does NOT consume that family. Loss magnitude is anchored on
    the per-industry magnitude prior, not the revenue-tier loss table.

    Args:
        industry: NAICS-2-aligned sector enum.
        revenue_tier: one of the IRIS 2025 incident-probability-by-revenue-tier
            keys (see vocabulary above).

    Raises:
        ValueError: if ``revenue_tier`` is not a recognized key, or if
            ``industry`` has no entry in ``PER_INDUSTRY_MAGNITUDE_PRIORS_2025``.

    Cited-σ loss derivation (Epic C-iii-a 2026-06-11; prior Epic B #326 re-gate
    Methodology BLOCKER-2; B-HLT-2 healthcare fix 2026-06-10)
    -------------------------------------------------------------------------
    For the 18 sector-table-cited industries in ``_SECTOR_TABLE_CITED``,
    ``primary_loss`` / ``secondary_loss`` are native LOGNORMAL with

        sigma = (ln(prior.p95) − ln(prior.p50)) / Z_0_95

    where ``Z_0_95 = 1.6448536269514722`` is the standard-normal 0.95 quantile
    (a p5/p95 90% interval). BOTH legs of σ trace to IRIS 2025 Figure A3, p. 35,
    pinned here so a reader can re-derive σ for each industry.

    For AGRICULTURE and MINING — both PERT (anecdotal anchors retained;
    see module docstring for exclusion rationale).

    The LOGNORMAL ``mean`` is log-space and anchors on ``ln(prior.p50)`` (median
    = exp(mean) = p50). σ is identical for primary and secondary loss because
    both are anchored to the same cited (p50, p95) magnitude spread; only the
    median (mean = ln p50) shifts (secondary ~30% of primary).

    Returns:
        FAIRParameters with TEF (TRIANGULAR), vulnerability (TRIANGULAR), and
        loss nodes that are LOGNORMAL (sector-table-cited industries) or PERT
        (AGRICULTURE and MINING with anecdotal anchors).
    """
    if revenue_tier not in iris_2025.ANNUAL_INCIDENT_PROBABILITY_BY_REVENUE_TIER_2024:
        valid = sorted(iris_2025.ANNUAL_INCIDENT_PROBABILITY_BY_REVENUE_TIER_2024.keys())
        raise ValueError(
            f"revenue_tier={revenue_tier!r} not recognized; valid IRIS 2025 tiers: {valid}"
        )
    if industry not in PER_INDUSTRY_MAGNITUDE_PRIORS_2025:
        raise ValueError(
            f"industry={industry!r} has no entry in PER_INDUSTRY_MAGNITUDE_PRIORS_2025"
        )

    industry_key = industry.value
    p_industry = iris_2025.ANNUAL_INCIDENT_PROBABILITY_BY_INDUSTRY_2024[industry_key]
    p_tier = iris_2025.ANNUAL_INCIDENT_PROBABILITY_BY_REVENUE_TIER_2024[revenue_tier]
    p_typical = iris_2025.ANNUAL_INCIDENT_PROBABILITY_TYPICAL_2024

    # Revenue-tier scaling: how much more / less likely is this tier
    # vs the typical org baseline (9.3%).
    tier_scale = p_tier / p_typical

    # Combined probability is bounded below 1.0 to keep -ln() finite.
    combined_prob = min(p_industry * tier_scale, 0.999)

    # FAIR translation: P -> LEF (Poisson) -> TEF (LEF / vulnerability).
    lef = _annual_prob_to_lef(combined_prob)
    vulnerability_base = _INDUSTRY_VULNERABILITY_BASE_2025.get(industry_key, 0.40)
    tef = lef / vulnerability_base

    # Loss magnitude anchors on the per-industry prior. Revenue-tier loss
    # scaling is intentionally NOT applied here — the magnitude prior is
    # the calibration anchor for v3, and per-tier loss scaling is left as
    # a future overlay (B6/B7) rather than baked into the base parameters.
    prior = PER_INDUSTRY_MAGNITUDE_PRIORS_2025[industry]
    prior_p50 = prior.p50

    if industry in _SECTOR_TABLE_CITED:  # _SECTOR_TABLE_CITED: all 18 mappable industries
        # Cited-σ derivation. BOTH p50 (IRIS 2025 Figure A3 p.35 sector median)
        # and p95 (IRIS 2025 Figure A3 p.35 sector 95th percentile) trace to a
        # paginated primary citation — see the builder docstring and
        # PER_INDUSTRY_MAGNITUDE_PRIORS_2025 for the per-industry pinning.
        # σ is identical for the primary and secondary loss because both are
        # anchored to the same cited (p50, p95) magnitude spread; only the
        # median (mean = ln p50) shifts (secondary ~30% of primary).
        sigma = (math.log(prior.p95) - math.log(prior_p50)) / Z_0_95
        primary_loss = FAIRDistribution(
            DistributionType.LOGNORMAL,
            {
                # LOGNORMAL params here are LOG-space (numpy semantics — see
                # the comment in FAIRDistribution.sample). exp(mean) gives the
                # median loss, anchored on the per-industry magnitude prior p50.
                "mean": math.log(prior_p50),
                "sigma": sigma,
            },
        )
        secondary_loss = FAIRDistribution(
            DistributionType.LOGNORMAL,
            {
                "mean": math.log(prior_p50 * 0.3),  # secondary ~30% of primary
                "sigma": sigma,
            },
        )
    else:
        # AGRICULTURE and MINING: near-point-mass Figure A3 rows excluded;
        # anecdotal p95 anchor ⇒ no defensible lognormal σ ⇒ PERT, NOT lognormal.
        # mode = p50, high = p95, low = p50*0.1 (coarse left tail).
        # Re-curation of these two sectors is deferred (see module docstring).
        primary_loss = FAIRDistribution(
            DistributionType.PERT,
            {
                "low": prior_p50 * 0.1,
                "mode": prior_p50,
                "high": prior.p95,
            },
        )
        secondary_loss = FAIRDistribution(
            DistributionType.PERT,
            {
                "low": prior_p50 * 0.3 * 0.1,
                "mode": prior_p50 * 0.3,  # secondary ~30% of primary
                "high": prior.p95 * 0.3,
            },
        )

    return FAIRParameters(
        threat_event_frequency=FAIRDistribution(
            DistributionType.TRIANGULAR,
            {
                "low": tef * 0.3,
                "mode": tef,
                "high": tef * 2.5,
            },
        ),
        vulnerability=FAIRDistribution(
            DistributionType.TRIANGULAR,
            {
                "low": vulnerability_base * 0.5,
                "mode": vulnerability_base,
                "high": min(vulnerability_base * 1.8, 0.99),  # capped <= 1
            },
        ),
        primary_loss=primary_loss,
        secondary_loss=secondary_loss,
    )
