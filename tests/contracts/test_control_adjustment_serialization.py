"""Issue #129 T2 — adapter iteration contract for breakdown serialization.

CLAUDE.md data-contract rule: for any list[ORM] → list[DTO] (or
single-DTO-with-list-field → dict-with-list-field), write a regression
test that N≥3 list items are preserved. Catches future [0] / [-1] /
[first] optimizations.
"""

from __future__ import annotations

from fair_cam.models.risk_enhanced import ControlAdjustment

from idraa.services.run_executor import _control_adjustment_to_dict


def test_breakdown_preserves_all_n_entries():
    """N=4 breakdown entries → 4 entries in serialized dict."""
    adj = ControlAdjustment(
        control_id="c1",
        control_name="Test",
        breakdown=[
            {"sub_function": f"sf_{i}", "unit": "probability", "capability_value_in": 0.5}
            for i in range(4)
        ],
    )
    result = _control_adjustment_to_dict(adj)
    assert len(result["breakdown"]) == 4
    assert [b["sub_function"] for b in result["breakdown"]] == [f"sf_{i}" for i in range(4)]


def test_breakdown_field_present_when_empty():
    """Empty breakdown list serializes as []; field present (not omitted)."""
    adj = ControlAdjustment(control_id="c1", control_name="Test")
    result = _control_adjustment_to_dict(adj)
    assert "breakdown" in result
    assert result["breakdown"] == []
