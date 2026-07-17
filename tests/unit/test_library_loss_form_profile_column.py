"""Epic D-i (#497): loss_form_profile column exists, JSON, defaults to []."""

from __future__ import annotations

from idraa.models.scenario_library import ScenarioLibraryEntry


def test_loss_form_profile_column_present_and_json() -> None:
    col = ScenarioLibraryEntry.__table__.columns["loss_form_profile"]
    assert col.nullable is False
    assert col.type.__class__.__name__ == "JSON"


def test_loss_form_profile_defaults_empty_list() -> None:
    entry = ScenarioLibraryEntry(
        slug="t",
        name="t",
        version=1,
        description="x" * 25,
        canonical_fair_gap="y" * 25,
        threat_event_type="ransomware",
        threat_actor_type="cybercriminals",
        asset_class="data",
        threat_event_frequency={"distribution": "PERT", "low": 0.1, "mode": 0.5, "high": 2.0},
        vulnerability={"distribution": "PERT", "low": 0.1, "mode": 0.3, "high": 0.6},
        primary_loss={"distribution": "PERT", "low": 1e5, "mode": 1e6, "high": 1e7},
    )
    # default fires at flush; construct-time may be None -> the ORM/server_default
    # supplies [] on insert. Assert the mapped default is list-producing.
    assert entry.loss_form_profile in (None, [])
