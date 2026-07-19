"""ScenarioService.update_from_wizard (#56).

Mirrors tests/unit/test_scenario_service.py's fixture/builder idiom
(``db_session`` + ``seed_org_user`` from the shared root conftest, a
minimal-valid ``_form()`` builder) rather than the ``tests/services/``
directory's self-contained ``db``/``org_id``/``actor_id`` fixtures — this
module exercises the same ``ScenarioService`` surface as the existing
update() suite and the two need to be directly comparable.

Task 2 contract (plan `docs/superpowers/plans/2026-07-19-wizard-reestimate.md`):
1. distributions replaced in place, row_version bumped, same scenario id.
2. row_version mismatch raises ScenarioVersionConflictError, scenario unchanged.
3. provenance flip is BY CONSTRUCTION (source/library_pin/vuln_framing) even
   when the vulnerability numeric triple is unchanged — stronger than
   update()'s changed-triple-only flip.
4. status / effect / scenario_type / descriptive version are preserved when
   the caller's form carries them through unchanged.
5. the audit row carries the extras diff (source, conversion_metadata,
   entry_currency) plus the pooling summary and row_version bump.
6. NOT a new test here: the existing update() suite
   (tests/unit/test_scenario_service.py) must stay green after the
   _capture_audit_before / _apply_form_fields / _audit_diff extraction —
   verified by running that module alongside this one, not duplicated here.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.audit_log import AuditLog
from idraa.models.enums import ScenarioEffect, ScenarioSource, ScenarioType, ThreatCategory
from idraa.schemas.scenario import ScenarioForm
from idraa.services.scenarios import ScenarioService, ScenarioVersionConflictError

SeedOrgUser = Callable[..., Awaitable[Any]]


def _form(
    *,
    name: str = "Ransomware",
    description: str | None = None,
    threat_event_frequency: dict[str, Any] | None = None,
    vulnerability: dict[str, Any] | None = None,
    primary_loss: dict[str, Any] | None = None,
    effect: str | None = None,
    scenario_type: ScenarioType = ScenarioType.CUSTOM,
    version: str = "1.0",
) -> ScenarioForm:
    """Minimal valid ScenarioForm for service tests (mirrors
    tests/unit/test_scenario_service.py's ``_form`` builder)."""
    return ScenarioForm(
        name=name,
        description=description,
        scenario_type=scenario_type,
        threat_category=ThreatCategory.RANSOMWARE,
        effect=effect,
        version=version,
        threat_event_frequency=threat_event_frequency
        or {
            "distribution": "PERT",
            "low": 0.1,
            "mode": 0.5,
            "high": 2.0,
        },
        vulnerability=vulnerability
        or {
            "distribution": "PERT",
            "low": 0.2,
            "mode": 0.4,
            "high": 0.6,
        },
        primary_loss=primary_loss
        or {
            "distribution": "PERT",
            "low": 50_000,
            "mode": 250_000,
            "high": 2_000_000,
        },
    )


async def test_updates_distributions_in_place_and_bumps_row_version(
    db_session: AsyncSession,
    seed_org_user: SeedOrgUser,
) -> None:
    org, user = await seed_org_user(db_session)

    service = ScenarioService(db_session)
    s = await service.create(
        organization_id=org.id,
        form=_form(name="Original"),
        current_user=user,
    )
    assert s.row_version == 1
    scenario_id = s.id

    new_tef = {"distribution": "PERT", "low": 0.05, "mode": 0.3, "high": 1.0}
    new_vuln = {"distribution": "PERT", "low": 0.1, "mode": 0.5, "high": 0.9}
    new_pl = {"distribution": "PERT", "low": 10_000, "mode": 100_000, "high": 1_000_000}
    new_form = _form(
        name="Re-estimated",
        threat_event_frequency=new_tef,
        vulnerability=new_vuln,
        primary_loss=new_pl,
    )

    updated = await service.update_from_wizard(
        organization_id=org.id,
        scenario_id=scenario_id,
        form=new_form,
        expected_row_version=1,
        actor=user,
    )

    assert updated.id == scenario_id
    assert updated.threat_event_frequency == new_tef
    assert updated.vulnerability == new_vuln
    assert updated.primary_loss == new_pl
    assert updated.row_version == 2


async def test_row_version_conflict_raises(
    db_session: AsyncSession,
    seed_org_user: SeedOrgUser,
) -> None:
    org, user = await seed_org_user(db_session)

    service = ScenarioService(db_session)
    s = await service.create(
        organization_id=org.id,
        form=_form(name="Original"),
        current_user=user,
    )

    with pytest.raises(ScenarioVersionConflictError):
        await service.update_from_wizard(
            organization_id=org.id,
            scenario_id=s.id,
            form=_form(name="Should not apply"),
            expected_row_version=99,
            actor=user,
        )

    await db_session.refresh(s)
    assert s.name == "Original"
    assert s.row_version == 1


async def test_provenance_flip(
    db_session: AsyncSession,
    seed_org_user: SeedOrgUser,
) -> None:
    """Provenance flips BY CONSTRUCTION — source/library_pin/vuln_framing
    all reset even though the vulnerability numeric triple is UNCHANGED
    between the seed and the re-estimation form. This is the stronger,
    unconditional flip vs. update()'s changed-triple-only flip."""
    org, user = await seed_org_user(db_session)

    service = ScenarioService(db_session)
    vuln = {"distribution": "PERT", "low": 0.2, "mode": 0.4, "high": 0.6}
    s = await service.create(
        organization_id=org.id,
        form=_form(name="Library clone", vulnerability=vuln),
        current_user=user,
    )
    # Simulate a library-derived, legacy-residual-framed scenario directly
    # on the ORM row (no library entry fixture needed — the service layer
    # under test never re-reads library_pin, it only clears it).
    s.source = ScenarioSource.LIBRARY_DERIVED
    s.library_pin = {"entry_id": "00000000-0000-0000-0000-000000000001", "version": 1}
    s.vuln_framing = "legacy_residual"
    await db_session.flush()

    updated = await service.update_from_wizard(
        organization_id=org.id,
        scenario_id=s.id,
        form=_form(name="Re-estimated", vulnerability=vuln),  # SAME triple
        expected_row_version=s.row_version,
        actor=user,
    )

    assert updated.source is ScenarioSource.EXPERT_JUDGMENT
    assert updated.library_pin is None
    assert updated.vuln_framing == "inherent"


async def test_status_and_effect_and_descriptive_version_preserved(
    db_session: AsyncSession,
    seed_org_user: SeedOrgUser,
) -> None:
    org, user = await seed_org_user(db_session)

    service = ScenarioService(db_session)
    s = await service.create(
        organization_id=org.id,
        form=_form(
            name="Original",
            effect=ScenarioEffect.AVAILABILITY.value,
            scenario_type=ScenarioType.CUSTOM,
            version="2.3",
        ),
        current_user=user,
    )
    assert s.status.value == "active"

    updated = await service.update_from_wizard(
        organization_id=org.id,
        scenario_id=s.id,
        form=_form(
            name="Re-estimated",
            effect=ScenarioEffect.AVAILABILITY.value,
            scenario_type=ScenarioType.CUSTOM,
            version="2.3",
        ),
        expected_row_version=s.row_version,
        actor=user,
    )

    assert updated.status.value == "active"
    # scenario.effect is assigned the raw str form value (no enum coercion
    # until reload — matches update()'s _val()/_capture_audit_before use of
    # getattr(x, "value", x) elsewhere in this file/service).
    assert getattr(updated.effect, "value", updated.effect) == "availability"
    assert getattr(updated.scenario_type, "value", updated.scenario_type) == "custom"
    assert updated.version == "2.3"


async def test_audit_row_carries_diff_and_pooling_summary(
    db_session: AsyncSession,
    seed_org_user: SeedOrgUser,
) -> None:
    org, user = await seed_org_user(db_session)

    service = ScenarioService(db_session)
    s = await service.create(
        organization_id=org.id,
        form=_form(name="Original"),
        current_user=user,
    )
    s.source = ScenarioSource.LIBRARY_DERIVED
    s.conversion_metadata = {"register_row_id": "abc-123", "likelihood_band": "high"}
    s.entry_currency = "EUR"
    s.entry_rate = 1.08
    await db_session.flush()
    assert s.row_version == 1

    await service.update_from_wizard(
        organization_id=org.id,
        scenario_id=s.id,
        form=_form(name="Re-estimated"),
        expected_row_version=1,
        actor=user,
        per_fieldset_pooling_summary={"tef": {"n_smes": 2}},
    )

    audit = (
        (
            await db_session.execute(
                select(AuditLog)
                .where(AuditLog.entity_id == s.id, AuditLog.action == "scenario.update")
                .order_by(AuditLog.timestamp.desc())
            )
        )
        .scalars()
        .first()
    )
    assert audit is not None
    changes = audit.changes
    assert changes["source"] == ["library_derived", "expert_judgment"]
    assert changes["conversion_metadata"][0] is not None
    assert changes["conversion_metadata"][1] is None
    assert changes["entry_currency"] == ["EUR", "USD"]
    assert changes["entry_rate"] == [1.08, None]
    assert changes["per_fieldset_pooling_summary"]["tef"]["n_smes"] == 2
    assert changes["row_version"] == [1, 2]

    await db_session.refresh(s)
    assert s.entry_currency == "USD"
