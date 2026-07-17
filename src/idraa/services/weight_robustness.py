"""Weight-uncertainty robustness: sample the FAIR-CAM composition weights from a
logit-normal perturbation kernel centered on the canonical guess (issue #419).
fair_cam owns the math; this only feeds it different weights.

The sampler implements a PERTURBATION KERNEL (Meth-B5/I1 -- NOT a non-informative
prior): deliberately centered on the unvalidated canonical guess and perturbs
around it. Each canonical weight w -> expit(logit(w) + sigma*Z). Naturally bounded
in (0,1) -- NO atom, NO clamp; equal dispersion in logit (relative) space (NOT
absolute-w -- near-ceiling weights swing less, Meth-B7).

Within-FUNCTION correlation (Meth-I5): weights of the SAME FAIR-CAM function
share ONE logit-space uncertainty draw (perfect within-function correlation), so
independent sampling cannot artificially narrow a prevention-/magnitude-dominated
control's band via partial cancellation (verified ~31% narrowing if sampled
independently). Different functions stay independent.
"""

from __future__ import annotations

import copy
import logging
import math
from collections.abc import Callable
from typing import Any

import numpy as np
from fair_cam.models.composition_topology import (
    GROUP_NODE_MAPPING,
    KAPPA_META_RELIABILITY,
    BooleanGroup,
    NodeMapping,
)

from idraa.config import get_settings

logger = logging.getLogger(__name__)

# One ensemble draw = (node-mapping override, kappa) as an OPAQUE unit threaded
# end-to-end (Slice 2 #439; plan-gate Arch-B1/Spec-B1/Sec-I1). ``None`` for the
# node_mapping means "use the canonical GROUP_NODE_MAPPING" — mirroring
# reduction_from_composition's ``node_mapping=None`` convention; the float is the
# meta->reliability coupling strength kappa for the draw. The perturbation
# samplers (sample_ensemble_draw / band_endpoint_draws) always emit a concrete
# mapping; the ``| None`` member exists purely so the sampler/value-fn contract
# also admits the canonical sentinel that the mapping-ignoring unit-test fakes use.
EnsembleDraw = tuple[dict[BooleanGroup, NodeMapping] | None, float]

# Distinct spawn-key namespace for the weight-robustness ensemble RNG
# (Sec-Repro-1). The executor derives the ensemble stream as
# ``SeedSequence(band["seed"], spawn_key=(WEIGHT_ROBUSTNESS_SPAWN_DOMAIN,))`` so it
# CANNOT collide with the main MC's ``.spawn(n)`` per-scenario streams off the same
# root seed — that gives the "decorrelated ensemble stream" guarantee real teeth.
# Must be a non-negative int: numpy ``SeedSequence.spawn_key`` only accepts ints
# (a string raises "unrecognized seed string"). The design doc's ``0x_C0DE_R0B``
# literal is invalid hex (``R`` is not a hex digit); this is the corrected valid
# hex sentinel in the same "CODE BOB" spirit.
WEIGHT_ROBUSTNESS_SPAWN_DOMAIN: int = 0x_C0DE_B0B

# Authoritative top-level key set for the persisted weight_robustness blob
# (SpecCompl-KeyShape1). Task 7's field-sync contract test imports this constant
# and asserts the persisted blob contains exactly these 14 keys. present-but-None
# counts as present (Spec-Compl-1). Do NOT change this set without a matching
# migration / view-model update. The writer (_build_weight_robustness) also
# enforces this set at runtime (methodology review F3: the test replica alone
# silently under-covered when the writer's augmentation grew).
# 2026-07-04 side-by-side: + "canonical_value_typical" (paired typical points)
# and "basis" ("mean" on new blobs; legacy blobs lack the key entirely, which
# is why absence-tolerant readers must treat missing basis as typical).
WEIGHT_ROBUSTNESS_KEYS: frozenset[str] = frozenset(
    {
        "band",
        "canonical_value",
        "canonical_value_typical",
        "basis",
        "headline",
        "per_control",
        "kendall_tau_p50",
        "topk_preservation_k",
        "topk_preservation_prob",
        "indistinguishable_pairs",
        "rank_stability_available",
        "draws_used",
        "degraded",
        "state",
    }
)

