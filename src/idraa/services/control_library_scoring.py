"""Standalone-scoring predicate for FAIR-CAM sub-functions (#437 T4, methodology B1).

Single source of truth; callers import ``classify_entry`` / ``entry_scores``;
``scores_standalone`` is the rubric-catalog predicate they delegate to.
DERIVED from the fair_cam topology so it tracks #439's channel changes instead of
drifting. A sub-function scores standalone in the current v(S) iff it is the currency
subtractor, OR its leaf group is an OR group with non-empty node targets. Detection
(empty targets), gated Response, and multi-member AND leaves (VMC id/corr, DSC) do NOT.
"""

from __future__ import annotations

from typing import Any, Literal

from fair_cam.models.composition_topology import (
    _NON_OPEFF_SUB_FUNCTIONS,
    GROUP_MEMBERSHIP,
    GROUP_NODE_MAPPING,
    GROUP_TYPE,
    BooleanGroup,
    GroupType,
)
from fair_cam.models.control import Control, FairCamControlFunctionAssignment
from fair_cam.models.sub_function import SUB_FUNCTION_UNITS, FairCamSubFunction, UnitType
from fair_cam.risk_engine.control_attribution import reduction_from_composition, scenario_base_ale
from fair_cam.risk_engine.fair_core import (
    DistributionType,
    FAIRDistribution,
    FAIRParameters,
)
from fair_cam.risk_engine.group_composition import compose_groups

# ---------------------------------------------------------------------------
# Topology-derived lookup tables (computed once at import time).
# ---------------------------------------------------------------------------

# Map sub-function string → its leaf BooleanGroup (same logic as sub_function_to_group
# but keyed on the string value so scores_standalone accepts raw strings).
_SF_TO_GROUP: dict[str, BooleanGroup] = {
    sf.value: g for g, members in GROUP_MEMBERSHIP.items() for sf in members
}

# Currency sub-functions (FAIR-CAM §3.3.2 loss-reduction subtractor).
# These are excluded from the opeff path but DO contribute a positive v(S).
_CURRENCY: frozenset[str] = frozenset(sf.value for sf in _NON_OPEFF_SUB_FUNCTIONS)

# Unit types whose capability_value has a bounded [0, 1] domain.  For these,
# defaulting a missing capability to 0.8 is semantically correct.  For natural-unit
# types (CURRENCY, ELAPSED_TIME) None must be preserved so the engine applies its
# own sentinel logic (CURRENCY → no subtractor; ELAPSED_TIME → τ·ln(2) fallback).
_BOUNDED_UNITS: frozenset[str] = frozenset(
    {UnitType.PROBABILITY.value, UnitType.PERCENT_REDUCTION.value}
)

