"""Seam tests for the node_mapping parameter in _group_comp_to_node_multipliers
and subset_reduction_closed_form (issue #419, Task 1).

These tests verify:
1. Passing GROUP_NODE_MAPPING explicitly is identical to omitting it (identity seam).
2. A perturbed mapping produces a different result (seam is load-bearing).
3. A broken 1-E*w invariant (negative-reduction invariant) raises ValueError (fail-loud guard).
"""

import copy

import pytest
from fair_cam.models.composition_topology import GROUP_NODE_MAPPING, BooleanGroup, NodeMapping
from fair_cam.risk_engine.control_attribution import subset_reduction_closed_form
from fair_cam.risk_engine.control_aware import _group_comp_to_node_multipliers
from fair_cam.risk_engine.group_composition import compose_groups
from fair_cam.tests.risk_engine._helpers import make_control, make_fair_parameters

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def sample_controls():
    """Three LEC-prevention controls with distinct capabilities."""
    return [
        make_control(
            control_id="a",
            assignments=[("lec_prev_resistance", "probability", 0.5)],
        ),
        make_control(
            control_id="b",
            assignments=[("lec_prev_resistance", "probability", 0.4)],
        ),
        make_control(
            control_id="c",
            assignments=[("lec_prev_resistance", "probability", 0.3)],
        ),
    ]


@pytest.fixture()
def sample_params():
    """Standard FAIRParameters for attribution tests."""
    return make_fair_parameters(tef=2.0, vuln=0.5, primary=1_000_000, secondary=400_000)


# ---------------------------------------------------------------------------
# Identity seam: omitting node_mapping == passing GROUP_NODE_MAPPING explicitly
# ---------------------------------------------------------------------------


def test_default_mapping_is_identity_to_global(sample_controls, sample_params):
    """Calling subset_reduction_closed_form without node_mapping returns the same
    result as calling it with the canonical GROUP_NODE_MAPPING.  This proves the
    default-arg seam is behaviour-preserving (no regression on the happy path).
    """
    assert subset_reduction_closed_form(sample_params, sample_controls) == (
        subset_reduction_closed_form(sample_params, sample_controls, GROUP_NODE_MAPPING)
    )


def test_group_comp_to_node_multipliers_default_identity(sample_controls):
    """_group_comp_to_node_multipliers with explicit GROUP_NODE_MAPPING == omitting it."""
    comp = compose_groups(sample_controls)
    assert _group_comp_to_node_multipliers(comp) == _group_comp_to_node_multipliers(
        comp, GROUP_NODE_MAPPING
    )


# ---------------------------------------------------------------------------
# Load-bearing seam: a perturbed mapping changes the result
# ---------------------------------------------------------------------------


def test_perturbed_mapping_changes_reduction(sample_controls, sample_params):
    """Substituting a different weight for LEC_PREVENTION changes the ALE reduction.

    This confirms the node_mapping seam is actually wired through — it is not
    dead code that ignores the parameter.
    """
    base = subset_reduction_closed_form(sample_params, sample_controls)
    bumped = copy.deepcopy(GROUP_NODE_MAPPING)
    nm = bumped[BooleanGroup.LEC_PREVENTION]
    bumped[BooleanGroup.LEC_PREVENTION] = NodeMapping(
        nm.targets,
        {"threat_event_frequency": 0.4, "vulnerability": 0.45},
        nm.citation,
        nm.weights_provenance,
    )
    assert subset_reduction_closed_form(sample_params, sample_controls, bumped) != base


# ---------------------------------------------------------------------------
# Fail-loud guard: negative-reduction invariant raises ValueError
# ---------------------------------------------------------------------------


def test_negative_reduction_raises(sample_params):
    """A node_mapping that forces a multiplier > 1 (breaking the 1-E*w <= 1 bound)
    must raise ValueError, not silently clamp or return a negative value.

    We achieve an invalid negative reduction by using a NEGATIVE weight on TEF
    while zeroing out the vulnerability weight (so no node reduction compensates).
    With w_tef=-10.0, w_vuln=0.0, and E=0.5, the TEF multiplier is
    1 - 0.5*(-10.0) = 6.0, which amplifies TEF 6× while leaving vulnerability
    unchanged.  adjusted_ale >> original_ale, so reduction < 0, tripping the
    fail-loud guard.

    Uses a SINGLE control so OR-composition gives a predictable E=0.5 and the
    amplification is not masked by vulnerability reduction on other nodes.

    In production (logit-normal w∈(0,1), E∈[0,1]) the guard NEVER fires
    (non-negative by construction); a negative reduction always means a broken
    invariant — fail loud, never silently floor.
    """
    single_control = make_control(
        control_id="x",
        assignments=[("lec_prev_resistance", "probability", 0.5)],
    )
    bad_mapping = copy.deepcopy(GROUP_NODE_MAPPING)
    nm = bad_mapping[BooleanGroup.LEC_PREVENTION]
    # w_tef=-10 => multiplier = 1 - 0.5*(-10) = 6.0 (massive TEF amplification)
    # w_vuln=0.0 => vulnerability unchanged → no compensating reduction
    bad_mapping[BooleanGroup.LEC_PREVENTION] = NodeMapping(
        nm.targets,
        {"threat_event_frequency": -10.0, "vulnerability": 0.0},
        nm.citation,
        nm.weights_provenance,
    )
    with pytest.raises(ValueError, match="negative subset reduction"):
        subset_reduction_closed_form(sample_params, [single_control], bad_mapping)