# Engine-applied groups mirror control_aware._group_comp_to_node_multipliers skip
# logic: non-empty targets AND not the D/R pair (applied via LEC_RESPONSE).
_SKIP: frozenset[BooleanGroup] = frozenset({BooleanGroup.LEC_DETECTION_RESPONSE_PAIR})


def _engine_slots() -> list[tuple[BooleanGroup, str]]:
    """Return (group, node) pairs for every engine-applied weight slot.

    Mirrors the skip logic from control_aware._group_comp_to_node_multipliers:
    groups with empty targets are no-ops; LEC_DETECTION_RESPONSE_PAIR is applied
    separately via LEC_RESPONSE gating and must not be double-perturbed here.
    """
    return [
        (g, node)
        for g, m in GROUP_NODE_MAPPING.items()
        if m.targets and g not in _SKIP
        for node in m.weights
    ]


def _param_key(group: BooleanGroup, node: str) -> str:
    """Derive a canonical parameter key from (group, node), collapsing shared constants.

    Co-variation rationale: collapse shared canonical constants to ONE parameter so
    the CORRELATION_GROUPS can assign them a single Z:
    - magnitude.secondary / magnitude.primary: the shared _MAGNITUDE_WEIGHTS were
      identical across LEC_RESPONSE, DSC_PREVENTION, DSC_IDENTIFICATION_CORRECTION_PAIR
      pre-Slice-2. Slice 2 (#439) D1 retired the DSC groups' direct magnitude
      targets, so only LEC_RESPONSE reaches this key now (see
      `tests/services/test_weight_robustness_resolver.py::
      test_param_map_covaries_shared_constants`); the key is kept even at N=1 for
      forward-compat with any future group that re-targets magnitude.

    The pre-Slice-2 ``vmc.vuln`` branch (VMC_VARIANCE_PREVENTION /
    VMC_IDENTIFICATION_CORRECTION_PAIR -> "vulnerability") was DELETED here (Slice 2
    #439 Task 4, Arch-N4): D1 emptied those groups' direct vulnerability targets, so
    ``_engine_slots()`` (which filters on ``m.targets``) can never reach it — the
    branch was unreachable. Meta effectiveness now flows exclusively through the
    kappa reliability coupling (KAPPA_PARAM_KEY), not a GROUP_NODE_MAPPING slot.
    """
    if node in ("secondary_loss", "primary_loss"):
        return f"magnitude.{'secondary' if node == 'secondary_loss' else 'primary'}"
    if group == BooleanGroup.LEC_PREVENTION:
        return f"prevention.{'tef' if node == 'threat_event_frequency' else 'vuln'}"
    # Defensive: any future engine slot added to GROUP_NODE_MAPPING gets its own key
    return f"{group.name}.{node}"


# ---------------------------------------------------------------------------
# CANONICAL_PARAM_SLOTS: the co-variation map.
#
# Maps each canonical parameter key -> list of (BooleanGroup, node) slots that
# share that parameter. Built once at import time from _engine_slots() so:
#  1. Adding a new slot to GROUP_NODE_MAPPING auto-appears here (no manual sync).
#  2. All callers see the SAME map with no per-call recomputation.
#
# Invariant: set(flatten(CORRELATION_GROUPS.values())) == set(CANONICAL_PARAM_SLOTS)
# is verified at import time below.
# ---------------------------------------------------------------------------
CANONICAL_PARAM_SLOTS: dict[str, list[tuple[BooleanGroup, str]]] = {}
for _g, _node in _engine_slots():
    CANONICAL_PARAM_SLOTS.setdefault(_param_key(_g, _node), []).append((_g, _node))


# Slice 2 (#439): the meta -> reliability coupling constant kappa is not a
# (group, node) GROUP_NODE_MAPPING slot -- the VMC/DSC direct node targets were
# retired by D1, so `CANONICAL_PARAM_SLOTS["meta.kappa"]` KeyErrors by design
# (there is no slot list for it). It is exposed as a canonical param key (in
# CORRELATION_GROUPS and canonical_param_values()) and IS sampled by the
# ensemble: `sample_ensemble_draw` draws it alongside the node-mapping weights
# and returns it as the second member of the (node_mapping, kappa)
# EnsembleDraw; `band_endpoint_draws` carries it on the deterministic
# endpoints too.
KAPPA_PARAM_KEY = "meta.kappa"