# ---------------------------------------------------------------------------
# Representative scenario parameters for entry-level engine scoring.
#
# Any positive-ALE scenario works; the test asserts only the sign (> 1e-9).
# Inline literal per brief NEW-#4: do NOT import the test-only make_fair_parameters.
# ---------------------------------------------------------------------------
_REPRESENTATIVE_RP: FAIRParameters = FAIRParameters(
    threat_event_frequency=FAIRDistribution(
        DistributionType.TRIANGULAR, {"low": 1.0, "mode": 2.0, "high": 3.0}
    ),
    vulnerability=FAIRDistribution(
        DistributionType.TRIANGULAR, {"low": 0.4, "mode": 0.5, "high": 0.6}
    ),
    primary_loss=FAIRDistribution(
        DistributionType.TRIANGULAR, {"low": 8e5, "mode": 1e6, "high": 1.2e6}
    ),
    secondary_loss=FAIRDistribution(
        DistributionType.TRIANGULAR, {"low": 3e5, "mode": 4e5, "high": 5e5}
    ),
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def scores_standalone(sub_function: str) -> bool:
    """Return True iff this sub-function produces v(S) > 0 when present alone.

    TOPOLOGY-DERIVED (#437 B1): logic reads GROUP_NODE_MAPPING / GROUP_TYPE /
    _NON_OPEFF_SUB_FUNCTIONS so it tracks #439's channel changes automatically.

    Correct standalone-scoring set = lec_prev_* | {lec_resp_loss_reduction}. Direct
    meta (vmc_*/dsc_*) targets were retired by Slice 2 D1 (#439) — meta value now
    flows exclusively via the kappa reliability coupling, which requires a
    co-present LEC channel to have anything to uplift, so no vmc_*/dsc_* member
    scores standalone anymore.

    Rules (derived from topology):
    - Currency subtractor (lec_resp_loss_reduction) → True (per-event SL reduction).
    - LEC_RESPONSE members other than the currency subtractor → False (detection-gated;
      the pair gate means Response alone yields no magnitude benefit).
    - Leaf groups with empty node targets (lec_det_*, vmc_id_*, vmc_corr_*, vmc_prev_*,
      dsc_*, and the VMC/DSC identification-correction pairs) → False.
    - Leaf groups with non-empty targets AND OR operator → True (one member suffices).
    - All other groups (AND / WEAK_AND with targets) → False (multi-member gate or
      pair requirement collapses a singleton to 0).
    """
    if sub_function in _CURRENCY:
        # Currency subtractor: contributes a direct per-event SL reduction → scores.
        return True

    g = _SF_TO_GROUP.get(sub_function)
    if g is None or g == BooleanGroup.LEC_RESPONSE:
        # Unknown sub-function or Response (detection-gated via LEC_DETECTION_RESPONSE_PAIR).
        return False

    if not GROUP_NODE_MAPPING[g].targets:
        # Empty-target leaf (lec_det_*, vmc_id_*, vmc_corr_*): the node contribution
        # is zero standalone; the effect only reaches a FAIR node via its pair group.
        return False

    return GROUP_TYPE[g] == GroupType.OR


def _seed_entry_to_control(entry: dict[str, Any]) -> Control:
    """Build a fair_cam Control from a library entry dict for engine-based scoring.

    Accepts both "full" entries (capability_default / coverage_default /
    reliability_default present) and minimal test-helper entries (keys absent,
    defaulting to 0.8).  NO `unit=` arg — FairCamControlFunctionAssignment has
    no unit field (brief NEW-#6).  Do NOT reuse services/controls.py (that builds
    the v3 ORM, not a fair_cam dataclass).
    """
    assignments: list[FairCamControlFunctionAssignment] = []
    for a in entry.get("assignments", []):
        s = a["sub_function"]
        cap = a.get("capability_default")
        # M-1/M-2: preserve None for natural-unit sub-functions (CURRENCY, ELAPSED_TIME)
        # so the engine applies its own sentinel logic (currency → no subtractor added;
        # elapsed_time → τ·ln(2) half-life fallback).  Only bounded units ([0,1]) may
        # safely fall back to 0.8 when the author hasn't supplied a value.
        sf_enum = FairCamSubFunction(s)
        unit_type = SUB_FUNCTION_UNITS.get(sf_enum, UnitType.PROBABILITY)
        cap_val: float | None = (
            cap if cap is not None else (0.8 if unit_type in _BOUNDED_UNITS else None)
        )
        # M-3: use `is not None` (not `or`) so an explicitly-authored 0.0 is not
        # silently promoted to 0.8.
        cov = a.get("coverage_default")
        cov_val: float = cov if cov is not None else 0.8
        rel = a.get("reliability_default")
        rel_val: float = rel if rel is not None else 0.8
        assignments.append(
            FairCamControlFunctionAssignment(
                sub_function=sf_enum,
                capability_value=cap_val,
                coverage=cov_val,
                reliability=rel_val,
            )
        )
    return Control(assignments=assignments)


def entry_scores(entry: dict[str, Any]) -> bool:
    """Pair-AWARE entry-level scoring (NEW-B2).

    An entry scores iff its assignment SET yields v(S) > 0 in the CURRENT engine.
    Computed via the actual closed form (compose_groups → reduction_from_composition),
    NOT a re-implementation of the topology — so VMC id∧corr and LEC det∧resp
    pair-completion are handled exactly, INCLUDING the multi-member AND 0-collapse
    (verified: a 1-of-2 VMC pair = $0; a full 2-id+2-corr pair scores > $0).

    `scores_standalone` (above) stays for the rubric CATALOG only; entry-level
    decisions MUST use this function.

    Self-coupling-free (Slice 2 D5): the entry is composed STANDALONE with
    ``kappa=0.0``, so catalog scores are standalone-control scores — an entry
    whose only channels are meta (VMC/DSC) must NOT score off its own
    meta→reliability coupling. A hybrid entry (meta + LEC on the same entry)
    therefore scores exactly as its LEC channel alone; the meta channel's
    coupling credit is realised only in a live run's Shapley value function
    (``subset_reduction_closed_form`` at default κ), where E_meta can uplift a
    co-present LEC reliability. Genuinely-meta entries fall to the
    non-scoring-residual bucket (#437 I4 → #439), which is the intended D1
    outcome, not a gap to graft a fake channel onto.
    """
    # M-4: zero-assignment entries must not reach Control() (which requires ≥1
    # assignment in its __post_init__).  Return False immediately; classify_entry
    # will bucket these as "under-authored".
    if not entry.get("assignments"):
        return False
    ctrl = _seed_entry_to_control(entry)
    base = scenario_base_ale(_REPRESENTATIVE_RP)
    return reduction_from_composition(base, compose_groups([ctrl], kappa=0.0), None) > 1e-9


def classify_entry(
    entry: dict[str, Any],
) -> Literal["under-authored", "scoring", "non-scoring-residual"]:
    """3-way triage for a library entry.

    - "scoring"              — entry_scores is True; at least one assignment set
                               yields v(S) > 0 (pair-complete or standalone scorer).
    - "under-authored"       — entry_scores is False AND ≤1 assignment; needs curation.
    - "non-scoring-residual" — entry_scores is False AND ≥2 assignments; curator
                               confirms genuinely-meta. Meta — credits via the κ
                               reliability coupling on runs (E_meta uplifts a
                               co-present Loss-Event control's reliability, #439 D1);
                               its standalone catalog score is $0 by design (the
                               catalog composes each entry alone with κ=0, so a
                               meta-only entry has no co-present LEC to uplift).

    The residual bucket is a first-class outcome (#437 I4): it defuses
    "assign-to-score" pressure — a genuinely-meta entry is correctly $0 standalone,
    NOT a gap to graft a fake direct channel onto.
    """
    if entry_scores(entry):
        return "scoring"
    subs = [a["sub_function"] for a in entry.get("assignments", [])]
    return "under-authored" if len(subs) <= 1 else "non-scoring-residual"
