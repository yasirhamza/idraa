"""Issue #129 T2 — breakdown surfacing in _control_adjustment_to_dict."""

from __future__ import annotations

import json

from fair_cam.models.risk_enhanced import ControlAdjustment

from idraa.services.run_executor import _control_adjustment_to_dict


def test_breakdown_jsonsafe_passthrough():
    """All breakdown dict values are JSON-safe primitives."""
    adj = ControlAdjustment(
        control_id="c1",
        control_name="Test",
        breakdown=[
            {
                "sub_function": "lec_det_monitoring",
                "unit": "elapsed_time",
                "capability_value_in": 7.0,
                "tau_canonical": 280.0,
                "t_used": 7.0,
                "capability_was_null": False,
                "opeff": 0.0245,
                "loss_reduction_per_event": None,
            }
        ],
    )
    result = _control_adjustment_to_dict(adj)
    json.dumps(result)  # raises if non-JSON-safe types