def canonical_param_values() -> dict[str, float]:
    """Return param key -> canonical weight from GROUP_NODE_MAPPING (read-only).

    Also includes KAPPA_PARAM_KEY -> KAPPA_META_RELIABILITY (Slice 2 #439):
    kappa is not a GROUP_NODE_MAPPING slot, so it is added explicitly here to
    keep this map and the CORRELATION_GROUPS/guard-set invariant consistent.
    """
    out: dict[str, float] = {}
    for key, slots in CANONICAL_PARAM_SLOTS.items():
        g, node = slots[0]
        out[key] = GROUP_NODE_MAPPING[g].weights[node]
    out[KAPPA_PARAM_KEY] = KAPPA_META_RELIABILITY
    return out


def _logit(w: float) -> float:
    """Logit (log-odds) transform: logit(w) = log(w / (1 - w))."""
    return math.log(w / (1.0 - w))


def _expit(x: float) -> float:
    """Inverse logit (sigmoid): expit(x) = 1 / (1 + exp(-x))."""
    return 1.0 / (1.0 + math.exp(-x))


def _apply_param_values(values: dict[str, float]) -> dict[BooleanGroup, NodeMapping]:
    """Build a perturbed GROUP_NODE_MAPPING from a param-key -> weight dict.

    Sec-N6: operates exclusively on a fresh deepcopy -- NEVER mutates the module-
    global GROUP_NODE_MAPPING. NodeMapping is frozen but weights is a mutable dict,
    so we can legally write to out[g].weights after deepcopy.

    KAPPA_PARAM_KEY is NOT a (group, node) slot — it must be popped off by every
    caller BEFORE reaching here. A defensive guard fails loud if it leaks in, so a
    kappa value can never be silently written as a FAIR node weight (Slice 2 #439).
    """
    if KAPPA_PARAM_KEY in values:
        raise ValueError(
            f"{KAPPA_PARAM_KEY!r} is not a node-weight slot; the caller must pop "
            "kappa from the draw before applying node weights (Slice 2 #439)"
        )
    out = copy.deepcopy(GROUP_NODE_MAPPING)
    for key, slots in CANONICAL_PARAM_SLOTS.items():
        w = values[key]
        # Fail loud rather than silently clamp to a 0/1 atom (the exact artifact the
        # logit-normal kernel exists to avoid). Both callers (sample_ensemble_draw,
        # band_endpoint_draws) produce expit(...) strictly in (0,1), so this never
        # fires on the real path; a future caller that passes an out-of-band value
        # gets a clear error instead of a corrupted FAIR weight.
        if not (0.0 < w < 1.0):
            raise ValueError(f"weight for {key!r} must be in (0,1), got {w!r}")
        for g, node in slots:
            out[g].weights[node] = w
    return out


# ---------------------------------------------------------------------------
# Within-function correlation groups (Meth-I5).
#
# Maps FAIR-CAM function name -> list of canonical param keys that share ONE
# logit-space draw. "Function" here = the Standard's §3/§4/§5 mechanism:
#   prevention: LEC_PREVENTION's tef+vuln weights (§3.1)
#   magnitude:  shared magnitude weights across Response + DSC (§3.3 / §5)
#   meta:       kappa, the meta -> reliability coupling constant (§4/§2.2/§2.3;
#               Slice 2 #439). Replaces the pre-Slice-2 "vmc": ["vmc.vuln"]
#               entry -- D1 retired the VMC direct vulnerability targets, so
#               "vmc.vuln" no longer exists as a CANONICAL_PARAM_SLOTS key.
#               kappa is NOT a (group,node) slot (see KAPPA_PARAM_KEY note
#               above); it is sampled by sample_ensemble_draw as its own
#               single-member correlation group (independent Z from the
#               weight groups).
#
# Invariant: set(flatten(CORRELATION_GROUPS.values())) == set(CANONICAL_PARAM_SLOTS) | {KAPPA_PARAM_KEY}
# Checked at import time to catch any GROUP_NODE_MAPPING additions that would
# go unassigned (silent independent sampling).
# ---------------------------------------------------------------------------
CORRELATION_GROUPS: dict[str, list[str]] = {
    "prevention": ["prevention.tef", "prevention.vuln"],
    "magnitude": ["magnitude.secondary", "magnitude.primary"],
    "meta": [KAPPA_PARAM_KEY],
}

