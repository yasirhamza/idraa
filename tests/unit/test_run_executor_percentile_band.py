"""Unit tests for the empirical central-95% percentile band (issue #202).

Issue #202: replace the heuristic "confidence interval" (control_aware's
``_calculate_confidence_metrics``, which derived a significance level from
``len(controls)`` and fed it into ``stats.norm.ppf`` to draw a Gaussian
SE-of-the-mean band) with an EMPIRICAL central-95% percentile band computed
in the v3 view-model layer from the persisted Monte-Carlo sample array.

The band reads p2.5/p97.5 of the residual (SINGLE) / aggregate-with-controls
(AGGREGATE) ``simulation_results`` — the pyfair 'Risk' column = the
annualized-loss / Risk distribution (the SAME surface the LEC and VaR read).
This is a descriptive statistic on already-simulated samples, a legitimate
v3 view-model derivation (not re-derived FAIR math).
"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from idraa.services.run_executor import (
    _BAND_HI_PCT,
    _BAND_INTERVAL_PCT,
    _BAND_LO_PCT,
    _build_loss_percentile_band,
)


def _fr(samples: list[float] | None) -> SimpleNamespace:
    return SimpleNamespace(
        simulation_results=np.asarray(samples) if samples else None,
        n_simulations=len(samples) if samples else 0,
    )


def test_band_constants_are_central_95() -> None:
    assert _BAND_LO_PCT == 2.5
    assert _BAND_HI_PCT == 97.5
    assert _BAND_INTERVAL_PCT == 95


def test_band_exact_values_on_known_array() -> None:
    # 1..1000 inclusive. numpy.percentile linear interpolation:
    #   index = (q/100)*(n-1) = (q/100)*999
    #   p2.5  -> index 24.975 -> 25 + 0.975*(26-25)   = 25.975
    #   p97.5 -> index 974.025 -> 975 + 0.025*(976-975) = 975.025
    samples = [float(x) for x in range(1, 1001)]
    band = _build_loss_percentile_band(_fr(samples))

    assert band["lower_bound"] == pytest.approx(25.975)
    assert band["upper_bound"] == pytest.approx(975.025)
    assert band["interval_pct"] == 95
    # sample_size preserved so the executive-PDF n_simulations source survives.
    assert band["sample_size"] == 1000


def test_band_has_no_heuristic_confidence_level_key() -> None:
    """The heuristic confidence_level / standard_error must be GONE.

    interval_pct is the fixed analyst-chosen central interval (95), not a
    function of control count. standard_error (Gaussian SE-of-the-mean) is
    meaningless for a percentile band and must not be persisted.
    """
    band = _build_loss_percentile_band(_fr([float(x) for x in range(1, 101)]))
    assert "confidence_level" not in band
    assert "standard_error" not in band
    assert band["interval_pct"] == 95


def test_band_empty_samples_returns_zero_band() -> None:
    band = _build_loss_percentile_band(_fr(None))
    assert band["lower_bound"] == 0.0
    assert band["upper_bound"] == 0.0
    # Still labelled with the fixed interval so consumers can detect a real
    # (new-schema) row vs a legacy row lacking interval_pct entirely.
    assert band["interval_pct"] == 95


def test_band_lower_below_upper_on_skewed_distribution() -> None:
    rng = np.random.default_rng(seed=7)
    samples = rng.lognormal(mean=10, sigma=1.5, size=20_000).tolist()
    band = _build_loss_percentile_band(_fr(samples))
    assert band["lower_bound"] < band["upper_bound"]
    # The band is the empirical central 95%, so exactly ~95% of mass lies
    # within [lo, hi] — independent of how many controls were applied.
    arr = np.asarray(samples)
    inside = ((arr >= band["lower_bound"]) & (arr <= band["upper_bound"])).mean()
    assert inside == pytest.approx(0.95, abs=0.01)
