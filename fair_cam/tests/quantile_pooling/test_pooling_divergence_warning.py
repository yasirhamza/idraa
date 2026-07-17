"""#343 interim guard: divergent-fit pooling emits a WARNING.

Parameter-averaging pooling (MD-1 convenience port) concentrates mass
BETWEEN disagreeing experts — the pooled distribution can cover neither
expert's stated range (risk-understating bias). Until true mixture pooling
lands (#243), pooling logs a WARNING whenever any pair of fits is divergent
(central 90% intervals disjoint on the location scale). Single-fit pooling
and agreeing experts stay silent — the common path is unchanged.
"""

from __future__ import annotations

import logging

import pytest
from fair_cam.quantile_pooling import (
    Z_0_95,
    LogNormalTruncFit,
    NormalTruncFit,
    combine_lognorm_trunc,
    combine_norm,
)
from fair_cam.quantile_pooling._types import _DIVERGENCE_Z

_LOGGER = "fair_cam.quantile_pooling._types"


def _lnfit(meanlog: float, sdlog: float) -> LogNormalTruncFit:
    return LogNormalTruncFit(
        meanlog=meanlog, sdlog=sdlog, min_support=0.0, max_support=float("inf")
    )


def test_divergence_z_matches_z_0_95() -> None:
    """_DIVERGENCE_Z is Z_0_95 itself (direct import — _lognormal_native
    imports nothing from _types, so no cycle). Tripwire against a future
    re-localization drifting the constant."""
    assert _DIVERGENCE_Z == Z_0_95


def test_issue_343_worked_example_warns(caplog: pytest.LogCaptureFixture) -> None:
    """The #343 worked example: SME A $1k-$10k vs SME B $1M-$50M pools to a
    distribution covering NEITHER range — must warn."""
    a = _lnfit(8.06, 0.70)
    b = _lnfit(15.77, 1.19)
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        pooled = combine_lognorm_trunc([a, b])
    assert any("divergent" in rec.message.lower() for rec in caplog.records)
    # The pooling result itself is unchanged — this is a warning, not a fix.
    assert pooled.meanlog == pytest.approx((8.06 + 15.77) / 2)


def test_agreeing_experts_do_not_warn(caplog: pytest.LogCaptureFixture) -> None:
    a = _lnfit(10.0, 0.8)
    b = _lnfit(10.5, 0.9)  # heavy overlap
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        combine_lognorm_trunc([a, b])
    assert not [rec for rec in caplog.records if "divergent" in rec.message.lower()]


def test_single_fit_does_not_warn(caplog: pytest.LogCaptureFixture) -> None:
    """Pooling one fit is identity under any scheme — never warn."""
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        combine_lognorm_trunc([_lnfit(8.06, 0.70)])
    assert not [rec for rec in caplog.records if "divergent" in rec.message.lower()]


def test_combine_norm_divergent_warns(caplog: pytest.LogCaptureFixture) -> None:
    a = NormalTruncFit(mean=0.05, sd=0.01, min_support=0.0, max_support=1.0)
    b = NormalTruncFit(mean=0.90, sd=0.02, min_support=0.0, max_support=1.0)
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        pooled = combine_norm([a, b])
    assert any("divergent" in rec.message.lower() for rec in caplog.records)
    # The warning never perturbs the pooled value (mirrors the lognormal pin).
    assert pooled.mean == pytest.approx((0.05 + 0.90) / 2)


def test_warning_carries_actionable_context(caplog: pytest.LogCaptureFixture) -> None:
    """The message must name the failure mode (mass between experts /
    understated tails) and point at the tracking issue for the real fix."""
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        combine_lognorm_trunc([_lnfit(8.06, 0.70), _lnfit(15.77, 1.19)])
    msg = next(rec.message for rec in caplog.records if "divergent" in rec.message.lower())
    assert "#343" in msg
    assert "#243" in msg  # forward-pointer to the real (mixture) fix
    assert "mixture" in msg
