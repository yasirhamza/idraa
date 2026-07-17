"""
Industry Parameter Calibration System

Provides industry-specific parameter benchmarks plus the public year-aware
translation API ``create_industry_calibrated_parameters`` that dispatches to
per-IRIS-year builders (currently IRIS 2025 only).

Headline values (per-industry TEF medians) in the BenchmarkData library are
translated from Cyentia IRIS 2025 (Figure 8 — annualized incident probability)
via the documented Poisson translation:

    LEF = -ln(1 - p_annual)
    TEF = LEF / vulnerability_base

The per-percentile (p10/p25/p75/p90) shapes around each median represent
within-industry variance and are NOT published by IRIS 2025 at this
granularity — they are FAIR-modeled from a lognormal-ish prior centered on
the IRIS 2025 median. Document any future re-tuning of those shapes against
its source in a comment on the BenchmarkData entry.

Loss-magnitude benchmarks similarly anchor on IRIS 2025 (overall median /
per-revenue-tier values from Table 1) via per-industry multipliers documented
in ``_iris_2025_calibration.py``. The pre-IRIS-2025 hard-coded loss percentiles
preserved in the BenchmarkData library were never traceable to a published
source; they are kept for backward compatibility but flagged as priors in the
data_source string.
"""

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Final

from fair_cam.data import LATEST_IRIS_YEAR

if TYPE_CHECKING:
    from fair_cam.risk_engine.fair_core import FAIRParameters
from fair_cam.data.iris_2025 import (
    ANNUAL_INCIDENT_PROBABILITY_BY_INDUSTRY_2024,
    DATA_SOURCE_NAME,
    PUBLICATION_DATE,
)


def _annual_prob_to_lef(p_annual: float) -> float:
    """Poisson translation: P(>=1 incident) -> LEF (events/year)."""
    return -math.log(1.0 - p_annual)


def _annual_prob_to_tef(p_annual: float, vulnerability: float) -> float:
    """FAIR translation: IRIS 2025 annual probability -> TEF.

    LEF = -ln(1 - p_annual); TEF = LEF / vulnerability.
    """
    return _annual_prob_to_lef(p_annual) / vulnerability


# Vulnerability priors used for the IRIS 2025 -> TEF translation. These
# mirror _INDUSTRY_VULNERABILITY_BASE in fair_core.py but are repeated here
# because this module is independent and we want the translation visible at
# its call site.
_TEF_TRANSLATION_VULNERABILITY: dict[str, float] = {
    "healthcare": 0.45,
    "financial": 0.30,
    "manufacturing": 0.40,
}


class IndustryType(Enum):
    """NAICS-2-aligned sector enum. Values exactly match the keys in
    fair_cam.data.iris_2025.ANNUAL_INCIDENT_PROBABILITY_BY_INDUSTRY_2024.
    """

    AGRICULTURE = "agriculture"
    MINING = "mining"
    UTILITIES = "utilities"
    CONSTRUCTION = "construction"
    MANUFACTURING = "manufacturing"
    TRADE = "trade"
    RETAIL = "retail"
    TRANSPORTATION = "transportation"
    INFORMATION = "information"
    FINANCIAL = "financial"
    REAL_ESTATE = "real_estate"
    PROFESSIONAL = "professional"
    MANAGEMENT = "management"
    ADMINISTRATIVE = "administrative"
    EDUCATION = "education"
    HEALTHCARE = "healthcare"
    ENTERTAINMENT = "entertainment"
    HOSPITALITY = "hospitality"
    OTHER = "other"
    PUBLIC = "public"


class OrganizationSize(Enum):
    """Organization size categories"""

    SMALL = "small"  # < 500 employees
    MEDIUM = "medium"  # 500-5,000 employees
    LARGE = "large"  # 5,000-10,000 employees
    ENTERPRISE = "enterprise"  # 10,000+ employees


class ThreatActorType(Enum):
    """Threat actor classifications"""

    CYBERCRIMINALS = "cybercriminals"
    NATION_STATE = "nation_state"
    INSIDER_MALICIOUS = "insider_malicious"
    INSIDER_ACCIDENTAL = "insider_accidental"
    HACKTIVISTS = "hacktivists"
    COMPETITORS = "competitors"


