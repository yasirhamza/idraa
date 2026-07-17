"""All shared string enums. StrEnum → value round-trips as plain string in the DB."""

from __future__ import annotations

from enum import StrEnum


class UserRole(StrEnum):
    ADMIN = "admin"
    ANALYST = "analyst"
    REVIEWER = "reviewer"
    VIEWER = "viewer"


class EntityStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    DEPRECATED = "deprecated"
    DELETED = "deleted"


class ControlImplementationStage(StrEnum):
    """Maturity of a control's real-world implementation (issue #395).

    SEPARATE axis from EntityStatus (which is publish / soft-delete
    lifecycle). This gates the FAIR-CAM composition: only ACTIVE controls
    contribute to a run — any other stage excludes the control entirely
    (its assignments never reach the engine). This is v3 view-model gating
    applied in the composition-assembly layer; fair_cam stays unaware of
    implementation stage.
    """

    NON_EXISTENT = "non_existent"
    PLANNED = "planned"
    IN_PROJECT = "in_project"
    ACTIVE = "active"

    @property
    def contributes_to_composition(self) -> bool:
        """True iff a control at this stage contributes to the FAIR-CAM
        composition. Expressed as a positive identity check (is ACTIVE)
        rather than a not-in-exclusion-set so that adding a future stage
        cannot accidentally let a non-operating control reduce modeled
        risk (issue #395 design §1)."""
        return self is ControlImplementationStage.ACTIVE

    @property
    def label(self) -> str:
        """Canonical human-readable label — the SINGLE humanization point
        (plan-gate Arch-S1). Templates render `stage.label`; never an ad-hoc
        `value | replace | title` (which would mangle 'in_project' to
        'In Project' instead of 'In project (implementing)')."""
        return {
            ControlImplementationStage.NON_EXISTENT: "Non-existent",
            ControlImplementationStage.PLANNED: "Proposed / Planned",
            ControlImplementationStage.IN_PROJECT: "In project (implementing)",
            ControlImplementationStage.ACTIVE: "Active",
        }[self]


class ControlDomain(StrEnum):
    LOSS_EVENT = "loss_event"
    VARIANCE_MANAGEMENT = "variance_management"
    DECISION_SUPPORT = "decision_support"


class ControlType(StrEnum):
    TECHNICAL = "technical"
    ADMINISTRATIVE = "administrative"
    PHYSICAL = "physical"


class ScenarioType(StrEnum):
    TEMPLATE = "template"
    CUSTOM = "custom"
    INDUSTRY_STANDARD = "industry_standard"


class ScenarioSource(StrEnum):
    """Provenance of a scenario row.
    Phase 1.3 shipped only EXPERT_JUDGMENT; Phase 1.5a promotes LIBRARY_DERIVED;
    P1 promotes FILE_IMPORT (CSV/JSON scenario import — calibration spec §7.5,
    generalized to cover JSON, hence FILE_IMPORT not CSV_IMPORT)."""

    EXPERT_JUDGMENT = "expert_judgment"
    LIBRARY_DERIVED = "library_derived"  # Phase 1.5a — promoted from comment to value
    FILE_IMPORT = "file_import"  # P1 — CSV/JSON scenario import (source-agnostic name)
    # Phase 2: QUALITATIVE_REGISTER_IMPORT (tidyrisk register import)


class ScenarioEffect(StrEnum):
    """FAIR taxonomy's fourth axis (Threat / Asset / Method / **Effect**).

    The CIA effect the loss event has on the asset. Author-time, one dominant
    effect per scenario (MVP; per-effect magnitude split is deferred). Drives the
    effect-type-aware recovery gate: AVAILABILITY events self-detect (a power
    outage / downed production line manifests observably — FAIR-CAM §3.3.2 p.19),
    so recovery→magnitude credit does not require a co-present Detection control.
    CONFIDENTIALITY / INTEGRITY (stealth) stay detection-gated (§3.3 p.18). NULL
    (unspecified) is treated as non-availability → detection-gated.
    """

    CONFIDENTIALITY = "confidentiality"
    INTEGRITY = "integrity"
    AVAILABILITY = "availability"


