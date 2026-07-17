"""Unit tests for services/library_calibration.py.

Org revenue-tier loss scaling was REMOVED 2026-07-07 (the IRIS sector envelope IS
the calibration; the per-entry anchor tier no longer matched the loss basis).
These tests pin the new behavior: ``library_calibrated_pre_fill`` returns
ENTRY-ABSOLUTE pl/sl (override fall-through preserved) and ``None`` metadata,
regardless of the org context. ``CalibrationAnchor`` is retained as the anchor
provenance-shape validator. Pure functions — no DB, no async, no mocks.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from pydantic import ValidationError

from idraa.models.enums import AssetClass, ThreatActorType, ThreatCategory
from idraa.models.scenario_library import (
    ScenarioLibraryEntry,
    ScenarioLibraryOverride,
)
from idraa.services.library_calibration import (
    CalibrationAnchor,
    library_calibrated_pre_fill,
)


def _entry(**overrides: Any) -> ScenarioLibraryEntry:
    base: dict[str, Any] = {
        "id": uuid.uuid4(),
        "version": 1,
        "slug": "calib-test",
        "name": "Test",
        "status": "published",
        "threat_event_type": ThreatCategory.RANSOMWARE,
        "threat_actor_type": ThreatActorType.CYBERCRIMINALS,
        "asset_class": AssetClass.SYSTEMS,
        "tags": [],
        "description": "d",
        "canonical_fair_gap": "g",
        "source_citations": [],
        "threat_event_frequency": {"distribution": "PERT", "low": 1.0, "mode": 4.0, "high": 12.0},
        "vulnerability": {"distribution": "PERT", "low": 0.05, "mode": 0.20, "high": 0.50},
        "primary_loss": {"distribution": "lognormal", "mean": 13.8155, "sigma": 1.9602},
        "secondary_loss": {"distribution": "lognormal", "mean": 12.5, "sigma": 1.9602},
        "suggested_control_ids": [],
        "calibration_anchor": None,
    }
    base.update(overrides)
    return ScenarioLibraryEntry(**base)


def _override(**overrides: Any) -> ScenarioLibraryOverride:
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


# ---------- CalibrationAnchor Pydantic validator (retained provenance shape) ----------


def test_calibration_anchor_accepts_known_industry_and_tier() -> None:
    anchor = CalibrationAnchor(industry="healthcare", revenue_tier="10b_to_100b")
    assert anchor.industry == "healthcare"
    assert anchor.revenue_tier == "10b_to_100b"


def test_calibration_anchor_rejects_unknown_industry() -> None:
    with pytest.raises(ValidationError) as exc:
        CalibrationAnchor(industry="not-an-industry", revenue_tier="10b_to_100b")
    assert "industry" in str(exc.value).lower()


def test_calibration_anchor_rejects_unknown_revenue_tier() -> None:
    with pytest.raises(ValidationError) as exc:
        CalibrationAnchor(industry="healthcare", revenue_tier="trillions")
    assert "revenue_tier" in str(exc.value).lower()


# ---------- library_calibrated_pre_fill: entry-absolute, NO org scaling ----------


def test_pre_fill_returns_entry_absolutes_with_none_metadata() -> None:
    entry = _entry(calibration_anchor=None)
    form_dict, metadata = library_calibrated_pre_fill(entry, override=None)
    assert metadata is None
    assert form_dict["tef"] == entry.threat_event_frequency
    assert form_dict["vuln"] == entry.vulnerability
    assert form_dict["pl"] == entry.primary_loss
    assert form_dict["sl"] == entry.secondary_loss


def test_pre_fill_does_not_scale_loss_across_tier_delta() -> None:
    """THE regression guard for the 2026-07-07 fix: even when the entry carries
    an anchor tier, PL/SL are returned BYTE-IDENTICAL to the entry (no
    revenue-tier multiplier is applied) — org context has no bearing on the
    pre-fill result at all (issue #516 removed the ``ctx`` parameter)."""
    entry = _entry(calibration_anchor={"industry": "healthcare", "revenue_tier": "10b_to_100b"})
    form_dict, metadata = library_calibrated_pre_fill(entry, override=None)
    assert metadata is None, "no calibration metadata / banner after scaling removal"
    assert form_dict["pl"] == entry.primary_loss  # unchanged — not scaled
    assert form_dict["sl"] == entry.secondary_loss
    assert form_dict["pl"]["mean"] == entry.primary_loss["mean"]  # lognormal mean untouched
    assert form_dict["tef"] == entry.threat_event_frequency
    assert form_dict["vuln"] == entry.vulnerability


def test_pre_fill_null_secondary_loss_stays_null() -> None:
    entry = _entry(
        calibration_anchor={"industry": "healthcare", "revenue_tier": "10b_to_100b"},
        secondary_loss=None,
    )
    form_dict, metadata = library_calibrated_pre_fill(entry, override=None)
    assert metadata is None
    assert form_dict["sl"] is None
    assert form_dict["pl"] == entry.primary_loss


def test_pre_fill_ignores_malformed_anchor_returns_entry_absolute() -> None:
    """A malformed anchor no longer matters — the anchor is not read for scaling,
    so PL/SL are entry-absolute and no error is raised."""
    entry = _entry(calibration_anchor={"industry": "healthcare"})  # missing revenue_tier
    form_dict, metadata = library_calibrated_pre_fill(entry, override=None)
    assert metadata is None
    assert form_dict["pl"] == entry.primary_loss
    assert form_dict["sl"] == entry.secondary_loss


def test_pre_fill_override_present_takes_precedence() -> None:
    entry = _entry(calibration_anchor={"industry": "healthcare", "revenue_tier": "10b_to_100b"})
    override = _override(
        primary_loss={"distribution": "lognormal", "mean": 1.0, "sigma": 1.0},
        secondary_loss={"distribution": "lognormal", "mean": 0.5, "sigma": 1.0},
    )
    form_dict, metadata = library_calibrated_pre_fill(entry, override=override)
    assert metadata is None
    assert form_dict["pl"] == override.primary_loss
    assert form_dict["sl"] == override.secondary_loss
    # tef/vuln fall through to entry (override's None means fall-through)
    assert form_dict["tef"] == entry.threat_event_frequency
    assert form_dict["vuln"] == entry.vulnerability


# ── C-iii-a: calibration_anchor schema extension (retained) ────────────────────

_MINIMAL_VALID_KWARGS: dict[str, Any] = {
    "slug": "test-anchor-shape",
    "name": "test",
    "status": "published",
    "threat_event_type": "ransomware",
    "threat_actor_type": "cybercriminals",
    "asset_class": "systems",
    "description": "test description, must be at least twenty characters long",
    "canonical_fair_gap": "test canonical fair gap, must be at least twenty characters long",
    "threat_event_frequency": {"distribution": "PERT", "low": 1.0, "mode": 4.0, "high": 12.0},
    "vulnerability": {"distribution": "PERT", "low": 0.05, "mode": 0.20, "high": 0.50},
    "primary_loss": {"distribution": "PERT", "low": 100.0, "mode": 1000.0, "high": 10000.0},
    "calibration_anchor": {"industry": "healthcare", "revenue_tier": "100m_to_1b"},
}


def test_calibration_anchor_accepts_loss_anchor_key() -> None:
    from idraa.services.seed_library_loader import LibraryEntrySeed

    anchor_with_loss = {
        "industry": "healthcare",
        "revenue_tier": "100m_to_1b",
        "loss_anchor": "IRIS 2025 Figure A3 p.35 healthcare pair",
    }
    seed = LibraryEntrySeed.model_validate(
        {**_MINIMAL_VALID_KWARGS, "calibration_anchor": anchor_with_loss}
    )
    assert seed.calibration_anchor["loss_anchor"] == "IRIS 2025 Figure A3 p.35 healthcare pair"


def test_calibration_anchor_accepts_vuln_posture_key() -> None:
    from idraa.services.seed_library_loader import LibraryEntrySeed

    anchor_with_vuln = {
        "industry": "healthcare",
        "revenue_tier": "100m_to_1b",
        "vuln_posture": "inherent (control-naive) per fair-cam-methodology 'Vulnerability anchor'",
    }
    seed = LibraryEntrySeed.model_validate(
        {**_MINIMAL_VALID_KWARGS, "calibration_anchor": anchor_with_vuln}
    )
    assert seed.calibration_anchor["vuln_posture"] is not None


def test_calibration_anchor_accepts_both_new_keys() -> None:
    from idraa.services.seed_library_loader import LibraryEntrySeed

    anchor_full = {
        "industry": "healthcare",
        "revenue_tier": "100m_to_1b",
        "loss_anchor": "IRIS 2025 Figure A3 p.35 healthcare pair; supersedes old",
        "vuln_posture": "inherent (control-naive) per fair-cam-methodology 'Vulnerability anchor'",
    }
    seed = LibraryEntrySeed.model_validate(
        {**_MINIMAL_VALID_KWARGS, "calibration_anchor": anchor_full}
    )
    assert seed.calibration_anchor["loss_anchor"] is not None
    assert seed.calibration_anchor["vuln_posture"] is not None


def test_calibration_anchor_rejects_unknown_extra_key() -> None:
    from idraa.services.seed_library_loader import LibraryEntrySeed

    anchor_with_unknown = {
        "industry": "healthcare",
        "revenue_tier": "100m_to_1b",
        "unknown_key": "this should not be allowed",
    }
    with pytest.raises(ValueError, match="calibration_anchor"):
        LibraryEntrySeed.model_validate(
            {**_MINIMAL_VALID_KWARGS, "calibration_anchor": anchor_with_unknown}
        )


def test_calibration_anchor_model_accepts_new_optional_fields() -> None:
    anchor = CalibrationAnchor.model_validate(
        {
            "industry": "manufacturing",
            "revenue_tier": "1b_to_10b",
            "loss_anchor": "IRIS 2025 Figure A3 p.35 manufacturing pair",
            "vuln_posture": "inherent (control-naive) per fair-cam-methodology 'Vulnerability anchor'",
        }
    )
    assert anchor.loss_anchor == "IRIS 2025 Figure A3 p.35 manufacturing pair"
    assert anchor.vuln_posture is not None


def test_calibration_anchor_model_rejects_unknown_extra() -> None:
    with pytest.raises(ValidationError):
        CalibrationAnchor.model_validate(
            {
                "industry": "manufacturing",
                "revenue_tier": "1b_to_10b",
                "unknown_extra": "bad",
            }
        )


def test_seed_loader_rejects_entry_without_calibration_anchor() -> None:
    """PR gamma-4 (#115): LibraryEntrySeed still requires calibration_anchor."""
    from idraa.services.seed_library_loader import LibraryEntrySeed

    minimal_valid_kwargs = {
        "slug": "test-no-anchor",
        "name": "test",
        "status": "published",
        "threat_event_type": "ransomware",
        "threat_actor_type": "cybercriminals",
        "asset_class": "systems",
        "description": "test description, must be at least twenty characters long",
        "canonical_fair_gap": "test canonical fair gap, must be at least twenty characters long",
        "threat_event_frequency": {"distribution": "PERT", "low": 1.0, "mode": 4.0, "high": 12.0},
        "vulnerability": {"distribution": "PERT", "low": 0.05, "mode": 0.20, "high": 0.50},
        "primary_loss": {"distribution": "PERT", "low": 100.0, "mode": 1000.0, "high": 10000.0},
        # calibration_anchor intentionally absent
    }
    with pytest.raises(ValidationError) as exc:
        LibraryEntrySeed.model_validate(minimal_valid_kwargs)
    assert "calibration_anchor" in str(exc.value).lower()
