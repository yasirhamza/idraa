"""Scenario-library export serializer — entry → LibraryEntrySeed-shaped dict (Task 5).

Pins the canonical export contract:

- ``EXPORT_FIELDS == list(LibraryEntrySeed.model_fields)`` — exactly the authored
  fields, in order. This is the load-bearing invariant that guarantees a
  downloaded bundle re-imports cleanly through the seed schema.
- ``entry_to_seed_obj`` emits EXACTLY those field keys; it EXCLUDES the
  DB-managed fields (``id`` / ``version`` / ``row_version`` / ``source`` /
  ``created_at`` / ``updated_at``) so a downloaded bundle is content-only and
  re-imports as fresh ``imported`` entries.
- Enum-valued attributes (threat_event_type / threat_actor_type / asset_class)
  serialize as their ``.value`` string, not the enum repr.
"""

from __future__ import annotations

import uuid

from idraa.models.enums import AssetClass, ThreatActorType, ThreatCategory
from idraa.models.scenario_library import ScenarioLibraryEntry
from idraa.services.library_bundle_export import (
    EXPORT_FIELDS,
    entry_to_seed_obj,
)
from idraa.services.seed_library_loader import LibraryEntrySeed


def _entry(**overrides: object) -> ScenarioLibraryEntry:
    """Build an in-memory entry with all authored fields populated.

    Not flushed — ``entry_to_seed_obj`` only needs attribute reads.
    """
    base: dict[str, object] = {
        "id": uuid.uuid4(),
        "version": 1,
        "slug": "export-unit-a",
        "name": "Export Unit A",
        "status": "published",
        "threat_event_type": ThreatCategory.RANSOMWARE,
        "threat_actor_type": ThreatActorType.CYBERCRIMINALS,
        "asset_class": AssetClass.SYSTEMS,
        "attack_vector": "phishing",
        "tags": ["a", "b"],
        "description": "A 20+ character description for the export unit test.",
        "example_incidents": "Some incident.",
        "source_citations": ["cite-1", "cite-2"],
        "canonical_fair_gap": "A 20+ character canonical FAIR gap note here.",
        "applicable_industries": ["manufacturing"],
        "applicable_sub_sectors": None,
        "applicable_org_sizes": None,
        "threat_event_frequency": {"distribution": "PERT", "low": 1, "mode": 2, "high": 3},
        "vulnerability": {"distribution": "PERT", "low": 0.1, "mode": 0.2, "high": 0.3},
        "primary_loss": {"distribution": "PERT", "low": 100000, "mode": 1000000, "high": 5000000},
        "secondary_loss": {"distribution": "PERT", "low": 1000, "mode": 5000, "high": 9000},
        "suggested_control_ids": ["ctrl-1"],
        "standards_references": {"nist_csf": ["PR.AC-1"]},
        "calibration_anchor": {"industry": "other", "revenue_tier": "100m_to_1b"},
        "loss_tier": "anecdotal",
        # Milestone B (#loss-pert-overhaul): transient ORM objects have no
        # server_default; real DB rows always carry a loss_shape.
        "loss_shape": "capped",
    }
    base.update(overrides)
    return ScenarioLibraryEntry(**base)


def test_export_fields_equal_seed_model_fields() -> None:
    """The load-bearing contract: EXPORT_FIELDS is EXACTLY the authored seed fields."""
    assert list(LibraryEntrySeed.model_fields) == EXPORT_FIELDS
    assert set(EXPORT_FIELDS) == set(LibraryEntrySeed.model_fields)


def test_entry_to_seed_obj_emits_exactly_seed_keys() -> None:
    out = entry_to_seed_obj(_entry())
    assert set(out.keys()) == set(LibraryEntrySeed.model_fields)


def test_entry_to_seed_obj_excludes_db_managed_fields() -> None:
    out = entry_to_seed_obj(_entry())
    for excluded in ("id", "version", "row_version", "source", "created_at", "updated_at"):
        assert excluded not in out


def test_entry_to_seed_obj_serializes_enums_as_value_strings() -> None:
    out = entry_to_seed_obj(_entry())
    assert out["threat_event_type"] == "ransomware"
    assert out["threat_actor_type"] == "cybercriminals"
    assert out["asset_class"] == "systems"
    # Plain-str column passes through unchanged.
    assert out["status"] == "published"


def test_entry_to_seed_obj_preserves_distributions_and_collections() -> None:
    out = entry_to_seed_obj(_entry())
    # Distributions come out as real dicts (not embedded strings).
    assert out["threat_event_frequency"] == {
        "distribution": "PERT",
        "low": 1,
        "mode": 2,
        "high": 3,
    }
    # int/float preserved exactly.
    assert isinstance(out["threat_event_frequency"]["low"], int)
    assert isinstance(out["vulnerability"]["low"], float)
    assert out["tags"] == ["a", "b"]
    assert out["calibration_anchor"] == {"industry": "other", "revenue_tier": "100m_to_1b"}


def test_entry_to_seed_obj_validates_back_through_seed_schema() -> None:
    """A single exported obj round-trips through LibraryEntrySeed cleanly."""
    out = entry_to_seed_obj(_entry())
    seed = LibraryEntrySeed.model_validate(out)
    assert seed.slug == "export-unit-a"
    assert seed.threat_event_type == "ransomware"