class LossTier(StrEnum):
    """Epistemic tier of an entry's loss-magnitude anchor (Epic C #335 §1).
    paginated -> lognormal full-confidence; vendor -> lognormal, lower-confidence
    badge; anecdotal -> stays PERT; none -> no loss anchor asserted."""

    PAGINATED = "paginated"
    VENDOR = "vendor"
    ANECDOTAL = "anecdotal"
    NONE = "none"


class LossShape(StrEnum):
    """Milestone B (#loss-pert-overhaul): distribution-shape class of an
    entry's loss magnitude. INDEPENDENT of LossTier (citation quality):
    capped -> bounded PERT (the high IS the economic ceiling);
    catastrophic -> uncapped lognormal (narrow curated existential class,
    spec 2026-07-09 §3)."""

    CAPPED = "capped"
    CATASTROPHIC = "catastrophic"


class ControlSource(StrEnum):
    """Provenance of a control row. CUSTOM = manually created (form / arbitrary-CSV
    import). LIBRARY_DERIVED = adopted (clone-snapshot) from the control library
    catalog (P2b). Both live in the same controls table; the engine treats them
    identically. Mirrors ScenarioSource."""

    CUSTOM = "custom"
    LIBRARY_DERIVED = "library_derived"


class CorrelationAssumption(StrEnum):
    INDEPENDENT = "independent"
    POSITIVE = "positive"
    NEGATIVE = "negative"
    CAUSAL = "causal"
    COMMON_CAUSE = "common_cause"


class IndustryType(StrEnum):
    """20 NAICS-2 supersector values aligning with fair_cam.parameters.industry_calibration.
    Expanded from 9 in Phase 1.5a (D2 alignment fix)."""

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
    PUBLIC = "public"
    OTHER = "other"


class OrganizationSize(StrEnum):
    SMALL = "small"
    MEDIUM = "medium"
    LARGE = "large"
    ENTERPRISE = "enterprise"


class SecurityMaturity(StrEnum):
    BASIC = "basic"
    DEVELOPING = "developing"
    DEFINED = "defined"
    MANAGED = "managed"
    OPTIMIZING = "optimizing"


class RiskAppetite(StrEnum):
    CONSERVATIVE = "conservative"
    MODERATE = "moderate"
    AGGRESSIVE = "aggressive"


class IndustrySubSector(StrEnum):
    """v3 canonical 8-value enum — superset of fair_cam's 5-value enum.

    Used by:
    - Organization.industry_sub_sector column (PR η F8).
    - Phase 1.5a scenario library content filter.

    Values:
    - First 5 values mirror fair_cam.parameters.sub_sector_overlays.IndustrySubSector
      exactly (parity test enforces fair_cam ⊆ v3).
    - NUCLEAR, PROCESS_MANUFACTURING, OTHER are 1.5a-only — they exist for
      library content filtering but resolve to NAICS-2 baseline at the
      calibration layer (silent fall-through per spec §6.6).

    Spec: docs/superpowers/specs/2026-04-28-pr-eta-iris-sub-sector-overlays-design.md §6.5.
    """

    CHEMICAL_MANUFACTURING = "chemical_manufacturing"
    ELECTRIC_UTILITY = "electric_utility"
    NUCLEAR = "nuclear"
    OIL_AND_GAS = "oil_and_gas"
    OTHER = "other"
    PIPELINE = "pipeline"
    PROCESS_MANUFACTURING = "process_manufacturing"
    WATER_UTILITY = "water_utility"


# fair_cam industries that have a calibration curve. Others fall back → warning banner.
CALIBRATED_INDUSTRIES: frozenset[IndustryType] = frozenset(
    {
        IndustryType.MANUFACTURING,
        IndustryType.HEALTHCARE,
        IndustryType.FINANCIAL,
        IndustryType.RETAIL,
    }
)


class ThreatActorType(StrEnum):
    """Mirrors fair_cam.parameters.industry_calibration.ThreatActorType.
    Parity verified by tests/unit/test_enum_parity.py."""

    CYBERCRIMINALS = "cybercriminals"
    NATION_STATE = "nation_state"
    INSIDER_MALICIOUS = "insider_malicious"
    INSIDER_ACCIDENTAL = "insider_accidental"
    HACKTIVISTS = "hacktivists"
    COMPETITORS = "competitors"


