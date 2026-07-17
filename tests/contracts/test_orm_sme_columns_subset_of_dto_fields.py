# tests/contracts/test_orm_sme_columns_subset_of_dto_fields.py
"""ORM<->DTO field sync for SubjectMatterExpert <-> SMECreate/SMEUpdate/SMERequest.

Spec §9.2: assert every non-server-derived ORM column on
`subject_matter_experts` is exposed by at least one analyst/admin DTO and
that the allowlist of server-derived columns is exhaustive.

The pyproject.toml [tool.idraa.contracts.field_sync.*] registry is not
extended here because the SME DTOs have a triangular relationship (write
DTOs are deliberately a proper subset of the ORM; SMECreate has notes,
SMERequest deliberately omits notes for analyst path). This direct test
expresses that contract clearly.
"""

from __future__ import annotations

from sqlalchemy.inspection import inspect

from idraa.models.sme import SubjectMatterExpert
from idraa.schemas.sme import SMECreate, SMERequest, SMEUpdate

# Server-derived / system columns that legitimately do NOT appear on any
# analyst/admin DTO. Sec-1 R1 mass-assignment defense: extending this
# allowlist requires a security-review nod.
_ALLOWLIST = {
    "id",  # IdMixin pk
    "organization_id",  # OrgMixin (server-derived from current_user)
    "created_at",  # TimestampMixin
    "updated_at",  # TimestampMixin
    "email_lower",  # DB-computed, never user-set
    "archived_at",  # set by archive route, never on create/update
    "archived_by",  # set by archive route
    "is_system_owned",  # IRIS SME flag; server-derived
    "created_via",  # admin / analyst_request / system
    "created_by",  # server-derived from session
}


def test_orm_sme_columns_subset_of_dto_union() -> None:
    """Every ORM column that's user-settable appears on SMECreate or SMEUpdate."""
    insp = inspect(SubjectMatterExpert)
    orm_columns = {col.key for col in insp.columns}
    dto_union = set(SMECreate.model_fields.keys()) | set(SMEUpdate.model_fields.keys())

    missing = orm_columns - dto_union - _ALLOWLIST
    assert not missing, (
        f"ORM column(s) {sorted(missing)} on subject_matter_experts are not "
        f"user-settable via SMECreate or SMEUpdate and not in the server-"
        f"derived allowlist. Either add the column to a DTO or extend the "
        f"allowlist with a security-review justification."
    )

    # Defense-in-depth: every allowlist entry is a real ORM column.
    stale = _ALLOWLIST - orm_columns
    assert not stale, (
        f"Allowlist entries {sorted(stale)} are not actual ORM columns on "
        f"subject_matter_experts. Remove from allowlist."
    )


def test_sme_request_dto_is_strict_subset_of_sme_create() -> None:
    """SMERequest (analyst path) is intentionally a subset of SMECreate (admin)."""
    create_fields = set(SMECreate.model_fields.keys())
    request_fields = set(SMERequest.model_fields.keys())
    extra_on_request = request_fields - create_fields
    assert not extra_on_request, (
        f"SMERequest fields {sorted(extra_on_request)} not on SMECreate. "
        f"Analyst-path DTO should be a subset of admin-path DTO."
    )
    # The deliberate gap is `notes` — analysts must not stamp notes.
    assert "notes" in create_fields and "notes" not in request_fields, (
        "Expected `notes` to live on SMECreate but NOT SMERequest (spec §6.1: notes is admin-only)."
    )
