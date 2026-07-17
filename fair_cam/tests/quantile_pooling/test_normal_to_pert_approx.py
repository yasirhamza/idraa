"""normal_to_pert_approx: 4-branch clamp + precedence + sentinel cases.
Per spec §9.1 Meth-1/2 R2 + Meth-9 R3 (vuln-specific via MD-4a)."""

from __future__ import annotations

import math

import pytest
from fair_cam.quantile_pooling import (
    ModeClampReason,
    NormalTruncFit,
    normal_to_pert_approx,
)


def test_mode_equals_mean_for_unclamped_normal() -> None:
    """Normal's mode = mean for the untruncated parent."""
    fit = NormalTruncFit(mean=0.5, sd=0.1, min_support=0.0, max_support=1.0)
    pert, reason = normal_to_pert_approx(fit)
    assert pert.mode == pytest.approx(0.5, abs=1e-9)
    assert reason is None
    assert pert.low <= pert.mode <= pert.high


def test_clamp_negative_mean_to_min_support() -> None:
    """Spec §9.1 explicit test: vuln fit with low=0.001, high=0.05 can
    pool to fit.mean ≈ -0.02; clamp to min_support=0.0."""
    fit = NormalTruncFit(mean=-0.05, sd=0.1, min_support=0.0, max_support=1.0)
    pert, reason = normal_to_pert_approx(fit)
    assert reason == ModeClampReason.UNTRUNCATED_MODE_BELOW_MIN_SUPPORT
    assert pert.mode >= 0.0


def test_clamp_above_max_support() -> None:
    fit = NormalTruncFit(mean=1.5, sd=0.1, min_support=0.0, max_support=1.0)
    pert, reason = normal_to_pert_approx(fit)
    assert reason == ModeClampReason.UNTRUNCATED_MODE_ABOVE_MAX_SUPPORT
    assert pert.mode <= 1.0


def test_precedence_support_boundary_wins_over_pert_boundary() -> None:
    """Meth-9 R3: when raw_mode > max_support AND raw_mode > high
    simultaneously, support-boundary clamp wins."""
    fit = NormalTruncFit(mean=2.0, sd=0.1, min_support=0.0, max_support=1.0)
    pert, reason = normal_to_pert_approx(fit)
    # raw_mode=2.0, max_support=1.0 -> support precedence
    assert reason == ModeClampReason.UNTRUNCATED_MODE_ABOVE_MAX_SUPPORT


def test_mode_unclamped_inside_bounds_no_reason() -> None:
    fit = NormalTruncFit(mean=0.3, sd=0.05, min_support=0.0, max_support=1.0)
    pert, reason = normal_to_pert_approx(fit)
    assert reason is None


def test_pert_triple_ordered() -> None:
    """low <= mode <= high invariant for any normal fit."""
    fit = NormalTruncFit(mean=0.5, sd=0.2, min_support=0.0, max_support=1.0)
    pert, _ = normal_to_pert_approx(fit)
    assert pert.low <= pert.mode <= pert.high


def test_unbounded_normal_does_not_crash() -> None:
    """When max_support=inf, _qnormtrunc must handle the inf branch."""
    fit = NormalTruncFit(mean=10.0, sd=2.0, min_support=-math.inf, max_support=math.inf)
    pert, reason = normal_to_pert_approx(fit)
    assert reason is None
    assert pert.low < pert.mode < pert.high
