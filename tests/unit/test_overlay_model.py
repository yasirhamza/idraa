"""OverlayDefinition + OverlayDefinitionRevision model unit tests."""

from __future__ import annotations

import uuid

import pytest


def test_overlay_definition_model_construction():
    """Constructed instance has UUID id, timestamps, expected required fields."""
    from idraa.models.overlay import OverlayDefinition

    org_id = uuid.uuid4()
    od = OverlayDefinition(
        organization_id=org_id,
        tag="critical_infrastructure",
        display_name="Critical Infrastructure",
        frequency_multiplier=1.4,
        magnitude_multiplier=2.0,
        sources=["docs/reference/calibration-sources/ic3_2025.md"],
        methodology="TEF +40%: ...",
    )

    assert od.id is not None
    assert od.created_at is not None
    assert od.updated_at is not None
    assert od.organization_id == org_id
    assert od.tag == "critical_infrastructure"
    assert od.frequency_multiplier == pytest.approx(1.4)
    assert od.magnitude_multiplier == pytest.approx(2.0)
    assert od.version == 1
    assert od.is_active is True


def test_overlay_definition_revision_model_construction():
    """Revision row carries methodology_change_reason and version explicitly."""
    from idraa.models.overlay import OverlayDefinitionRevision

    odr = OverlayDefinitionRevision(
        overlay_definition_id=uuid.uuid4(),
        version=2,
        tag="critical_infrastructure",
        display_name="Critical Infrastructure",
        frequency_multiplier=1.5,
        magnitude_multiplier=2.1,
        sources=["docs/reference/calibration-sources/ic3_2025.md"],
        methodology="Updated TEF based on Q2 advisory rate review.",
        methodology_change_reason="Q2 2026 review of CISA advisory rate for CI.",
    )

    assert odr.id is not None
    assert odr.version == 2
    assert odr.methodology_change_reason.startswith("Q2 2026 review")


def test_overlay_definition_uniqueness_via_table_args():
    """OverlayDefinition table has uniqueness on (organization_id, tag).
    Verify the table args / __table_args__ are declared."""
    from idraa.models.overlay import OverlayDefinition

    table_args = OverlayDefinition.__table_args__
    constraint_names = []
    for ta in table_args:
        if hasattr(ta, "name"):
            constraint_names.append(ta.name)
    assert "uq_overlay_per_org_tag" in constraint_names
