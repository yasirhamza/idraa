"""Adapter iteration contract (data-contract policy): adopt must preserve ALL N
assignments (N>=3), catching a future [0]/first optimization."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from idraa.models.control_library import (
    ControlLibraryEntry,
    ControlLibraryEntryAssignment,
)
from idraa.models.enums import ControlType
from idraa.models.enums import FairCamSubFunction as F
from idraa.services.controls import adopt_from_library


@pytest.mark.asyncio
async def test_adopt_preserves_all_three_assignments(
    db_session: Any,
    seed_org_user: Callable[..., Awaitable[Any]],
) -> None:
    org, user = await seed_org_user(db_session)
    e = ControlLibraryEntry(
        version=1,
        slug="seg",
        name="Network Segmentation",
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
    funcs = [
        F.LEC_PREV_RESISTANCE,
        F.LEC_DET_VISIBILITY,
        F.LEC_RESP_LOSS_REDUCTION,
    ]
    for fn in funcs:
        db_session.add(
            ControlLibraryEntryAssignment(
                library_entry_id=e.id,
                library_entry_version=1,
                sub_function=fn,
                capability_default=0.7,
                coverage_default=0.8,
                reliability_default=0.8,
            )
        )
    await db_session.flush()
    control = await adopt_from_library(
        db_session, org_id=org.id, user_id=user.id, entry_id=e.id, version=None
    )
    await db_session.commit()
    await db_session.refresh(control, attribute_names=["assignments"])
    assert {a.sub_function for a in control.assignments} == set(funcs)  # all 3 preserved
