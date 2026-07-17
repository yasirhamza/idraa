"""PR iota end-to-end smoke test + Phase-2 forward-compat sentinel assertions.

The browser-flow smoke test (login → create control → run → confirm) is
skip-stubbed pending Phase 1.5b E2E infrastructure (ephemeral per-run DB,
CSRF-aware bootstrap). Same skip pattern as existing E2E stubs in
tests/e2e/conftest.py.

Forward-compat sentinels run unconditionally — pure model/SQL assertions
that do not require a live server.

Spec §11.2, §11.4.
"""

from __future__ import annotations

import uuid

import pytest

_E2E_SKIP = (
    "E2E browser flow requires Phase 1.5b infrastructure: ephemeral per-run SQLite, "
    "CSRF-aware bootstrap. Deferred per tests/e2e/conftest.py._E2E_SEED_SKIP_REASON."
)


# Forward-compat sentinel assertions (run unconditionally)


def test_derived_from_assignment_id_column_exists_on_orm() -> None:
    """ControlFunctionAssignment.derived_from_assignment_id column exists.

    Reserved-but-unused in PR iota (spec §4.9, Decision 9). Phase 2 populates
    this column for computed-virtual DSC_CORR_MISALIGNED rows.
    """
    from sqlalchemy import inspect

    from idraa.models.control_function_assignment import ControlFunctionAssignment

    mapper = inspect(ControlFunctionAssignment)
    col_names = {c.key for c in mapper.mapper.columns}
    assert "derived_from_assignment_id" in col_names, (
        "derived_from_assignment_id column not found on ControlFunctionAssignment ORM. "
        "Required: reserved-but-unused for Phase 2 computed-virtual rows (spec §4.9)."
    )


def test_assignments_relationship_eager_loadable() -> None:
    """Control.assignments relationship uses selectin loading (eager by default).

    Phase-2 forward-compat: the bridge and snapshot writer depend on
    control.assignments being populated without an explicit eager-load
    call at the route layer. (spec §6.1)
    """
    from sqlalchemy import inspect as sa_inspect

    from idraa.models.control import Control

    mapper = sa_inspect(Control)
    rels = {r.key: r for r in mapper.mapper.relationships}
    assert "assignments" in rels, (
        "Control.assignments relationship not found. "
        "Required for bridge and snapshot writer (spec §6.1)."
    )
    asgn_rel = rels["assignments"]
    assert asgn_rel.lazy == "selectin", (
        f"Control.assignments lazy strategy is '{asgn_rel.lazy}', expected 'selectin'. "
        "The bridge and snapshot writer require eager loading. (spec §6.1)"
    )


def test_control_snapshot_v1_and_v2_coexist_in_discriminated_union() -> None:
    """ControlSnapshot union dispatches correctly for both versions.

    Phase-2 forward-compat: historical v1 runs must remain readable indefinitely.
    (spec §15)
    """
    from pydantic import TypeAdapter

    from idraa.schemas.run_snapshot import (
        ControlSnapshot,
        ControlSnapshotV1,
        ControlSnapshotV2,
    )

    ta: TypeAdapter[ControlSnapshot] = TypeAdapter(ControlSnapshot)

    v1_dict = {
        "name": "Old Control",
        "control_id": str(uuid.uuid4()),
        "domain": "loss_event",
        "function": "PREVENTIVE",
        "type": "technical",
        "control_strength": 0.7,
        "control_reliability": 0.8,
        "control_coverage": 0.8,
        # No snapshot_version → discriminator callable defaults to 1 (M3)
    }
    v2_dict = {
        "snapshot_version": 2,
        "name": "New Control",
        "control_id": str(uuid.uuid4()),
        "domains": ["loss_event"],
        "type": "technical",
        "assignments": [
            {
                "sub_function": "lec_prev_resistance",
                "capability_value": 0.85,
                "coverage": 0.9,
                "reliability": 0.8,
                "confirmed_by_user_at": None,
                "derived_from_assignment_id": None,
                "measured_at": None,
                "measured_by": None,
            }
        ],
    }

    parsed_v1 = ta.validate_python(v1_dict)
    parsed_v2 = ta.validate_python(v2_dict)

    assert isinstance(parsed_v1, ControlSnapshotV1)
    assert parsed_v1.snapshot_version == 1
    assert isinstance(parsed_v2, ControlSnapshotV2)
    assert parsed_v2.snapshot_version == 2


# Browser-flow smoke test (skip-stubbed — Phase 1.5b infrastructure required)


@pytest.mark.asyncio
async def test_pr_iota_full_browser_flow_smoke() -> None:
    """Full PR iota browser flow: login → control import → run → confirm.

    Skip-stubbed: E2E infrastructure (ephemeral DB, CSRF-aware bootstrap)
    deferred to Phase 1.5b. Same skip pattern as existing tests/e2e/ stubs.

    When Phase 1.5b infrastructure lands, this test should:
    1. Log in as analyst.
    2. POST /controls/import with a single-control CSV (LOSS_EVENT + PREVENTIVE).
    3. Assert import created one ControlFunctionAssignment with
       sub_function=lec_prev_resistance, confirmed_by_user_at=NULL.
    4. POST /scenarios/{id}/run to trigger Monte Carlo.
    5. Poll GET /runs/{id}/status until terminal.
    6. GET /runs/{id} — assert response contains v2 snapshot markers.
    7. POST /controls/{ctrl_id}/assignments/{asgn_id}/confirm.
    8. GET /controls/maintenance — assert unconfirmed_count == 0.
    """
    pytest.skip(_E2E_SKIP)
