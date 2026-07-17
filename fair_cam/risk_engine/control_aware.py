"""
Control-Aware Risk Calculator

This module integrates control effectiveness into FAIR risk calculations,
providing dynamic, real-time risk assessment capabilities.
"""

import logging
from collections.abc import Mapping
from dataclasses import dataclass

# Re-exported for back-compat (#130 Task 0): `_NON_OPEFF_SUB_FUNCTIONS` moved to
# composition_topology to break the control_aware <-> group_composition import
# cycle (plan-gate B-arch-1). Existing importers of this name keep working.
from ..models.composition_topology import (
    _NON_OPEFF_SUB_FUNCTIONS as _NON_OPEFF_SUB_FUNCTIONS,
)
from ..models.composition_topology import (
    GROUP_NODE_MAPPING,
    BooleanGroup,
    NodeMapping,
)
from ..models.risk_enhanced import (
    ConfidenceMetrics,
    ControlEnhancedRisk,
    FAIRRisk,
)

# #130 Task 3: the Layer-2 diagnostic now delegates Boolean group composition to
# the single shared routine (consumed by the engine ALE path too) so they cannot
# drift (D2). Import direction is one-way: control_aware -> group_composition.
from .group_composition import GroupComposition

# FAIR node keys the engine adjusts. Kept here (not derived) so the multiplier
# accumulator initialises every node to identity even when no group targets it.
_NODE_KEYS: tuple[str, ...] = (
    "threat_event_frequency",
    "vulnerability",
    "primary_loss",
    "secondary_loss",
)


def _group_comp_to_node_multipliers(
    group_comp: GroupComposition,
    node_mapping: Mapping[BooleanGroup, NodeMapping] = GROUP_NODE_MAPPING,
    *,
    availability_self_detection: bool = False,
) -> dict[str, float]:
    """Map composed group effectivenesses to cumulative per-FAIR-node multipliers.

    #130 Task 5 (full migration). For each composed group `g` with effectiveness
    `E` and per-target weight `w` (from `GROUP_NODE_MAPPING`), the node multiplier
    is `1 - E·w`; multipliers compose multiplicatively across groups onto each
    target node. Returns a dict over `_NODE_KEYS`, each defaulting to identity
    (1.0) when no group reduces that node.

    Response gating (D4 + D8): the `LEC_RESPONSE` group's effectiveness is
    SUBSTITUTED by the `LEC_DETECTION_RESPONSE_PAIR` effectiveness (Detection-gated
    weak-AND). When the pair eff is `None` (no Detection present), Response
    contributes identity (no magnitude benefit). The bare `LEC_DETECTION` entry
    (empty targets) and the `LEC_DETECTION_RESPONSE_PAIR` entry are skipped as
    standalone applications — Detection has no standalone node, and the pair is
    applied via this Response substitution — so the gate is never double-counted.

    Effect-type-aware recovery gate (Slice 1, availability only): when
    ``availability_self_detection`` is True, the ``LEC_RESPONSE`` group's
    magnitude multiplier is credited from the RAW ``LEC_RESPONSE`` group
    effectiveness instead of the detection-gated ``LEC_DETECTION_RESPONSE_PAIR``.
    Rationale: an availability event manifests observably (the power-outage /
    downed-production-line Resilience example, FAIR-CAM §3.3.2 p.19), so the
    Detection->Response AND precondition (§3.3 p.18) is intrinsically satisfied
    and recovery credit does not require a co-present Detection control. This is
    a Standard-*consistent interpretation*, SCOPED TO AVAILABILITY EFFECTS ONLY —
    NEVER generalized to stealth Confidentiality/Integrity events, which stay
    detection-gated (generalizing would be Standard-deviant per §3.3 p.18). The
    raw ``LEC_RESPONSE`` effectiveness is a WEAK_AND (§3.3.1 p.19), so a single
    deficient Response member diminishes but does not zero the credit. The flag
    is a structural boolean, not a calibrated weight — no new numeric parameter.
    Self-detection is modeled as instantaneous / perfect (``detection_eff = 1.0``);
    detection-TIMELINESS grading (a partial self-detection efficiency) is deferred
    to Slice 3 (§3.2.2 p.16). Slice 1 credits the raw Response group in full.

    VMC single-application (reviewer BLOCKER fix): the §4 Identification∧Correction
    AND is one composed effect that must reach the vulnerability node EXACTLY ONCE.
    The `VMC_IDENTIFICATION` and `VMC_CORRECTION` leaf entries therefore carry empty
    targets in `GROUP_NODE_MAPPING` (skipped here by the `not mapping.targets`
    guard, mirroring `LEC_DETECTION`); the composed VMC effect is applied solely
    through `VMC_IDENTIFICATION_CORRECTION_PAIR`. Without this, routing the two
    leaves AND the pair to vulnerability would apply the same §4 effect three times
    (vuln_mult = 0.7³ ≈ 0.343 instead of the single-application 0.7 at full leaf
    effectiveness). `VMC_VARIANCE_PREVENTION` is a distinct OR group (no pair) and
    applies once. Unlike Response, the VMC pair maps to its own node directly (no
    substitution): the generic loop applies it because it is neither
    `LEC_DETECTION_RESPONSE_PAIR` nor `LEC_RESPONSE`, and an absent child leaf makes
    the pair eff `None` → identity (a VMC control needs BOTH ID and Correction to
    reduce vulnerability, the §4 AND semantics).
    """
    multipliers: dict[str, float] = dict.fromkeys(_NODE_KEYS, 1.0)

    for group, mapping in node_mapping.items():
        if not mapping.targets:
            # e.g. LEC_DETECTION — no standalone node (it gates Response).
            continue
        if group == BooleanGroup.LEC_DETECTION_RESPONSE_PAIR:
            # Applied via the LEC_RESPONSE substitution below; skip here so the
            # Detection->Response gate is applied exactly once.
            continue

        if group == BooleanGroup.LEC_RESPONSE:
            if availability_self_detection:
                # §3.3.2 p.19 — availability events self-manifest; the
                # Detection->Response AND precondition is intrinsically satisfied,
                # so credit the RAW Response group effectiveness (no co-present
                # Detection required). Availability-scoped; never stealth (§3.3 p.18).
                effectiveness = group_comp.group_effectiveness.get(BooleanGroup.LEC_RESPONSE)
            else:
                # D8 (§3.3 p.18): stealth C/I events require Detection — use the
                # AND-pair effectiveness, identity when no Detection is present.
                effectiveness = group_comp.group_effectiveness.get(
                    BooleanGroup.LEC_DETECTION_RESPONSE_PAIR
                )
        else:
            effectiveness = group_comp.group_effectiveness.get(group)

        if effectiveness is None:
            continue  # no operands / gate absent -> identity multiplier

        for target in mapping.targets:
            weight = mapping.weights[target]
            multipliers[target] *= 1.0 - effectiveness * weight

    return multipliers


