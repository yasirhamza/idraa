"""Unit tests for _dict_to_fair_distribution dispatch (Task 3, Epic B #326).

Verifies the lognormal branch and regression-guards the existing PERT branch.
"""

from __future__ import annotations

import pytest
from fair_cam.risk_engine.fair_core import DistributionType

from idraa.services.run_executor import _dict_to_fair_distribution


def test_lognormal_dispatch():
    d = _dict_to_fair_distribution({"distribution": "lognormal", "mean": 10.0, "sigma": 1.2})
    assert d.distribution_type == DistributionType.LOGNORMAL
    assert d.parameters == {"mean": 10.0, "sigma": 1.2}


def test_pert_dispatch_unregressed():
    d = _dict_to_fair_distribution({"distribution": "pert", "low": 1, "mode": 2, "high": 3})
    assert d.distribution_type == DistributionType.PERT
    assert d.parameters == {"low": 1.0, "mode": 2.0, "high": 3.0}


def test_lognormal_missing_key_raises():
    with pytest.raises((KeyError, ValueError)):
        _dict_to_fair_distribution({"distribution": "lognormal", "mean": 10.0})
