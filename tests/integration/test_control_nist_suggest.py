"""#144 — NIST-tag crosswalk suggest partial + form round-trip."""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.control import Control
from idraa.models.enums import FairCamSubFunction
from idraa.models.framework_crosswalk import FrameworkControl, FrameworkControlFairCam


async def _seed_crosswalk_row(db: AsyncSession) -> None:
    """Minimal PR.AC-7 -> lec_prev_resistance crosswalk row (the pytest DB is
    create_all-built; the real crosswalk arrives via migrations only)."""
    fc = FrameworkControl(
        framework="nist_csf",
        framework_version="1.1",
        code="PR.AC-7",
        title="Users, devices, and other assets are authenticated",
        description=None,
        asset_type=None,
        security_function=None,
        citation={"source": "test"},
    )
    db.add(fc)
    await db.flush()
    db.add(
        FrameworkControlFairCam(
            framework_control_id=fc.id,
            fair_cam_function=FairCamSubFunction.LEC_PREV_RESISTANCE,
        )
    )
    await db.flush()
    await db.commit()


@pytest.mark.asyncio
async def test_nist_suggest_partial_grounds_functions(
    admin_client: AsyncClient, db_session: AsyncSession
) -> None:
    # PR.AC-7 -> lec_prev_resistance mirrors the seeded FAIR-Institute
    # crosswalk (hand-verified spot-check in test_crosswalk_fidelity).
    await _seed_crosswalk_row(db_session)
    r = await admin_client.get(
        "/controls/nist-suggest", params={"nist_csf_functions": "PR.AC-7, NO.PE-99"}
    )
    assert r.status_code == 200
    assert "lec_prev_resistance" in r.text
    assert "NO.PE-99" in r.text  # flagged as unknown
    assert "Not in the NIST CSF 1.1 crosswalk" in r.text


@pytest.mark.asyncio
async def test_nist_suggest_empty_input_renders_nothing(admin_client: AsyncClient) -> None:
    r = await admin_client.get("/controls/nist-suggest", params={"nist_csf_functions": ""})
    assert r.status_code == 200
    assert "badge-info" not in r.text


@pytest.mark.asyncio
async def test_control_form_csv_round_trip_persists_nist_tags(
    admin_client: AsyncClient, db_session: AsyncSession
) -> None:
    """The form field posts a CSV string; ControlForm.split_csv parses it and
    the service persists the list (suite convention: create via service layer,
    mirroring test_controls_crud)."""
    from idraa.models.organization import Organization
    from idraa.schemas.control import ControlForm, ControlFunctionAssignmentDTO
    from idraa.services import controls as controls_svc

    org = (await db_session.execute(select(Organization))).scalars().first()
    form = ControlForm(
        name="NIST-tagged control (#144)",
        type="technical",
        annual_cost="1000",
        nist_csf_functions="PR.AC-7, DE.CM-1",  # CSV exactly as the input posts it
        assignments=[
            ControlFunctionAssignmentDTO(
                sub_function="lec_prev_resistance",
                capability_value=0.7,
                coverage=0.8,
                reliability=0.8,
            )
        ],
    )
    assert form.nist_csf_functions == ["PR.AC-7", "DE.CM-1"]
    control = await controls_svc.create_control(db_session, org_id=org.id, user_id=None, form=form)
    await db_session.commit()
    row = (await db_session.execute(select(Control).where(Control.id == control.id))).scalar_one()
    assert row.nist_csf_functions == ["PR.AC-7", "DE.CM-1"]


@pytest.mark.asyncio
async def test_resync_routes_guard_and_apply(
    admin_client: AsyncClient, db_session: AsyncSession
) -> None:
    """SWE-review NTH: route-level coverage for the destructive POST —
    409 when in sync, 303 redirect with the stale-run count after a real
    re-curation, 404 for a foreign/unknown control id."""
    import uuid as _uuid

    from idraa.models.organization import Organization
    from tests.conftest import csrf_post
    from tests.services.test_control_adopt import _published_entry
    from tests.services.test_control_resync import _recurate_to_v2

    org = (await db_session.execute(select(Organization))).scalars().first()
    entry = await _published_entry(db_session, slug="resync-route-mfa")
    from idraa.services.controls import adopt_from_library

    control = await adopt_from_library(
        db_session, org_id=org.id, user_id=None, entry_id=entry.id, version=None
    )
    await db_session.commit()

    # In sync -> POST is a 409 (ValueError translated).
    r = await csrf_post(admin_client, f"/controls/{control.id}/resync", {})
    assert r.status_code == 409

    # Recurate -> GET review renders, POST applies with a 303 redirect.
    await _recurate_to_v2(db_session, entry)
    await db_session.commit()
    r = await admin_client.get(f"/controls/{control.id}/resync")
    assert r.status_code == 200 and "Apply re-sync" in r.text
    r = await csrf_post(admin_client, f"/controls/{control.id}/resync", {}, follow_redirects=False)
    assert r.status_code == 303
    assert f"/controls/{control.id}?resynced=1" in r.headers["location"]

    # Unknown id -> 404.
    r = await csrf_post(admin_client, f"/controls/{_uuid.uuid4()}/resync", {})
    assert r.status_code == 404