class ThreatCategory(StrEnum):
    """Threat event classification per FAIR Feb 2025 taxonomy.
    OT-first commitment: includes OT_SAFETY_TAMPERING + OT_AVAILABILITY (spec §3.1)."""

    MALWARE = "malware"
    RANSOMWARE = "ransomware"
    DATA_DISCLOSURE = "data_disclosure"
    DATA_TAMPERING = "data_tampering"
    DENIAL_OF_SERVICE = "denial_of_service"
    SOCIAL_ENGINEERING = "social_engineering"
    PHYSICAL_TAMPERING = "physical_tampering"
    SUPPLY_CHAIN = "supply_chain"
    INSIDER_MISUSE = "insider_misuse"
    OT_SAFETY_TAMPERING = "ot_safety_tampering"
    OT_AVAILABILITY = "ot_availability"
    OT_INTEGRITY = "ot_integrity"  # process-integrity / manipulation-of-view (FAIR CIA "I")
    MISCELLANEOUS = "miscellaneous"


class AssetClass(StrEnum):
    """Asset categorization per FAIR Feb 2025 taxonomy (Figure 2, p7).

    FAIR canonical asset types (10): Sensitive Personal Data, IP & Trade
    Secrets Data, Co-Owned Proprietary Data, Confidential Business Information,
    Business Process Generating Revenue, Business Process Impacting Third-Party
    Revenue, Business Process Generating Cost, Product or Service,
    Cash or Cash Equivalent, Physical Assets & Facilities.

    Current v3 coverage of FAIR canonical:
    - DATA: collapsed coverage of the 4 data sub-types (Sensitive Personal,
      IP/Trade Secrets, Co-Owned Proprietary, Confidential Business Info).
      Expansion to per-sub-type values is deferred (would require row migration).
    - SYSTEMS: maps loosely to "Product or Service" (name kept for back-compat).
    - FACILITIES: maps to "Physical Assets & Facilities" (name kept for back-compat).
    - CASH_OR_EQUIVALENT, BUSINESS_PROCESS_*: FAIR canonical, added 2026-05-25
      after UAT surfaced "no way to express cash/cash-equivalent scenarios."

    v3-only additions beyond FAIR canonical:
    - PEOPLE: not in FAIR canonical; retained as a v3 convenience for HR /
      insider-risk scenarios that don't map cleanly to a FAIR business-process.
    - OT_SYSTEMS / SAFETY_SYSTEMS: OT-first commitment (spec §3.1).
    - OTHER: catch-all sentinel.
    """

    DATA = "data"
    SYSTEMS = "systems"
    PEOPLE = "people"
    FACILITIES = "facilities"
    OT_SYSTEMS = "ot_systems"
    SAFETY_SYSTEMS = "safety_systems"
    CASH_OR_EQUIVALENT = "cash_or_equivalent"
    BUSINESS_PROCESS_REVENUE = "business_process_revenue"
    BUSINESS_PROCESS_THIRD_PARTY_REVENUE = "business_process_third_party_revenue"
    BUSINESS_PROCESS_COST = "business_process_cost"
    OTHER = "other"