# Import-time invariant guard: every canonical param key (including the
# non-slot KAPPA_PARAM_KEY, Slice 2 #439) must be assigned to exactly one
# correlation group (no silent independent sampling for orphan keys).
_all_group_keys = {k for keys in CORRELATION_GROUPS.values() for k in keys}
_all_slot_keys = set(CANONICAL_PARAM_SLOTS.keys()) | {KAPPA_PARAM_KEY}
if _all_group_keys != _all_slot_keys:  # pragma: no cover -- would only fire on topology change
    _missing = _all_slot_keys - _all_group_keys
    _extra = _all_group_keys - _all_slot_keys
    raise RuntimeError(
        f"CORRELATION_GROUPS is out of sync with CANONICAL_PARAM_SLOTS: "
        f"unassigned slot keys={_missing!r}, unknown group keys={_extra!r}"
    )


def sample_ensemble_draw(
    rng: np.random.Generator,
    sigma: float | None = None,
) -> tuple[dict[BooleanGroup, NodeMapping], float]:
    """One logit-normal ensemble draw over ALL canonical params as an OPAQUE unit.

    Draws the node-mapping weights AND the meta->reliability coupling kappa in a
    single pass (Slice 2 #439). Returns ``(node_mapping, kappa)`` — the payload the
    whole ensemble pipeline threads end-to-end (plan-gate Arch-B1/Spec-B1/Sec-I1).

    Meth-B5/I1 -- NOT a non-informative prior: deliberately CENTERED on the
    unvalidated canonical guess and perturbs around it. Each canonical weight (and
    kappa) w -> expit(logit(w) + sigma*Z). Naturally bounded in (0,1) -- NO atom, NO
    clamp; equal dispersion in logit (relative) space (NOT absolute-w -- near-
    ceiling weights swing less, Meth-B7).

    Params of one FAIR-CAM function share a single Z (Meth-I5 within-function
    correlation); kappa is its own single-member "meta" function so it draws an
    independent Z. sigma=0 => identity (Test-N1): the FP logit round-trip is
    ~1e-16, so kappa returns to KAPPA_META_RELIABILITY within float tolerance.

    sigma-reuse convention (Final-Meth-3, #439): kappa is perturbed with the
    SAME logit-sigma as the node-mapping weights — a convenience convention
    (reusing a routing-weight sigma for a coupling gain), not a calibrated
    choice; do NOT introduce per-param sigma (#419 discipline).

    Sec-I1: guards 0 < w < 1 before logit() and math.isfinite on each draw.

    Args:
        rng: NumPy random generator (caller-owned; mutated in-place).
        sigma: logit-space perturbation width. Defaults to
            settings.weight_band_logit_sigma (0.6). Pass 0.0 for identity.

    Returns:
        ``(node_mapping, kappa)`` -- node_mapping is a deepcopy of
        GROUP_NODE_MAPPING with weights perturbed (never the module global); kappa
        is the drawn meta->reliability coupling strength in (0,1).
    """
    s = get_settings().weight_band_logit_sigma if sigma is None else sigma
    canon = canonical_param_values()
    drawn: dict[str, float] = {}

    for _function, keys in CORRELATION_GROUPS.items():
        z = float(rng.standard_normal())  # ONE shared draw per FAIR-CAM function (Meth-I5)
        for key in keys:
            w0 = canon[key]
            if not (0.0 < w0 < 1.0):  # logit domain guard; fail loud (Sec-I1)
                raise ValueError(
                    f"canonical weight for {key!r} must be strictly in (0,1) (got {w0!r})"
                )
            val = _expit(_logit(w0) + s * z)  # s == 0.0 => val ~= w0 (FP round-trip, ~1e-16)
            if not math.isfinite(val):  # Sec-I1: finiteness check
                raise ValueError(f"non-finite weight draw for {key!r}: {val!r}")
            drawn[key] = val

    # kappa is NOT a node-weight slot — pop it BEFORE building the node mapping so
    # _apply_param_values only ever sees FAIR node weights (guarded there too).
    kappa = drawn.pop(KAPPA_PARAM_KEY)
    return _apply_param_values(drawn), kappa


