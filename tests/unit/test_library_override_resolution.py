"""merge_canonical_and_override: field-wise merge of entry + override.

Spec §7.4:
- threat_event_frequency: override.tef if not None else entry.tef
- vulnerability: override.vulnerability if not None else entry.vulnerability
- primary_loss: override.primary_loss if not None else entry.primary_loss
- secondary_loss: override.secondary_loss if not None else entry.secondary_loss
- entry's narrative/taxonomy fields are NOT overridable.
"""

from __future__ import annotations

import uuid
from typing import Any

from idraa.models.enums import AssetClass, ThreatActorType, ThreatCategory
from idraa.models.scenario_library import ScenarioLibraryEntry, ScenarioLibraryOverride
from idraa.services.scenario_library import merge_canonical_and_override


def _e(**overrides: Any) -> ScenarioLibraryEntry:
    base: dict[str, Any] = {
        "id": uuid.uuid4(),
        "version": 1,
        "slug": "x",
        "name": "x",
        "status": "published",
        "threat_event_type": ThreatCategory.RANSOMWARE,
        "threat_actor_type": ThreatActorType.CYBERCRIMINALS,
        "asset_class": AssetClass.SYSTEMS,
        "tags": [],
        "description": "d",
        "canonical_fair_gap": "g",
        "source_citations": [],
        "threat_event_frequency": {"low": 1.0, "mode": 4.0, "high": 12.0},
        "vulnerability": {"low": 0.05, "mode": 0.20, "high": 0.50},
        "primary_loss": {"low": 1.0, "mode": 2.0, "high": 3.0},
        "secondary_loss": None,
        "suggested_control_ids": [],
    }
    base.update(overrides)
    return ScenarioLibraryEntry(**base)


def _o(**overrides: Any) -> ScenarioLibraryOverride:
    base: dict[str, Any] = {
        "organization_id": uuid.uuid4(),
        "library_entry_id": uuid.uuid4(),
        "library_entry_version": 1,
        "threat_event_frequency": None,
        "vulnerability": None,
        "primary_loss": None,
        "secondary_loss": None,
        "reason": "r",
        "version": 1,
    }
    base.update(overrides)
    return ScenarioLibraryOverride(**base)


def test_merge_no_override_returns_entry_values() -> None:
    entry = _e()
    merged = merge_canonical_and_override(entry, override=None)
    assert merged.threat_event_frequency == entry.threat_event_frequency
    assert merged.vulnerability == entry.vulnerability
    assert merged.primary_loss == entry.primary_loss
    assert merged.secondary_loss == entry.secondary_loss


def test_merge_override_tef_replaces_entry_tef() -> None:
    entry = _e()
    override = _o(threat_event_frequency={"low": 2.0, "mode": 8.0, "high": 24.0})
    merged = merge_canonical_and_override(entry, override)
    assert merged.threat_event_frequency == {"low": 2.0, "mode": 8.0, "high": 24.0}
    assert merged.vulnerability == entry.vulnerability
    assert merged.primary_loss == entry.primary_loss


def test_merge_override_with_null_field_falls_through_to_entry() -> None:
    entry = _e()
    override = _o(
        threat_event_frequency={"low": 2.0, "mode": 8.0, "high": 24.0},
        vulnerability=None,
    )
    merged = merge_canonical_and_override(entry, override)
    assert merged.vulnerability == entry.vulnerability


def test_merge_secondary_loss_passthrough_both_null() -> None:
    entry = _e(secondary_loss=None)
    override = _o(secondary_loss=None)
    merged = merge_canonical_and_override(entry, override)
    assert merged.secondary_loss is None