class FairCamSubFunction(StrEnum):
    """FAIR-CAM Standard V1.0 sub-function identifiers.

    26 values spanning 3 domains (LEC, VMC, DSC). One value is virtual
    (DSC_CORR_MISALIGNED) — no distinct control should be assigned to it;
    enforcement via Pydantic validator + DB CHECK constraint (spec §4.3).

    SLUG FREEZE: These values appear in serialized risk_analysis_runs.snapshot
    JSON and in control library seed data (PR lambda). A rename requires a data
    migration touching immutable audit records. Do not rename without a migration
    plan and spec amendment. See spec §15 for the slug rename procedure.

    Standard: FAIR Controls Analytics Model Standard V1.0 (January 2025).
    Canonical reference: docs/reference/fair-cam-standard-alignment.md §3.
    """

    # LEC — Loss Event Control (9 sub-functions)
    LEC_PREV_AVOIDANCE = "lec_prev_avoidance"  # §3.1.1 p9-10
    LEC_PREV_DETERRENCE = "lec_prev_deterrence"  # §3.1.2 p11
    LEC_PREV_RESISTANCE = "lec_prev_resistance"  # §3.1.3 p12-13
    LEC_DET_VISIBILITY = "lec_det_visibility"  # §3.2.1 p14-15
    LEC_DET_MONITORING = "lec_det_monitoring"  # §3.2.2 p16
    LEC_DET_RECOGNITION = "lec_det_recognition"  # §3.2.3 p17
    LEC_RESP_EVENT_TERMINATION = "lec_resp_event_termination"  # §3.3.1 p18-19
    LEC_RESP_RESILIENCE = "lec_resp_resilience"  # §3.3.2 p19
    LEC_RESP_LOSS_REDUCTION = "lec_resp_loss_reduction"  # §3.3.3 p20

    # VMC — Variance Management Control (6 sub-functions)
    VMC_PREV_REDUCE_CHANGE_FREQ = "vmc_prev_reduce_change_freq"  # §4.1.1 p23
    VMC_PREV_REDUCE_VARIANCE_PROB = "vmc_prev_reduce_variance_prob"  # §4.1.2 p24
    VMC_ID_THREAT_INTELLIGENCE = "vmc_id_threat_intelligence"  # §4.2.1 p25-26
    VMC_ID_CONTROL_MONITORING = "vmc_id_control_monitoring"  # §4.2.2 p26-27
    VMC_CORR_TREATMENT_SELECTION = "vmc_corr_treatment_selection"  # §4.3.1 p28
    VMC_CORR_IMPLEMENTATION = "vmc_corr_implementation"  # §4.3.2 p28-29

    # DSC — Decision Support Control (11 sub-functions; 10 distinct + 1 virtual)
    DSC_PREV_DEFINED_EXPECTATIONS = "dsc_prev_defined_expectations"  # §5.1.1 p36-37
    DSC_PREV_COMMUNICATION = "dsc_prev_communication"  # §5.1.2 p37-38
    DSC_PREV_SA_DATA_ASSET = "dsc_prev_sa_data_asset"  # §5.1.3.1.1 p38-39
    DSC_PREV_SA_DATA_THREAT = "dsc_prev_sa_data_threat"  # §5.1.3.1.2 p40
    DSC_PREV_SA_DATA_CONTROLS = "dsc_prev_sa_data_controls"  # §5.1.3.1.3 p41
    DSC_PREV_SA_ANALYSIS = "dsc_prev_sa_analysis"  # §5.1.3.2 p42-43
    DSC_PREV_SA_REPORTING = "dsc_prev_sa_reporting"  # §5.1.3.3 p43-44
    DSC_PREV_ENSURE_CAPABILITY = "dsc_prev_ensure_capability"  # §5.1.4 p44-45
    DSC_PREV_INCENTIVES = "dsc_prev_incentives"  # §5.1.5 p45-46
    DSC_ID_MISALIGNED = "dsc_id_misaligned"  # §5.2 p47-48
    DSC_CORR_MISALIGNED = "dsc_corr_misaligned"  # §5.3 p49-50 VIRTUAL


class ScenarioFieldset(StrEnum):
    """Per spec §6.2 + Arch-17 R2 (lives in models/enums.py per convention)."""

    TEF = "tef"
    VULN = "vuln"
    PL = "pl"
    SL = "sl"


class UnitType(StrEnum):
    """Unit types for FairCamSubFunction capability_value fields.

    Used by the M1 unit-type validator in ControlFunctionAssignmentDTO
    and by the OQ7 bridge skip logic in _v3_to_fair_cam_control.

    Standard: audit §2.6 unit-type table.
    """

    PROBABILITY = "probability"  # [0, 1] bounded
    PERCENT_REDUCTION = "percent_reduction"  # [0, 1] bounded
    ELAPSED_TIME = "elapsed_time"  # non-negative, no upper bound
    CURRENCY = "currency"  # non-negative, no upper bound


