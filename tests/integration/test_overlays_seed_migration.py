"""Verifies the seed callable populates STARTER_OVERLAYS per organization.

Note: ``db_session`` builds schema via ``Base.metadata.create_all`` and never
runs Alembic data migrations, so these tests exercise the async callable
``seed_starter_overlays_for_org`` directly. The Alembic seed migration is
the parallel sync path used in production; both paths are required to
produce identical seeded rows (plan §C3, B6).
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from idraa.models.overlay import OverlayDefinition, OverlayDefinitionRevision


async def test_starter_overlays_seeded_for_existing_organization(db_session, organization):
    """After invoking the seed callable, the organization should have one
    OverlayDefinition row per STARTER_OVERLAYS entry."""
    from idraa.services._starter_overlays_seed_data import STARTER_OVERLAYS
    from idraa.services.overlays import seed_starter_overlays_for_org

    seeded = await seed_starter_overlays_for_org(db_session, organization_id=organization.id)
    assert seeded == len(STARTER_OVERLAYS)

    rows = await db_session.execute(
        select(OverlayDefinition).where(OverlayDefinition.organization_id == organization.id)
    )
    by_tag = {od.tag: od for od in rows.scalars().all()}

    for overlay in STARTER_OVERLAYS:
        assert overlay.tag in by_tag, (
            f"STARTER_OVERLAYS[{overlay.tag}] not seeded for organization {organization.id}"
        )
        seeded_row = by_tag[overlay.tag]
        assert seeded_row.frequency_multiplier == pytest.approx(overlay.frequency_multiplier)
        assert seeded_row.magnitude_multiplier == pytest.approx(overlay.magnitude_multiplier)
        assert seeded_row.version == 1
        assert seeded_row.is_active is True
        assert seeded_row.methodology, "seeded overlay must have non-empty methodology"


async def test_starter_overlays_have_v1_revision(db_session, organization):
    """Every seeded OverlayDefinition has a corresponding v1 revision row
    with methodology_change_reason='initial seed from STARTER_OVERLAYS'."""
    from idraa.services.overlays import seed_starter_overlays_for_org

    await seed_starter_overlays_for_org(db_session, organization_id=organization.id)

    rows = await db_session.execute(
        select(OverlayDefinition).where(OverlayDefinition.organization_id == organization.id)
    )
    for od in rows.scalars().all():
        rev_rows = await db_session.execute(
            select(OverlayDefinitionRevision).where(
                OverlayDefinitionRevision.overlay_definition_id == od.id,
                OverlayDefinitionRevision.version == 1,
            )
        )
        revisions = list(rev_rows.scalars().all())
        assert len(revisions) == 1
        assert revisions[0].methodology_change_reason == "initial seed from STARTER_OVERLAYS"
        assert revisions[0].created_by_user_id is None


async def test_seed_is_idempotent_on_repeat_call(db_session, organization):
    """Calling seed_starter_overlays_for_org twice on the same org returns 0
    on the second call and does not duplicate rows. B6 invariant."""
    from idraa.services._starter_overlays_seed_data import STARTER_OVERLAYS
    from idraa.services.overlays import seed_starter_overlays_for_org

    first = await seed_starter_overlays_for_org(db_session, organization_id=organization.id)
    second = await seed_starter_overlays_for_org(db_session, organization_id=organization.id)

    assert first == len(STARTER_OVERLAYS)
    assert second == 0

    rows = await db_session.execute(
        select(OverlayDefinition).where(OverlayDefinition.organization_id == organization.id)
    )
    by_tag: dict[str, list[OverlayDefinition]] = {}
    for od in rows.scalars().all():
        by_tag.setdefault(od.tag, []).append(od)
    for tag, defs in by_tag.items():
        assert len(defs) == 1, f"tag {tag!r} duplicated: {len(defs)} rows"


async def test_seed_raises_runtime_error_on_missing_methodology(
    db_session, organization, monkeypatch
):
    """seed_starter_overlays_for_org raises RuntimeError when
    STARTER_OVERLAY_PROVENANCE is missing methodology for a starter tag.
    B14 invariant — fail loud, never silent skip."""
    from idraa.services import _starter_overlays_seed_data as overlay_mod
    from idraa.services.overlays import seed_starter_overlays_for_org

    target_tag = overlay_mod.STARTER_OVERLAYS[0].tag
    patched = dict(overlay_mod.STARTER_OVERLAY_PROVENANCE)
    patched[target_tag] = {**patched[target_tag], "methodology": ""}
    monkeypatch.setattr(overlay_mod, "STARTER_OVERLAY_PROVENANCE", patched)

    with pytest.raises(RuntimeError, match="missing methodology for tag"):
        await seed_starter_overlays_for_org(db_session, organization_id=organization.id)
