"""ControlForm carries implementation_stage and it maps to the ORM (#395)."""

from idraa.models.control import Control
from idraa.models.enums import ControlImplementationStage
from idraa.schemas.control import ControlForm


def test_controlform_has_implementation_stage_default_active():
    # type/assignments passed as raw literals; Pydantic coerces them at
    # construction. mypy cannot model that coercion through the typed
    # constructor signature, hence the targeted ignores.
    f = ControlForm(
        name="x",
        type="technical",  # type: ignore[arg-type]
        assignments=[
            {  # type: ignore[list-item]
                "sub_function": "lec_prev_avoidance",
                "capability_value": 0.5,
                "coverage": 0.8,
                "reliability": 0.8,
            }
        ],
    )
    assert f.implementation_stage is ControlImplementationStage.ACTIVE


def test_controlform_field_is_orm_column():
    # The field must exist on both sides so create_control's **form_data unpack
    # populates the column (no silent drop).
    assert "implementation_stage" in ControlForm.model_fields
    assert hasattr(Control, "implementation_stage")
