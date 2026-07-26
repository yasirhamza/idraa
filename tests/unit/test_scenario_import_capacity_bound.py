"""PR2 D13/D18/D19 capacity-bound epic: import producer (Task 4b).

Covers `docs/superpowers/plans/2026-07-25-capacity-bound-pr2.md` Task 4b
acceptance criteria at the PURE `_validate_rows`/`_structural_dist_problem`
layer -- ``capacity_max`` is a plain value the caller computes (per the
design, ``_validate_rows`` stays pure: no org fetch, no module global), so
every case below is exercised WITHOUT a database:

- Key-set widening: lognormal/lognormal_mixture tolerate an OPTIONAL `max`;
  any other unknown key is still rejected.
- Library ENTRIES (a shared chokepoint with `library_bundle_import`) must
  NOT acquire `max` -- covered in tests/unit/test_library_bundle_validate.py
  (the `allow_max=False` call-site flag).
- CSV structurally cannot carry `max` -> minted from the importing org.
  JSON explicit `max` + revenue set -> preserved, never overwritten.
  JSON explicit `max` + NULL-revenue org -> STILL triggers D18 (explicit
  `max` does not bypass D18).
- D18 row-level error naming the row, with the design's pinned copy
  (reused verbatim from Task 4a via services/capacity_bound_copy.py).
- D19 floor conflict -> row-level error with the pinned floor-conflict copy
  (reused verbatim via services/capacity_bound_copy.py's wrap function).

routes/scenario_import.py's swap from `require_sole_org` to
`db.get(Organization, user.organization_id)` is covered at the route level
in tests/integration/test_scenario_import_routes.py (existing RBAC/happy-path
coverage already exercises that code path end to end for every request).
"""

from __future__ import annotations

import math
from typing import Any

import pytest

from idraa.services.capacity_bound_copy import D18_REVENUE_MESSAGE, D19_FLOOR_MARKER
from idraa.services.scenario_import import _structural_dist_problem, _validate_rows

_Z95 = 1.6448536269514722  # norm.ppf(0.95), matches fair_cam_validation._Z95


def _fd(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "name": "S",
        "description": None,
        "scenario_type": "custom",
        "threat_category": "ransomware",
        "threat_actor_type": "cybercriminals",
        "attack_vector": None,
        "asset_class": "systems",
        "version": "1.0",
        "status": "active",
        "threat_event_frequency": {"distribution": "PERT", "low": 0.1, "mode": 0.5, "high": 2},
        "vulnerability": {"distribution": "PERT", "low": 0.2, "mode": 0.35, "high": 0.6},
        "primary_loss": {"distribution": "PERT", "low": 100000, "mode": 1000000, "high": 15000000},
        "secondary_loss": None,
    }
    base.update(over)
    return base


def _lognormal(mean: float = 6.9, sigma: float = 1.0, **extra: Any) -> dict[str, Any]:
    return {"distribution": "lognormal", "mean": mean, "sigma": sigma, **extra}


