import uuid

import pytest

from idraa.models.control_library import ControlLibraryEntry, ControlLibraryEntryAssignment
from idraa.models.enums import ControlType, FairCamSubFunction
from idraa.services.control_library import ControlLibraryBrowseFilters, ControlLibraryService


async def _entry(
    db, slug, *, status="published", control_type=ControlType.TECHNICAL, nist=None, funcs=()
):
    e = ControlLibraryEntry(
        version=1,
        slug=slug,
        name=slug.upper(),
        description="a" * 25,
        control_type=control_type,
        nist_csf_subcategories=nist or [],
        cis_safeguards=[],
        iso_27001_controls=[],
        compliance_mappings={},
        applicable_industries=[],
        applicable_org_sizes=[],
        tags=[],
        source_citations=[],
        status=status,
    )
    db.add(e)
    await db.flush()
    for fn in funcs:
        db.add(
            ControlLibraryEntryAssignment(
                library_entry_id=e.id,
                library_entry_version=1,
                sub_function=fn,
                capability_default=0.7,
                coverage_default=0.8,
                reliability_default=0.8,
            )
        )
    await db.flush()
    return e


@pytest.mark.asyncio
async def test_lists_only_published(db_session):
    await _entry(db_session, "pub", status="published")
    await _entry(db_session, "draft", status="draft")
    page = await ControlLibraryService(db_session).list_browseable(
        filters=ControlLibraryBrowseFilters(), page=1, page_size=50
    )
    assert {e.slug for e in page.entries} == {"pub"}
    assert page.total == 1


@pytest.mark.asyncio
async def test_filter_by_fair_cam_function(db_session):
    await _entry(db_session, "mfa", funcs=[FairCamSubFunction.LEC_PREV_RESISTANCE])
    await _entry(db_session, "edr", funcs=[FairCamSubFunction.LEC_DET_VISIBILITY])
    page = await ControlLibraryService(db_session).list_browseable(
        filters=ControlLibraryBrowseFilters(sub_functions=[FairCamSubFunction.LEC_PREV_RESISTANCE]),
        page=1,
        page_size=50,
    )
    assert {e.slug for e in page.entries} == {"mfa"}


@pytest.mark.asyncio
async def test_filter_by_control_type_and_search(db_session):
    await _entry(db_session, "mfa", control_type=ControlType.TECHNICAL)
    await _entry(db_session, "aup", control_type=ControlType.ADMINISTRATIVE)
    svc = ControlLibraryService(db_session)
    page = await svc.list_browseable(
        filters=ControlLibraryBrowseFilters(control_types=[ControlType.ADMINISTRATIVE]),
        page=1,
        page_size=50,
    )
    assert {e.slug for e in page.entries} == {"aup"}
    page2 = await svc.list_browseable(
        filters=ControlLibraryBrowseFilters(search_text="MFA"), page=1, page_size=50
    )
    assert {e.slug for e in page2.entries} == {"mfa"}


@pytest.mark.asyncio
async def test_filter_by_nist_cis_and_industry(db_session):
    svc = ControlLibraryService(db_session)
    e1 = await _entry(db_session, "mfa", nist=["PR.AC-7"])
    e1.cis_safeguards = ["6.3"]
    e1.applicable_industries = ["healthcare"]
    e2 = await _entry(db_session, "edr", nist=["DE.CM-1"])
    e2.applicable_industries = ["finance"]
    await db_session.flush()
    by_nist = await svc.list_browseable(
        filters=ControlLibraryBrowseFilters(nist_csf_subcategories=["PR.AC-7"]),
        page=1,
        page_size=50,
    )
    assert {e.slug for e in by_nist.entries} == {"mfa"}
    by_cis = await svc.list_browseable(
        filters=ControlLibraryBrowseFilters(cis_safeguards=["6.3"]), page=1, page_size=50
    )
    assert {e.slug for e in by_cis.entries} == {"mfa"}
    by_ind = await svc.list_browseable(
        filters=ControlLibraryBrowseFilters(industries=["finance"]), page=1, page_size=50
    )
    assert {e.slug for e in by_ind.entries} == {"edr"}


@pytest.mark.asyncio
async def test_browse_surfaces_only_latest_published_version(db_session):
    eid = uuid.uuid4()
    # v1 claims RESISTANCE; v2 (latest published) drops it and claims VISIBILITY instead.
    for v, fn in [
        (1, FairCamSubFunction.LEC_PREV_RESISTANCE),
        (2, FairCamSubFunction.LEC_DET_VISIBILITY),
    ]:
        e = ControlLibraryEntry(
            id=eid,
            version=v,
            slug="fw",
            name="Firewall",
            description="a" * 25,
            control_type=ControlType.TECHNICAL,
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
        db_session.add(e)
        await db_session.flush()
        db_session.add(
            ControlLibraryEntryAssignment(
                library_entry_id=eid,
                library_entry_version=v,
                sub_function=fn,
                capability_default=0.7,
                coverage_default=0.8,
                reliability_default=0.8,
            )
        )
    await db_session.flush()
    svc = ControlLibraryService(db_session)
    # browse surfaces exactly ONE row (latest = v2), not two:
    allpage = await svc.list_browseable(filters=ControlLibraryBrowseFilters(), page=1, page_size=50)
    fw = [e for e in allpage.entries if e.slug == "fw"]
    assert len(fw) == 1 and fw[0].version == 2
    # function filter reflects the SURFACED version's assignments:
    # RESISTANCE (v1-only) → no match; VISIBILITY (v2) → match
    res = await svc.list_browseable(
        filters=ControlLibraryBrowseFilters(sub_functions=[FairCamSubFunction.LEC_PREV_RESISTANCE]),
        page=1,
        page_size=50,
    )
    assert not [e for e in res.entries if e.slug == "fw"]
    vis = await svc.list_browseable(
        filters=ControlLibraryBrowseFilters(sub_functions=[FairCamSubFunction.LEC_DET_VISIBILITY]),
        page=1,
        page_size=50,
    )
    assert [e for e in vis.entries if e.slug == "fw"]
