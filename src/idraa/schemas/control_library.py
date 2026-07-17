# src/idraa/schemas/control_library.py
"""Seed-validation schema for the control library catalog (P2b). Validates
data/seed_control_library_entries.json rows at migration time. Reuses the SHARED
M1 unit-bound helper (schemas/_unit_bounds.py) — the same one ControlFunctionAssignmentDTO
uses — so a seeded assignment obeys identical FAIR-CAM unit bounds as a user-authored
one, with zero copy-drift. This is a seed-validation schema, NOT a contract DTO — do
not register it in any contract snapshot."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from idraa.models.enums import SUB_FUNCTION_UNITS, ControlType, FairCamSubFunction
from idraa.schemas._unit_bounds import validate_capability_unit_bound

EXPERT_ESTIMATE_CEILING = 0.8  # I4: estimates may not exceed this without a citation
# NEW-#1: the ceiling applies ONLY to bounded [0,1] inputs. capability for ELAPSED_TIME /
# CURRENCY sub-functions stores natural units (days / dollars) and is unbounded above per
# validate_capability_unit_bound — exempt those (coverage/reliability are always fractions).
_CEILING_UNIT_VALUES = {"probability", "percent_reduction"}


class ControlLibraryAssignmentSeed(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sub_function: FairCamSubFunction
    capability_default: float | None = None
    coverage_default: float = Field(ge=0.0, le=1.0)
    reliability_default: float = Field(ge=0.0, le=1.0)
    capability_provenance: Literal["cited", "expert-estimate"] | None = None
    capability_citations: list[str] = Field(default_factory=list)
    coverage_provenance: Literal["cited", "expert-estimate"] = "expert-estimate"
    coverage_citations: list[str] = Field(default_factory=list)
    reliability_provenance: Literal["cited", "expert-estimate"] = "expert-estimate"
    reliability_citations: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def reject_virtual(self) -> ControlLibraryAssignmentSeed:
        # Stricter than the live DTO: the catalog has no derived_from concept, so the
        # virtual DSC_CORR_MISALIGNED can NEVER be legitimately claimed by a catalog entry.
        if self.sub_function == FairCamSubFunction.DSC_CORR_MISALIGNED:
            raise ValueError("sub_function dsc_corr_misaligned is virtual; no control may claim it")
        return self

    @model_validator(mode="after")
    def validate_capability_unit(self) -> ControlLibraryAssignmentSeed:
        validate_capability_unit_bound(
            self.capability_default, self.sub_function, field_name="capability_default"
        )
        return self

    @model_validator(mode="after")
    def _provenance_rules(self) -> ControlLibraryAssignmentSeed:
        # NEW-B1: AUTO-FILL capability provenance — an un-authored capability value IS an
        # expert-estimate (mirrors coverage/reliability defaults); this keeps the ~55
        # un-recurated existing entries valid under pilot-only scope. Only the ORPHAN case
        # (provenance set with no value) is an error. Do NOT raise on the iff.
        if self.capability_default is not None and self.capability_provenance is None:
            self.capability_provenance = "expert-estimate"
        if self.capability_default is None and self.capability_provenance is not None:
            raise ValueError("capability_provenance set but capability_default is None (orphan)")
        triples = [
            (
                "capability",
                self.capability_default,
                self.capability_provenance,
                self.capability_citations,
            ),
            ("coverage", self.coverage_default, self.coverage_provenance, self.coverage_citations),
            (
                "reliability",
                self.reliability_default,
                self.reliability_provenance,
                self.reliability_citations,
            ),
        ]
        for name, value, prov, cites in triples:
            if prov is None:  # capability absent
                continue
            if prov == "cited" and not any(c.strip() for c in cites):
                raise ValueError(f"{name}_provenance='cited' requires >=1 {name}_citation")
            # NEW-#1: ceiling only on bounded [0,1] inputs; natural-unit capability is exempt
            unit_v = getattr(SUB_FUNCTION_UNITS[self.sub_function], "value", "")
            bounded = name != "capability" or unit_v in _CEILING_UNIT_VALUES
            if (
                prov == "expert-estimate"
                and value is not None
                and bounded
                and value > EXPERT_ESTIMATE_CEILING
            ):
                raise ValueError(
                    f"{name} expert-estimate {value} exceeds ceiling {EXPERT_ESTIMATE_CEILING}; cite it"
                )
            # IT-1: BOTH natural-unit capabilities manufacture score at their extreme tail —
            # CURRENCY (upper tail: larger $ → larger subtractor) and ELAPSED_TIME (lower tail:
            # smaller time → opeff=exp(-t/τ)→1). Neither can be ceiling-bounded so honesty MUST
            # come from a citation. No expert-estimate is allowed for either natural-unit capability.
            if (
                name == "capability"
                and unit_v not in _CEILING_UNIT_VALUES
                and prov == "expert-estimate"
                and value is not None
            ):
                raise ValueError(
                    f"{unit_v} capability is unbounded natural-unit; must be provenance='cited', not expert-estimate"
                )
        return self


class ControlLibraryEntrySeed(BaseModel):
    model_config = ConfigDict(extra="forbid")
    slug: str
    name: str
    description: str = Field(min_length=20)
    control_type: ControlType
    reference_annual_cost: Decimal | None = None
    nist_csf_subcategories: list[str] = []
    cis_safeguards: list[str] = []
    iso_27001_controls: list[str] = []
    compliance_mappings: dict[str, Any] = {}
    applicable_industries: list[str] = []
    applicable_org_sizes: list[str] = []
    tags: list[str] = []
    source_citations: list[str] = []
    status: str = Field(pattern="^(draft|published|deprecated)$")
    assignments: list[ControlLibraryAssignmentSeed] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_sub_functions(self) -> ControlLibraryEntrySeed:
        seen = [a.sub_function for a in self.assignments]
        if len(seen) != len(set(seen)):
            raise ValueError("duplicate sub_function in assignments")
        return self
