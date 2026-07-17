import uuid

import pytest
from sqlalchemy import select, text

from idraa.models.control_library import ControlLibraryEntry, ControlLibraryEntryAssignment
from idraa.models.enums import ControlType, FairCamSubFunction


@pytest.mark.asyncio
async def test_entry_composite_pk_and_assignment_cascade(db_session):
    entry = ControlLibraryEntry(
        version=1,
        slug="mfa",
        name="Multi-Factor Authentication",
        description="MFA adds an authentication factor.",
        control_type=ControlType.TECHNICAL,
        reference_annual_cost=None,
        nist_csf_subcategories=["PR.AC-7"],
        cis_safeguards=[],
        iso_27001_controls=[],
        compliance_mappings={},
        applicable_industries=[],
        applicable_org_sizes=[],
        tags=[],
        source_citations=["FAIR Institute NIST CSF 1.1 mapping"],
        status="published",
    )
    db_session.add(entry)
    await db_session.flush()
    db_session.add(
        ControlLibraryEntryAssignment(
            library_entry_id=entry.id,
            library_entry_version=entry.version,
            sub_function=FairCamSubFunction.LEC_PREV_RESISTANCE,
            capability_default=0.7,
            coverage_default=0.8,
            reliability_default=0.8,
        )
    )
    await db_session.flush()
    # DB-level cascade on parent delete (composite FK):
    await db_session.execute(
        text("DELETE FROM control_library_entries WHERE id = :i AND version = :v"),
        {"i": entry.id.hex, "v": entry.version},
    )
    rows = (await db_session.execute(select(ControlLibraryEntryAssignment))).all()
    assert rows == []


@pytest.mark.asyncio
async def test_same_id_two_versions_coexist(db_session):
    eid = uuid.uuid4()
    for v in (1, 2):
        db_session.add(
            ControlLibraryEntry(
                id=eid,
                version=v,
                slug="edr",
                name="EDR",
                description="Endpoint detection.",
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
        )
    await db_session.flush()
    rows = (
        (await db_session.execute(select(ControlLibraryEntry).where(ControlLibraryEntry.id == eid)))
        .scalars()
        .all()
    )
    assert {r.version for r in rows} == {1, 2}
