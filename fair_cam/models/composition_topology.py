"""Standard §2.5 + §3-§5 Boolean composition topology.

Defines which sub-functions compose under which operator (OR / AND / weak-AND).
The audit doc at `docs/reference/fair-cam-standard-alignment.md` §3 table is
the canonical source.

This module is data-only; the operator implementations live in
`fair_cam/composition.py` (T5).
"""

from dataclasses import dataclass
from enum import StrEnum

from .sub_function import SUB_FUNCTION_UNITS, FairCamSubFunction, UnitType


class GroupType(StrEnum):
    OR = "or"
    AND = "and"
    WEAK_AND = "weak_and"


class BooleanGroup(StrEnum):
    LEC_PREVENTION = "lec_prevention"
    LEC_DETECTION = "lec_detection"
    LEC_RESPONSE = "lec_response"
    LEC_DETECTION_RESPONSE_PAIR = "lec_detection_response_pair"
    VMC_VARIANCE_PREVENTION = "vmc_variance_prevention"
    VMC_IDENTIFICATION = "vmc_identification"
    VMC_CORRECTION = "vmc_correction"
    VMC_IDENTIFICATION_CORRECTION_PAIR = "vmc_identification_correction_pair"
    DSC_PREVENTION = "dsc_prevention"
    DSC_IDENTIFICATION_CORRECTION_PAIR = "dsc_identification_correction_pair"


GROUP_TYPE: dict[BooleanGroup, GroupType] = {
    BooleanGroup.LEC_PREVENTION: GroupType.OR,
    BooleanGroup.LEC_DETECTION: GroupType.AND,
    BooleanGroup.LEC_RESPONSE: GroupType.WEAK_AND,
    BooleanGroup.LEC_DETECTION_RESPONSE_PAIR: GroupType.AND,
    BooleanGroup.VMC_VARIANCE_PREVENTION: GroupType.OR,
    # Slice 2 D3 — v3 arithmetic choice where NO intra-pair operator is
    # prescribed: §4.2 p.25 has TI and controls-monitoring identifying
    # DIFFERENT variance sources (threat-landscape vs the controls
    # themselves) — complementary coverage, union approximated by OR. Each
    # member's prescribed "[...] Boolean AND with Variance Correction"
    # (condensed; §4.2.1 p.26, §4.2.2 p.27) is preserved by the id∧corr pair
    # AND below.
    BooleanGroup.VMC_IDENTIFICATION: GroupType.OR,
    # PRESCRIBED: §4.3.1/§4.3.2 p.28 (Treatment Selection AND
    # Implementation). Slice 2 keeps the AND; the implementation-gated
    # handling of an ABSENT selection member (a documented deviation from
    # 0.0-padding, justified in the spec D3) lives in the meta composition
    # (group_composition.py), not in this table.
    BooleanGroup.VMC_CORRECTION: GroupType.AND,
    # PRESCRIBED: §4 p.21 ("Variance Identification and Variance Correction
    # have a Boolean AND relationship to one another").
    BooleanGroup.VMC_IDENTIFICATION_CORRECTION_PAIR: GroupType.AND,
    # Slice 2 D3 — documented DEVIATION from the nine §5.1.x pp.36-45
    # Boolean-AND prescriptions: the Standard's AND gloss describes
    # degradation, not total inhibition, and strict product with 0.0-padded
    # absent members zeroes every real org. Mean-of-present overstates
    # E_dsc for sparse authoring — accepted, bounded by kappa*(1-r0), and
    # the whole DSC channel is a labeled v3 proxy (spec D3).
    BooleanGroup.DSC_PREVENTION: GroupType.WEAK_AND,
    # PRESCRIBED: §5 p.30 ("both must exist in order to mitigate the risk
    # of poor decisions").
    BooleanGroup.DSC_IDENTIFICATION_CORRECTION_PAIR: GroupType.AND,
}


