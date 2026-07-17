# tests/contracts/test_scenario_sme_estimates_orm_dto_sync.py
"""ORM<->DTO sync for ScenarioSMEEstimate <-> WizardStep3Submit.SMEEstimateRow.

ScenarioSMEEstimate has no dedicated input DTO; the row payload is the
nested SMEEstimateRow on WizardStep3Submit. The contract: every
user-settable column on ScenarioSMEEstimate either appears on SMEEstimateRow
or is documented as server-derived.

Spec §9.2 calls this out as a regression guard against a future column
addition (e.g., a `confidence` or `unit` column) being silently dropped on
the wizard finalize path because the schema was missed.
"""

from __future__ import annotations

from sqlalchemy.inspection import inspect

from idraa.models.scenario_sme_estimate import ScenarioSMEEstimate
from idraa.schemas.wizard_step3 import SMEEstimateRow

# Server-derived columns that legitimately don't appear on the user-input DTO.
_ALLOWLIST = {
    "id",  # IdMixin
    "organization_id",  # OrgMixin
    "scenario_id",  # threaded by route from URL/state, not the row payload
    "fieldset",  # threaded by route from the outer FieldsetRows key
    "recorded_at",  # stamped by persist_estimates (server clock)
    "recorded_by",  # stamped by persist_estimates (current_user)
}


def test_sme_estimate_orm_payload_subset_of_dto() -> None:
    """Every user-settable ScenarioSMEEstimate column lives on SMEEstimateRow."""
    insp = inspect(ScenarioSMEEstimate)
    orm_columns = {col.key for col in insp.columns}
    dto_fields = set(SMEEstimateRow.model_fields.keys())

    missing = orm_columns - dto_fields - _ALLOWLIST
    assert not missing, (
        f"ScenarioSMEEstimate column(s) {sorted(missing)} not on "
        f"SMEEstimateRow and not in the server-derived allowlist. "
        f"Adding a column here without extending the DTO/allowlist means "
        f"the wizard finalize path will silently drop user input."
    )

    # Defense-in-depth: every allowlist entry is a real ORM column.
    stale = _ALLOWLIST - orm_columns
    assert not stale, (
        f"Allowlist entries {sorted(stale)} are not actual ORM columns on "
        f"scenario_sme_estimates. Remove from allowlist."
    )


def test_sme_estimate_dto_has_no_mass_assignment_surface() -> None:
    """Sec-1 R1: SMEEstimateRow must NOT expose server-derived columns.

    A pre-T9 regression class: adding `organization_id` or `recorded_by` to
    the DTO would let an analyst forge cross-org writes. Pin the DTO's
    field set explicitly so future additions surface in code review.
    """
    expected = {"sme_id", "sme_name", "low", "high"}
    actual = set(SMEEstimateRow.model_fields.keys())
    assert actual == expected, (
        f"SMEEstimateRow field-set drift: "
        f"unexpected={actual - expected!r}, missing={expected - actual!r}. "
        f"Any addition to this DTO needs Sec-1 mass-assignment review."
    )
