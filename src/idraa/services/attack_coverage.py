"""ATT&CK scenario-coverage view model (issue #475).

**v3 view-model derivation, not FAIR-grounded** — coverage ratios are
reporting derivations over taxonomy metadata; nothing here feeds fair_cam.

Reconciliation covenant FULFILLED: the dashboard redesign (#477) merged to
main first and shipped the ONE shared coverage primitive at
``idraa.services.coverage`` (``CoverageResult`` / ``coverage(reference,
covered)``). This module no longer defines its own ``CoverageResult`` /
``coverage`` — it imports the shared ones and only builds the ATT&CK-specific
view model (tactic/domain rollups, per-technique scenario-name rows) on top
of them. The reference (denominator) is always DATA read from the seeded
catalog — never a list literal — and is passed to the shared helper as a
sorted list so ``missing``/``present`` order stays deterministic (the shared
helper preserves reference first-seen order; it does not sort internally).

Covered = techniques mapped by the org's ACTIVE scenarios. Deprecated
techniques are excluded from the reference (they can't be newly added), but
existing mappings to them still render on scenario pages.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.attack import DOMAIN_LABELS as _DOMAIN_LABELS  # #482 single-source
from idraa.models.attack import DOMAIN_ORDER as _DOMAIN_ORDER
from idraa.models.attack import AttackTactic, AttackTechnique, ScenarioAttackMapping
from idraa.models.enums import EntityStatus
from idraa.models.scenario import Scenario
from idraa.services.coverage import CoverageResult, coverage


@dataclass(frozen=True)
class TechniqueCoverageRow:
    technique_id: str
    name: str
    covered: bool
    scenario_names: list[str]  # capped at 3 (Arch-I6 — hundreds-of-scenarios rule)
    scenario_overflow_count: int  # how many more scenarios model it beyond the cap


@dataclass(frozen=True)
class TacticCoverage:
    tactic_id: str
    name: str
    shortname: str
    result: CoverageResult
    techniques: list[TechniqueCoverageRow]


@dataclass(frozen=True)
class DomainCoverage:
    domain: str
    label: str
    overall: CoverageResult
    tactics: list[TacticCoverage]


@dataclass(frozen=True)
class AttackCoverageViewModel:
    domains: list[DomainCoverage]
    mapped_scenario_count: int
    unmapped_pinned_scenario_count: int


async def build_attack_coverage(
    db: AsyncSession, *, organization_id: uuid.UUID
) -> AttackCoverageViewModel:
    tactics = (
        (await db.execute(select(AttackTactic).order_by(AttackTactic.display_order)))
        .scalars()
        .all()
    )
    techniques = (
        (await db.execute(select(AttackTechnique).where(AttackTechnique.deprecated.is_(False))))
        .scalars()
        .all()
    )
    mapping_rows = (
        await db.execute(
            select(ScenarioAttackMapping.technique_id, Scenario.name, Scenario.id)
            .join(Scenario, Scenario.id == ScenarioAttackMapping.scenario_id)
            .where(
                ScenarioAttackMapping.organization_id == organization_id,
                # Sec-N1: belt-and-suspenders — the mapping org filter
                # should imply this, but a corrupted row must not leak
                # another org's scenario NAME onto this page.
                Scenario.organization_id == organization_id,
                Scenario.status == EntityStatus.ACTIVE,
            )
        )
    ).all()
    scenarios_by_technique: dict[uuid.UUID, list[str]] = {}
    mapped_scenario_ids: set[uuid.UUID] = set()
    for technique_uuid, scenario_name, scenario_id in mapping_rows:
        scenarios_by_technique.setdefault(technique_uuid, []).append(scenario_name)
        mapped_scenario_ids.add(scenario_id)

    name_cap = 3  # Arch-I6: cap covering-scenario names in the view model

    # Enterprise first, then lexical — a stable tiebreak so a future third
    # domain (ATLAS) groups deterministically instead of interleaving with
    # set-iteration order (str hashing is randomized per-process).
    domains_present = sorted(
        {t.domain for t in tactics}, key=lambda d: (_DOMAIN_ORDER.get(d, 99), d)
    )

    domain_blocks: list[DomainCoverage] = []
    for domain in domains_present:
        domain_techs = [t for t in techniques if t.domain == domain]
        reference = {t.technique_id for t in domain_techs}
        # Meth-I1/Arch-I1: covered is computed PER DOMAIN via catalog-UUID
        # membership — (domain, technique_id) is the identity. A bare
        # technique_id set would let an Enterprise mapping mark a same-ID ICS
        # technique covered (the schema explicitly permits same-ID-two-domains).
        covered = {t.technique_id for t in domain_techs if t.id in scenarios_by_technique}
        tactic_blocks: list[TacticCoverage] = []
        for tactic in [t for t in tactics if t.domain == domain]:
            in_tactic = [t for t in domain_techs if tactic.shortname in t.tactics]
            rows = []
            for t in sorted(in_tactic, key=lambda t: t.technique_id):
                names = sorted(scenarios_by_technique.get(t.id, []))
                rows.append(
                    TechniqueCoverageRow(
                        technique_id=t.technique_id,
                        name=t.name,
                        covered=t.id in scenarios_by_technique,
                        scenario_names=names[:name_cap],
                        scenario_overflow_count=max(0, len(names) - name_cap),
                    )
                )
            tactic_blocks.append(
                TacticCoverage(
                    tactic_id=tactic.tactic_id,
                    name=tactic.name,
                    shortname=tactic.shortname,
                    result=coverage(sorted({t.technique_id for t in in_tactic}), covered),
                    techniques=rows,
                )
            )
        domain_blocks.append(
            DomainCoverage(
                domain=domain,
                label=_DOMAIN_LABELS.get(domain, domain),
                overall=coverage(sorted(reference), covered),
                tactics=tactic_blocks,
            )
        )

    # ACTIVE library-pinned scenarios with zero mappings → partial-curation banner.
    # Arch2-I3 (empirically verified): ScenarioService.create passes
    # library_pin=None explicitly, and SQLAlchemy's JSON type stores that as
    # the JSON text 'null' — NOT SQL NULL — so `library_pin.is_not(None)`
    # matches every custom scenario and would inflate this count. Filter
    # Python-side with the established truthiness idiom (routes/scenarios.py:647).
    pinned_rows = (
        await db.execute(
            select(Scenario.id, Scenario.library_pin).where(
                Scenario.organization_id == organization_id,
                Scenario.status == EntityStatus.ACTIVE,
            )
        )
    ).all()
    unmapped_pinned = len(
        [
            sid
            for sid, pin in pinned_rows
            if pin and pin.get("entry_id") and sid not in mapped_scenario_ids
        ]
    )

    return AttackCoverageViewModel(
        domains=domain_blocks,
        mapped_scenario_count=len(mapped_scenario_ids),
        unmapped_pinned_scenario_count=unmapped_pinned,
    )


@dataclass(frozen=True)
class AttackDomainSummary:
    """Dashboard tactic-rollup for one catalog domain (issue #475 follow-up).

    ``tactic_result``: reference = the domain's seeded tactics in
    ``display_order``; covered = tactics with >=1 non-deprecated technique
    mapped by the org's ACTIVE scenarios. Denominators are always catalog
    data — never a literal (v19 split Defense Evasion into Stealth +
    Defense Impairment; Enterprise is 15, not the classic 14).
    """

    domain: str
    label: str
    tactic_result: CoverageResult
    technique_count_mapped: int


async def build_attack_coverage_summary(
    db: AsyncSession, *, organization_id: uuid.UUID
) -> list[AttackDomainSummary]:
    """Slim tactic-level rollup for the dashboard (one row per domain).

    Unlike :func:`build_attack_coverage`, this never materializes
    per-technique rows or covering-scenario names (Arch-I6) — the dashboard
    links to ``/scenarios/attack-coverage`` for that breakdown. Empty
    catalog (pre-seed DB) returns ``[]`` and the template hides the block.
    """
    tactics = (
        (await db.execute(select(AttackTactic).order_by(AttackTactic.display_order)))
        .scalars()
        .all()
    )
    techniques = (
        (await db.execute(select(AttackTechnique).where(AttackTechnique.deprecated.is_(False))))
        .scalars()
        .all()
    )
    mapped_technique_uuids = set(
        (
            await db.execute(
                select(ScenarioAttackMapping.technique_id)
                .join(Scenario, Scenario.id == ScenarioAttackMapping.scenario_id)
                .where(
                    ScenarioAttackMapping.organization_id == organization_id,
                    # Sec-N1 belt-and-suspenders, same as build_attack_coverage.
                    Scenario.organization_id == organization_id,
                    Scenario.status == EntityStatus.ACTIVE,
                )
            )
        )
        .scalars()
        .all()
    )

    domains_present = sorted(
        {t.domain for t in tactics}, key=lambda d: (_DOMAIN_ORDER.get(d, 99), d)
    )
    summaries: list[AttackDomainSummary] = []
    for domain in domains_present:
        # Meth-I1/Arch-I1: membership by catalog-row UUID, scoped to this
        # domain's technique rows — same-ID-two-domains never cross-covers.
        mapped_domain_techs = [
            t for t in techniques if t.domain == domain and t.id in mapped_technique_uuids
        ]
        covered_shortnames = {s for t in mapped_domain_techs for s in t.tactics}
        domain_tactics = [t for t in tactics if t.domain == domain]
        # Reference in display_order (deterministic); the shared helper
        # preserves reference order for missing/present.
        reference = [t.tactic_id for t in domain_tactics]
        covered = {t.tactic_id for t in domain_tactics if t.shortname in covered_shortnames}
        summaries.append(
            AttackDomainSummary(
                domain=domain,
                label=_DOMAIN_LABELS.get(domain, domain),
                tactic_result=coverage(reference, covered),
                technique_count_mapped=len(mapped_domain_techs),
            )
        )
    return summaries