@dataclass
class BenchmarkData:
    """Industry benchmark data point"""

    parameter_category: str
    industry: IndustryType
    org_size: OrganizationSize
    threat_type: ThreatActorType | None = None

    # Statistical data
    percentile_10: float = 0
    percentile_25: float = 0
    percentile_50: float = 0  # median
    percentile_75: float = 0
    percentile_90: float = 0
    mean_value: float = 0
    std_dev: float = 0

    # Metadata
    sample_size: int = 0
    data_source: str = ""
    confidence_level: float = 0.8
    last_updated: str = ""
    notes: str = ""


@dataclass
class ParameterBenchmark:
    """Comprehensive parameter benchmark with contextual guidance"""

    parameter_name: str
    description: str
    industry_data: list[BenchmarkData]

    # Expert guidance
    estimation_guidance: str = ""
    common_mistakes: list[str] = field(default_factory=list)
    calibration_tips: list[str] = field(default_factory=list)

    # Related factors
    influencing_factors: list[str] = field(default_factory=list)
    seasonal_variations: bool = False
    trend_direction: str | None = None  # "increasing", "decreasing", "stable"


class IndustryParameterLibrary:
    """Comprehensive library of industry-calibrated parameters"""

    def __init__(self) -> None:
        self.benchmarks: dict[str, ParameterBenchmark] = {}
        self._load_iris_2025_data()
        self._load_expert_benchmarks()

    def _load_iris_2025_data(self) -> None:
        """Load IRIS 2025 incident data for parameter calibration.

        TEF medians are translated from
        ``fair_cam.data.iris_2025.ANNUAL_INCIDENT_PROBABILITY_BY_INDUSTRY_2024``
        (Figure 8) via the documented Poisson + vulnerability translation.
        Per-percentile shapes (p10/p25/p75/p90) represent within-industry
        variance and are FAIR-modeled priors — IRIS 2025 does not publish
        per-industry distributions at this granularity in the public report.
        """

        iris_data_source = f"{DATA_SOURCE_NAME} (FAIR-translated)"

        # Translate IRIS 2025 Figure 8 probabilities to FAIR TEF medians.
        # Healthcare: P(>=1)=0.091 -> LEF=-ln(0.909)=0.0954; vuln=0.45 -> TEF=0.212/yr
        # Financial:  P(>=1)=0.068 -> LEF=-ln(0.932)=0.0704; vuln=0.30 -> TEF=0.235/yr
        # Manufacturing: P(>=1)=0.112 -> LEF=-ln(0.888)=0.1188; vuln=0.40 -> TEF=0.297/yr
        healthcare_tef_median = _annual_prob_to_tef(
            ANNUAL_INCIDENT_PROBABILITY_BY_INDUSTRY_2024["healthcare"],
            _TEF_TRANSLATION_VULNERABILITY["healthcare"],
        )
        financial_tef_median = _annual_prob_to_tef(
            ANNUAL_INCIDENT_PROBABILITY_BY_INDUSTRY_2024["financial"],
            _TEF_TRANSLATION_VULNERABILITY["financial"],
        )
        manufacturing_tef_median = _annual_prob_to_tef(
            ANNUAL_INCIDENT_PROBABILITY_BY_INDUSTRY_2024["manufacturing"],
            _TEF_TRANSLATION_VULNERABILITY["manufacturing"],
        )

        # Threat Event Frequency benchmarks grounded in IRIS 2025 Figure 8.
        # The 0.20x / 0.50x / 2.00x / 4.00x percentile shape is a FAIR-modeled
        # prior — IRIS 2025 does not publish within-industry TEF distributions
        # at this granularity. If/when org-specific telemetry becomes available
        # it should override these shapes.
        tef_healthcare = BenchmarkData(
            parameter_category="threat_event_frequency",
            industry=IndustryType.HEALTHCARE,
            org_size=OrganizationSize.MEDIUM,
            percentile_10=healthcare_tef_median * 0.20,
            percentile_25=healthcare_tef_median * 0.50,
            percentile_50=healthcare_tef_median,  # IRIS 2025 Figure 8 (translated)
            percentile_75=healthcare_tef_median * 2.00,
            percentile_90=healthcare_tef_median * 4.00,
            mean_value=healthcare_tef_median * 1.30,
            std_dev=healthcare_tef_median * 1.50,
            sample_size=0,  # not published per-industry
            data_source=iris_data_source,
            confidence_level=0.80,
            last_updated=PUBLICATION_DATE,
            notes=(
                "Median from IRIS 2025 Figure 8 (P(>=1 incident)=9.1%) translated "
                "via LEF=-ln(1-p), TEF=LEF/vuln(=0.45). Percentile shape is a "
                "FAIR-modeled prior — IRIS 2025 doesn't publish within-industry "
                "TEF distributions in the public report."
            ),
        )

        tef_financial = BenchmarkData(
            parameter_category="threat_event_frequency",
            industry=IndustryType.FINANCIAL,
            org_size=OrganizationSize.MEDIUM,
            percentile_10=financial_tef_median * 0.20,
            percentile_25=financial_tef_median * 0.50,
            percentile_50=financial_tef_median,  # IRIS 2025 Figure 8 (translated)
            percentile_75=financial_tef_median * 2.00,
            percentile_90=financial_tef_median * 4.00,
            mean_value=financial_tef_median * 1.30,
            std_dev=financial_tef_median * 1.50,
            sample_size=0,
            data_source=iris_data_source,
            confidence_level=0.80,
            last_updated=PUBLICATION_DATE,
            notes=(
                "Median from IRIS 2025 Figure 8 (P(>=1 incident)=6.8%) translated "
                "via LEF=-ln(1-p), TEF=LEF/vuln(=0.30). Financial sector vulnerability "
                "set lower per IRIS 2025 Q3 commentary on declining financial-sector rate."
            ),
        )

        tef_manufacturing = BenchmarkData(
            parameter_category="threat_event_frequency",
            industry=IndustryType.MANUFACTURING,
            org_size=OrganizationSize.MEDIUM,
            percentile_10=manufacturing_tef_median * 0.20,
            percentile_25=manufacturing_tef_median * 0.50,
            percentile_50=manufacturing_tef_median,  # IRIS 2025 Figure 8 (translated)
            percentile_75=manufacturing_tef_median * 2.00,
            percentile_90=manufacturing_tef_median * 4.00,
            mean_value=manufacturing_tef_median * 1.30,
            std_dev=manufacturing_tef_median * 1.50,
            sample_size=0,
            data_source=iris_data_source,
            confidence_level=0.80,
            last_updated=PUBLICATION_DATE,
            notes=(
                "Median from IRIS 2025 Figure 8 (P(>=1 incident)=11.2%, the highest "
                "of any sector) translated via LEF=-ln(1-p), TEF=LEF/vuln(=0.40)."
            ),
        )

        # Primary Loss Magnitude benchmarks.
        # IRIS 2025 does NOT publish per-industry loss medians in the public
        # report — only overall (Figure 9, $603K p50) and per-revenue-tier
        # (Table 1) values, plus three sector trend panels (Figure 12). The
        # per-industry loss percentiles below are pre-IRIS-2025 priors retained
        # for backward compatibility; they are flagged in data_source as
        # "FAIR prior, not IRIS 2025-published" so callers don't mistake them
        # for measured values. Full per-industry loss distributions are in
        # Cyentia's paid dataset and would replace these if licensed.
        loss_iris_data_source = (
            f"{DATA_SOURCE_NAME} headline anchor; per-industry shape is FAIR prior"
        )

        loss_healthcare = BenchmarkData(
            parameter_category="primary_loss_magnitude",
            industry=IndustryType.HEALTHCARE,
            org_size=OrganizationSize.MEDIUM,
            percentile_10=125000,
            percentile_25=850000,
            percentile_50=4200000,  # FAIR prior; IRIS 2025 doesn't publish this per-industry
            percentile_75=12800000,
            percentile_90=32500000,
            mean_value=8900000,
            std_dev=11200000,
            sample_size=0,
            data_source=loss_iris_data_source,
            confidence_level=0.65,
            last_updated=PUBLICATION_DATE,
            notes=(
                "Per-industry loss distribution is a FAIR prior — IRIS 2025 doesn't "
                "publish per-industry loss medians in the public report. Anchor: "
                "OVERALL_LOSS_MEDIAN=$603K (Figure 9), x ~7 healthcare prior. "
                "Includes regulatory fines and compliance costs."
            ),
        )

        loss_financial = BenchmarkData(
            parameter_category="primary_loss_magnitude",
            industry=IndustryType.FINANCIAL,
            org_size=OrganizationSize.MEDIUM,
            percentile_10=200000,
            percentile_25=1200000,
            percentile_50=5800000,
            percentile_75=18500000,
            percentile_90=47200000,
            mean_value=12400000,
            std_dev=15800000,
            sample_size=0,
            data_source=loss_iris_data_source,
            confidence_level=0.65,
            last_updated=PUBLICATION_DATE,
            notes=(
                "Per-industry loss distribution is a FAIR prior — IRIS 2025 publishes "
                "only overall (Figure 9) and per-revenue-tier (Table 1) loss medians."
            ),
        )

        loss_manufacturing = BenchmarkData(
            parameter_category="primary_loss_magnitude",
            industry=IndustryType.MANUFACTURING,
            org_size=OrganizationSize.MEDIUM,
            percentile_10=75000,
            percentile_25=420000,
            percentile_50=2100000,
            percentile_75=7800000,
            percentile_90=22100000,
            mean_value=5200000,
            std_dev=7900000,
            sample_size=0,
            data_source=loss_iris_data_source,
            confidence_level=0.65,
            last_updated=PUBLICATION_DATE,
            notes=(
                "Per-industry loss distribution is a FAIR prior — IRIS 2025 doesn't "
                "publish per-industry loss medians. Includes production downtime and "
                "supply chain impacts."
            ),
        )

        # Create benchmark objects
        self.benchmarks["threat_event_frequency"] = ParameterBenchmark(
            parameter_name="Threat Event Frequency",
            description="Annual frequency of threat events attempting to compromise assets",
            industry_data=[tef_healthcare, tef_financial, tef_manufacturing],
            estimation_guidance="""
            Consider multiple data sources:
            1. Historical security incident logs (3-5 years)
            2. SIEM alert patterns and confirmed incidents
            3. Industry threat intelligence feeds
            4. Peer organization benchmarks

            Account for detection capabilities - organizations with better detection
            will report higher frequencies than those with limited visibility.
            """,
            common_mistakes=[
                "Using only detected incidents (underestimates actual frequency)",
                "Not accounting for seasonal variations",
                "Mixing different types of threats in single parameter",
                "Ignoring organization-specific threat landscape",
            ],
            calibration_tips=[
                "Start with industry median and adjust for organizational factors",
                "Consider threat actor motivation for your industry/assets",
                "Factor in geopolitical climate for nation-state threats",
                "Validate against multiple years of data when available",
            ],
            influencing_factors=[
                "Industry attractiveness to attackers",
                "Organization visibility/profile",
                "Geographic location",
                "Data/asset value",
                "Security posture maturity",
                "Economic conditions",
            ],
            seasonal_variations=True,
            trend_direction="increasing",
        )

        self.benchmarks["primary_loss_magnitude"] = ParameterBenchmark(
            parameter_name="Primary Loss Magnitude",
            description="Direct financial impact of a successful attack",
            industry_data=[loss_healthcare, loss_financial, loss_manufacturing],
            estimation_guidance="""
            Include all direct costs:
            1. Incident response and investigation
            2. System recovery and remediation
            3. Data recovery and reconstruction
            4. Legal and forensic costs
            5. Regulatory fines and penalties
            6. Customer notification costs

            Use bottom-up estimation when possible, validated against industry data.
            """,
            common_mistakes=[
                "Underestimating incident response costs",
                "Not including regulatory penalties",
                "Forgetting legal and PR costs",
                "Using outdated cost estimates",
                "Not scaling for organization size",
            ],
            calibration_tips=[
                "Use multiple estimation methods and triangulate",
                "Consider worst-case regulatory penalty scenarios",
                "Factor in cyber insurance deductibles and coverage gaps",
                "Validate against recent similar incidents in your industry",
            ],
            influencing_factors=[
                "Data sensitivity and volume",
                "Regulatory environment",
                "Customer base size",
                "Revenue dependency on systems",
                "Insurance coverage",
                "Legal jurisdiction",
            ],
            trend_direction="increasing",
        )

    def _load_expert_benchmarks(self) -> None:
        """Load expert-curated parameter benchmarks"""

        # Vulnerability parameter benchmarks
        vuln_data = [
            BenchmarkData(
                parameter_category="vulnerability",
                industry=IndustryType.HEALTHCARE,
                org_size=OrganizationSize.MEDIUM,
                threat_type=ThreatActorType.CYBERCRIMINALS,
                percentile_10=0.05,
                percentile_25=0.12,
                percentile_50=0.28,
                percentile_75=0.52,
                percentile_90=0.78,
                mean_value=0.34,
                std_dev=0.23,
                data_source="Expert Assessment",
                confidence_level=0.75,
                notes="Based on penetration testing and red team exercises",
            ),
            BenchmarkData(
                parameter_category="vulnerability",
                industry=IndustryType.FINANCIAL,
                org_size=OrganizationSize.MEDIUM,
                threat_type=ThreatActorType.CYBERCRIMINALS,
                percentile_10=0.02,
                percentile_25=0.08,
                percentile_50=0.18,
                percentile_75=0.35,
                percentile_90=0.58,
                mean_value=0.22,
                std_dev=0.18,
                data_source="Expert Assessment",
                confidence_level=0.72,
            ),
        ]

        self.benchmarks["vulnerability"] = ParameterBenchmark(
            parameter_name="Vulnerability",
            description="Probability that a threat action will result in loss",
            industry_data=vuln_data,
            estimation_guidance="""
            Assess organizational vulnerability across multiple dimensions:
            1. Technical controls effectiveness
            2. Process maturity and compliance
            3. Human factors and security awareness
            4. Physical security measures
            5. Third-party/supply chain risks

            Use both quantitative assessments (pen tests, red teams) and
            qualitative expert judgment.
            """,
            common_mistakes=[
                "Overconfidence in control effectiveness",
                "Not considering human factors",
                "Ignoring supply chain vulnerabilities",
                "Using outdated vulnerability assessments",
            ],
            calibration_tips=[
                "Combine multiple assessment methods",
                "Consider attack paths not just individual controls",
                "Factor in attacker sophistication vs. your defenses",
                "Validate through tabletop exercises",
            ],
            influencing_factors=[
                "Security control maturity",
                "Employee security awareness",
                "System complexity",
                "Change management practices",
                "Third-party integrations",
                "Legacy system presence",
            ],
        )

    def get_benchmark(
        self,
        parameter_name: str,
        industry: IndustryType,
        org_size: OrganizationSize,
        threat_type: ThreatActorType | None = None,
    ) -> BenchmarkData | None:
        """Get specific benchmark data"""

        if parameter_name not in self.benchmarks:
            return None

        benchmark = self.benchmarks[parameter_name]

        # Find best match
        best_match = None
        match_score = 0

        for data in benchmark.industry_data:
            score = 0

            # Industry match (most important)
            if data.industry == industry:
                score += 3

            # Size match
            if data.org_size == org_size:
                score += 2

            # Threat type match
            if threat_type and data.threat_type == threat_type:
                score += 1

            if score > match_score:
                match_score = score
                best_match = data

        return best_match

    def get_parameter_guidance(self, parameter_name: str) -> ParameterBenchmark | None:
        """Get comprehensive parameter guidance"""
        return self.benchmarks.get(parameter_name)

    def suggest_parameter_values(
        self,
        parameter_name: str,
        industry: IndustryType,
        org_size: OrganizationSize,
        confidence_approach: str = "conservative",
    ) -> dict[str, float] | None:
        """Suggest parameter values based on benchmarks"""

        benchmark_data = self.get_benchmark(parameter_name, industry, org_size)
        if not benchmark_data:
            return None

        if confidence_approach == "conservative":
            # Use wider ranges, higher values for losses/frequencies
            return {
                "low": benchmark_data.percentile_10,
                "mode": benchmark_data.percentile_75,  # Conservative: use 75th percentile
                "high": benchmark_data.percentile_90 * 1.2,  # Add buffer
            }
        elif confidence_approach == "optimistic":
            # Use narrower ranges, lower values
            return {
                "low": benchmark_data.percentile_25,
                "mode": benchmark_data.percentile_25,  # Optimistic: use 25th percentile
                "high": benchmark_data.percentile_75,
            }
        else:  # "realistic"
            return {
                "low": benchmark_data.percentile_25,
                "mode": benchmark_data.percentile_50,  # Use median
                "high": benchmark_data.percentile_75,
            }

    def get_industry_comparison(
        self, parameter_name: str, value: float, industry: IndustryType, org_size: OrganizationSize
    ) -> dict[str, Any] | None:
        """Compare parameter value against industry benchmarks"""

        benchmark_data = self.get_benchmark(parameter_name, industry, org_size)
        if not benchmark_data:
            return None

        # Determine percentile
        percentile = None
        interpretation = ""

        if value <= benchmark_data.percentile_10:
            percentile = "< 10th"
            interpretation = "Very low compared to industry"
        elif value <= benchmark_data.percentile_25:
            percentile = "10th - 25th"
            interpretation = "Below average"
        elif value <= benchmark_data.percentile_50:
            percentile = "25th - 50th"
            interpretation = "Below median"
        elif value <= benchmark_data.percentile_75:
            percentile = "50th - 75th"
            interpretation = "Above median"
        elif value <= benchmark_data.percentile_90:
            percentile = "75th - 90th"
            interpretation = "Above average"
        else:
            percentile = "> 90th"
            interpretation = "Very high compared to industry"

        return {
            "percentile_range": percentile,
            "interpretation": interpretation,
            "industry_median": benchmark_data.percentile_50,
            "industry_mean": benchmark_data.mean_value,
            "sample_size": benchmark_data.sample_size,
            "confidence_level": benchmark_data.confidence_level,
            "data_source": benchmark_data.data_source,
        }

    def export_benchmarks(self) -> dict[str, Any]:
        """Export all benchmark data"""
        export_data: dict[str, Any] = {}

        for name, benchmark in self.benchmarks.items():
            export_data[name] = {
                "description": benchmark.description,
                "estimation_guidance": benchmark.estimation_guidance,
                "common_mistakes": benchmark.common_mistakes,
                "calibration_tips": benchmark.calibration_tips,
                "influencing_factors": benchmark.influencing_factors,
                "data": [],
            }

            for data in benchmark.industry_data:
                export_data[name]["data"].append(
                    {
                        "industry": data.industry.value,
                        "org_size": data.org_size.value,
                        "threat_type": data.threat_type.value if data.threat_type else None,
                        "statistics": {
                            "p10": data.percentile_10,
                            "p25": data.percentile_25,
                            "p50": data.percentile_50,
                            "p75": data.percentile_75,
                            "p90": data.percentile_90,
                            "mean": data.mean_value,
                            "std_dev": data.std_dev,
                        },
                        "metadata": {
                            "sample_size": data.sample_size,
                            "data_source": data.data_source,
                            "confidence_level": data.confidence_level,
                            "notes": data.notes,
                        },
                    }
                )

        return export_data


