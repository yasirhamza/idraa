"""Pure-math tests for elapsed_time_to_opeff (PR μ.1)."""

import math

import pytest

from fair_cam.normalization import elapsed_time_to_opeff


def test_zero_elapsed_yields_full_opeff() -> None:
    assert elapsed_time_to_opeff(0.0, tau=7.0) == 1.0


def test_median_elapsed_yields_half_opeff() -> None:
    tau = 7.0
    assert elapsed_time_to_opeff(tau * math.log(2), tau=tau) == pytest.approx(0.5)


def test_large_elapsed_underflows_toward_zero() -> None:
    assert elapsed_time_to_opeff(1000.0, tau=7.0) < 1e-50


def test_negative_tau_raises() -> None:
    with pytest.raises(ValueError, match="tau must be positive"):
        elapsed_time_to_opeff(1.0, tau=0.0)


def test_negative_elapsed_raises() -> None:
    with pytest.raises(ValueError, match="elapsed_time must be non-negative"):
        elapsed_time_to_opeff(-1.0, tau=7.0)
