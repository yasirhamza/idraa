"""Issue #131 one-time migration: null out stale ELAPSED_TIME capability_value
rows on the six reclassified-to-PROBABILITY sub-functions, with audit-log
record per Control mutated.

Context
-------
Issue #131 reclassified six sub-functions from ``UnitType.ELAPSED_TIME`` to
``UnitType.PROBABILITY`` (the six lacked primary-cited calibration sources;
see ``docs/plans/2026-05-15-issue-131-tau-calibration-design.md``). Existing
production rows on those sub-functions may carry ``capability_value > 1.0``
(stored as a day-count under the old ELAPSED_TIME semantics). Under the new
PROBABILITY semantics those values are out-of-range. Without this migration,
the new PROBABILITY branch in
``fair_cam.composition.compute_assignment_opeff_two_branch`` would compute
e.g. ``2.0 * coverage * reliability = 1.62`` — a meaningless opeff > 1.

Strategy
--------
For each pre-existing assignment whose ``sub_function`` is in the
reclassified set AND whose ``capability_value > 1.0``: set
``capability_value`` to NULL so the new branch's ``_null_safe_default``
(``0.5 * coverage * reliability``, plan-gate Arch3-N1) takes over. NULL is
the only safe value at migration time — the operator has no calibrated
probability to backfill with; the methodology-curated overrides arrive in
T4 (library values) or via the per-org override layer (future).

Idempotency
-----------
Scoped by ``(organization_id, entity_id, action)``: if a row already exists
in ``audit_log`` for the (org, control, ``reclassify_unit_type_issue_131``)
triple, the Control is skipped on a subsequent run. Safe to re-run.

Audit-log shape
---------------
For each Control with at least one mutated assignment, ONE audit_log row is
inserted with::

    organization_id  = org.id            (Sec3-B1: NOT NULL on the model)
    entity_type      = "control"          (project convention)
    entity_id        = control.id
    user_id          = None               (system-marker; not an interactive change)
    action           = "reclassify_unit_type_issue_131"  (30 chars; fits String(32))
    changes          = {
        "nulled_assignments": [
            {"sub_function": slug, "previous_capability_value": float},
            ...
        ],
        "note": "..."
    }

The per-entry ``previous_capability_value`` field captures the pre-#131
day-count input for audit reconstruction — without it the original
calibration is destroyed by ``asgn.capability_value = None`` and cannot
be recovered post-migration.

Deployment ordering (Arch3-N2)
------------------------------
Run this script ONLY AFTER all application pods cycle to the post-#131
image. In a rolling-deploy environment, in-flight pre-#131 pods may
freshly write ``capability_value > 1.0`` rows on the reclassified
sub-functions (because their version of SUB_FUNCTION_UNITS still classes
them as ELAPSED_TIME) — a script run mid-deploy would leave those
freshly-written rows unmodified. In single-rolling-deploy dev / staging
environments the window is short and the risk is low, but the order is
explicit: app cutover → script.

Edge case — in-range legacy values (Arch-N2)
--------------------------------------------
This script only nulls ``capability_value > 1.0`` because pre-#131
ELAPSED_TIME day-counts on the six reclassified sub-functions were
typically > 1.0 (days). In-range values (e.g. ``0.5`` or ``1.0``) cannot
be distinguished post-hoc as "0.5 days" vs "0.5 probability" — both are
syntactically valid under both unit semantics. Such rows are silently
re-interpreted as probabilities post-migration (50%, 100% respectively),
which may or may not reflect operator intent.

Operators SHOULD inspect pre-#131 ``capability_value <= 1.0`` values on
the following six reclassified sub-function slugs manually before
running this script and decide whether a separate sweep (e.g. setting
the row to NULL, or rewriting under the new PROBABILITY semantics with
operator-supplied values) is warranted:

  * ``lec_resp_resilience``
  * ``vmc_id_threat_intelligence``
  * ``vmc_id_control_monitoring``
  * ``vmc_corr_treatment_selection``
  * ``dsc_id_misaligned``
  * ``dsc_corr_misaligned``

The ``> 1.0`` threshold is the locked migration contract (Sec3-B1 /
plan-gate-3) and must not be relaxed here without a fresh plan-gate
review — broader thresholds risk nulling already-valid PROBABILITY
inputs written post-#131.

Run via::

    uv run python scripts/issue_131_audit_log_migration.py
"""

from __future__ import annotations

import asyncio
import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from idraa.db import get_session
from idraa.models.audit_log import AuditLog
from idraa.models.control import Control
from idraa.models.enums import FairCamSubFunction
from idraa.models.organization import Organization

logger = logging.getLogger(__name__)

