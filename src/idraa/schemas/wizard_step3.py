"""WizardStep3Submit with version_token + mass-assignment defense. Per spec §7.5.

Sec-1 R1 mass-assignment defense (extra="forbid" on every nested model — no
organization_id, created_by, is_system_owned, created_via, archived_at,
archived_by fields anywhere in the tree; all server-derived).

Sec-9 PR1 vuln per-row upper-bound guard: rejects high > 1.0 instead of
silently clamping (FAIR Vulnerability is a probability — caller must not
exceed the unit interval).

Sec-4 PR1 cap: per-fieldset row count bounded by
get_settings().max_smes_per_fieldset, defending the finalize pipeline's
per-fit Nelder-Mead loop from unbounded fan-out.

Spec-F PR3: sl is OPTIONAL (matches REQUIRED_FIELDSETS = ("tef","vuln","pl")
in the finalize pipeline — secondary-loss fieldset is opt-in per spec §5.2).
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from idraa.config import get_settings


class _ForbidExtra(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SMEEstimateRow(_ForbidExtra):
    sme_id: UUID | None = None
    sme_name: Annotated[str | None, Field(min_length=1, max_length=200)] = None
    low: Annotated[float, Field(gt=0)]
    high: Annotated[float, Field(ge=0)]

    @model_validator(mode="after")
    def _xor_identity(self) -> SMEEstimateRow:
        if (self.sme_id is None) == (self.sme_name is None):
            raise ValueError("Each estimate row must have exactly one of sme_id or sme_name.")
        return self

    @model_validator(mode="after")
    def _high_ge_low(self) -> SMEEstimateRow:
        if self.high < self.low:
            raise ValueError(f"high ({self.high}) must be >= low ({self.low})")
        return self


def _validate_cap(rows: list[SMEEstimateRow]) -> None:
    """Sec-4 PR1: per-fieldset row count bounded by max_smes_per_fieldset.
    Shared between ``VulnFieldsetRows`` and ``FieldsetRows`` so the cap
    cannot drift between vuln-specific and generic fieldsets."""
    cap = get_settings().max_smes_per_fieldset
    if len(rows) > cap:
        raise ValueError(f"Max {cap} SMEs per fieldset")


class VulnFieldsetRows(_ForbidExtra):
    rows: list[SMEEstimateRow]

    @model_validator(mode="after")
    def _vuln_upper_bound(self) -> VulnFieldsetRows:
        for r in self.rows:
            if r.high > 1.0:
                raise ValueError(f"vuln rows must have high <= 1.0 (got {r.high})")
        return self

    @model_validator(mode="after")
    def _enforce_cap(self) -> VulnFieldsetRows:
        _validate_cap(self.rows)
        return self


class FieldsetRows(_ForbidExtra):
    rows: list[SMEEstimateRow]

    @model_validator(mode="after")
    def _enforce_cap(self) -> FieldsetRows:
        _validate_cap(self.rows)
        return self


class WizardStep3Submit(_ForbidExtra):
    tef: FieldsetRows
    vuln: VulnFieldsetRows
    pl: FieldsetRows
    # Spec-F PR3 fix: SL is OPTIONAL per spec §5.2 + REQUIRED_FIELDSETS.
    # Schema and service must agree — was required here, contradicting both.
    sl: FieldsetRows | None = None
    version_token: int