GROUP_MEMBERSHIP: dict[BooleanGroup, set[FairCamSubFunction]] = {
    BooleanGroup.LEC_PREVENTION: {
        FairCamSubFunction.LEC_PREV_AVOIDANCE,
        FairCamSubFunction.LEC_PREV_DETERRENCE,
        FairCamSubFunction.LEC_PREV_RESISTANCE,
    },
    BooleanGroup.LEC_DETECTION: {
        FairCamSubFunction.LEC_DET_VISIBILITY,
        FairCamSubFunction.LEC_DET_MONITORING,
        FairCamSubFunction.LEC_DET_RECOGNITION,
    },
    BooleanGroup.LEC_RESPONSE: {
        FairCamSubFunction.LEC_RESP_EVENT_TERMINATION,
        FairCamSubFunction.LEC_RESP_RESILIENCE,
        FairCamSubFunction.LEC_RESP_LOSS_REDUCTION,
    },
    # Detection-Response AND-pair has no sub-function members of its own;
    # it composes the LEC_DETECTION group output with the LEC_RESPONSE group output.
    # Members are the pair's two child groups, modeled at composition time.
    BooleanGroup.LEC_DETECTION_RESPONSE_PAIR: set(),
    BooleanGroup.VMC_VARIANCE_PREVENTION: {
        FairCamSubFunction.VMC_PREV_REDUCE_CHANGE_FREQ,
        FairCamSubFunction.VMC_PREV_REDUCE_VARIANCE_PROB,
    },
    BooleanGroup.VMC_IDENTIFICATION: {
        FairCamSubFunction.VMC_ID_THREAT_INTELLIGENCE,
        FairCamSubFunction.VMC_ID_CONTROL_MONITORING,
    },
    BooleanGroup.VMC_CORRECTION: {
        FairCamSubFunction.VMC_CORR_TREATMENT_SELECTION,
        FairCamSubFunction.VMC_CORR_IMPLEMENTATION,
    },
    BooleanGroup.VMC_IDENTIFICATION_CORRECTION_PAIR: set(),
    BooleanGroup.DSC_PREVENTION: {
        FairCamSubFunction.DSC_PREV_DEFINED_EXPECTATIONS,
        FairCamSubFunction.DSC_PREV_COMMUNICATION,
        FairCamSubFunction.DSC_PREV_SA_DATA_ASSET,
        FairCamSubFunction.DSC_PREV_SA_DATA_THREAT,
        FairCamSubFunction.DSC_PREV_SA_DATA_CONTROLS,
        FairCamSubFunction.DSC_PREV_SA_ANALYSIS,
        FairCamSubFunction.DSC_PREV_SA_REPORTING,
        FairCamSubFunction.DSC_PREV_ENSURE_CAPABILITY,
        FairCamSubFunction.DSC_PREV_INCENTIVES,
    },
    BooleanGroup.DSC_IDENTIFICATION_CORRECTION_PAIR: {
        FairCamSubFunction.DSC_ID_MISALIGNED,
        FairCamSubFunction.DSC_CORR_MISALIGNED,
    },
}


_REVERSE: dict[FairCamSubFunction, BooleanGroup] = {
    sf: group for group, members in GROUP_MEMBERSHIP.items() for sf in members
}


def sub_function_to_group(sf: FairCamSubFunction) -> BooleanGroup:
    """Return the leaf Boolean group containing this sub-function.

    Note: AND-pair groups (LEC_DETECTION_RESPONSE_PAIR,
    VMC_IDENTIFICATION_CORRECTION_PAIR) compose group outputs, not raw
    sub-functions. This helper returns only leaf groups.
    """
    return _REVERSE[sf]


