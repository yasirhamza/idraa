"""Hardcoded scenario-context question templates per MD-5. Per spec §7.4."""

from __future__ import annotations

from dataclasses import dataclass

from idraa.models.enums import AssetClass, ThreatActorType

QUESTION_TEMPLATES = {
    "tef": (
        "In a typical year, how often might {threat_actor_phrase} try to "
        "compromise your {asset_class_phrase}{attack_vector_phrase}?"
    ),
    # Inherent-susceptibility framing (methodology/vuln-inherent-framing):
    # vulnerability is the asset's CONTROL-NAIVE inherent weakness, not a
    # residual-of-current-controls number. The FAIR-CAM control layer reduces it
    # separately, so eliciting it net-of-controls here would double-count the
    # controls (once in the analyst's head, once via the control multiplier).
    "vuln": (
        "If the attempt happens, how likely is the attacker to succeed against "
        "the asset's inherent weaknesses, before any of your mitigating controls?"
    ),
    "pl": "If the attack succeeds, what's the direct financial loss?",
    # Plan-gate M-N2: event-conditional (matches PL), not annualized.
    "sl": "If the attack succeeds, what's the indirect or downstream loss?",
}


@dataclass(frozen=True)
class ScenarioContext:
    threat_actor_type: ThreatActorType | None
    attack_vector: str | None
    asset_class: AssetClass | None


# Arch-3 PR1 fix: enum values must match models/enums.py:ThreatActorType actual definition.
# Verify against `grep -n "class ThreatActorType" src/idraa/models/enums.py` before T4.
_THREAT_ACTOR_PHRASES = {
    ThreatActorType.CYBERCRIMINALS: "cybercriminals",
    ThreatActorType.NATION_STATE: "a nation-state actor",
    ThreatActorType.INSIDER_MALICIOUS: "a malicious insider",
    ThreatActorType.INSIDER_ACCIDENTAL: "an accidental insider",
    ThreatActorType.HACKTIVISTS: "hacktivists",
    ThreatActorType.COMPETITORS: "a competitor",
}


def humanize_threat_actor(actor: ThreatActorType | None) -> str:
    if actor is None:
        return "an attacker"
    return _THREAT_ACTOR_PHRASES.get(actor, "an attacker")


def humanize_attack_vector(vector: str | None) -> str:
    """Render the attack-vector clause for the TEF question.

    Returns " via phishing" (leading space, set) or "" (unset) so the
    template reads "... compromise your OT/ICS systems via phishing?" or
    "... compromise your OT/ICS systems?" with no dangling preposition or
    double space.
    """
    if vector is None:
        return ""
    # attack_vector is stored as an enum slug (e.g. "email_phishing"); humanize
    # the underscores so the rendered question reads "via email phishing".
    return f" via {vector.strip().lower().replace('_', ' ')}"


# Arch-4 PR1 fix: enum values must match models/enums.py:AssetClass actual definition.
# Verify against `grep -n "class AssetClass" src/idraa/models/enums.py` before T4.
_ASSET_CLASS_PHRASES = {
    AssetClass.DATA: "data",
    AssetClass.SYSTEMS: "systems",
    AssetClass.PEOPLE: "personnel",
    AssetClass.FACILITIES: "facilities",
    AssetClass.OT_SYSTEMS: "OT/ICS systems",
    AssetClass.SAFETY_SYSTEMS: "safety-instrumented systems",
    AssetClass.CASH_OR_EQUIVALENT: "cash or cash-equivalent assets",
    AssetClass.BUSINESS_PROCESS_REVENUE: "revenue-generating business processes",
    AssetClass.BUSINESS_PROCESS_THIRD_PARTY_REVENUE: "third-party-revenue-impacting business processes",
    AssetClass.BUSINESS_PROCESS_COST: "cost-generating business processes",
    # Plan-as-written had AssetClass.OTHER -> "asset", but that collides with the
    # None-fallback sentinel and trips the "every enum value mapped" regression
    # test. Distinct phrase so the test's intent (every enum gets an explicit,
    # distinguishable phrase) holds.
    AssetClass.OTHER: "other assets",
}


def humanize_asset_class(asset: AssetClass | None) -> str:
    if asset is None:
        return "asset"
    return _ASSET_CLASS_PHRASES.get(asset, "asset")


def render_question(fieldset: str, ctx: ScenarioContext) -> str:
    template = QUESTION_TEMPLATES[fieldset]
    return template.format(
        threat_actor_phrase=humanize_threat_actor(ctx.threat_actor_type),
        attack_vector_phrase=humanize_attack_vector(ctx.attack_vector),
        asset_class_phrase=humanize_asset_class(ctx.asset_class),
    )


# Operator-facing explainers that moved out of the question copy into (i)
# tooltips per the 2026-05-28 step-3 redesign (spec §5). Keyed by fieldset.
QUESTION_TOOLTIPS = {
    "tef": (
        # Plan-gate M-N1: restate the per-year basis so a tooltip-only reader
        # doesn't anchor on a non-annual figure.
        "Each SME gives a low (5%) and high (95%) — the range they're 90% "
        "sure the true number of attempts per year falls inside."
    ),
    "vuln": (
        "Estimate the asset's INHERENT susceptibility, before your controls — "
        "they're modelled separately and reduce this. Enter 0 to 1: 0 = the "
        "threat action essentially never succeeds, 1 = always."
    ),
    "pl": (
        "What you'd spend responding — incident response, forensics, customer "
        "notification, legal counsel, replacement hardware."
    ),
    "sl": (
        "Regulatory fines, lost revenue, customer churn, reputational damage, "
        "increased insurance premiums."
    ),
}

# Page → (fieldset_key, legend label) tuples for the split step-3 wizard.
LIKELIHOOD_FIELDSETS = [("tef", "Threat event frequency"), ("vuln", "Vulnerability")]
IMPACT_FIELDSETS = [("pl", "Primary loss"), ("sl", "Secondary loss (optional)")]
