"""#299 hand-math anchor (register C7 / C7a): an inherited meta-only variant
composes IDENTICALLY to the same meta rows linked as an ordinary control, and
the hybrid's LEC row acts only when the full control is linked.

Hand math (EDR lec_prev_resistance cap 0.7, cov 0.8, r0 0.8; meta
vmc_prev_reduce_variance_prob cap 0.7, cov 0.8, r0 0.85; kappa 0.5):
  E_meta      = 0.7 * 0.8 * 0.85                 = 0.476
  r_eff       = 0.8 + 0.2 * 0.5 * 0.476          = 0.8476
  E_lec_prev  = 0.7 * 0.8 * 0.8476               = 0.474656
  tef mult    = 1 - 0.8 * 0.474656               = 0.6202752
  vuln mult   = 1 - 0.9 * 0.474656               = 0.5728096
Full hybrid linked (adds lec_prev_resistance 0.9/1.0/1.0):
  E_lec_prev  = 1 - (1 - 0.474656) * (1 - 0.9)   = 0.9474656
Expected column = these constants; actual column = the engine (plan-gate r1,
methodology lane §3 anchor A). Pins are approx(rel=1e-12) per the repo's
cross-platform float-pin convention.
"""

from __future__ import annotations

import pytest

from fair_cam.models.composition_topology import BooleanGroup
from fair_cam.models.control import (
    Control,
    ControlType,
    CostModel,
    FairCamControlFunctionAssignment,
    subfunction_to_domain,
)
from fair_cam.models.sub_function import FairCamSubFunction as FcSF
from fair_cam.risk_engine.control_aware import _group_comp_to_node_multipliers
from fair_cam.risk_engine.group_composition import GroupComposition, compose_groups

REL = 1e-12
E_META = 0.476
E_LEC_PREV = 0.474656
TEF_MULT = 0.6202752
VULN_MULT = 0.5728096
E_LEC_PREV_FULL_HYBRID = 0.9474656


def _ctrl(cid: str, rows: list[tuple[FcSF, float, float, float]]) -> Control:
    asg = [
        FairCamControlFunctionAssignment(
            sub_function=sf, capability_value=cap, coverage=cov, reliability=rel
        )
        for sf, cap, cov, rel in rows
    ]
    return Control(
        control_id=cid,
        name=cid,
        description="pin",
        domain=subfunction_to_domain(asg[0].sub_function),
        control_type=ControlType.TECHNICAL,
        cost_model=CostModel(),
        assignments=asg,
    )


EDR = _ctrl("edr", [(FcSF.LEC_PREV_RESISTANCE, 0.7, 0.8, 0.8)])
VARIANT = _ctrl("pm:meta", [(FcSF.VMC_PREV_REDUCE_VARIANCE_PROB, 0.7, 0.8, 0.85)])
TWIN = _ctrl("twin", [(FcSF.VMC_PREV_REDUCE_VARIANCE_PROB, 0.7, 0.8, 0.85)])
PM_FULL = _ctrl(
    "pm",
    [
        (FcSF.VMC_PREV_REDUCE_VARIANCE_PROB, 0.7, 0.8, 0.85),
        (FcSF.LEC_PREV_RESISTANCE, 0.9, 1.0, 1.0),
    ],
)


def _mults(comp: GroupComposition) -> dict[str, float]:
    return _group_comp_to_node_multipliers(comp)


def test_variant_matches_hand_math():
    comp = compose_groups([EDR, VARIANT])
    assert comp.meta_strength == pytest.approx(E_META, rel=REL)
    assert comp.group_effectiveness[BooleanGroup.LEC_PREVENTION] == pytest.approx(
        E_LEC_PREV, rel=REL
    )
    m = _mults(comp)
    assert m["threat_event_frequency"] == pytest.approx(TEF_MULT, rel=REL)
    assert m["vulnerability"] == pytest.approx(VULN_MULT, rel=REL)


def test_variant_is_bit_identical_to_explicit_twin():
    a = compose_groups([EDR, VARIANT])
    b = compose_groups([EDR, TWIN])
    # same operations on the same operands in the same process -> exact equality is valid
    assert a.meta_strength == b.meta_strength
    assert a.group_effectiveness == b.group_effectiveness
    assert _mults(a) == _mults(b)


def test_lec_row_of_hybrid_acts_only_when_fully_linked():
    inherited = compose_groups([EDR, VARIANT]).group_effectiveness[BooleanGroup.LEC_PREVENTION]
    linked = compose_groups([EDR, PM_FULL]).group_effectiveness[BooleanGroup.LEC_PREVENTION]
    assert inherited is not None
    assert linked is not None
    assert inherited == pytest.approx(E_LEC_PREV, rel=REL)
    assert linked == pytest.approx(E_LEC_PREV_FULL_HYBRID, rel=REL)
    assert linked > inherited
