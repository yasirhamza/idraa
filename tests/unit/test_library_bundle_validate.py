from __future__ import annotations

from typing import Any

import pytest

from idraa.services.library_bundle_import import _validate_entries

# Published-community allowlist for the mechanical _validate_entries(published_slugs=...)
# rule (brief Task 5 §5).
_PUB = {"cybercriminals", "nation_state", "privileged_insider", "hacktivists", "third_party"}


def _e(**over: Any) -> dict[str, Any]:
    base = {
        "slug": "s1",
        "name": "N",
        "status": "published",
        "threat_event_type": "ransomware",
        "threat_actor_type": "cybercriminals",
        "threat_community": "cybercriminals",
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
    preview, errors, seeds = _validate_entries(
        [(0, _e())], existing_slugs=set(), published_slugs=_PUB
    )
    assert errors == [] and preview[0]["action"] == "add" and seeds[0] is not None


def test_existing_slug_skipped() -> None:
    preview, errors, seeds = _validate_entries(
        [(0, _e(slug="dup"))], existing_slugs={"dup"}, published_slugs=_PUB
    )
    assert preview[0]["action"] == "skip" and seeds[0] is None and errors == []


def test_intra_bundle_duplicate_slug_skipped() -> None:
    preview, errors, seeds = _validate_entries(
        [(0, _e(slug="x")), (1, _e(slug="x"))], existing_slugs=set(), published_slugs=_PUB
    )
    assert preview[0]["action"] == "add" and preview[1]["action"] == "skip"


def test_short_description_is_error() -> None:
    preview, errors, seeds = _validate_entries(
        [(0, _e(description="short"))], existing_slugs=set(), published_slugs=_PUB
    )
    assert preview[0]["action"] == "error" and errors


def test_bad_revenue_tier_is_error() -> None:
    preview, errors, seeds = _validate_entries(
        [(0, _e(calibration_anchor={"industry": "other", "revenue_tier": "bogus"}))],
        existing_slugs=set(),
        published_slugs=_PUB,
    )
    assert preview[0]["action"] == "error"


def test_bad_enum_is_error() -> None:
    preview, errors, seeds = _validate_entries(
        [(0, _e(threat_event_type="nope"))], existing_slugs=set(), published_slugs=_PUB
    )
    assert preview[0]["action"] == "error"


def test_non_pert_distribution_is_error() -> None:
    preview, errors, seeds = _validate_entries(
        [(0, _e(primary_loss={"distribution": "normal", "low": 1, "mode": 2, "high": 3}))],
        existing_slugs=set(),
        published_slugs=_PUB,
    )
    assert preview[0]["action"] == "error"


def test_inf_distribution_is_error() -> None:
    preview, errors, seeds = _validate_entries(
        [(0, _e(primary_loss={"distribution": "PERT", "low": 1, "mode": 2, "high": float("inf")}))],
        existing_slugs=set(),
        published_slugs=_PUB,
    )
    assert preview[0]["action"] == "error"


def test_vuln_above_one_is_error() -> None:
    preview, errors, seeds = _validate_entries(
        [(0, _e(vulnerability={"distribution": "PERT", "low": 0.1, "mode": 0.5, "high": 1.5}))],
        existing_slugs=set(),
        published_slugs=_PUB,
    )
    assert preview[0]["action"] == "error"
    assert errors and "vulner" in (errors[0]["field"] + errors[0]["reason"]).lower()


def test_oversize_description_is_error() -> None:
    preview, errors, seeds = _validate_entries(
        [(0, _e(description="x" * 5000))], existing_slugs=set(), published_slugs=_PUB
    )
    assert preview[0]["action"] == "error" and any(e["field"] == "description" for e in errors)


def test_oversize_citation_list_is_error() -> None:
    preview, errors, seeds = _validate_entries(
        [(0, _e(source_citations=["c"] * 100))], existing_slugs=set(), published_slugs=_PUB
    )
    assert preview[0]["action"] == "error"


def test_unknown_key_is_error() -> None:
    preview, errors, seeds = _validate_entries(
        [(0, _e(surprise="x"))], existing_slugs=set(), published_slugs=_PUB
    )
    assert preview[0]["action"] == "error" and any("unknown" in e["reason"].lower() for e in errors)


def test_non_published_status_is_error() -> None:
    preview, errors, seeds = _validate_entries(
        [(0, _e(status="draft"))], existing_slugs=set(), published_slugs=_PUB
    )
    assert preview[0]["action"] == "error" and any(e["field"] == "status" for e in errors)


# --- Epic B (#326): lognormal bundle entry -----------------------------------


def test_lognormal_primary_loss_entry_is_add() -> None:
    preview, errors, seeds = _validate_entries(
        [(0, _e(primary_loss={"distribution": "lognormal", "mean": 6.9, "sigma": 1.0}))],
        existing_slugs=set(),
        published_slugs=_PUB,
    )
    assert errors == [] and preview[0]["action"] == "add" and seeds[0] is not None


def test_lognormal_vulnerability_entry_is_error() -> None:
    # vuln must stay PERT even in bundle imports.
    preview, errors, seeds = _validate_entries(
        [(0, _e(vulnerability={"distribution": "lognormal", "mean": -1.0, "sigma": 0.5}))],
        existing_slugs=set(),
        published_slugs=_PUB,
    )
    assert preview[0]["action"] == "error"


def test_lognormal_bundle_bad_sigma_is_error() -> None:
    preview, errors, seeds = _validate_entries(
        [(0, _e(primary_loss={"distribution": "lognormal", "mean": 6.9, "sigma": 50}))],
        existing_slugs=set(),
        published_slugs=_PUB,
    )
    assert preview[0]["action"] == "error"


def test_lognormal_bundle_non_numeric_mean_is_error() -> None:
    preview, errors, seeds = _validate_entries(
        [(0, _e(primary_loss={"distribution": "lognormal", "mean": "abc", "sigma": 1.0}))],
        existing_slugs=set(),
        published_slugs=_PUB,
    )
    assert preview[0]["action"] == "error"


# --- PR2 D14 (Task 4b): library entries must NOT acquire `max` ---------------
# library_bundle_import shares scenario_import's _structural_dist_problem
# chokepoint; the widened key-set that lets a SCENARIO row optionally carry a
# `max` (Task 4b) must not also let a smuggled foreign-org cap onto an
# org-agnostic library template (D14). allow_max=False at that call site
# rejects `max` exactly like any other unknown key.


def test_lognormal_bundle_entry_with_max_is_rejected() -> None:
    preview, errors, seeds = _validate_entries(
        [
            (
                0,
                _e(
                    primary_loss={
                        "distribution": "lognormal",
                        "mean": 6.9,
                        "sigma": 1.0,
                        "max": 1_000_000_000.0,
                    }
                ),
            )
        ],
        existing_slugs=set(),
        published_slugs=_PUB,
    )
    assert preview[0]["action"] == "error"
    assert seeds[0] is None
    assert errors and "primary_loss" in errors[0]["field"]


def test_lognormal_mixture_bundle_entry_with_max_is_rejected() -> None:
    mix = {
        "distribution": "lognormal_mixture",
        "components": [
            {"mean": 8.06, "sigma": 0.70, "weight": 0.5},
            {"mean": 15.77, "sigma": 1.19, "weight": 0.5},
        ],
        "max": 1_000_000_000.0,
    }
    preview, errors, seeds = _validate_entries(
        [(0, _e(primary_loss=mix))], existing_slugs=set(), published_slugs=_PUB
    )
    assert preview[0]["action"] == "error"
    assert seeds[0] is None


# --- Task 5: threat_community resolution + published-community gate ---------


def test_resolve_entry_threat_community_rules() -> None:
    from idraa.services.library_bundle_import import resolve_entry_threat_community

    assert (
        resolve_entry_threat_community({"threat_actor_type": "insider_malicious"})
        == "privileged_insider"
    )
    assert (
        resolve_entry_threat_community(
            {"threat_community": "third_party", "threat_actor_type": "cybercriminals"}
        )
        == "third_party"
    )
    with pytest.raises(KeyError):
        resolve_entry_threat_community({"threat_actor_type": "martians"})


def test_validate_entries_unknown_slug_and_unknown_legacy_are_per_entry_errors() -> None:
    good = _e(threat_community="cybercriminals")
    bad_slug = _e(slug="b", threat_community="martians")
    bad_legacy = {k: v for k, v in _e(slug="c").items() if k != "threat_community"} | {
        "threat_actor_type": "martians"
    }
    preview, errors, seeds = _validate_entries(
        [(0, good), (1, bad_slug), (2, bad_legacy)],
        existing_slugs=set(),
        published_slugs={"cybercriminals", "nation_state"},
    )
    assert [p["action"] for p in preview] == ["add", "error", "error"]
    assert {e["index"] for e in errors} == {1, 2} and seeds[1] is None and seeds[2] is None
