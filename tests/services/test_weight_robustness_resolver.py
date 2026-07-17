"""Tests for the weight-robustness resolver (issue #419, Task 2).

Covers: co-variation map shape, canonical values, logit-normal sampling
correctness (unit-interval, shared-Z within-function co-variation),
σ=0 identity, band endpoints, and canonical immutability.
"""

from __future__ import annotations

import numpy as np
import pytest
from fair_cam.models.composition_topology import BooleanGroup

from idraa.services.weight_robustness import (
    CANONICAL_PARAM_SLOTS,
    _logit,
    band_endpoint_mappings,
    canonical_param_values,
    sample_ensemble_draw,
)


def test_param_map_covaries_shared_constants():
    """Slice 2 (#439) D1 update: DSC_PREVENTION's direct secondary_loss/primary_loss
    target was retired (value now flows through the kappa reliability coupling,
    not GROUP_NODE_MAPPING), so `magnitude.secondary` drops from 3 shared slots
    (LEC_RESPONSE + DSC_PREVENTION + DSC_IDENTIFICATION_CORRECTION_PAIR) to 1
    (LEC_RESPONSE only). Likewise `vmc.vuln` (VMC_VARIANCE_PREVENTION +
    VMC_IDENTIFICATION_CORRECTION_PAIR) drops to 0 slots and no longer exists as
    a CANONICAL_PARAM_SLOTS key -- it is replaced by the non-slot `meta.kappa`
    param key (see canonical_param_values() / CORRELATION_GROUPS "meta" group).
    """
    slots = CANONICAL_PARAM_SLOTS["magnitude.secondary"]
    assert (BooleanGroup.LEC_RESPONSE, "secondary_loss") in slots
    assert len(slots) == 1
    assert "vmc.vuln" not in CANONICAL_PARAM_SLOTS
    # no no-op param exposed
    assert "magnitude.secondary" in CANONICAL_PARAM_SLOTS
    assert not any(
        g == BooleanGroup.LEC_DETECTION_RESPONSE_PAIR
        for slots in CANONICAL_PARAM_SLOTS.values()
        for g, _ in slots
    )


def test_canonical_values():
    v = canonical_param_values()
    assert v["prevention.tef"] == 0.8 and v["magnitude.secondary"] == 0.5


def test_sample_covaries_and_stays_in_unit_interval():
    """Slice 2 (#439) D1 update: the pre-Slice-2 cross-group co-variation example
    (LEC_RESPONSE and DSC_PREVENTION sharing the "magnitude.secondary" key) no
    longer applies -- DSC_PREVENTION's direct secondary_loss target was retired,
    so every CANONICAL_PARAM_SLOTS key now maps to exactly ONE (group, node)
    slot (verified below), leaving no second slot to compare for equality.
    Cross-slot co-variation for keys WITH multiple slots is still covered by
    `test_within_function_weights_covary` (shared logit-Z shift) and
    `tests/contracts/test_weight_robustness_covariation.py`.
    """
    rng = np.random.default_rng(0)
    m = sample_ensemble_draw(rng, sigma=0.6)[0]
    # logit-normal => every weight strictly in (0,1), no atom at the ceiling
    for _key, slots in CANONICAL_PARAM_SLOTS.items():
        assert len(slots) == 1  # D1: no shared-constant multi-slot keys remain
        for g, node in slots:
            assert 0.0 < m[g].weights[node] < 1.0


def test_within_function_weights_covary():  # Meth-I5: prevention tef & vuln share one Z
    canon = canonical_param_values()
    # the shared-Z logit shift is identical for both prevention weights on any draw:
    rng = np.random.default_rng(5)
    m = sample_ensemble_draw(rng, sigma=0.6)[0]
    shift_tef = _logit(m[BooleanGroup.LEC_PREVENTION].weights["threat_event_frequency"]) - _logit(
        canon["prevention.tef"]
    )
    shift_vuln = _logit(m[BooleanGroup.LEC_PREVENTION].weights["vulnerability"]) - _logit(
        canon["prevention.vuln"]
    )
    assert shift_tef == pytest.approx(shift_vuln)  # one shared draw, not two independent


def test_sigma_zero_is_identity():  # Test-N1: degenerate band collapses to canonical
    m = sample_ensemble_draw(np.random.default_rng(0), sigma=0.0)[0]
    canon = canonical_param_values()
    for key, slots in CANONICAL_PARAM_SLOTS.items():
        for g, node in slots:
            assert m[g].weights[node] == pytest.approx(canon[key])


def test_endpoints_low_base_high():
    e = band_endpoint_mappings()  # ±2σ logit-space edges; base == canonical
    base = e["base"][BooleanGroup.LEC_PREVENTION].weights["vulnerability"]
    lo = e["low"][BooleanGroup.LEC_PREVENTION].weights["vulnerability"]
    hi = e["high"][BooleanGroup.LEC_PREVENTION].weights["vulnerability"]
    assert base == 0.9
    assert 0.0 < lo < base < hi < 1.0  # smooth, bounded, no clamp at 1


def test_canonical_immutability():
    """Sec-N6: _apply_param_values must never mutate the module-global GROUP_NODE_MAPPING."""
    from fair_cam.models.composition_topology import GROUP_NODE_MAPPING

    before = {g: dict(m.weights) for g, m in GROUP_NODE_MAPPING.items()}
    # sample a draw (which calls _apply_param_values internally)
    rng = np.random.default_rng(42)
    sample_ensemble_draw(rng, sigma=0.6)
    # also exercise endpoints
    band_endpoint_mappings()
    after = {g: dict(m.weights) for g, m in GROUP_NODE_MAPPING.items()}
    assert before == after, "GROUP_NODE_MAPPING was mutated by weight_robustness"
