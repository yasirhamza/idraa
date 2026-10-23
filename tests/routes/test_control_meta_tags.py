"""Issue #268 (spec §3.6): VMC / DSC tags on the controls list, the control detail
header and the control library entry card.

A tag is derived from a control's sub-function slugs and never stored: ``VMC`` if any
``vmc_*`` slug, ``DSC`` if any ``dsc_*`` slug (a derived ``dsc_corr_misaligned`` row
counts). The detail page additionally mutes the three tags that do not, on their own,
credit loss reduction (``badge-warning``, reason in ``title``). Markers: every tag carries
``data-meta-tag="VMC|DSC"``.
"""

from __future__ import annotations

import re
import uuid
from decimal import Decimal
from typing import Any

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.control import Control
from idraa.models.control_function_assignment import ControlFunctionAssignment
from idraa.models.enums import ControlType, EntityStatus, FairCamSubFunction

VMC_TITLE = (
    "Variance Management — strengthens the reliability of every loss-event control in the "
    "scenario (or scenario group in an aggregate run); it does not reduce loss on its own."
)
DSC_TITLE = (
    "Decision Support — strengthens the reliability of every loss-event control in the "
    "scenario (or scenario group in an aggregate run); it does not reduce loss on its own."
)
TS_NOTE = "label-only: Treatment Selection is recorded, never scored (register C8)"
DSC_ID_NOTE = (
    "identifies misaligned decisions; scores only when a control in the same run corrects them"
)
DSC_CORR_NOTE = (
    "corrects misaligned decisions; scores only when a control in the same run identifies them"
)


def _tag(body: str, code: str) -> str | None:
    """The opening ``<span ...>`` of the first ``data-meta-tag="<code>"`` badge, or None."""
    m = re.search(rf'<span[^>]*data-meta-tag="{code}"[^>]*>', body)
    return m.group(0) if m else None


async def _seed_control(
    db: AsyncSession,
    org_id: uuid.UUID,
    name: str,
    slugs: list[FairCamSubFunction],
    *,
    derived_corr_from: FairCamSubFunction | None = None,
) -> Control:
    """A control with one plain assignment per slug, plus (optionally) a derived
    ``dsc_corr_misaligned`` row linked to the plain row with sub-function
    ``derived_corr_from`` (stored sentinels: capability NULL, coverage 0.0, reliability 0.0)."""
    ctrl = Control(
        organization_id=org_id,
        name=name,
        type=ControlType.TECHNICAL,
        annual_cost=Decimal("0"),
        status=EntityStatus.ACTIVE,
        version="1.0",
    )
    db.add(ctrl)
    await db.flush()
    rows: dict[FairCamSubFunction, ControlFunctionAssignment] = {}
    for sf in slugs:
        row = ControlFunctionAssignment(
            organization_id=org_id,
            control_id=ctrl.id,
            sub_function=sf,
            capability_value=0.8,
            coverage=0.85,
            reliability=0.9,
        )
        db.add(row)
        rows[sf] = row
    await db.flush()
    if derived_corr_from is not None:
        db.add(
            ControlFunctionAssignment(
                organization_id=org_id,
                control_id=ctrl.id,
                sub_function=FairCamSubFunction.DSC_CORR_MISALIGNED,
                capability_value=None,
                coverage=0.0,
                reliability=0.0,
                derived_from_assignment_id=rows[derived_corr_from].id,
            )
        )
    await db.commit()
    return ctrl