# Sub-functions excluded from Layer-2 opeff composition (no opeff semantic).
#
# Moved here from `control_aware.py` in #130 Task 0 (plan-gate B-arch-1): it is
# topology DATA derived purely from `SUB_FUNCTION_UNITS`, with zero dependency on
# the engine. Living here lets the shared composition routine
# (`risk_engine/group_composition.py`, Task 2) import it without re-entering
# `control_aware`, breaking the `control_aware -> group_composition ->
# control_aware` cycle. `control_aware` re-exports it for back-compat.
#
# Only CURRENCY (LEC_RESP_LOSS_REDUCTION) is excluded from the opeff path: it is
# a per-event dollar subtractor, not an effectiveness probability (#130 D3).
# ELAPSED_TIME sub-functions DO contribute opeffs via two-branch math (PR μ.1).
#
# DISTINCT from `sub_function.TIME_UNIT_EXCLUDED` (= ELAPSED_TIME + CURRENCY):
# `_NON_OPEFF_SUB_FUNCTIONS` is CURRENCY-only and is a PROPER SUBSET of
# `TIME_UNIT_EXCLUDED`. Do NOT collapse the two into one constant — a "dedupe"
# that did so would wrongly drop ELAPSED_TIME opeffs from composition. The
# proper-subset relationship is pinned by a test (#130 Task 0, NIT NEW-5).
_NON_OPEFF_SUB_FUNCTIONS: frozenset[FairCamSubFunction] = frozenset(
    sf for sf, ut in SUB_FUNCTION_UNITS.items() if ut == UnitType.CURRENCY
)


# Pair-group topology — moved here from `control_aware.py` in #130 Task 0.
#
# AND-pair groups have NO sub-function members of their own (empty
# GROUP_MEMBERSHIP); they compose the outputs of two LEAF groups. PAIR_RECIPES
# maps each pair group to its (left, right) child groups; PAIR_GROUPS is the key
# set. The shared composition routine (Task 2) and the Layer-2 diagnostic both
# consume these so engine and diagnostic share ONE pair composition (#130 D2/D8).
PAIR_RECIPES: dict[BooleanGroup, tuple[BooleanGroup, BooleanGroup]] = {
    BooleanGroup.LEC_DETECTION_RESPONSE_PAIR: (
        BooleanGroup.LEC_DETECTION,
        BooleanGroup.LEC_RESPONSE,
    ),
    BooleanGroup.VMC_IDENTIFICATION_CORRECTION_PAIR: (
        BooleanGroup.VMC_IDENTIFICATION,
        BooleanGroup.VMC_CORRECTION,
    ),
}

PAIR_GROUPS: frozenset[BooleanGroup] = frozenset(PAIR_RECIPES.keys())