# Static mapping: 26 entries, one per FairCamSubFunction slug.
# Source: audit §2.6 unit-type table (docs/reference/fair-cam-standard-alignment.md).
# Used by ControlFunctionAssignmentDTO.validate_capability_value_unit (M1)
# and _v3_to_fair_cam_control bridge (OQ7 skip logic).
SUB_FUNCTION_UNITS: dict[FairCamSubFunction, UnitType] = {
    # LEC — 9 sub-functions
    FairCamSubFunction.LEC_PREV_AVOIDANCE: UnitType.PROBABILITY,
    FairCamSubFunction.LEC_PREV_DETERRENCE: UnitType.PROBABILITY,
    FairCamSubFunction.LEC_PREV_RESISTANCE: UnitType.PROBABILITY,
    FairCamSubFunction.LEC_DET_VISIBILITY: UnitType.PROBABILITY,
    FairCamSubFunction.LEC_DET_MONITORING: UnitType.ELAPSED_TIME,
    FairCamSubFunction.LEC_DET_RECOGNITION: UnitType.PROBABILITY,
    FairCamSubFunction.LEC_RESP_EVENT_TERMINATION: UnitType.ELAPSED_TIME,
    # Issue #131: reclassified ELAPSED_TIME → PROBABILITY (evidentially-deferred).
    # No primary-cited industry survey publishes a defensible median/mean
    # recovery-time benchmark for this sub-function (the original v3 default
    # τ=33d was a "3-week BCM heuristic" with no canonical source).
    FairCamSubFunction.LEC_RESP_RESILIENCE: UnitType.PROBABILITY,
    FairCamSubFunction.LEC_RESP_LOSS_REDUCTION: UnitType.CURRENCY,
    # VMC — 6 sub-functions
    FairCamSubFunction.VMC_PREV_REDUCE_CHANGE_FREQ: UnitType.PERCENT_REDUCTION,
    FairCamSubFunction.VMC_PREV_REDUCE_VARIANCE_PROB: UnitType.PERCENT_REDUCTION,
    # Issue #131: reclassified ELAPSED_TIME → PROBABILITY (evidentially-deferred).
    # Intel-feed-lag and control-drift-detection medians lack primary-cited
    # industry surveys (the original v3 defaults τ=7d were unsourced heuristics).
    FairCamSubFunction.VMC_ID_THREAT_INTELLIGENCE: UnitType.PROBABILITY,
    FairCamSubFunction.VMC_ID_CONTROL_MONITORING: UnitType.PROBABILITY,
    # Issue #131: reclassified ELAPSED_TIME → PROBABILITY (evidentially-deferred).
    # NIST SP 800-61 r3 is a process guide and doesn't publish a treatment-
    # decision-time benchmark (original v3 default τ=14d was unsourced).
    FairCamSubFunction.VMC_CORR_TREATMENT_SELECTION: UnitType.PROBABILITY,
    FairCamSubFunction.VMC_CORR_IMPLEMENTATION: UnitType.ELAPSED_TIME,
    # DSC — 11 sub-functions
    FairCamSubFunction.DSC_PREV_DEFINED_EXPECTATIONS: UnitType.PROBABILITY,
    FairCamSubFunction.DSC_PREV_COMMUNICATION: UnitType.PROBABILITY,
    FairCamSubFunction.DSC_PREV_SA_DATA_ASSET: UnitType.PROBABILITY,
    FairCamSubFunction.DSC_PREV_SA_DATA_THREAT: UnitType.PROBABILITY,
    FairCamSubFunction.DSC_PREV_SA_DATA_CONTROLS: UnitType.PROBABILITY,
    FairCamSubFunction.DSC_PREV_SA_ANALYSIS: UnitType.PROBABILITY,
    FairCamSubFunction.DSC_PREV_SA_REPORTING: UnitType.PROBABILITY,
    FairCamSubFunction.DSC_PREV_ENSURE_CAPABILITY: UnitType.PROBABILITY,
    FairCamSubFunction.DSC_PREV_INCENTIVES: UnitType.PROBABILITY,
    # Issue #131: reclassified ELAPSED_TIME → PROBABILITY (evidentially-deferred).
    # "Quarterly governance review cycle" was hand-waving; no primary citation.
    FairCamSubFunction.DSC_ID_MISALIGNED: UnitType.PROBABILITY,
    # Issue #131: reclassified ELAPSED_TIME → PROBABILITY (standard-virtual per
    # FAIR-CAM §5.3 — "no distinct controls serve this function"; reclassified
    # for parity with sibling DSC_ID_MISALIGNED. DTO validator (schemas/control.py
    # reject_virtual_unless_derived) still rejects writes unless
    # derived_from_assignment_id is set.
    FairCamSubFunction.DSC_CORR_MISALIGNED: UnitType.PROBABILITY,
}


