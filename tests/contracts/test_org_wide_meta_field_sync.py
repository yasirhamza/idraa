"""#299: org_wide_meta exists on ControlForm AND the ORM; the run column and
snapshot field exist with the documented defaults."""

from typing import Any

import sqlalchemy as sa

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


def test_inherited_column_stores_explicit_python_none_as_sql_null():
    """The pre-feature marker is SQL NULL under BOTH write paths. Plain ``JSON`` would
    persist an explicitly assigned ``None`` as the text ``'null'`` (``IS NULL`` false),
    giving the marker two encodings; the migration downgrade tests ``IS NOT NULL``."""
    col_type = RiskAnalysisRun.__table__.c.inherited_control_ids_per_scenario.type
    assert isinstance(col_type, sa.JSON)
    assert col_type.none_as_null is True

    probe = sa.Table(
        "probe",
        sa.MetaData(),
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("v", col_type, nullable=True),
    )
    engine = sa.create_engine("sqlite://")
    probe.create(engine)
    with engine.begin() as conn:
        conn.execute(probe.insert().values(id=1, v=None))
        conn.execute(probe.insert().values(id=2))  # attribute never set
        conn.execute(probe.insert().values(id=3, v={}))
        rows: dict[int, int] = dict(conn.execute(sa.text("SELECT id, v IS NULL FROM probe")).all())
    assert rows == {1: 1, 2: 1, 3: 0}