# --------------------------------------------------------------------------- #
# Group -> FAIR-node mapping (#130 Task 4)
# --------------------------------------------------------------------------- #
#
# Declarative table mapping each Boolean group's COMPOSED effectiveness E to the
# FAIR node(s) it adjusts, via the multiplier form `multiplier = 1 - E·w` (the
# form `effectiveness.py` already uses). Separates "which node + weight + which
# Standard clause" (DATA, here) from "how to compose" (the shared routine in
# `risk_engine/group_composition.py`). The engine ALE path (Task 5) and the
# provenance emitter (Task 8) both consume this table; it is the single source
# of truth for group->node routing so engine and diagnostic cannot drift (D2).
#
# WEIGHTS ARE NOT STANDARD-GROUNDED. The FAIR-CAM Standard V1.0 describes the
# group->node relationships qualitatively only; it gives no numeric weights.
# Every weight below is flagged `weights_provenance="implementation-calibration"`
# and is a named item for the future calibration issue (spec §8.2). The lone
# exception classification "Standard-grounded" is reserved for a future weight
# that traces to a primary citation; today none qualify.
#
# ----------------------------- NODE-TARGET AUDIT --------------------------- #
# (#130 spec §7.3 / §12.3 — DOCUMENTATION sub-step of Task 4, no code change
#  beyond this note + the Response re-route below.)
#
# The pre-#130 engine routed groups purely by the coarse `ControlDomain` prefix
# bucket (`models/control.py:subfunction_to_domain`): every `lec_*` sub-function
# -> LOSS_EVENT -> TEF ×0.8 / Vuln ×0.9; every `vmc_*` -> Vuln ×0.3; every
# `dsc_*` -> secondary ×0.5 / primary ×0.2. That coarseness is exactly why the
# Response group was mis-routed. Auditing each LEC group's node target against
# the Standard:
#
#   * LEC_PREVENTION — §3.1 (p.9). Prevention reduces *likelihood* of a loss
#     event (Avoidance/Deterrence/Resistance act before the event). Targeting
#     threat_event_frequency + vulnerability is CONSISTENT with the Standard.
#     RETAINED (re-routing out of scope; no drift found).
#   * LEC_DETECTION — §3.2 (pp.15–17). Detection on its own does not reduce loss;
#     it enables Response (the §3.3 p.18 Detection->Response AND dependency). In
#     v3 the Detection group has no standalone node multiplier — its effect is
#     carried through the LEC_DETECTION_RESPONSE_PAIR (below). There is no leaf
#     LEC_DETECTION entry in this table by design: routing Detection's raw
#     effectiveness to a frequency node would double-count the gate. (If a future
#     issue gives Detection a standalone "earlier-recognition shortens dwell"
#     magnitude effect, add it then; OUT OF SCOPE for #130.)
#   * LEC_RESPONSE — §3.3 (p.18) + §3.3.1 (pp.18–19). Response acts AFTER the
#     event begins to limit the *amount of loss* (time-limits-loss mechanism).
#     The pre-#130 TEF/Vuln target was WRONG (frequency node for a magnitude
#     effect). #130 RE-ROUTES Response -> Loss Magnitude (primary + secondary
#     loss) per D4. This is the headline fix.
#   * LEC_DETECTION_RESPONSE_PAIR — §3.3 p.18 AND dependency (D8). The pair's
#     composed effectiveness is what actually drives the magnitude multiplier:
#     Response benefit is GATED on Detection presence (no Detection -> pair
#     effectiveness None -> multiplier 1.0). Shares Response's Loss-Magnitude
#     targets + D7 weights.
#   * VMC_* — §4 (p.21). Variance Management ensures other controls perform as
#     expected; through #130 this was modeled as a second-order vulnerability
#     reducer (Vuln ×0.3), applied EXACTLY ONCE via
#     VMC_IDENTIFICATION_CORRECTION_PAIR (VMC_IDENTIFICATION and VMC_CORRECTION
#     leaves carried empty targets to avoid a ×3 double/triple-count; see prior
#     revision history below). Slice 2 (#439) REVERSES the #130 "RETAINED"
#     judgment on primary-source grounds: §2.2 p.5's diagram places VMC/DSC in
#     the "Indirectly Affect Risk" box, acting only THROUGH Loss Event Controls
#     — not directly on a FAIR node — and §4 p.21 says VMC acts on "the
#     Operational Performance of OTHER controls" (capitalization emphasis
#     added), i.e. reliability, not vulnerability. The direct vulnerability
#     targets on VMC_VARIANCE_PREVENTION and VMC_IDENTIFICATION_CORRECTION_PAIR
#     are RETIRED; VMC value now flows ONLY through the kappa reliability
#     coupling (KAPPA_META_RELIABILITY, defined below) onto the reliability of
#     co-present Loss Event Controls.
#     (Prior #130 double-count history, preserved for context: the
#     Identification∧Correction AND is a single composed effect that must reach
#     a target exactly once; VMC_IDENTIFICATION/VMC_CORRECTION leaves therefore
#     stayed empty across both #130 and Slice 2, mirroring LEC_DETECTION.)
#   * DSC_* — §2.2 p.5 / §5 (p.30). Decision Support improves management
#     decisions; through #130 this was modeled as a Loss-Magnitude reducer
#     (RETAINED at that time, §5.1 citation spot-check pending — N-M8). Slice 2
#     (#439) REVERSES that judgment: §2.2 p.5 places DSC, like VMC, in the
#     "Indirectly Affect Risk" box — it has no direct FAIR-node target in the
#     Standard. The direct Loss-Magnitude targets on DSC_PREVENTION and
#     DSC_IDENTIFICATION_CORRECTION_PAIR are RETIRED; DSC value now flows
#     through the SAME kappa reliability coupling as VMC, as a v3 PROXY (the
#     Standard frames DSC as affecting decisions, not control reliability
#     directly — routing it through the reliability channel is an explicit v3
#     modeling choice, not a Standard-grounded claim).
#
# Conclusion: #130 found only LEC_RESPONSE's node target contradicted the
# Standard; Slice 2 (#439) additionally retires the VMC_* and DSC_* DIRECT node
# targets on the §2.2 p.5 "Indirectly Affect Risk" grounds above — meta value
# now flows exclusively through the kappa reliability coupling, not through a
# direct vulnerability/magnitude multiplier. This table merely makes the
# (group -> node + weight + citation) routing explicit and audit-traceable.


