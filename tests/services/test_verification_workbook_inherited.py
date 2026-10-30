"""#299 spec M5 / Meth-I5: the workbook's per-scenario control subset includes
the ":meta" variant rows for inherited controls and only the plain row where
linked — for AGGREGATE and SINGLE shapes alike. Legacy runs are untouched."""

from types import SimpleNamespace

from idraa.services.inherited_controls import variant_id
from idraa.services.verification_workbook import _per_scenario_controls_snapshot

EDR, PM = "e" * 36, "p" * 36


def _snap(*ids: str) -> list[dict[str, str]]:
    return [{"control_id": i, "name": i} for i in ids]


def _ids(run: SimpleNamespace, snapshot: list[dict[str, str]], sid: str) -> list[str]:
    return [c["control_id"] for c in _per_scenario_controls_snapshot(run, snapshot, sid)]


def test_aggregate_subset_includes_variants_and_excludes_unlinked_plain_rows() -> None:
    snapshot = _snap(EDR, PM, variant_id(PM))
    run = SimpleNamespace(
        aggregate_control_ids_per_scenario={"A": [EDR, PM], "B": [EDR]},
        inherited_control_ids_per_scenario={"A": [], "B": [PM]},
    )
    assert _ids(run, snapshot, "A") == [EDR, PM]
    assert _ids(run, snapshot, "B") == [EDR, variant_id(PM)]


def test_single_feature_active_nothing_inherited_is_identity() -> None:
    snapshot = _snap(EDR, PM)
    run = SimpleNamespace(
        aggregate_control_ids_per_scenario=None,
        inherited_control_ids_per_scenario={"S": []},
    )
    assert _ids(run, snapshot, "S") == [EDR, PM]


def test_single_with_inheritance_keeps_variant_not_plain() -> None:
    snapshot = _snap(EDR, PM, variant_id(PM))  # a stray plain PM row must be dropped
    run = SimpleNamespace(
        aggregate_control_ids_per_scenario=None,
        inherited_control_ids_per_scenario={"S": [PM]},
    )
    assert _ids(run, snapshot, "S") == [EDR, variant_id(PM)]


def test_legacy_runs_unchanged() -> None:
    snapshot = _snap(EDR)
    agg = SimpleNamespace(
        aggregate_control_ids_per_scenario={"A": [EDR]},
        inherited_control_ids_per_scenario=None,
    )
    single = SimpleNamespace(
        aggregate_control_ids_per_scenario=None,
        inherited_control_ids_per_scenario=None,
    )
    assert _ids(agg, snapshot, "A") == [EDR]
    assert _ids(single, snapshot, "S") == [EDR]


def test_inherited_everywhere_linked_nowhere_is_exactly_the_variant_rows() -> None:
    """METH-T7-N2 / review-focus #6: no scenario links anything, so every stored
    per-scenario list is empty (a TRUTHY dict of empty lists -- the branch must
    not read it as "no map"), and each scenario's subset is exactly its variants."""
    dsc = "d" * 36
    snapshot = _snap(variant_id(PM), variant_id(dsc))  # no plain rows at all
    run = SimpleNamespace(
        aggregate_control_ids_per_scenario={"A": [], "B": []},
        inherited_control_ids_per_scenario={"A": [PM], "B": [PM, dsc]},
    )
    assert _ids(run, snapshot, "A") == [variant_id(PM)]
    assert _ids(run, snapshot, "B") == [variant_id(PM), variant_id(dsc)]
