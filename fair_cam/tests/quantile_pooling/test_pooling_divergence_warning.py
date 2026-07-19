"""Issue #27: divergent-fit pooling logs INFO-level observability.

Post-mixture (issue #27 via #25), ``combine_lognorm_trunc``/``combine_norm``
no longer parameter-average divergent fits into a single distribution
covering neither expert's stated range -- each fit survives as its own
explicit ``LognormMixture``/``NormMixture`` component (linear opinion pool,
Clemen & Winkler 1999). The divergence criterion (central 90% intervals
disjoint on the location scale) is UNCHANGED from the #343 interim guard,
but the log demotes WARNING -> INFO: it is no longer a defect signal (the
pool no longer distorts anything), just observability that two experts
disagreed sharply. Single-fit pooling and agreeing experts stay silent --
the common path is unchanged.
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


def test_issue_343_worked_example_logs_info(caplog: pytest.LogCaptureFixture) -> None:
    """The #343 worked example: SME A $1k-$10k vs SME B $1M-$50M. Under
    the mixture (#27) this no longer needs a WARNING -- both experts'
    fits survive verbatim as explicit components, not averaged -- but the
    divergence is still observable at INFO."""
    a = _lnfit(8.06, 0.70)
    b = _lnfit(15.77, 1.19)
    with caplog.at_level(logging.INFO, logger=_LOGGER):
        pooled = combine_lognorm_trunc([a, b])
    assert any("divergent" in rec.message.lower() for rec in caplog.records)
    # The mixture keeps both fits verbatim -- nothing left to distort.
    assert pooled.components == (a, b)
    assert pooled.weights == pytest.approx((0.5, 0.5))


def test_agreeing_experts_do_not_log_info(caplog: pytest.LogCaptureFixture) -> None:
    a = _lnfit(10.0, 0.8)
    b = _lnfit(10.5, 0.9)  # heavy overlap
    with caplog.at_level(logging.INFO, logger=_LOGGER):
        combine_lognorm_trunc([a, b])
    assert not [rec for rec in caplog.records if "divergent" in rec.message.lower()]


def test_single_fit_does_not_log_info(caplog: pytest.LogCaptureFixture) -> None:
    """Pooling one fit is identity under any scheme — never logs."""
    with caplog.at_level(logging.INFO, logger=_LOGGER):
        combine_lognorm_trunc([_lnfit(8.06, 0.70)])
    assert not [rec for rec in caplog.records if "divergent" in rec.message.lower()]


def test_combine_norm_divergent_logs_info(caplog: pytest.LogCaptureFixture) -> None:
    a = NormalTruncFit(mean=0.05, sd=0.01, min_support=0.0, max_support=1.0)
    b = NormalTruncFit(mean=0.90, sd=0.02, min_support=0.0, max_support=1.0)
    with caplog.at_level(logging.INFO, logger=_LOGGER):
        pooled = combine_norm([a, b])
    assert any("divergent" in rec.message.lower() for rec in caplog.records)
    # The log never perturbs the pooled components (mirrors the lognormal pin).
    assert pooled.components == (a, b)
    assert pooled.weights == pytest.approx((0.5, 0.5))


def test_divergence_log_carries_actionable_context(caplog: pytest.LogCaptureFixture) -> None:
    """The message must name that divergence is now represented by the
    mixture (not distorted by averaging) and point at the issue."""
    with caplog.at_level(logging.INFO, logger=_LOGGER):
        combine_lognorm_trunc([_lnfit(8.06, 0.70), _lnfit(15.77, 1.19)])
    msg = next(rec.message for rec in caplog.records if "divergent" in rec.message.lower())
    assert "mixture" in msg.lower()
    assert "#27" in msg