@dataclass(frozen=True)
class NodeMapping:
    """How a composed group's effectiveness maps onto FAIR node multiplier(s).

    `targets` — the FAIR node key(s) the group's effectiveness `E` adjusts.
    `weights` — per-target weight `w` in `multiplier = 1 - E·w` (keys == targets).
    `citation` — the FAIR-CAM Standard §/page the node *direction* traces to.
    `weights_provenance` — "Standard-grounded" | "implementation-calibration".
        The Standard gives only qualitative relationships, so all current
        numeric weights are "implementation-calibration" (spec §8.2).
    """

    targets: tuple[str, ...]
    weights: dict[str, float]
    citation: str
    weights_provenance: str


# Shared Loss-Magnitude weights (D7): secondary > primary asymmetry grounded in
# §3.3.3's predominantly secondary-loss Response examples (insurance, legal,
# credit monitoring) — NOT in any DSC numeric coincidence (plan-gate I-M5).
_MAGNITUDE_WEIGHTS: dict[str, float] = {"secondary_loss": 0.5, "primary_loss": 0.2}
_MAGNITUDE_TARGETS: tuple[str, ...] = ("secondary_loss", "primary_loss")


# --------------------------------------------------------------------------- #
# Slice 2 (#439): meta -> reliability coupling constant.
#
# r_eff(a) = r0(a) + (1 - r0(a)) * KAPPA_META_RELIABILITY * E_meta
#
# Anchor: half of composed meta strength converts to recovery of the (1 - r0)
# reliability headroom. Midpoint chosen for symmetry; the value is
# implementation-calibration and NON-IDENTIFIABLE at single-org scale (#419
# decision — do not propose per-org calibration; uncertainty is reported as
# ranges via the weight-robustness ensemble (wired in Task 4), param key
# "meta.kappa").
# Grounding: direction/bound/floor from FAIR-CAM §2.2 p.5, §2.3 pp.5-6, §4
# p.21 (VMC affects the reliability of other controls); the interpolation
# SHAPE is a v3 view-model choice; the DSC contribution to E_meta is a v3
# PROXY (Standard frames DSC as affecting decisions, §2.2 p.5 / §5 p.30).
# ANCHOR CONVENTION for the authored input (spec D2, calibration semantics):
# an assignment's authored `reliability` (r0) is its expected reliability
# ABSENT co-present meta uplift. Authors estimating from observed in-situ
# performance (which already includes their VMC/DSC environment) should
# discount accordingly, or accept the estimate as conservative-high; the
# residual bias is absorbed into kappa's non-identifiability and surfaces in
# the reported ranges.
# Scope note: meta->meta cascade (a VMC/DSC control raising the reliability of
# ANOTHER meta control, rather than a Loss Event Control) is NOT modeled here
# — §2.2 p.5's diagram does not depict meta-on-meta edges, and Slice 2 confines
# E_meta's uplift to co-present Loss Event Controls only.
# sigma-reuse convention (Final-Meth-3, #439): in the weight-robustness
# ensemble (src/idraa/services/weight_robustness.py:sample_ensemble_draw)
# kappa is perturbed with the SAME logit-sigma as the node-mapping weights —
# a convenience convention (reusing a routing-weight sigma for a coupling
# gain), not a calibrated choice; do NOT introduce per-param sigma (#419
# discipline).
# Pinned by fair_cam/tests/test_composition_topology.py::test_kappa_meta_reliability_pin.
KAPPA_META_RELIABILITY: float = 0.5


