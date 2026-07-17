"""Control form DTOs.

Mirrors the `Control` ORM minus the mixin-owned columns
(`id` / `created_at` / `updated_at` / `organization_id`) and
`created_by`, which the service sets from the authenticated user.

ControlFunctionAssignmentDTO is the per-sub-function effectiveness
contract shared across the wizard (PR λ), importer, API, and the
transitional v3→fair_cam adapter bridge. See spec §7.1.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from idraa.models.enums import (
    ControlImplementationStage,
    ControlType,
    EntityStatus,
    FairCamSubFunction,
)
from idraa.schemas._csv import split_csv
from idraa.schemas.organization import _MONEY_MAX


class ControlFunctionAssignmentDTO(BaseModel):
    """Per-sub-function effectiveness assignment (spec §7.1).

    Single source of truth for the per-assignment shape across the wizard,
    importer, API, and the transitional v3→fair_cam adapter.

    Validators enforce:
      1. Virtual-function check: DSC_CORR_MISALIGNED requires derived_from
         (Decision 3, reject_virtual_unless_derived).
      2. Unit-correct capability_value bounds (M1, validate_capability_value_unit).

    The defense-in-depth assertion that PR iota forbids non-NULL
    derived_from_assignment_id lives at the SERVICE LAYER (services/controls.py),
    NOT on this DTO. Reason: this DTO is shared with _snapshot_control_v2 and will
    be constructed from Phase 2 rows that have non-NULL derived_from_assignment_id.
    A DTO-level rejection would block Phase 2 snapshot reads. (B-NEW3)
    """

    # derived_from_assignment_id declared first so it is available in model_validator
    # before checking virtual sub-function guard (Decision 3, spec §4.3).
    derived_from_assignment_id: uuid.UUID | None = None
    sub_function: FairCamSubFunction
    capability_value: float | None = Field(
        default=None,
        description=(
            "The primary effectiveness scalar. NULLABLE: NULL is permitted "
            "for TIME/CURRENCY-unit sub-functions (sentinel for time-unit backfills). "
            "Units derived from sub_function per audit §3 unit-type table. "
            "Probability and percent-reduction sub-functions are [0,1]-bounded; "
            "elapsed-time and currency sub-functions have no upper bound."
        ),
    )

    @field_validator("capability_value", mode="before")
    @classmethod
    def _coerce_blank_to_none(cls, v: object) -> object:
        """Empty-string and comma-bearing currency inputs both become None
        or stripped numerics. Background (UAT 2026-05-21):

        - The controls form submits ``capability_value=""`` for any row
          the user leaves blank (HTML form behavior for empty inputs);
          without this coercion Pydantic raises 'unable to parse string
          as number' on Save for any existing row whose currency
          capability is NULL — including the no-changes Save path.
        - The currency input renders pre-existing values with thousands
          separators ("5,000") for readability, and a user may re-type
          them the same way. Strip commas/spaces before float coercion
          so a round-trip Save of an unchanged value works.
        """
        if v is None:
            return None
        if isinstance(v, str):
            s = v.strip().replace(",", "").replace(" ", "")
            if not s:
                return None
            return s
        return v

    coverage: float = Field(
        ge=0.0,
        le=1.0,
        description="Deployment breadth [0,1]. Standard §2.4.2 pages 6-7.",
    )
    reliability: float = Field(
        ge=0.0,
        le=1.0,
        description="Probability control performs consistently. Standard §2.4.3 page 7.",
    )
    confirmed_by_user_at: datetime | None = None
    measured_at: datetime | None = None
    measured_by: uuid.UUID | None = None

    @model_validator(mode="after")
    def reject_virtual_unless_derived(self) -> ControlFunctionAssignmentDTO:
        """Enforce Standard §5.3 (page 50): DSC_CORR_MISALIGNED is a virtual
        sub-function — 'there are no distinct controls that serve this function.'
        Assignment is only permitted when derived_from_assignment_id references
        the governing LEC Response or VMC Variance Correction assignment.

        Decision 3 (spec §4.3): enforce at Pydantic layer AND DB CHECK constraint.
        """
        if (
            self.sub_function == FairCamSubFunction.DSC_CORR_MISALIGNED
            and self.derived_from_assignment_id is None
        ):
            raise ValueError(
                "virtual sub-function 'dsc_corr_misaligned' (Standard §5.3, page 50) "
                "requires derived_from_assignment_id to reference the governing "
                "LEC Response or VMC Variance Correction assignment. (Decision 3)"
            )
        return self

    @model_validator(mode="after")
    def validate_capability_value_unit(self) -> ControlFunctionAssignmentDTO:
        from idraa.schemas._unit_bounds import validate_capability_unit_bound

        validate_capability_unit_bound(
            self.capability_value, self.sub_function, field_name="capability_value"
        )
        return self


class ControlForm(BaseModel):
    """HTTP form DTO for control create / update (spec §7.2).

    Removed from this schema (PR iota):
      - (deleted) `function` column — classical taxonomy dropped (Decision 1)
      - `control_strength`, `control_reliability`, `control_coverage`
        (Decision 6 — effectiveness moves to per-assignment ControlFunctionAssignmentDTO)

    Removed from this schema (issue #90):
      - `domain` field — domain is now derived from `Control.domains` property
        which decodes each assignment's `sub_function`. No editable user input.

    PR kappa: max_length cap removed; multiple assignments now permitted (spec §6.1).
    DB UNIQUE constraint uq_cfa_control_sub_function enforces uniqueness at the
    persistence layer; the model_validator below catches duplicates earlier with
    friendly error messages (defense-in-depth, spec §6.1). (OQ3)

    `model_config`: intentionally NOT set to `extra="forbid"`. The Pydantic
    default `ignore` keeps a stale `domain=<slug>` field on a browser back-button
    replay from 422-ing the request after issue #90 migration. (Task 3)
    """

    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    type: ControlType
    assignments: list[ControlFunctionAssignmentDTO] = Field(
        min_length=1,
        description=(
            "One or more sub-function effectiveness assignments per control. "
            "Each sub_function must be unique within the control (enforced by "
            "model_validator + DB UNIQUE constraint). "
            "Each assignment carries its own Capability/Coverage/Reliability triple "
            "per Standard §2.4 pages 6-7."
        ),
    )

    @model_validator(mode="after")
    def _validate_unique_sub_functions(self) -> ControlForm:
        sub_fns = [a.sub_function for a in self.assignments]
        if len(sub_fns) != len(set(sub_fns)):
            raise ValueError(
                "duplicate sub_function in assignments — each must be unique per control"
            )
        return self

    annual_cost: Decimal = Field(
        default=Decimal("0"),
        ge=Decimal("0"),
        le=_MONEY_MAX,
        max_digits=18,
        decimal_places=2,
    )
    nist_csf_functions: list[str] = Field(default_factory=list)
    iso_27001_domains: list[str] = Field(default_factory=list)
    compliance_mappings: dict[str, Any] = Field(default_factory=dict)
    skill_requirements: list[str] = Field(default_factory=list)
    technology_dependencies: list[str] = Field(default_factory=list)
    applicable_industries: list[str] = Field(default_factory=list)
    applicable_org_sizes: list[str] = Field(default_factory=list)
    status: EntityStatus = EntityStatus.ACTIVE
    implementation_stage: ControlImplementationStage = ControlImplementationStage.ACTIVE
    version: str = "1.0"

    @field_validator(
        "nist_csf_functions",
        "iso_27001_domains",
        "skill_requirements",
        "technology_dependencies",
        "applicable_industries",
        "applicable_org_sizes",
        mode="before",
    )
    @classmethod
    def _csv_to_list(cls, v: object) -> list[str]:
        return split_csv(v)  # type: ignore[arg-type]
