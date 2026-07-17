"""OverlayRepo unit tests — get_active, list_active, get_for_org.

The historical ``fetch_revision_dto`` / ``fetch_revision_with_provenance``
tests were dropped in PR pi F12 alongside the methods they exercised.
"""

from __future__ import annotations

import uuid


async def test_get_active_returns_live_row(
    db_session, organization, seeded_critical_infrastructure_overlay
):
    """get_active returns the OverlayDefinition row matching (org, tag) when active."""
    from idraa.repositories.overlay_repo import OverlayRepo

    repo = OverlayRepo(db_session)
    od = await repo.get_active(
        organization_id=organization.id,
        tag="critical_infrastructure",
    )

    assert od is not None
    assert od.tag == "critical_infrastructure"


async def test_get_active_returns_none_for_inactive_or_missing(db_session, organization):
    from idraa.repositories.overlay_repo import OverlayRepo

    repo = OverlayRepo(db_session)
    result = await repo.get_active(
        organization_id=organization.id,
        tag="totally_made_up_tag_does_not_exist",
    )
    assert result is None


async def test_list_active_returns_only_active_for_org(
    db_session, organization, seeded_critical_infrastructure_overlay
):
    """list_active returns the live overlay rows for the organization."""
    from idraa.repositories.overlay_repo import OverlayRepo

    repo = OverlayRepo(db_session)
    rows = await repo.list_active(organization_id=organization.id)
    tags = {od.tag for od in rows}
    assert "critical_infrastructure" in tags
    # All STARTER_OVERLAYS get seeded by the fixture's seed callable.
    assert all(od.is_active for od in rows)


async def test_get_for_org_returns_none_on_org_mismatch(
    db_session, organization, seeded_critical_infrastructure_overlay
):
    """get_for_org returns None (NOT raises) when overlay belongs to a
    different org. Caller treats None as 404 to avoid leaking existence."""
    from idraa.repositories.overlay_repo import OverlayRepo

    repo = OverlayRepo(db_session)
    # Real overlay id, but pretend it belongs to a different org.
    other_org_id = uuid.uuid4()
    result = await repo.get_for_org(
        overlay_id=seeded_critical_infrastructure_overlay.id,
        organization_id=other_org_id,
    )
    assert result is None


async def test_get_for_org_returns_row_on_org_match(
    db_session, organization, seeded_critical_infrastructure_overlay
):
    from idraa.repositories.overlay_repo import OverlayRepo

    repo = OverlayRepo(db_session)
    od = await repo.get_for_org(
        overlay_id=seeded_critical_infrastructure_overlay.id,
        organization_id=organization.id,
    )
    assert od is not None
    assert od.id == seeded_critical_infrastructure_overlay.id


async def test_get_for_org_returns_none_for_unknown_overlay(db_session, organization):
    from idraa.repositories.overlay_repo import OverlayRepo

    repo = OverlayRepo(db_session)
    result = await repo.get_for_org(overlay_id=uuid.uuid4(), organization_id=organization.id)
    assert result is None