# Six sub-functions reclassified ELAPSED_TIME → PROBABILITY in issue #131
# (T2). The set is hardcoded — referencing SUB_FUNCTION_UNITS at script-run
# time would give a different answer if a future PR re-classifies more
# entries; this script is one-time and is pinned to the issue-#131 set.
RECLASSIFIED_SUB_FUNCTIONS: frozenset[FairCamSubFunction] = frozenset(
    {
        FairCamSubFunction.LEC_RESP_RESILIENCE,
        FairCamSubFunction.VMC_ID_THREAT_INTELLIGENCE,
        FairCamSubFunction.VMC_ID_CONTROL_MONITORING,
        FairCamSubFunction.VMC_CORR_TREATMENT_SELECTION,
        FairCamSubFunction.DSC_ID_MISALIGNED,
        FairCamSubFunction.DSC_CORR_MISALIGNED,
    }
)

ACTION_VERB = "reclassify_unit_type_issue_131"  # 30 chars; fits AuditLog.action String(32)


async def _already_migrated(
    db: AsyncSession,
    organization_id: uuid.UUID,
    control_id: uuid.UUID,
) -> bool:
    """Idempotency precondition: scoped by (organization_id, entity_id, action)."""
    stmt = select(AuditLog.id).where(
        AuditLog.organization_id == organization_id,
        AuditLog.entity_type == "control",
        AuditLog.entity_id == control_id,
        AuditLog.action == ACTION_VERB,
    )
    result = await db.execute(stmt)
    return result.first() is not None


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logger.info("Issue #131 audit-log migration starting.")
    total_orgs = 0
    total_controls_seen = 0
    total_controls_mutated = 0
    total_assignments_nulled = 0

    async with get_session() as db:
        org_result = await db.execute(select(Organization))
        organizations = org_result.scalars().all()

        for org in organizations:
            total_orgs += 1
            ctrl_stmt = (
                select(Control)
                .where(Control.organization_id == org.id)
                .options(selectinload(Control.assignments))
            )
            ctrl_result = await db.execute(ctrl_stmt)
            controls = ctrl_result.scalars().all()

            for control in controls:
                total_controls_seen += 1

                if await _already_migrated(db, org.id, control.id):
                    logger.debug(
                        "Skipping already-migrated control id=%s (org=%s)",
                        control.id,
                        org.id,
                    )
                    continue

                # Capture pre-mutation state per assignment so the audit-log
                # payload preserves the original calibration (Sec-I1). Cast
                # capability_value to plain ``float`` to avoid numpy /
                # Decimal coercion surprises in the JSON serializer.
                affected: list[dict[str, str | float]] = []
                for asgn in control.assignments:
                    if (
                        asgn.sub_function in RECLASSIFIED_SUB_FUNCTIONS
                        and asgn.capability_value is not None
                        and asgn.capability_value > 1.0
                    ):
                        previous_value = float(asgn.capability_value)
                        asgn.capability_value = None
                        affected.append(
                            {
                                "sub_function": asgn.sub_function.value,
                                "previous_capability_value": previous_value,
                            }
                        )
                        total_assignments_nulled += 1

                if not affected:
                    continue

                # NOTE: organization_id is REQUIRED on AuditLog (non-nullable
                # per Sec3-B1 / models/audit_log.py:43-47). Skipping it leaves
                # the row susceptible to a cross-org RBAC leak.
                db.add(
                    AuditLog(
                        organization_id=org.id,
                        entity_type="control",
                        entity_id=control.id,
                        user_id=None,  # system-marker; not an interactive change
                        action=ACTION_VERB,
                        changes={
                            "nulled_assignments": affected,
                            "note": (
                                "sub_function reclassified ELAPSED_TIME → PROBABILITY "
                                "under #131; pre-existing >1.0 capability_value nulled "
                                "so NULL fallback applies. The per-entry "
                                "`previous_capability_value` field captures the pre-#131 "
                                "day-count input for audit reconstruction."
                            ),
                        },
                    )
                )
                total_controls_mutated += 1
                logger.info(
                    "Mutated control id=%s (org=%s); nulled %d assignment(s): %s",
                    control.id,
                    org.id,
                    len(affected),
                    [entry["sub_function"] for entry in affected],
                )

        # Single commit at the end — the get_session context manager auto-commits
        # on clean exit (per idraa.db.get_session implementation).

    logger.info(
        "Issue #131 audit-log migration complete: orgs=%d controls_seen=%d "
        "controls_mutated=%d assignments_nulled=%d",
        total_orgs,
        total_controls_seen,
        total_controls_mutated,
        total_assignments_nulled,
    )


if __name__ == "__main__":
    asyncio.run(main())
