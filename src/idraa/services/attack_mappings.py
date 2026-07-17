"""Scenario ↔ ATT&CK technique mapping authoring (issue #475).

Three operations, all org-scoped (the form-key extractor lives in
routes/scenario_form_helpers.py — form-encoding is a routes-layer concern):

- ``ensure_attack_techniques_addable`` — route-side PRE-validation (Sec2-I2).
  Called BEFORE ScenarioService.create/update: rejecting an unknown or
  deprecated technique AFTER create succeeds would 422 the operator while the
  auto-commit-on-success session persists the scenario anyway (half-applied
  write + misleading audit trail). set_scenario_attack_mappings repeats the
  same checks internally as defense-in-depth (deliberate duplication).
- ``set_scenario_attack_mappings`` — diff-apply of the submitted technique
  set. Survivors keep their row (source + rationale intact — a library-
  inherited mapping stays labeled 'library' across unrelated edits); removed
  rows are deleted; additions insert as source='user'. Deprecated techniques
  are blocked for NEW adds only (existing mappings survive a resubmit so a
  catalog refresh can't make a form unsubmittable). Emits a scenario-update-
  family AUDIT row when the diff is non-empty (Sec-I1: ScenarioService.update
  audits only ScenarioForm field diffs, so a mapping-only edit would
  otherwise be invisible); no row on a no-op.
- ``copy_library_attack_mappings`` — clone-time copy of an entry-version's
  curated mappings (copy-on-clone, same convention as distributions: the org
  row-set is independent of the canonical layer after creation). Runs inside
  scenario creation, which already writes the create audit entry.

Techniques are taxonomy metadata only — nothing here touches FAIR math.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.errors import ValidationError
from idraa.models.attack import (
    AttackTechnique,
    ScenarioAttackMapping,
    ScenarioLibraryEntryAttackMapping,
)
from idraa.services.audit import AuditWriter


async def _check_addable(
    db: AsyncSession, to_add: list[uuid.UUID]
) -> dict[uuid.UUID, AttackTechnique]:
    """Shared unknown/deprecated gate; returns the resolved techniques."""
    if not to_add:
        return {}
    found = (
        (await db.execute(select(AttackTechnique).where(AttackTechnique.id.in_(to_add))))
        .scalars()
        .all()
    )
    found_by_id = {t.id: t for t in found}
    unknown = [str(tid) for tid in to_add if tid not in found_by_id]
    if unknown:
        raise ValidationError(f"unknown ATT&CK technique id(s): {', '.join(unknown)}")
    dead = [t.technique_id for t in found if t.deprecated]
    if dead:
        raise ValidationError(
            f"deprecated ATT&CK technique(s) cannot be added: {', '.join(sorted(dead))}"
        )
    return found_by_id


async def ensure_attack_techniques_addable(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    scenario_id: uuid.UUID | None,
    technique_ids: list[uuid.UUID],
) -> None:
    """Sec2-I2 route-side pre-validation — see module docstring."""
    existing_ids: set[uuid.UUID] = set()
    if scenario_id is not None:
        existing_ids = set(
            (
                await db.execute(
                    select(ScenarioAttackMapping.technique_id).where(
                        ScenarioAttackMapping.scenario_id == scenario_id,
                        ScenarioAttackMapping.organization_id == organization_id,
                    )
                )
            )
            .scalars()
            .all()
        )
    await _check_addable(db, [tid for tid in technique_ids if tid not in existing_ids])


async def set_scenario_attack_mappings(
    db: AsyncSession,
    *,
    scenario_id: uuid.UUID,
    organization_id: uuid.UUID,
    technique_ids: list[uuid.UUID],
    actor_id: uuid.UUID | None = None,
    ip_address: str | None = None,
) -> None:
    existing = (
        (
            await db.execute(
                select(ScenarioAttackMapping).where(
                    ScenarioAttackMapping.scenario_id == scenario_id,
                    ScenarioAttackMapping.organization_id == organization_id,
                )
            )
        )
        .scalars()
        .all()
    )
    existing_by_tech = {row.technique_id: row for row in existing}
    submitted = set(technique_ids)
    to_add = [tid for tid in technique_ids if tid not in existing_by_tech]

    # Defense-in-depth: routes already ran ensure_attack_techniques_addable
    # before create/update (Sec2-I2); repeat the gate here so no other caller
    # can bypass it.
    found_by_id = await _check_addable(db, to_add)

    removed = [tid for tid in existing_by_tech if tid not in submitted]
    for tech_id in removed:
        await db.delete(existing_by_tech[tech_id])
    for tid in to_add:
        db.add(
            ScenarioAttackMapping(
                organization_id=organization_id,
                scenario_id=scenario_id,
                technique_id=tid,
                source="user",
            )
        )

    if to_add or removed:
        # Sec-I1: mapping-only edits must leave an audit trail. Mirrors the
        # audit emission ScenarioService.update uses (services/scenarios.py:534-542):
        #     await AuditWriter(self._db).log(
        #         organization_id=organization_id,
        #         entity_type="scenario",
        #         entity_id=scenario.id,
        #         action="scenario.update",
        #         changes=changes,
        #         user_id=current_user.id,
        #         ip_address=ip_address,
        #     )
        # Same entity_type ("scenario") / entity_id (scenario_id) / kwarg
        # shape; user_id is this function's actor_id (may be None — e.g. the
        # clone-copy path never calls this function directly).
        #
        # Payload discipline: the repo's audit contract (services/audit.py
        # docstring) mandates every `changes` value be a [prev, new] PAIR
        # (Sec2-I1) — never a {"before","after"} dict. Ids are DOMAIN-
        # QUALIFIED ("enterprise/T1566") because the same technique_id may
        # exist in both domains (Meth2-I2 — same identity rule as coverage).
        def _qualified(t: AttackTechnique) -> str:
            return f"{t.domain}/{t.technique_id}"

        before = sorted(_qualified(row.technique) for row in existing_by_tech.values())
        after = sorted(
            [
                _qualified(existing_by_tech[tid].technique)
                for tid in existing_by_tech
                if tid in submitted
            ]
            + [_qualified(found_by_id[tid]) for tid in to_add]
        )
        await AuditWriter(db).log(
            organization_id=organization_id,
            entity_type="scenario",
            entity_id=scenario_id,
            action="scenario.update",
            changes={"attack_techniques": [before, after]},
            user_id=actor_id,
            ip_address=ip_address,
        )

    await db.flush()


async def copy_library_attack_mappings(
    db: AsyncSession,
    *,
    scenario_id: uuid.UUID,
    organization_id: uuid.UUID,
    entry_id: uuid.UUID,
    entry_version: int,
) -> int:
    curated = (
        (
            await db.execute(
                select(ScenarioLibraryEntryAttackMapping).where(
                    ScenarioLibraryEntryAttackMapping.library_entry_id == entry_id,
                    ScenarioLibraryEntryAttackMapping.library_entry_version == entry_version,
                )
            )
        )
        .scalars()
        .all()
    )
    for row in curated:
        db.add(
            ScenarioAttackMapping(
                organization_id=organization_id,
                scenario_id=scenario_id,
                technique_id=row.technique_id,
                source="library",
                rationale=row.rationale,
            )
        )
    await db.flush()
    return len(curated)
