"""Epic D-i (#497): LibraryEntrySeed.loss_form_profile parses + validates."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from idraa.services.seed_library_loader import LibraryEntrySeed

_BASE: dict[str, object] = {
    "slug": "s",
    "name": "n",
    "status": "published",
    "threat_event_type": "ransomware",
    "threat_actor_type": "cybercriminals",
    "asset_class": "data",
    "description": "d" * 25,
    "canonical_fair_gap": "g" * 25,
    "threat_event_frequency": {"distribution": "PERT", "low": 0.1, "mode": 0.5, "high": 2.0},
    "vulnerability": {"distribution": "PERT", "low": 0.1, "mode": 0.3, "high": 0.6},
    "primary_loss": {"distribution": "lognormal", "mean": 13.0, "sigma": 1.5},
    "calibration_anchor": {"industry": "manufacturing", "revenue_tier": "100m_to_1b"},
}


def test_loss_form_profile_defaults_empty() -> None:
    seed = LibraryEntrySeed(**_BASE)
    assert seed.loss_form_profile == []


def test_loss_form_profile_parses_valid_forms() -> None:
    seed = LibraryEntrySeed(
        **_BASE,
        loss_form_profile=[
            {
                "form": "response",
                "kind": "primary",
                "magnitude_basis": "IBM CODB 2024 per-record",
                "citations": ["IBM CODB 2024 Fig 5 p.12"],
                "verified": True,
                "composition_role": "dominant",
            },
        ],
    )
    assert seed.loss_form_profile[0].form == "response"
    assert seed.loss_form_profile[0].kind == "primary"


def test_loss_form_profile_rejects_bad_kind() -> None:
    with pytest.raises(ValidationError):
        LibraryEntrySeed(
            **_BASE,
            loss_form_profile=[
                {
                    "form": "response",
                    "kind": "tertiary",
                    "magnitude_basis": "x",
                    "citations": [],
                    "verified": False,
                    "composition_role": "dominant",
                }
            ],
        )


def test_loss_form_profile_rejects_unknown_key() -> None:
    # extra="forbid" (Sec2): a typo'd/unknown form key is rejected, not dropped.
    with pytest.raises(ValidationError):
        LibraryEntrySeed(
            **_BASE,
            loss_form_profile=[
                {
                    "form": "response",
                    "kind": "primary",
                    "magnitude_basis": "x",
                    "citations": [],
                    "verified": True,
                    "composition_role": "dominant",
                    "bogus": 1,
                }
            ],
        )


def test_loss_form_profile_caps_oversized_fields() -> None:
    # Sec1 caps enforced pre-persist: magnitude_basis > 512, a citation > 512,
    # citations list > 32, and profile length > 12 all reject.
    base_form = {
        "form": "response",
        "kind": "primary",
        "verified": True,
        "composition_role": "dominant",
        "citations": [],
    }
    with pytest.raises(ValidationError):  # magnitude_basis too long
        LibraryEntrySeed(**_BASE, loss_form_profile=[{**base_form, "magnitude_basis": "x" * 513}])
    with pytest.raises(ValidationError):  # single citation too long
        LibraryEntrySeed(
            **_BASE,
            loss_form_profile=[{**base_form, "magnitude_basis": "ok", "citations": ["y" * 513]}],
        )
    with pytest.raises(ValidationError):  # too many citations
        LibraryEntrySeed(
            **_BASE,
            loss_form_profile=[{**base_form, "magnitude_basis": "ok", "citations": ["c"] * 33}],
        )
    with pytest.raises(ValidationError):  # profile too long
        LibraryEntrySeed(**_BASE, loss_form_profile=[{**base_form, "magnitude_basis": "ok"}] * 13)
