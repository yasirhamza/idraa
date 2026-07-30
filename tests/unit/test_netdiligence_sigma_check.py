"""Pins for scripts/netdiligence_sigma_check.py (sigma-recal PR3, Task 0).

Every numeric pin below was EXECUTED before pinning (side-by-side vs
hand-math: nano smaller root = z - sqrt(z^2 - 2 ln(max/mean)) =
3.4814 - 1.8795 = 1.6019, executed 1.6018935371017509) and cross-checks two
independent plan-gate executions to displayed precision.
"""

from __future__ import annotations

import math

import pytest
from scipy.stats import norm
from scripts.netdiligence_sigma_check import (
    CAUSE_ROWS_SME,
    REVENUE_BANDS,
    SIGMA_READ_EXCLUDED,
    exact_emax_sigma_root,
    implied_sigma_roots,
    sampling_band,
)


def test_populations_pinned() -> None:
    assert [b[0] for b in REVENUE_BANDS] == [
        "nano_lt_50m",
        "micro_50m_300m",
        "small_300m_2b",
        "mid_2b_10b",
        "large_10b_100b",
        "mega_gt_100b",
        "unknown_rev",
    ]
    assert [c[0] for c in CAUSE_ROWS_SME] == [
        "business_email_compromise",
        "ransomware",
        "hacker",
        "wire_transfer_fraud",
        "theft_of_money",
    ]
    assert set(SIGMA_READ_EXCLUDED) == {"mega_gt_100b", "unknown_rev"}


def test_roots_satisfy_quadratic() -> None:
    for _name, n, mean, mx in REVENUE_BANDS + CAUSE_ROWS_SME:
        roots = implied_sigma_roots(n, mean, mx)
        if roots is None:
            continue
        z = float(norm.ppf(1 - 1 / n))
        for s in roots:
            assert abs(s * s - 2 * z * s + 2 * math.log(mx / mean)) < 1e-9


def test_plugin_no_root_set_is_exactly_the_executed_three() -> None:
    # Executed 2026-07-30 (matches both plan-gate round-3/4 executions):
    # the plug-in discriminant is negative for exactly these rows — a
    # plug-in ARTIFACT, never "heavier than lognormal".
    no_root = {
        name for name, n, mean, mx in REVENUE_BANDS if implied_sigma_roots(n, mean, mx) is None
    }
    assert no_root == {"mid_2b_10b", "large_10b_100b", "mega_gt_100b"}


def test_exact_emax_roots_real_where_plugin_has_none() -> None:
    # The exact-E[max] estimator returns real roots for all three plug-in
    # no-root rows — proving the no-root outcomes are plug-in artifacts.
    # Executed values (2026-07-30): mid 2.0934, large 1.8467, mega 0.8041.
    expected = {
        "mid_2b_10b": 2.0934,
        "large_10b_100b": 1.8467,
        "mega_gt_100b": 0.8041,
    }
    rows = {name: (n, mean, mx) for name, n, mean, mx in REVENUE_BANDS}
    for name, want in expected.items():
        n, mean, mx = rows[name]
        got = exact_emax_sigma_root(n, mean, mx)
        assert got is not None
        assert got == pytest.approx(want, abs=5e-4)


def test_exact_emax_bracket_guard() -> None:
    # A root exists iff max/mean < n (h is bounded above by ln n).
    assert exact_emax_sigma_root(4, 1.0, 5.0) is None  # ratio 5 >= n 4
    assert exact_emax_sigma_root(4, 38_300_000.0, 75_000_000.0) is not None


def test_nano_plugin_roots_pinned() -> None:
    # Hand-math side-by-side performed before pinning (module docstring).
    roots = implied_sigma_roots(4009, 142_000.0, 10_400_000.0)
    assert roots is not None
    assert roots[0] == pytest.approx(1.6018935371017509, rel=1e-12)
    assert roots[1] == pytest.approx(5.360823210881672, rel=1e-12)


def test_sampling_band_deterministic_and_pinned() -> None:
    # Same seed literal -> identical dict (protects printed doc figures from
    # silent RNG drift); nano p5/p50/p95 pinned from the executed run.
    b1 = sampling_band(4009)
    b2 = sampling_band(4009)
    assert b1 == b2
    assert b1["p5"] == pytest.approx(1.4749088026389687, rel=1e-12)
    assert b1["p50"] == pytest.approx(1.7912346025149577, rel=1e-12)
    assert b1["p95"] == pytest.approx(2.5195553939981545, rel=1e-12)
    assert b1["no_root_rate"] == pytest.approx(0.02, abs=1e-12)
    assert b1["z_ceiling"] == pytest.approx(3.4813583739917116, rel=1e-12)