def _mixture(components: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    return {"distribution": "lognormal_mixture", "components": components, **extra}


# --- Key-set widening (acceptance criterion 1) -------------------------------


def test_structural_lognormal_accepted_without_max() -> None:
    assert (
        _structural_dist_problem("primary_loss", _lognormal(), allow_lognormal=True, allow_max=True)
        is None
    )


def test_structural_lognormal_accepted_with_max() -> None:
    dist = _lognormal(max=1_000_000.0)
    assert (
        _structural_dist_problem("primary_loss", dist, allow_lognormal=True, allow_max=True) is None
    )


def test_structural_lognormal_max_non_numeric_is_error() -> None:
    dist = _lognormal(max="lots")
    problem = _structural_dist_problem("primary_loss", dist, allow_lognormal=True, allow_max=True)
    assert problem is not None and "max" in problem


def test_structural_lognormal_other_unknown_key_still_rejected() -> None:
    dist = _lognormal(max=1_000_000.0, junk="x" * 50)
    problem = _structural_dist_problem("primary_loss", dist, allow_lognormal=True, allow_max=True)
    assert problem is not None


def test_structural_mixture_accepted_without_max() -> None:
    mix = _mixture([{"mean": 8.0, "sigma": 0.7, "weight": 1.0}])
    assert (
        _structural_dist_problem("primary_loss", mix, allow_lognormal=True, allow_max=True) is None
    )


def test_structural_mixture_accepted_with_max() -> None:
    mix = _mixture([{"mean": 8.0, "sigma": 0.7, "weight": 1.0}], max=1_000_000.0)
    assert (
        _structural_dist_problem("primary_loss", mix, allow_lognormal=True, allow_max=True) is None
    )


def test_structural_mixture_other_unknown_key_still_rejected() -> None:
    mix = _mixture([{"mean": 8.0, "sigma": 0.7, "weight": 1.0}], max=1_000_000.0, junk="x" * 50)
    problem = _structural_dist_problem("primary_loss", mix, allow_lognormal=True, allow_max=True)
    assert problem is not None


# --- Library-entry flag: max REJECTED regardless of scenario-side widening --
# (allow_max=False call-site tests live in test_library_bundle_validate.py;
# this pins the flag's OWN behavior directly on the shared chokepoint.
# allow_max is a REQUIRED keyword-only arg (milestone gate finding (p): no
# permissive default -- every caller, including these direct-call tests,
# must decide explicitly).


def test_structural_lognormal_with_max_rejected_when_allow_max_false() -> None:
    dist = _lognormal(max=1_000_000.0)
    problem = _structural_dist_problem("primary_loss", dist, allow_lognormal=True, allow_max=False)
    assert problem is not None


def test_structural_mixture_with_max_rejected_when_allow_max_false() -> None:
    mix = _mixture([{"mean": 8.0, "sigma": 0.7, "weight": 1.0}], max=1_000_000.0)
    problem = _structural_dist_problem("primary_loss", mix, allow_lognormal=True, allow_max=False)
    assert problem is not None


def test_structural_lognormal_without_max_still_accepted_when_allow_max_false() -> None:
    # allow_max=False only rejects a PRESENT max; it must not disturb the
    # ordinary lognormal-without-max shape library entries already use.
    assert (
        _structural_dist_problem(
            "primary_loss", _lognormal(), allow_lognormal=True, allow_max=False
        )
        is None
    )


# --- CSV mint: no `max` key + revenue set -> minted from capacity_max -------


def test_csv_shaped_lognormal_mints_capacity_max() -> None:
    capacity_max = 5_000_000.0
    preview, errors, forms, _meta, _am = _validate_rows(
        [(2, _fd(primary_loss=_lognormal(mean=6.9, sigma=1.0)))],
        existing_names=set(),
        capacity_max=capacity_max,
    )
    assert errors == []
    assert preview[0]["action"] == "create"
    assert forms[0] is not None
    assert forms[0].primary_loss["max"] == capacity_max


def test_csv_shaped_mixture_mints_shared_capacity_max() -> None:
    capacity_max = 5_000_000.0
    mix = _mixture(
        [
            {"mean": 8.06, "sigma": 0.70, "weight": 0.5},
            {"mean": 10.0, "sigma": 0.50, "weight": 0.5},
        ]
    )
    preview, errors, forms, _meta, _am = _validate_rows(
        [(2, _fd(primary_loss=mix))],
        existing_names=set(),
        capacity_max=capacity_max,
    )
    assert errors == []
    assert preview[0]["action"] == "create"
    assert forms[0] is not None
    assert forms[0].primary_loss["max"] == capacity_max
    # Every component preserved (adapter-iteration contract) -- minting the
    # cap must not drop or collapse the components list.
    assert len(forms[0].primary_loss["components"]) == 2


# --- JSON explicit max + revenue set -> PRESERVED, never overwritten -------


def test_json_explicit_max_with_revenue_set_is_preserved_not_overwritten() -> None:
    explicit_max = 42_000_000.0
    capacity_max = 5_000_000.0  # deliberately DIFFERENT from explicit_max
    preview, errors, forms, _meta, _am = _validate_rows(
        [(2, _fd(primary_loss=_lognormal(mean=6.9, sigma=1.0, max=explicit_max)))],
        existing_names=set(),
        capacity_max=capacity_max,
    )
    assert errors == []
    assert preview[0]["action"] == "create"
    assert forms[0] is not None
    assert forms[0].primary_loss["max"] == explicit_max  # NOT capacity_max


# --- D18: revenue-unset (capacity_max=None) blocks the catastrophic row ----


def test_d18_blocks_catastrophic_pl_when_capacity_max_none() -> None:
    preview, errors, forms, _meta, _am = _validate_rows(
        [(2, _fd(primary_loss=_lognormal()))],
        existing_names=set(),
        capacity_max=None,
    )
    assert preview[0]["action"] == "error"
    assert forms[0] is None
    assert errors and errors[0]["column"] == "primary_loss.max"
    assert errors[0]["reason"] == D18_REVENUE_MESSAGE


def test_d18_blocks_catastrophic_sl_names_secondary_loss_column() -> None:
    preview, errors, forms, _meta, _am = _validate_rows(
        [(2, _fd(secondary_loss=_lognormal()))],
        existing_names=set(),
        capacity_max=None,
    )
    assert preview[0]["action"] == "error"
    assert errors and errors[0]["column"] == "secondary_loss.max"
    assert errors[0]["reason"] == D18_REVENUE_MESSAGE


def test_d18_pert_only_row_unaffected_by_capacity_max_none() -> None:
    # A capped (PERT) row must never be gated by D18 -- capacity is
    # irrelevant to a non-catastrophic loss shape.
    preview, errors, forms, _meta, _am = _validate_rows(
        [(2, _fd())], existing_names=set(), capacity_max=None
    )
    assert errors == []
    assert preview[0]["action"] == "create"


def test_d18_explicit_max_does_not_bypass_null_revenue() -> None:
    # JSON can carry an explicit `max`; a NULL-revenue org still blocks --
    # the explicit cap does not bypass the D18 precondition (only the
    # expert form's D17 override, Task 4c, is allowed to do that).
    preview, errors, forms, _meta, _am = _validate_rows(
        [(2, _fd(primary_loss=_lognormal(max=1_000_000_000.0)))],
        existing_names=set(),
        capacity_max=None,
    )
    assert preview[0]["action"] == "error"
    assert forms[0] is None
    assert errors and errors[0]["column"] == "primary_loss.max"
    assert errors[0]["reason"] == D18_REVENUE_MESSAGE


def test_d18_fires_once_when_both_pl_and_sl_are_catastrophic() -> None:
    preview, errors, forms, _meta, _am = _validate_rows(
        [
            (
                2,
                _fd(
                    primary_loss=_lognormal(),
                    secondary_loss=_lognormal(),
                ),
            )
        ],
        existing_names=set(),
        capacity_max=None,
    )
    assert preview[0]["action"] == "error"
    assert len(errors) == 1
    assert errors[0]["column"] == "primary_loss.max"


# --- D19: floor conflict -> row-level error with the pinned floor copy -----


def test_d19_floor_conflict_blocks_with_wrapped_remedies() -> None:
    mean, sigma = 6.9, 1.0
    p95 = math.exp(mean + _Z95 * sigma)
    capacity_max = p95 * 0.5  # at/below the p95 -> floor violation
    preview, errors, forms, _meta, _am = _validate_rows(
        [(2, _fd(primary_loss=_lognormal(mean=mean, sigma=sigma)))],
        existing_names=set(),
        capacity_max=capacity_max,
    )
    assert preview[0]["action"] == "error"
    assert forms[0] is None
    assert errors
    reason = errors[0]["reason"]
    assert D19_FLOOR_MARKER in reason
    # The three pinned remedies (Task 4a's wrap, reused verbatim).
    assert "lower the loss estimates" in reason
    assert "annual revenue" in reason
    assert "expert form with an explicit max cap" in reason


def test_d19_floor_holds_just_above_p95() -> None:
    mean, sigma = 6.9, 1.0
    p95 = math.exp(mean + _Z95 * sigma)
    capacity_max = p95 * 1.01  # just above -> should PASS
    preview, errors, forms, _meta, _am = _validate_rows(
        [(2, _fd(primary_loss=_lognormal(mean=mean, sigma=sigma)))],
        existing_names=set(),
        capacity_max=capacity_max,
    )
    assert errors == []
    assert preview[0]["action"] == "create"
    assert forms[0] is not None
    assert forms[0].primary_loss["max"] == pytest.approx(capacity_max)


def test_d19_mixture_floor_conflict_blocks() -> None:
    # A cap that sits above ONE component's p95 but at/below the OTHER's
    # must still block -- the floor is `max > every component's p95`
    # (Task 3b's _validate_capacity_floor, exercised here through the full
    # import pipeline rather than re-derived).
    comp_a = {"mean": 8.0, "sigma": 0.3, "weight": 0.5}  # small p95
    comp_b = {"mean": 15.0, "sigma": 1.0, "weight": 0.5}  # large p95
    p95_b = math.exp(15.0 + _Z95 * 1.0)
    capacity_max = p95_b * 0.9  # below comp_b's p95 -> floor violation
    preview, errors, forms, _meta, _am = _validate_rows(
        [(2, _fd(primary_loss=_mixture([comp_a, comp_b])))],
        existing_names=set(),
        capacity_max=capacity_max,
    )
    assert preview[0]["action"] == "error"
    assert errors and D19_FLOOR_MARKER in errors[0]["reason"]