# Human-readable descriptions for each FairCamSubFunction. Rendered as
# helper text under the combobox + as the listbox option subtitle on
# the controls form. Format per entry: short label — definition.
# Examples: <concrete controls>. Measured as <unit semantics>.
# FAIR-CAM §<section>. Sourced from FAIR-CAM Standard §3-5 definitions
# (see enum-class line comments for the per-sub-function section refs).
SUB_FUNCTION_DESCRIPTIONS: dict[FairCamSubFunction, str] = {
    # LEC — Loss Event Control (manages threat events directly)
    FairCamSubFunction.LEC_PREV_AVOIDANCE: (
        "Avoidance — remove the opportunity for a threat event entirely. "
        "Examples: decommission the service, take a system off-network, "
        "stop collecting sensitive data, choose a non-vulnerable design. "
        "Measured as probability [0, 1] that the opportunity is removed "
        "from the threat actor. FAIR-CAM §3.1.1."
    ),
    FairCamSubFunction.LEC_PREV_DETERRENCE: (
        "Deterrence — discourage threat actors from initiating the event. "
        "Examples: visible CCTV + warning signs, legal-action notices, "
        "honeypots that hint at instrumentation, public prosecution records. "
        "Measured as probability [0, 1] the actor chooses NOT to attempt "
        "after observing the deterrent. FAIR-CAM §3.1.2."
    ),
    FairCamSubFunction.LEC_PREV_RESISTANCE: (
        "Resistance — withstand the threat's force once the actor initiates. "
        "Examples: MFA, encryption at rest + in transit, network segmentation, "
        "least-privilege IAM, EDR with kernel-level enforcement, patched OS, "
        "WAF rules. Measured as probability [0, 1] the control resists when "
        "triggered (1.0 = always resists). FAIR-CAM §3.1.3."
    ),
    FairCamSubFunction.LEC_DET_VISIBILITY: (
        "Visibility — make threat-relevant activity observable. "
        "Examples: OS + application logging, NetFlow capture, EDR telemetry, "
        "auth events, SaaS audit-log export, network-tap mirrors. Measured "
        "as probability [0, 1] that an event of interest produces a signal "
        "that COULD be seen (whether anyone watches is separate). FAIR-CAM §3.2.1."
    ),
    FairCamSubFunction.LEC_DET_MONITORING: (
        "Monitoring — continuously watch the observable signals. "
        "Examples: 24/7 SOC, SIEM correlation rules, anomaly-detection ML, "
        "scheduled log review, automated alert pipelines. Measured as "
        "ELAPSED_TIME (days) — mean-time-to-investigate from signal arrival. "
        "Shorter = faster catch. FAIR-CAM §3.2.2."
    ),
    FairCamSubFunction.LEC_DET_RECOGNITION: (
        "Recognition — correctly classify a signal as an actual threat event "
        "(not a false positive). Examples: tuned alert thresholds, analyst "
        "triage playbooks, ML classifiers, threat-hunting hypotheses. "
        "Measured as probability [0, 1] a real event is recognised as such "
        "(true-positive rate). FAIR-CAM §3.2.3."
    ),
    FairCamSubFunction.LEC_RESP_EVENT_TERMINATION: (
        "Event termination — stop the in-progress threat event. Examples: "
        "kill malicious process, block source IP at firewall, isolate "
        "compromised host, revoke session tokens, disable compromised account. "
        "Measured as ELAPSED_TIME (days) — mean-time-to-contain. Shorter = "
        "less damage accumulated. FAIR-CAM §3.3.1."
    ),
    FairCamSubFunction.LEC_RESP_RESILIENCE: (
        "Resilience — keep operating during a loss event. Examples: hot/warm "
        "DR site, multi-region failover, redundant providers, documented BCP "
        "with tested runbooks, automated DB replication. Measured as "
        "probability [0, 1] of continued operation during the event "
        "(uptime fraction). FAIR-CAM §3.3.2."
    ),
    FairCamSubFunction.LEC_RESP_LOSS_REDUCTION: (
        "Loss reduction — reduce per-event dollar loss. Examples: cyber "
        "insurance payout, restore-from-backup procedure, third-party IR "
        "retainer, breach-notification template, legal hold tooling. "
        "Measured as CURRENCY — dollar reduction per loss event "
        "(subtractor against secondary loss). FAIR-CAM §3.3.3."
    ),
    # VMC — Variance Management Control (manages drift in the protective landscape)
    FairCamSubFunction.VMC_PREV_REDUCE_CHANGE_FREQ: (
        "Reduce change frequency — minimise how often the protected environment "
        "changes, so there are fewer chances for a change to introduce a "
        "vulnerability. Examples: change-management boards, blackout windows, "
        "code-freeze periods, infra-as-code review gates. Measured as "
        "probability [0, 1] a proposed change is deferred / batched. FAIR-CAM §4.1.1."
    ),
    FairCamSubFunction.VMC_PREV_REDUCE_VARIANCE_PROB: (
        "Reduce variance probability — lower the chance any given change "
        "introduces a vulnerability. Examples: pre-merge SAST + dependency "
        "scanning, mandatory code review, automated security tests in CI, "
        "configuration-as-code linting. Measured as probability [0, 1] a "
        "change passes WITHOUT introducing variance. FAIR-CAM §4.1.2."
    ),
    FairCamSubFunction.VMC_ID_THREAT_INTELLIGENCE: (
        "Threat intelligence — identify new threats via external + internal "
        "intel. Examples: commercial TI feeds, ISAC participation, internal "
        "honeypot telemetry, vendor security bulletins, threat-modelling "
        "workshops. Measured as probability [0, 1] a novel threat is "
        "identified within the relevant intel cycle. FAIR-CAM §4.2.1."
    ),
    FairCamSubFunction.VMC_ID_CONTROL_MONITORING: (
        "Control monitoring — detect when an existing control has failed, "
        "degraded, or drifted from baseline. Examples: control-effectiveness "
        "dashboards, configuration-drift detection, periodic control testing, "
        "EDR-coverage gap reports. Measured as probability [0, 1] a control "
        "failure is detected within a meaningful window. FAIR-CAM §4.2.2."
    ),
    FairCamSubFunction.VMC_CORR_TREATMENT_SELECTION: (
        "Treatment selection — choose the right corrective action for an "
        "identified variance. Examples: severity-based playbooks, risk-based "
        "patch prioritisation, vulnerability-management workflow, exception "
        "process with sign-off. Measured as probability [0, 1] the selected "
        "treatment actually addresses the variance. FAIR-CAM §4.3.1."
    ),
    FairCamSubFunction.VMC_CORR_IMPLEMENTATION: (
        "Implementation — execute the chosen correction. Examples: patch "
        "deployment pipeline, hotfix release process, configuration push, "
        "vulnerability-remediation SLAs. Measured as ELAPSED_TIME (days) — "
        "mean-time-to-remediate from when treatment is selected. Shorter = "
        "smaller exposure window. FAIR-CAM §4.3.2."
    ),
    # DSC — Decision Support Control (manages alignment between decisions and risk)
    FairCamSubFunction.DSC_PREV_DEFINED_EXPECTATIONS: (
        "Defined expectations — set explicit, written behaviour standards "
        "for decision-makers. Examples: acceptable-use policy, data-handling "
        "standards, vendor-selection criteria, risk-tolerance statements, "
        "code-of-conduct. Measured as probability [0, 1] a decision has an "
        "applicable, current expectation to anchor on. FAIR-CAM §5.1.1."
    ),
    FairCamSubFunction.DSC_PREV_COMMUNICATION: (
        "Communication — convey expectations to decision-makers so they "
        "actually know them. Examples: onboarding training, periodic "
        "refreshers, just-in-time policy reminders, internal comms cadence, "
        "policy-change announcements. Measured as probability [0, 1] the "
        "decision-maker has received + understood the relevant expectation. "
        "FAIR-CAM §5.1.2."
    ),
    FairCamSubFunction.DSC_PREV_SA_DATA_ASSET: (
        "Situational awareness · asset data — inventory + classify what is "
        "at risk. Examples: CMDB, software bill of materials, data-asset "
        "catalogue with sensitivity tags, system criticality ratings. "
        "Measured as probability [0, 1] a real asset is correctly represented "
        "in the inventory at decision time. FAIR-CAM §5.1.3.1.1."
    ),
    FairCamSubFunction.DSC_PREV_SA_DATA_THREAT: (
        "Situational awareness · threat data — collect data about threats "
        "relevant to the org. Examples: threat-actor profiles, MITRE "
        "ATT&CK mapping, sector-specific intel, internal incident history. "
        "Measured as probability [0, 1] relevant threat data is available "
        "when a decision needs it. FAIR-CAM §5.1.3.1.2."
    ),
    FairCamSubFunction.DSC_PREV_SA_DATA_CONTROLS: (
        "Situational awareness · controls data — collect data about existing "
        "controls. Examples: control-catalogue with effectiveness ratings, "
        "control-coverage maps, recent audit results, pen-test findings. "
        "Measured as probability [0, 1] the decision-maker can see the "
        "current control state. FAIR-CAM §5.1.3.1.3."
    ),
    FairCamSubFunction.DSC_PREV_SA_ANALYSIS: (
        "Situational awareness · analysis — turn raw data into risk insights. "
        "Examples: FAIR quantitative risk analyses, qualitative risk register, "
        "scenario modelling, Monte Carlo simulation, attack-path analysis. "
        "Measured as probability [0, 1] available data is converted into an "
        "actionable insight. FAIR-CAM §5.1.3.2."
    ),
    FairCamSubFunction.DSC_PREV_SA_REPORTING: (
        "Situational awareness · reporting — deliver insights to "
        "decision-makers in usable form. Examples: exec dashboards, monthly "
        "risk reports, board briefings, just-in-time decision-support tools. "
        "Measured as probability [0, 1] insights reach the right decision-maker "
        "in time. FAIR-CAM §5.1.3.3."
    ),
    FairCamSubFunction.DSC_PREV_ENSURE_CAPABILITY: (
        "Ensure capability — give decision-makers the skills + tools to "
        "decide well. Examples: security-training program, decision-support "
        "tooling, expert-on-call rotation, hiring competent leadership. "
        "Measured as probability [0, 1] the decision-maker is equipped to "
        "make the decision well. FAIR-CAM §5.1.4."
    ),
    FairCamSubFunction.DSC_PREV_INCENTIVES: (
        "Incentives — align incentive structures with desired decisions. "
        "Examples: bonus tied to security KPIs, penalties for policy "
        "violation, recognition for good security choices, performance "
        "reviews that weight risk-aware behaviour. Measured as probability "
        "[0, 1] the incentive system motivates the desired choice. "
        "FAIR-CAM §5.1.5."
    ),
    FairCamSubFunction.DSC_ID_MISALIGNED: (
        "Identify misalignment — detect when actual decisions deviate from "
        "expectations. Examples: audit reviews, exception-tracking, policy-"
        "compliance scans, governance committee review cycles. Measured as "
        "probability [0, 1] a misaligned decision is identified within a "
        "meaningful window. FAIR-CAM §5.2."
    ),
    FairCamSubFunction.DSC_CORR_MISALIGNED: (
        "Correct misalignment — re-align decision-makers when their decisions "
        "have drifted from expectations. Examples: retraining, reassignment, "
        "enforcement action, escalation to leadership. VIRTUAL sub-function "
        "per FAIR-CAM §5.3 — no distinct controls of its own; requires "
        "derived_from_assignment_id pointing at the governing LEC Response "
        "or VMC Variance Correction control."
    ),
}


