"""Issue #129 one-time migration: null out legacy [0, 1.0) placeholder
capability_value rows on the 4 remaining unit-aware sub-functions
(LEC_DET_MONITORING, LEC_RESP_EVENT_TERMINATION,
VMC_CORR_IMPLEMENTATION, LEC_RESP_LOSS_REDUCTION).

Strategy
--------
Pre-PR-μ.1, the wizard widget accepted only [0,1] capability_value for
all sub-functions. After PR μ.1 introduced unit-aware semantics, rows
on these 4 sub-functions with capability_value < 1.0 (Sec-I5 conservative
threshold preserves legitimate sub-1.0 day-count values) are silently
re-interpreted as days/dollars. Set them to NULL so the NULL-safe-default
branch (opeff = 0.5 * coverage * reliability) takes over.

Per-org commit pattern (Sec-B3/Arch-I4 round-1 + Arch-2-I2 round-2):
commits inside the org loop, skips empty commits via org_mutations
counter. Bounds transaction size, releases locks faster, gives operator
partial-progress visibility, preserves idempotency.

Audit payload (Meth-2-I3 round-2 + Meth-3-I2 round-3): per-entry dict
records {sub_function, previous_capability_value, interpreted_as_pre_mu1}.
The third field captures the pre-μ.1 calculator semantic
('effectiveness_multiplier', not 'probability' — pre-μ.1 the value
flowed through cap * coverage * reliability, not as a FAIR probability
node).

Idempotency
-----------
Scoped by (organization_id, entity_id, action='null_fallback_issue_129').
Safe to re-run.

Deployment ordering (Arch-I3 round-1):
Run ONLY AFTER all application pods cycle to the post-μ.1b image.
In rolling-deploy environments, in-flight pre-μ.1b pods may freshly
write placeholder values mid-script. Pre-migration runs in the deploy
window will persist ``breakdown`` entries with incorrect
``capability_was_null=False`` + nonsensical opeff (legacy [0,1) value
read as days). Operator should:
1. Note deploy timestamp
2. Run this script promptly after pod cycle
3. Identify analyses created in the gap via
   ``SELECT id, created_at FROM risk_analysis_runs WHERE created_at
   BETWEEN $deploy_ts AND $migration_ts``
4. Re-run those analyses if their audit-replay matters

Run via::

    uv run python scripts/issue_129_audit_log_migration.py
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
from idraa.services.audit import AuditWriter

logger = logging.getLogger(__name__)

# The 4 unit-aware sub-functions that survived #131. Hardcoded — referencing
# SUB_FUNCTION_UNITS at script-run time would give a different answer if a
# future PR reclassifies more entries; this script is pinned to the issue-#129
# scope.
UNIT_AWARE_SUB_FUNCTIONS: frozenset[FairCamSubFunction] = frozenset(
    {
        FairCamSubFunction.LEC_DET_MONITORING,
        FairCamSubFunction.LEC_RESP_EVENT_TERMINATION,
        FairCamSubFunction.VMC_CORR_IMPLEMENTATION,
        FairCamSubFunction.LEC_RESP_LOSS_REDUCTION,
    }
)

ACTION_VERB = (
    "null_fallback_issue_129"  # 24 chars; fits AuditLog.action String(64) post-T6 widening
)
THRESHOLD = 1.0


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


async def _migrate_control(db: AsyncSession, control: Control) -> int:
    """Returns count of assignments nulled on this control."""
    nulled: list[dict[str, str | float]] = []
    for asgn in control.assignments:
        if asgn.sub_function not in UNIT_AWARE_SUB_FUNCTIONS:
            continue
        if asgn.capability_value is None:
            continue
        if asgn.capability_value >= THRESHOLD:
            continue
        nulled.append(
            {
                "sub_function": asgn.sub_function.value,
                "previous_capability_value": float(asgn.capability_value),
                # Meth-3-I2: pre-PR-mu.1 the value was an op-effectiveness multiplier
                # (cap * coverage * reliability), NOT literally a FAIR probability.
                "interpreted_as_pre_mu1": "effectiveness_multiplier",
            }
        )
        asgn.capability_value = None

    if not nulled:
        return 0

    # Sec-I5 round-1 + Sec-2-I3 round-2 + Sec-3-T8-N1 round-3:
    # defensive invariant. The frozenset has 4 members; the unique constraint
    # (control_id, sub_function) bounds nulled at 4. raise (not assert) so
    # python -O still enforces.
    if len(nulled) > 4:  # pragma: no cover  -- defensive tripwire
        raise ValueError(f"unexpected nulled count {len(nulled)} > 4")

    audit = AuditWriter(db)
    await audit.log(
        organization_id=control.organization_id,
        entity_type="control",
        entity_id=control.id,
        action=ACTION_VERB,
        changes={
            "nulled_assignments": nulled,
            "note": (
                "PR μ.1b backfill: legacy [0, 1.0) placeholders on 4 unit-aware "
                "sub-functions nulled per Sec-I5 conservative threshold (#129). "
                "Note: cap=1.0 exactly preserved as legitimate 1-day/$1 value."
            ),
        },
        user_id=None,
        ip_address=None,
    )
    return len(nulled)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logger.info("Issue #129 audit-log migration starting.")
    total_orgs = 0
    total_controls_seen = 0
    total_controls_mutated = 0
    total_assignments_nulled = 0

    async with get_session() as db:
        org_result = await db.execute(select(Organization))
        organizations = org_result.scalars().all()

        for org in organizations:
            total_orgs += 1
            org_mutations_this_run = 0  # Arch-2-I2 round-2: skip empty per-org commits.
            controls_stmt = (
                select(Control)
                .where(Control.organization_id == org.id)
                .options(selectinload(Control.assignments))
            )
            controls = (await db.execute(controls_stmt)).scalars().all()

            for control in controls:
                total_controls_seen += 1
                if await _already_migrated(db, org.id, control.id):
                    logger.debug(
                        "Skipping already-migrated control id=%s (org=%s)",
                        control.id,
                        org.id,
                    )
                    continue
                nulled_count = await _migrate_control(db, control)
                if nulled_count:
                    total_controls_mutated += 1
                    total_assignments_nulled += nulled_count
                    org_mutations_this_run += 1
                    logger.info(
                        "Mutated control id=%s (org=%s); nulled %d assignment(s).",
                        control.id,
                        org.id,
                        nulled_count,
                    )

            # Sec-B3 + Arch-I4 round-1 + Arch-2-I2 round-2:
            # per-org commit + skip empty.
            if org_mutations_this_run > 0:
                await db.commit()
                logger.info(
                    "Issue #129 migration committed org=%s controls_mutated_so_far=%d",
                    org.id,
                    total_controls_mutated,
                )

    logger.info(
        "Issue #129 migration complete: orgs=%d controls_seen=%d "
        "controls_mutated=%d assignments_nulled=%d",
        total_orgs,
        total_controls_seen,
        total_controls_mutated,
        total_assignments_nulled,
    )


if __name__ == "__main__":
    asyncio.run(main())
