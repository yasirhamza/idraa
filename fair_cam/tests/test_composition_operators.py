"""Property tests pinning the weak-AND composition operator's contract (#130).

These encode spec §6 ("weak-AND operator — property proof"). The *rule*
(weak-AND) is FAIR-CAM Standard-cited; the *operator formula* (equal-weighted
arithmetic mean) is implementation-defined — see ``WEAK_AND_OPERATOR_PROVENANCE``
in ``fair_cam.composition``. These tests lock the five §6 properties so a future
re-implementation cannot silently regress the semantic.

Nesting-invariance is intentionally NOT tested: the FAIR-CAM Boolean topology is
flat-per-group (one weak-AND per group across its sub-functions), so weak-ANDs
never nest — there is no nesting invariant to preserve (plan-gate I-M6).
"""

import pytest

from fair_cam.composition import and_compose, weak_and_compose


def test_weak_and_bounded():
    # §6 row 1: output is an effectiveness, must stay in [0, 1].
    assert 0.0 <= weak_and_compose([0.0, 1.0]) <= 1.0


def test_weak_and_non_inhibition():
    # §6 row 2 (the defining property vs strict-AND): one zero does NOT zero
    # the group ("won't necessarily inhibit entirely"); strict-AND does.
    assert weak_and_compose([0.0, 0.8]) == pytest.approx(0.4)
    assert and_compose([0.0, 0.8]) == 0.0


def test_weak_and_monotonic_nondecreasing():
    # §6 row 3: diminishment — improving any operand never lowers the group.
    assert weak_and_compose([0.5, 0.5]) <= weak_and_compose([0.5, 0.9])


def test_weak_and_unanimity():
    # §6 row 4 (unanimity): all-perfect operands -> group perfect.
    assert weak_and_compose([1.0, 1.0, 1.0]) == pytest.approx(1.0)


def test_weak_and_idempotence():
    # §6 row 4 (idempotence, distinct from unanimity per plan-gate I-M6):
    # equal operands -> the group equals that common value, W(c,...,c) = c.
    assert weak_and_compose([0.3, 0.3]) == pytest.approx(0.3)


def test_weak_and_all_zero_is_zero():
    # plan-gate I-M6: all-zero operands -> 0.0 (boundary of non-inhibition).
    assert weak_and_compose([0.0, 0.0]) == 0.0


def test_weak_and_not_weak_or():
    # §6 row 5: must not exceed max(inputs) (a mean can never beat the max).
    vals = [0.2, 0.9]
    assert weak_and_compose(vals) <= max(vals)


def test_weak_and_empty_is_none():
    # spec §3.2.4: empty -> None ('all operands excluded' != '0 effectiveness').
    assert weak_and_compose([]) is None
