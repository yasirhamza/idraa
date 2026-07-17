"""Smoke tests for the MC iteration benchmark harness (Task 8, Arch-I2).

Keeps n small (2k-8k) so the suite stays fast -- the real envelope-gate
numbers (10k/100k/500k/1M) are captured separately via
`python -m scripts.bench_mc_iterations`, NOT run in pytest.
"""

from __future__ import annotations

import sys

import pytest
from scripts.bench_mc_iterations import measure_in_subprocess


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="resource is POSIX-only; peak-RSS unmeasurable on Windows",
)
def test_subprocess_measure_returns_metrics() -> None:
    r = measure_in_subprocess(n=2_000, m=1)
    assert r["wall_s"] > 0
    assert r["peak_rss_mb"] > 0
    assert r["stored_bytes"] > 0


def test_stored_bytes_scale_with_n() -> None:
    small = measure_in_subprocess(n=2_000, m=1)["stored_bytes"]
    big = measure_in_subprocess(n=8_000, m=1)["stored_bytes"]
    assert big > small