def band_endpoint_draws(
    sigma: float | None = None,
) -> dict[str, tuple[dict[BooleanGroup, NodeMapping], float]]:
    """Deterministic low/base/high ensemble endpoints (weights + kappa).

    Each endpoint is an ``(node_mapping, kappa)`` unit — the same opaque payload
    ``sample_ensemble_draw`` emits — so the insufficient-budget degraded path
    (``_deterministic_envelope``) carries kappa uncertainty exactly as the full
    ensemble does (Slice 2 #439; plan-gate converged BLOCKER).

    base = canonical weights + KAPPA_META_RELIABILITY unchanged.
    low  = expit(logit(w) - 2*sigma) for each canonical param (weights + kappa).
    high = expit(logit(w) + 2*sigma) for each canonical param (weights + kappa).

    The +-2*sigma logit-space band corresponds to an approximate 95% interval for
    the logit-normal perturbation kernel.

    Args:
        sigma: logit-space perturbation width. Defaults to
            settings.weight_band_logit_sigma when None. Callers that hold a
            PINNED band sigma (e.g. the degraded-path fallback in
            _deterministic_envelope) MUST pass it explicitly so the endpoints
            reproduce under live-Settings drift (Sec-I2 reproducibility).

    All endpoints are naturally bounded in (0,1) -- no clamping, no atom at 0
    or 1.
    """
    s = get_settings().weight_band_logit_sigma if sigma is None else sigma
    canon = canonical_param_values()

    def _at(shift: float) -> tuple[dict[BooleanGroup, NodeMapping], float]:
        if shift == 0.0:
            # Meth3-I1: the logit round-trip is NOT bit-exact
            # (expit(logit(0.9)) == 0.8999999999999999) — the base endpoint must
            # return the EXACT canonical values (strict pin
            # test_weight_robustness_resolver.py `base == 0.9`, and the workbook
            # Base column must equal the engine's canonical weights). ``sigma==0``
            # collapses low/base/high to this branch (-0.0 == 0.0 == +0.0), so the
            # degenerate band is exact-canonical everywhere (Test-N1).
            vals = dict(canon)
        else:
            vals = {k: _expit(_logit(w) + shift) for k, w in canon.items()}
        kappa = vals.pop(KAPPA_PARAM_KEY)
        return _apply_param_values(vals), kappa

    return {"low": _at(-2 * s), "base": _at(0.0), "high": _at(+2 * s)}


def band_endpoint_mappings(
    sigma: float | None = None,
) -> dict[str, dict[BooleanGroup, NodeMapping]]:
    """Mapping-only view of ``band_endpoint_draws`` (weights, no kappa).

    Retained for the verification workbook's controls-sheet deterministic
    sensitivity block (``verification_workbook.py``), which is deliberately
    weight-only (Task 6). Thin delegation over ``band_endpoint_draws`` — the
    endpoints there always emit a concrete mapping (never the canonical ``None``
    sentinel), so ``v[0]`` is always a real ``dict[BooleanGroup, NodeMapping]``.
    """
    return {k: v[0] for k, v in band_endpoint_draws(sigma).items()}


# ---------------------------------------------------------------------------
# Ensemble runner + rank-stability metrics (issue #419, Task 3)
# ---------------------------------------------------------------------------


def _kendall_tau(order_a: list[str], order_b: list[str]) -> float:
    """Kendall tau-a (no tie correction) between two orderings of the same elements.

    Near-ties treated as discordances is intentional: when two controls are nearly
    indistinguishable in a given draw (identical values sorted by name tie-break), the
    pair's order is maximally uncertain and we want that to register as a discordance
    rather than being erased by a tie-correction factor.
    """
    pos = {c: i for i, c in enumerate(order_b)}
    n = len(order_a)
    conc, disc = 0, 0
    for i in range(n):
        for j in range(i + 1, n):
            s = pos[order_a[i]] - pos[order_a[j]]
            if s < 0:
                conc += 1
            elif s > 0:
                disc += 1
    total = conc + disc
    return 1.0 if total == 0 else (conc - disc) / total