GROUP_NODE_MAPPING: dict[BooleanGroup, NodeMapping] = {
    BooleanGroup.LEC_PREVENTION: NodeMapping(
        ("threat_event_frequency", "vulnerability"),
        {"threat_event_frequency": 0.8, "vulnerability": 0.9},
        "FAIR-CAM §3.1 p.9 (Prevention reduces loss-event likelihood)",
        "implementation-calibration",
    ),
    BooleanGroup.LEC_DETECTION: NodeMapping(
        # Detection has no STANDALONE node multiplier (it gates Response via the
        # pair); routing its raw effectiveness alone would double-count. Modeled
        # as identity here so the table is total over BooleanGroup, but the
        # engine reads the PAIR for the magnitude effect, never this leaf.
        (),
        {},
        "FAIR-CAM §3.2 pp.15-17 (Detection enables Response; no standalone node)",
        "implementation-calibration",
    ),
    BooleanGroup.LEC_RESPONSE: NodeMapping(  # #130 D4 RE-ROUTE: was TEF/Vuln
        _MAGNITUDE_TARGETS,
        dict(_MAGNITUDE_WEIGHTS),
        "FAIR-CAM §3.3 p.18 + §3.3.1 pp.18-19 "
        "('limiting the time a threat can persist… can significantly affect the "
        "amount of loss'); modeled magnitude-only per D4",
        "implementation-calibration",
    ),
    BooleanGroup.LEC_DETECTION_RESPONSE_PAIR: NodeMapping(
        _MAGNITUDE_TARGETS,
        dict(_MAGNITUDE_WEIGHTS),
        "FAIR-CAM §3.3 p.18 (Detection->Response AND dependency, D8); "
        "carries the Response magnitude effect gated on Detection",
        "implementation-calibration",
    ),
    BooleanGroup.VMC_VARIANCE_PREVENTION: NodeMapping(
        (),
        {},
        "FAIR-CAM §4 p.21 — VMC affects risk via the RELIABILITY of other "
        "controls; direct vulnerability target retired in Slice 2 (#439), "
        "value now flows through the kappa reliability coupling",
        "implementation-calibration",
    ),
    BooleanGroup.VMC_IDENTIFICATION: NodeMapping(
        # #130 Task 5 fix (reviewer BLOCKER): VMC Identification has NO standalone
        # node multiplier. It is a child operand of the §4 Identification∧Correction
        # AND-pair; the composed VMC effect reaches vulnerability ONCE via
        # VMC_IDENTIFICATION_CORRECTION_PAIR below. Routing the leaf's raw
        # effectiveness here too would apply the same §4 VMC effect onto
        # vulnerability multiple times (ID leaf + Correction leaf + pair = ×3),
        # the inverse of the #130 Response double-count bug. Mirrors the
        # LEC_DETECTION empty-targets pattern: Identification alone (no Correction)
        # yields no variance-management benefit (AND collapses), so empty targets
        # here is also the correct AND semantics for a single-leaf control.
        (),
        {},
        "FAIR-CAM §4 p.21 (Identification is an AND-pair operand; no standalone node)",
        "implementation-calibration",
    ),
    BooleanGroup.VMC_CORRECTION: NodeMapping(
        # #130 Task 5 fix (reviewer BLOCKER): symmetric to VMC_IDENTIFICATION above
        # — Correction is the other §4 AND-pair operand and has no standalone node;
        # the composed VMC effect reaches vulnerability ONCE via the pair.
        (),
        {},
        "FAIR-CAM §4 p.21 (Correction is an AND-pair operand; no standalone node)",
        "implementation-calibration",
    ),
    BooleanGroup.VMC_IDENTIFICATION_CORRECTION_PAIR: NodeMapping(
        (),
        {},
        "FAIR-CAM §4 p.21 — VMC affects risk via the RELIABILITY of other "
        "controls; direct vulnerability target retired in Slice 2 (#439), "
        "value now flows through the kappa reliability coupling",
        "implementation-calibration",
    ),
    BooleanGroup.DSC_PREVENTION: NodeMapping(
        (),
        {},
        "FAIR-CAM §2.2 p.5 / §5 p.30 — DSC affects decisions; routing DSC "
        "through the reliability coupling is a v3 PROXY (Slice 2 #439); "
        "direct magnitude target retired",
        "implementation-calibration",
    ),
    BooleanGroup.DSC_IDENTIFICATION_CORRECTION_PAIR: NodeMapping(
        (),
        {},
        "FAIR-CAM §2.2 p.5 / §5 p.30 — DSC affects decisions; routing DSC "
        "through the reliability coupling is a v3 PROXY (Slice 2 #439); "
        "direct magnitude target retired",
        "implementation-calibration",
    ),
}