# ---------------------------------------------------------------------------
# Controls list
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_tags_vmc_and_dsc_controls_and_leaves_others_untagged(
    authed_admin: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    client, org_id = authed_admin
    await _seed_control(
        db_session, org_id, "Only-VMC", [FairCamSubFunction.VMC_ID_THREAT_INTELLIGENCE]
    )
    r = await client.get("/controls")
    assert r.status_code == 200, r.text[:300]
    vmc = _tag(r.text, "VMC")
    assert vmc is not None
    assert f'title="{VMC_TITLE}"' in vmc
    assert "badge-ghost" in vmc
    assert _tag(r.text, "DSC") is None

    # A DSC-only control adds the DSC tag; a plain loss-event control adds none.
    await _seed_control(
        db_session, org_id, "Only-DSC", [FairCamSubFunction.DSC_PREV_DEFINED_EXPECTATIONS]
    )
    r = await client.get("/controls")
    dsc = _tag(r.text, "DSC")
    assert dsc is not None
    assert f'title="{DSC_TITLE}"' in dsc


@pytest.mark.asyncio
async def test_list_no_tags_for_plain_loss_event_control(
    authed_admin: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    client, org_id = authed_admin
    await _seed_control(db_session, org_id, "Plain-LEC", [FairCamSubFunction.LEC_PREV_RESISTANCE])
    r = await client.get("/controls")
    assert r.status_code == 200
    assert "Plain-LEC" in r.text
    assert 'data-meta-tag="' not in r.text


@pytest.mark.asyncio
async def test_list_hybrid_control_shows_both_tags_beside_the_name(
    authed_admin: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    client, org_id = authed_admin
    await _seed_control(
        db_session,
        org_id,
        "Hybrid-Ctl",
        [
            FairCamSubFunction.LEC_PREV_RESISTANCE,
            FairCamSubFunction.VMC_ID_CONTROL_MONITORING,
            FairCamSubFunction.DSC_ID_MISALIGNED,
        ],
    )
    r = await client.get("/controls")
    # Desktop table + mobile card stack both render the name cell: name then its tags.
    assert re.search(r"Hybrid-Ctl\s*<[^>]*>\s*<span[^>]*data-meta-tag=\"VMC\"", r.text)
    assert _tag(r.text, "VMC") is not None and _tag(r.text, "DSC") is not None


@pytest.mark.asyncio
async def test_list_derived_correcting_row_counts_toward_the_dsc_tag(
    authed_admin: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    client, org_id = authed_admin
    await _seed_control(
        db_session,
        org_id,
        "Corrector",
        [FairCamSubFunction.VMC_CORR_IMPLEMENTATION],
        derived_corr_from=FairCamSubFunction.VMC_CORR_IMPLEMENTATION,
    )
    r = await client.get("/controls")
    assert _tag(r.text, "VMC") is not None
    assert _tag(r.text, "DSC") is not None


@pytest.mark.asyncio
async def test_list_tags_are_not_muted_and_name_stays_escaped(
    authed_admin: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    """Muting is a DETAIL-page feature (spec §3.6); and the name cell is now composed
    markup, so a hostile name must still be escaped."""
    client, org_id = authed_admin
    await _seed_control(
        db_session,
        org_id,
        "<img src=x onerror=alert(1)>",
        [FairCamSubFunction.VMC_CORR_TREATMENT_SELECTION],
    )
    r = await client.get("/controls")
    assert r.status_code == 200
    assert "<img src=x" not in r.text
    assert "&lt;img src=x onerror=alert(1)&gt;" in r.text
    vmc = _tag(r.text, "VMC")
    assert vmc is not None
    assert "badge-warning" not in vmc
    assert TS_NOTE not in r.text


# ---------------------------------------------------------------------------
# Control detail header
# ---------------------------------------------------------------------------


async def _detail(client: AsyncClient, ctrl: Control) -> str:
    r = await client.get(f"/controls/{ctrl.id}")
    assert r.status_code == 200, r.text[:300]
    return r.text


@pytest.mark.asyncio
async def test_detail_header_tag_unmuted_when_the_control_credits(
    authed_admin: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    client, org_id = authed_admin
    ctrl = await _seed_control(
        db_session,
        org_id,
        "Implementer",
        [
            FairCamSubFunction.VMC_CORR_IMPLEMENTATION,
            FairCamSubFunction.DSC_PREV_DEFINED_EXPECTATIONS,
        ],
    )
    body = await _detail(client, ctrl)
    vmc, dsc = _tag(body, "VMC"), _tag(body, "DSC")
    assert vmc is not None and dsc is not None
    assert f'title="{VMC_TITLE}"' in vmc and "badge-ghost" in vmc
    assert f'title="{DSC_TITLE}"' in dsc and "badge-ghost" in dsc
    assert "badge-warning" not in vmc and "badge-warning" not in dsc
    # Inside the page header, beside the name (above the breadcrumb-less content body).
    assert body.index('data-meta-tag="VMC"') < body.index("FAIR-CAM function assignments")


@pytest.mark.asyncio
async def test_detail_treatment_selection_only_vmc_tag_is_muted(
    authed_admin: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    client, org_id = authed_admin
    ctrl = await _seed_control(
        db_session, org_id, "TS-only", [FairCamSubFunction.VMC_CORR_TREATMENT_SELECTION]
    )
    vmc = _tag(await _detail(client, ctrl), "VMC")
    assert vmc is not None
    assert "badge-warning" in vmc and "badge-ghost" not in vmc
    assert f'title="{TS_NOTE}"' in vmc


@pytest.mark.asyncio
async def test_detail_dsc_id_misaligned_only_tag_is_muted(
    authed_admin: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    client, org_id = authed_admin
    ctrl = await _seed_control(
        db_session, org_id, "Identifier", [FairCamSubFunction.DSC_ID_MISALIGNED]
    )
    body = await _detail(client, ctrl)
    dsc = _tag(body, "DSC")
    assert dsc is not None
    assert "badge-warning" in dsc and f'title="{DSC_ID_NOTE}"' in dsc
    assert _tag(body, "VMC") is None


@pytest.mark.asyncio
async def test_detail_derived_correcting_row_only_dsc_tag_is_muted(
    authed_admin: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    client, org_id = authed_admin
    ctrl = await _seed_control(
        db_session,
        org_id,
        "Corrector",
        [FairCamSubFunction.VMC_CORR_IMPLEMENTATION],
        derived_corr_from=FairCamSubFunction.VMC_CORR_IMPLEMENTATION,
    )
    body = await _detail(client, ctrl)
    dsc = _tag(body, "DSC")
    assert dsc is not None
    assert "badge-warning" in dsc and f'title="{DSC_CORR_NOTE}"' in dsc
    # The VMC tag of the same control is NOT muted (Implementation is scored).
    vmc = _tag(body, "VMC")
    assert vmc is not None and "badge-ghost" in vmc


@pytest.mark.asyncio
async def test_detail_dsc_pair_together_is_not_muted(
    authed_admin: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    client, org_id = authed_admin
    ctrl = await _seed_control(
        db_session,
        org_id,
        "Pair",
        [FairCamSubFunction.VMC_CORR_IMPLEMENTATION, FairCamSubFunction.DSC_ID_MISALIGNED],
        derived_corr_from=FairCamSubFunction.VMC_CORR_IMPLEMENTATION,
    )
    dsc = _tag(await _detail(client, ctrl), "DSC")
    assert dsc is not None
    assert "badge-ghost" in dsc and "badge-warning" not in dsc


@pytest.mark.asyncio
async def test_detail_without_vmc_or_dsc_has_no_tags(
    authed_admin: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    client, org_id = authed_admin
    ctrl = await _seed_control(
        db_session, org_id, "Plain", [FairCamSubFunction.LEC_PREV_RESISTANCE]
    )
    assert 'data-meta-tag="' not in await _detail(client, ctrl)


@pytest.mark.asyncio
async def test_detail_viewer_sees_tags_too(
    viewer_client: AsyncClient, db_session: AsyncSession, authed_viewer: Any
) -> None:
    _client, org_id = authed_viewer
    ctrl = await _seed_control(
        db_session, org_id, "ViewerSees", [FairCamSubFunction.VMC_ID_THREAT_INTELLIGENCE]
    )
    assert _tag(await _detail(viewer_client, ctrl), "VMC") is not None


# ---------------------------------------------------------------------------
# Control library entry card
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def seed_meta_library_entries(db_session: AsyncSession) -> None:
    """Three published entries: VMC+DSC hybrid, plain LEC, DSC-only."""
    from idraa.models.control_library import ControlLibraryEntry, ControlLibraryEntryAssignment

    def _entry(slug: str, name: str) -> ControlLibraryEntry:
        return ControlLibraryEntry(
            id=uuid.uuid4(),
            version=1,
            slug=slug,
            name=name,
            description="Fixture entry.",
            control_type=ControlType.TECHNICAL,
            reference_annual_cost=1000,
            nist_csf_subcategories=[],
            cis_safeguards=[],
            iso_27001_controls=[],
            compliance_mappings={},
            applicable_industries=[],
            applicable_org_sizes=[],
            tags=[],
            source_citations=[],
            status="published",
        )

    spec = {
        "meta-hybrid": (
            "Hybrid Meta Entry",
            [FairCamSubFunction.VMC_ID_CONTROL_MONITORING, FairCamSubFunction.DSC_ID_MISALIGNED],
        ),
        "plain-lec": ("Plain Loss Event Entry", [FairCamSubFunction.LEC_PREV_RESISTANCE]),
        "dsc-only": ("Decision Support Entry", [FairCamSubFunction.DSC_PREV_DEFINED_EXPECTATIONS]),
    }
    for slug, (name, sfs) in spec.items():
        entry = _entry(slug, name)
        db_session.add(entry)
        for sf in sfs:
            db_session.add(
                ControlLibraryEntryAssignment(
                    library_entry_id=entry.id,
                    library_entry_version=1,
                    sub_function=sf,
                    capability_default=0.7,
                    coverage_default=0.8,
                    reliability_default=0.9,
                )
            )
    await db_session.commit()


def _card(body: str, name: str) -> str:
    """The HTML of the one ``<div class="card ...">`` block that names ``name``."""
    parts = body.split('<div class="card bg-surface-1 shadow">')
    hits = [p for p in parts if name in p]
    assert len(hits) == 1, f"expected one card for {name!r}, got {len(hits)}"
    return hits[0]


@pytest.mark.asyncio
async def test_library_card_tags_follow_the_entry_assignments(
    viewer_client: AsyncClient, seed_meta_library_entries: None
) -> None:
    r = await viewer_client.get("/controls/library/_partials/cards")
    assert r.status_code == 200, r.text[:300]
    hybrid = _card(r.text, "Hybrid Meta Entry")
    assert _tag(hybrid, "VMC") is not None and _tag(hybrid, "DSC") is not None
    plain = _card(r.text, "Plain Loss Event Entry")
    assert 'data-meta-tag="' not in plain
    dsc_only = _card(r.text, "Decision Support Entry")
    assert _tag(dsc_only, "DSC") is not None and _tag(dsc_only, "VMC") is None
    # Library cards never mute (no run, no per-control view: spec §3.6, detail page only).
    assert "badge-warning" not in (_tag(hybrid, "DSC") or "")
    # The tag sits in the card title, beside the entry name.
    assert hybrid.index("Hybrid Meta Entry") < hybrid.index('data-meta-tag="VMC"')
    assert hybrid.index('data-meta-tag="VMC"') < hybrid.index("badges flex")
