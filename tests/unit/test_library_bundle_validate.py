from __future__ import annotations

from typing import Any

from idraa.services.library_bundle_import import _validate_entries


def _e(**over: Any) -> dict[str, Any]:
    base = {
        "slug": "s1",
        "name": "N",
        "status": "published",
        "threat_event_type": "ransomware",
        "threat_actor_type": "cybercriminals",
        "asset_class": "systems",
        "description": "d" * 25,
        "canonical_fair_gap": "g" * 25,
        "threat_event_frequency": {"distribution": "PERT", "low": 1, "mode": 2, "high": 3},
        "vulnerability": {"distribution": "PERT", "low": 0.1, "mode": 0.2, "high": 0.3},
        "primary_loss": {"distribution": "PERT", "low": 1, "mode": 2, "high": 3},
        "calibration_anchor": {"industry": "other", "revenue_tier": "100m_to_1b"},
    }
    base.update(over)
    return base


def test_valid_entry_is_add() -> None:
    preview, errors, seeds = _validate_entries([(0, _e())], existing_slugs=set())
    assert errors == [] and preview[0]["action"] == "add" and seeds[0] is not None


def test_existing_slug_skipped() -> None:
    preview, errors, seeds = _validate_entries([(0, _e(slug="dup"))], existing_slugs={"dup"})
    assert preview[0]["action"] == "skip" and seeds[0] is None and errors == []


def test_intra_bundle_duplicate_slug_skipped() -> None:
    preview, errors, seeds = _validate_entries(
        [(0, _e(slug="x")), (1, _e(slug="x"))], existing_slugs=set()
    )
    assert preview[0]["action"] == "add" and preview[1]["action"] == "skip"


def test_short_description_is_error() -> None:
    preview, errors, seeds = _validate_entries([(0, _e(description="short"))], existing_slugs=set())
    assert preview[0]["action"] == "error" and errors


def test_bad_revenue_tier_is_error() -> None:
    preview, errors, seeds = _validate_entries(
        [(0, _e(calibration_anchor={"industry": "x", "revenue_tier": "bogus"}))],
        existing_slugs=set(),
    )
    assert preview[0]["action"] == "error"


def test_bad_enum_is_error() -> None:
    preview, errors, seeds = _validate_entries(
        [(0, _e(threat_event_type="nope"))], existing_slugs=set()
    )
    assert preview[0]["action"] == "error"


def test_non_pert_distribution_is_error() -> None:
    preview, errors, seeds = _validate_entries(
        [(0, _e(primary_loss={"distribution": "normal", "low": 1, "mode": 2, "high": 3}))],
        existing_slugs=set(),
    )
    assert preview[0]["action"] == "error"


def test_inf_distribution_is_error() -> None:
    preview, errors, seeds = _validate_entries(
        [(0, _e(primary_loss={"distribution": "PERT", "low": 1, "mode": 2, "high": float("inf")}))],
        existing_slugs=set(),
    )
    assert preview[0]["action"] == "error"


def test_vuln_above_one_is_error() -> None:
    preview, errors, seeds = _validate_entries(
        [(0, _e(vulnerability={"distribution": "PERT", "low": 0.1, "mode": 0.5, "high": 1.5}))],
        existing_slugs=set(),
    )
    assert preview[0]["action"] == "error"
    assert errors and "vulner" in (errors[0]["field"] + errors[0]["reason"]).lower()


def test_oversize_description_is_error() -> None:
    preview, errors, seeds = _validate_entries(
        [(0, _e(description="x" * 5000))], existing_slugs=set()
    )
    assert preview[0]["action"] == "error" and any(e["field"] == "description" for e in errors)


def test_oversize_citation_list_is_error() -> None:
    preview, errors, seeds = _validate_entries(
        [(0, _e(source_citations=["c"] * 100))], existing_slugs=set()
    )
    assert preview[0]["action"] == "error"


def test_unknown_key_is_error() -> None:
    preview, errors, seeds = _validate_entries([(0, _e(surprise="x"))], existing_slugs=set())
    assert preview[0]["action"] == "error" and any("unknown" in e["reason"].lower() for e in errors)


def test_non_published_status_is_error() -> None:
    preview, errors, seeds = _validate_entries([(0, _e(status="draft"))], existing_slugs=set())
    assert preview[0]["action"] == "error" and any(e["field"] == "status" for e in errors)


# --- Epic B (#326): lognormal bundle entry -----------------------------------


def test_lognormal_primary_loss_entry_is_add() -> None:
    preview, errors, seeds = _validate_entries(
        [(0, _e(primary_loss={"distribution": "lognormal", "mean": 6.9, "sigma": 1.0}))],
        existing_slugs=set(),
    )
    assert errors == [] and preview[0]["action"] == "add" and seeds[0] is not None


def test_lognormal_vulnerability_entry_is_error() -> None:
    # vuln must stay PERT even in bundle imports.
    preview, errors, seeds = _validate_entries(
        [(0, _e(vulnerability={"distribution": "lognormal", "mean": -1.0, "sigma": 0.5}))],
        existing_slugs=set(),
    )
    assert preview[0]["action"] == "error"


def test_lognormal_bundle_bad_sigma_is_error() -> None:
    preview, errors, seeds = _validate_entries(
        [(0, _e(primary_loss={"distribution": "lognormal", "mean": 6.9, "sigma": 50}))],
        existing_slugs=set(),
    )
    assert preview[0]["action"] == "error"


def test_lognormal_bundle_non_numeric_mean_is_error() -> None:
    preview, errors, seeds = _validate_entries(
        [(0, _e(primary_loss={"distribution": "lognormal", "mean": "abc", "sigma": 1.0}))],
        existing_slugs=set(),
    )
    assert preview[0]["action"] == "error"
