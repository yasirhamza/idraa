"""Epic D-i: Fenton–Wilkinson loss-form composition. Hand-math anchored per
CLAUDE.md numeric-verification rule (expected derived analytically in comments,
actual from the helper)."""

from __future__ import annotations

import math

import pytest

from idraa.services.loss_forms import compose_forms_to_lognormal


def test_single_form_is_identity() -> None:
    # One active form composes to itself unchanged.
    assert compose_forms_to_lognormal([(5.0, 1.3)]) == (5.0, 1.3)


def test_two_identical_forms_fenton_wilkinson() -> None:
    # Hand derivation for two identical forms (μ=0, σ=1) — analytic truth
    # (verified against an independent computation, finding M1):
    #   m = e^0.5 = 1.6487212707 ; v = (e^1 − 1)·e^1 = 4.6707742705
    #   M = 2m = 3.2974425414 ; V = 2v = 9.3415485409
    #   M² = 10.8731273138 ; 1 + V/M² = 1.8591409142
    #   σ_S² = ln(1.8591409142) = 0.6201145070 → σ_S = 0.7874734960
    #   ln(M) = ln2 + 0.5 = 1.1931471806
    #   μ_S = ln(M) − σ_S²/2 = 1.1931471806 − 0.3100572535 = 0.8830899271
    mu_s, sigma_s = compose_forms_to_lognormal([(0.0, 1.0), (0.0, 1.0)])
    assert mu_s == pytest.approx(0.8830899271, abs=1e-9)
    assert sigma_s == pytest.approx(0.7874734960, abs=1e-9)


def test_mean_is_preserved() -> None:
    # FW is mean-preserving: the composed lognormal's arithmetic mean equals
    # the sum of the input forms' arithmetic means (the property the dropped
    # dominant-form shortcut violated by up to 10%).
    forms = [(13.0, 1.0), (12.0, 1.5)]
    expected_mean = sum(math.exp(mu + s * s / 2) for mu, s in forms)
    mu_s, sigma_s = compose_forms_to_lognormal(forms)
    composed_mean = math.exp(mu_s + sigma_s * sigma_s / 2)
    assert composed_mean == pytest.approx(expected_mean, rel=1e-12)


def test_empty_forms_rejected() -> None:
    with pytest.raises(ValueError, match="at least one"):
        compose_forms_to_lognormal([])


def test_nonfinite_rejected() -> None:
    with pytest.raises(ValueError, match="finite"):
        compose_forms_to_lognormal([(0.0, float("inf"))])