logger = logging.getLogger(__name__)


@dataclass
class AggregateEnhancedRisk:
    """Aggregate-level FAIR-CAM risk model -- elementwise rollup of N scenarios.

    Symmetric with ControlEnhancedRisk on the single-scenario side. The rollup
    is produced natively by ``NativeControlAwareRiskCalculator`` (epic #324: the
    pyfair FairMetaModel that previously produced it was removed) — the
    aggregate sample array is the elementwise sum of the per-scenario base /
    residual sample arrays. Contains only FAIR-CAM artefacts; portfolio
    decomposition metrics (contribution %, diversification benefit, top
    contributors) are derived at the consumer layer from raw per_scenario data
    per the PR psi + PR phi methodology cleanup.
    """

    # Per-scenario constituent details (full SINGLE-shape per scenario)
    per_scenario: list[ControlEnhancedRisk]

    # Aggregate rollups -- full FAIRRisk with sample arrays (elementwise sum)
    aggregate_without_controls: FAIRRisk  # sum of per-scenario BASE sample arrays
    aggregate_with_controls: FAIRRisk  # sum of per-scenario RESIDUAL sample arrays

    # Executive headline (the design-doc 1.4b Control Value metric)
    control_value_dollars: float  # = aggregate_without.ALE - aggregate_with.ALE
    control_value_percent: float  # = control_value_dollars / aggregate_without.ALE * 100

    # Aggregate-level CI (computed from aggregate_with_controls.simulation_results)
    confidence_intervals: ConfidenceMetrics

    # Metadata
    n_scenarios: int
    n_simulations: int  # unified across all scenarios