# === Public year-aware translation API =============================================

SUPPORTED_IRIS_YEARS: Final[tuple[int, ...]] = (2025,)


def create_industry_calibrated_parameters(
    industry: IndustryType,
    revenue_tier: str,
    *,
    iris_year: int | None = None,
) -> "FAIRParameters":
    """Return FAIR parameters calibrated against an IRIS publication.

    Year-aware translation API that dispatches to per-IRIS-year builders.
    Currently only ``iris_year=2025`` is supported; future IRIS publications
    add another branch here without changing call sites.

    Args:
        industry: NAICS-2-aligned sector enum.
        revenue_tier: one of the IRIS-defined revenue tiers (e.g. ``"1b_to_10b"``).
            See ``_iris_2025_calibration.build_from_iris_2025`` for the
            canonical 2025 vocabulary.
        iris_year: IRIS publication year. ``None`` selects ``LATEST_IRIS_YEAR``.

    Raises:
        ValueError: ``iris_year`` not in ``SUPPORTED_IRIS_YEARS``, or
            ``industry`` / ``revenue_tier`` unknown to the named year.
    """
    year = iris_year if iris_year is not None else LATEST_IRIS_YEAR
    if year not in SUPPORTED_IRIS_YEARS:
        raise ValueError(f"iris_year={year!r} not supported; choose from {SUPPORTED_IRIS_YEARS}")
    if year == 2025:
        from fair_cam.parameters._iris_2025_calibration import build_from_iris_2025

        return build_from_iris_2025(industry, revenue_tier)
    raise AssertionError(f"unreachable: year={year}")  # pragma: no cover
