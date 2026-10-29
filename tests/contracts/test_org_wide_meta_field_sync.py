"""#299: org_wide_meta exists on ControlForm AND the ORM; the run column and
snapshot field exist with the documented defaults."""

from typing import Any

from idraa.models.control import Control
from idraa.models.risk_analysis_run import RiskAnalysisRun
from idraa.schemas.control import ControlForm
from idraa.schemas.run_snapshot import ControlSnapshotV3


def _form(**kw: Any) -> ControlForm:
    return ControlForm(
        name="x",
        type="technical",  # type: ignore[arg-type]
        assignments=[
            {  # type: ignore[list-item]
                "sub_function": "vmc_prev_reduce_variance_prob",
                "capability_value": 0.5,
                "coverage": 0.8,
                "reliability": 0.8,
            }
        ],
        **kw,
    )


def test_controlform_org_wide_meta_defaults_false_and_is_orm_column():
    assert _form().org_wide_meta is False
    assert _form(org_wide_meta=True).org_wide_meta is True
    assert "org_wide_meta" in ControlForm.model_fields
    assert hasattr(Control, "org_wide_meta")


def test_run_has_inherited_column_defaulting_to_none():
    assert hasattr(RiskAnalysisRun, "inherited_control_ids_per_scenario")
    col = RiskAnalysisRun.__table__.c.inherited_control_ids_per_scenario
    assert col.nullable is True


def test_snapshot_v3_inherited_variant_of_is_optional_and_version_stays_3():
    snap = ControlSnapshotV3(control_id="c", name="n", domains=[], type="technical", assignments=[])
    assert snap.inherited_variant_of is None
    assert snap.snapshot_version == 3
    assert "inherited_variant_of" in snap.model_dump(mode="json")