def subfunction_to_domain(subfn: FairCamSubFunction) -> ControlDomain:
    """Decode a FairCamSubFunction's domain from its slug prefix.

    Used by the importer (services/controls_importer.py) to derive
    Control.domain = first recognized sub-function's domain when a CSV
    row carries multiple sub-function paths. Pure decoder — no I/O.

    Co-located here with SUB_FUNCTION_UNITS because both are
    enum → derived-attribute mappings.
    """
    slug = subfn.value
    if slug.startswith("lec_"):
        return ControlDomain.LOSS_EVENT
    if slug.startswith("vmc_"):
        return ControlDomain.VARIANCE_MANAGEMENT
    if slug.startswith("dsc_"):
        return ControlDomain.DECISION_SUPPORT
    raise ValueError(f"Unknown sub-function domain prefix: {subfn!r}")


def sub_functions_for_domain(domain: ControlDomain) -> list[FairCamSubFunction]:
    """Reverse of subfunction_to_domain: enumerate the sub-functions in a domain.

    Used by services.controls.list_controls(domain=...) to build an
    `IN (...)` predicate against ControlFunctionAssignment.sub_function
    when filtering controls by their (derived) domain set. Pure decoder —
    no I/O. (issue #90)
    """
    return [s for s in FairCamSubFunction if subfunction_to_domain(s) == domain]
