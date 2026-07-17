"""ControlImplementationStage gate predicate (issue #395)."""

from idraa.models.enums import ControlImplementationStage as Stage


def test_only_active_contributes():
    assert Stage.ACTIVE.contributes_to_composition is True
    for s in (Stage.NON_EXISTENT, Stage.PLANNED, Stage.IN_PROJECT):
        assert s.contributes_to_composition is False


def test_values_are_stable_slugs():
    # Stored verbatim in the DB; renaming a value is a migration, not a rename.
    assert {s.value for s in Stage} == {
        "non_existent",
        "planned",
        "in_project",
        "active",
    }


def test_active_is_the_sole_contributing_member():
    # Positive predicate, not a not-in-exclusion-set: a future stage must
    # default to NON-contributing.
    contributing = [s for s in Stage if s.contributes_to_composition]
    assert contributing == [Stage.ACTIVE]


def test_labels_are_canonical_single_point():
    # Single humanization point (plan-gate Arch-S1) — templates render .label,
    # never an ad-hoc value|replace|title (which would give "In Project").
    assert Stage.NON_EXISTENT.label == "Non-existent"
    assert Stage.PLANNED.label == "Proposed / Planned"
    assert Stage.IN_PROJECT.label == "In project (implementing)"
    assert Stage.ACTIVE.label == "Active"