def _deterministic_envelope(
    per_control_value_fn: Callable[[list[EnsembleDraw]], list[dict[str, float]]],
    control_ids: list[str],
    sigma: float | None = None,
) -> tuple[dict[str, dict[str, Any]], dict[str, float]]:
    """Insufficient-budget fallback: low/base/high band-endpoint reductions (3 evals).

    Deterministic envelope -- NO stability verdict (state='insufficient_budget').
    Caller sets stability_class='not_assessed' on each per-control entry.

    Uses ``band_endpoint_draws`` so each endpoint is a full ``(node_mapping, kappa)``
    EnsembleDraw — the degraded path MUST carry kappa uncertainty (it fires on the
    large-portfolio resource-pressure path; Slice 2 #439 plan-gate converged BLOCKER).

    Co-moving diagonal (all params shifted together), not a box extremum —
    inherited #419 envelope convention; mixed weight/kappa corners are unevaluated.

    Args:
        per_control_value_fn: BATCHED callable(list[EnsembleDraw]) ->
            list[{control_id: float}] (one dict per draw, in the SAME order).
        control_ids: canonical order of control IDs.
        sigma: PINNED logit-space perturbation width from the stored band; when
            None defaults to live Settings. Must be passed as the pinned value
            so the endpoints reproduce under Settings drift (Sec-I2).
    """
    ends = band_endpoint_draws(sigma=sigma)
    names = list(ends)
    vlist = per_control_value_fn([ends[n] for n in names])
    vals = {names[i]: vlist[i] for i in range(len(names))}
    out: dict[str, dict[str, Any]] = {}
    for c in control_ids:
        lo, base, hi = sorted((vals["low"][c], vals["base"][c], vals["high"][c]))
        out[c] = {
            "reduction_p5": lo,
            "reduction_p50": base,
            "reduction_p95": hi,
            "rank_p50": control_ids.index(c),
            "rank_min": control_ids.index(c),
            "rank_max": control_ids.index(c),
            "stability_class": "not_assessed",
        }
    # headline = v(N) = sum per-control (efficiency); deterministic endpoint envelope
    hl = sorted(sum(vals[n][c] for c in control_ids) for n in ("low", "base", "high"))
    return out, {"reduction_p5": hl[0], "reduction_p50": hl[1], "reduction_p95": hl[2]}


# #432 item 1: finalize_composition cost as a fraction of a FULL compose
# (precompose_parts + finalize_composition) for one v(S) evaluation. With the
# cross-draw ComposedParts cache, draw 1 pays full compose per distinct subset
# while draws 2..K pay only finalize — so the per-draw budget charge is
# two-rate, not linear. Measured 2026-07-10 (timeit min-of-5, Darwin arm64,
# .venv python; fair_cam.tests helpers, mixed LEC/VMC/DSC assignments),
# covering BOTH the exact branch (n<=12) and the sampled branch up to
# MAX_ATTRIBUTION_CONTROLS = 64 (methodology review: the application domain
# must not exceed the measurement domain):
#   n=5:  ratio 0.375    n=16: ratio 0.342
#   n=8:  ratio 0.379    n=32: ratio 0.304
#   n=12: ratio 0.400    n=48: ratio 0.270
#                        n=64: ratio 0.251
# The ratio PEAKS at the exact-branch max (0.400 at n=12) and FALLS with
# subset size beyond it (precompose grows faster than finalize), so the pin
# above the global max under-claims capacity across the whole n<=64 domain —
# an operational cost-model constant, not a FAIR calibration.
# NOT read back at ensemble time: the executor pins the value into the stored
# band ("finalize_cost_ratio") at band creation so re-runs reproduce under a
# future re-measurement of this constant (same Sec-I2 discipline as
# eval_budget/min_draws).
FINALIZE_TO_COMPOSE_COST_RATIO = 0.45


def run_weight_ensemble(
    per_control_value_fn: Callable[[list[EnsembleDraw]], list[dict[str, float]]],
    control_ids: list[str],
    *,
    rng: np.random.Generator,
    draws: int,
    eval_cost_per_draw: int,
    min_draws: int | None = None,
    eval_budget: int | None = None,
    first_draw_cost: int | None = None,
    sampler: Callable[..., EnsembleDraw] = sample_ensemble_draw,
    sigma: float | None = None,
    compute_rank_stability: bool = True,
) -> dict[str, Any]:
    """K draws -> per-control reduction ranges + (if compute_rank_stability) rank-stability.

    Stability vs CANONICAL rank (+-1 tolerance band, Meth-I4) + pairwise
    indistinguishability. SINGLE runs pass compute_rank_stability=False (Meth-B6): the
    displayed SINGLE order is effectiveness-based, not the Shapley basis the ensemble
    ranks, so only the basis-agnostic dollar RANGES are faithful -- stability/
    indistinguishable verdicts are deferred.

    Degrades K under budget; below min_draws it does NOT emit a stability verdict
    (Arch-B1/Meth-I2): deterministic band-endpoint envelope with
    state='insufficient_budget'.

    Always returns the PINNED key set: headline, per_control, kendall_tau_p50,
    topk_preservation_k, topk_preservation_prob, indistinguishable_pairs,
    rank_stability_available, draws_used, degraded, state.

    Args:
        per_control_value_fn: BATCHED callable(list[EnsembleDraw]) ->
            list[{control_id: float}] — one dict per draw in the SAME order.
            Receives ALL K ``(node_mapping, kappa)`` units at once (opaque hand-off
            — the runner never inspects the payloads) so the callee can vectorize
            the per-coalition value computation across the draw axis (#419/#439).
        control_ids: canonical (band-center, descending) order of control IDs.
        rng: NumPy random generator (caller-owned; mutated in-place for reproducibility).
        draws: requested number of ensemble draws.
        eval_cost_per_draw: estimated Shapley eval cost per draw (for budget gating).
        min_draws: minimum draws required for stability verdict; MUST be passed as the
            PINNED value from the stored band (not None / live Settings) for degraded-path
            reproducibility (Sec-I2). Defaults to settings.weight_ensemble_min_draws only
            when no pinned value is available (first run before the band is stored).
        eval_budget: total evaluation budget; MUST be passed as the PINNED value from the
            stored band for reproducibility. Defaults to settings.weight_ensemble_eval_budget
            only when no pinned value is available.
        sampler: callable(rng) -> EnsembleDraw. Defaults to sample_ensemble_draw.
        sigma: PINNED logit-space perturbation width from the stored band; when None defaults
            to live Settings. Passed through to the band-endpoint fallback
            (_deterministic_envelope) so the deterministic endpoints also honor the pinned
            sigma and reproduce under Settings drift (Sec-I2).
        compute_rank_stability: False for SINGLE-scenario runs (ranges only).

    Returns:
        dict with pinned keys (see above).
    """
    cfg = get_settings()
    budget = cfg.weight_ensemble_eval_budget if eval_budget is None else eval_budget
    kmin = cfg.weight_ensemble_min_draws if min_draws is None else min_draws
    k, degraded = draws, False

    if first_draw_cost is not None:
        # #432 two-rate (cache-credited) cost model: draw 1 pays the full
        # compose cost per distinct subset (first_draw_cost); draws 2..K hit
        # the cross-draw ComposedParts cache and pay only the finalize-rate
        # eval_cost_per_draw. Affordable K solves
        #   first_draw_cost + (K-1) * eval_cost_per_draw <= budget.
        total = first_draw_cost + max(0, k - 1) * eval_cost_per_draw
        if k > 0 and total > budget:
            if first_draw_cost > budget:
                k = 0
            elif eval_cost_per_draw > 0:
                k = min(k, 1 + (budget - first_draw_cost) // eval_cost_per_draw)
            degraded = k < draws
            if degraded:
                logger.warning(
                    "weight ensemble degraded from %d to %d draws "
                    "(budget %d, first draw %d, %d/subsequent-draw)",
                    draws,
                    k,
                    budget,
                    first_draw_cost,
                    eval_cost_per_draw,
                )
    elif eval_cost_per_draw > 0 and k * eval_cost_per_draw > budget:
        k = max(0, budget // eval_cost_per_draw)
        degraded = True
        logger.warning(
            "weight ensemble degraded from %d to %d draws (budget %d, %d/draw)",
            draws,
            k,
            budget,
            eval_cost_per_draw,
        )

    canonical_rank = {c: i for i, c in enumerate(control_ids)}

    if k < kmin:
        logger.warning(
            "weight ensemble below min_draws (%d < %d) -> band-endpoint fallback", k, kmin
        )
        env, headline = _deterministic_envelope(per_control_value_fn, control_ids, sigma=sigma)
        return {
            "headline": headline,
            "per_control": env,
            "kendall_tau_p50": None,
            "topk_preservation_k": None,  # no verdict on the insufficient-budget path (spec §4)
            "topk_preservation_prob": None,
            "indistinguishable_pairs": [],
            "rank_stability_available": False,
            "draws_used": k,
            "degraded": degraded,
            "state": "insufficient_budget",
        }

    values: dict[str, list[float]] = {c: [] for c in control_ids}
    ranks: dict[str, list[int]] = {c: [] for c in control_ids}
    taus: list[float] = []
    headline_draws: list[float] = []  # v(N) per draw = sum per-control (efficiency)
    topk = max(1, len(control_ids) // 3)
    canon_topk = set(control_ids[:topk])
    topk_hits = 0
    # ordered canonical pairs (a before b in control_ids); count order-flips
    pairs = [
        (control_ids[i], control_ids[j])
        for i in range(len(control_ids))
        for j in range(i + 1, len(control_ids))
    ]
    flips: dict[tuple[str, str], int] = dict.fromkeys(pairs, 0)

    # Pre-sample the K draws in the SAME rng order as the old per-iteration loop
    # (K sequential sampler(rng) calls => identical rng evolution => identical
    # draws => reproducibility-preserving), then batch the value computation
    # across the whole draw axis (#419/#439). The accumulation body is UNCHANGED.
    draws_list = [sampler(rng) for _ in range(k)]  # opaque (node_mapping, kappa) EnsembleDraws
    per_draw_vals = per_control_value_fn(draws_list)
    for i in range(k):
        vals = per_draw_vals[i]
        # tie-break by control_id for determinism when values are equal
        order = sorted(control_ids, key=lambda c: (-vals[c], c))
        rank_of = {c: r for r, c in enumerate(order)}
        for c in control_ids:
            values[c].append(vals[c])
            ranks[c].append(rank_of[c])
        headline_draws.append(sum(vals[c] for c in control_ids))
        taus.append(_kendall_tau(order, control_ids))
        if set(order[:topk]) == canon_topk:
            topk_hits += 1
        for a, b in pairs:
            if rank_of[a] > rank_of[b]:  # canonical a-before-b flipped
                flips[(a, b)] += 1

    def _pct(xs: list[float], q: float) -> float:
        return float(np.percentile(xs, q)) if xs else 0.0

    stable_thr = cfg.weight_rank_stable_threshold
    indist_thr = cfg.weight_rank_indistinguishable_threshold

    per_control: dict[str, dict[str, Any]] = {}
    for c in control_ids:
        rk = ranks[c]
        # Meth-I4: tolerance-band vs CANONICAL rank (+-1), not exact-hold.
        # Exact-rank-hold floods the dense middle of large rankings with 'unstable'
        # flags that are methodologically meaningless (adjacent ranks differ by
        # <1 OOM in risk reduction). stability_class is a SECONDARY signal;
        # top-k preservation + indistinguishable_pairs are the decision-relevant
        # verdicts at scale.
        held = sum(1 for r in rk if abs(r - canonical_rank[c]) <= 1) / len(rk)
        per_control[c] = {
            "reduction_p5": _pct(values[c], 5),
            "reduction_p50": _pct(values[c], 50),
            "reduction_p95": _pct(values[c], 95),
            "rank_p50": int(np.median(rk)),
            "rank_min": min(rk),
            "rank_max": max(rk),
            # Meth-B6: only emit stability verdict when ensemble basis == displayed order
            "stability_class": (
                ("stable" if held >= stable_thr else "unstable")
                if compute_rank_stability
                else "not_applicable"
            ),
        }

    indistinguishable = (
        [[a, b] for (a, b), n in flips.items() if (n / k) >= indist_thr]
        if compute_rank_stability
        else []
    )

    return {
        "headline": {
            "reduction_p5": _pct(headline_draws, 5),
            "reduction_p50": _pct(headline_draws, 50),
            "reduction_p95": _pct(headline_draws, 95),
        },
        "per_control": per_control,
        "kendall_tau_p50": float(np.median(taus)) if compute_rank_stability else None,
        "topk_preservation_k": topk if compute_rank_stability else None,
        "topk_preservation_prob": (topk_hits / k) if compute_rank_stability else None,
        "indistinguishable_pairs": indistinguishable,
        "rank_stability_available": compute_rank_stability,
        "draws_used": k,
        "degraded": degraded,
        "state": "ok",
    }
