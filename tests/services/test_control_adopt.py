"""Adopt (clone-snapshot) service tests (P2b Task 8).

A published ControlLibraryEntry clones into a new editable org Control
(source=LIBRARY_DERIVED, library_pin set, assignments copied UNCONFIRMED).
D1 tag mapping: nist_csf_subcategories -> nist_csf_functions (named column),
iso_27001_controls -> iso_27001_domains (named column), cis_safeguards stashed
into compliance_mappings (no named column) WITHOUT re-stashing nist/iso.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from idraa.errors import LibraryEntryNotFoundError
from idraa.models.control_library import (
    ControlLibraryEntry,
    ControlLibraryEntryAssignment,
)
from idraa.models.enums import ControlSource, ControlType
from idraa.models.enums import FairCamSubFunction as F
from idraa.services.controls import adopt_from_library


async def _published_entry(
    db: Any, *, slug: str = "mfa", n_assignments: int = 3
) -> ControlLibraryEntry:
    e = ControlLibraryEntry(
        version=1,
        slug=slug,
        name="Multi-Factor Authentication",
        description="a" * 25,
        control_type=ControlType.TECHNICAL,
        reference_annual_cost=30000,
        nist_csf_subcategories=["PR.AC-7"],
        cis_safeguards=["6.3"],
        iso_27001_controls=["A.9.4.2"],
        compliance_mappings={"csa_ccm_v4": ["IAM-01"]},
        applicable_industries=[],
        applicable_org_sizes=[],
        tags=[],
        source_citations=[],
        status="published",
    )
    db.add(e)
    await db.flush()
    funcs = [
        F.LEC_PREV_RESISTANCE,
        F.LEC_DET_VISIBILITY,
        F.VMC_ID_CONTROL_MONITORING,
    ][:n_assignments]
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
async def test_adopt_clones_into_library_derived_control(
    db_session: Any,
    seed_org_user: Callable[..., Awaitable[Any]],
) -> None:
    org, user = await seed_org_user(db_session)
    entry = await _published_entry(db_session)
    control = await adopt_from_library(
        db_session, org_id=org.id, user_id=user.id, entry_id=entry.id, version=None
    )
    await db_session.commit()
    # Refresh the full row (commit expired it) so annual_cost reflects the
    # Numeric(18,2) round-trip and the assignments collection is populated.
    await db_session.refresh(control)
    await db_session.refresh(control, attribute_names=["assignments"])
    assert control.source == ControlSource.LIBRARY_DERIVED
    assert control.library_pin == {"entry_id": str(entry.id), "version": 1}
    assert control.organization_id == org.id
    assert control.name == "Multi-Factor Authentication"
    assert str(control.annual_cost) == "30000.00"
    # D1 tag mapping: named columns carry nist/iso, compliance_mappings carries cis.
    assert control.nist_csf_functions == ["PR.AC-7"]
    assert control.iso_27001_domains == ["A.9.4.2"]
    assert control.compliance_mappings.get("cis_safeguards") == ["6.3"]
    # entry's own compliance_mappings preserved.
    assert control.compliance_mappings.get("csa_ccm_v4") == ["IAM-01"]
    # D1 deduped: nist/iso NOT re-stashed into compliance_mappings.
    assert "nist_csf_subcategories" not in control.compliance_mappings
    assert "iso_27001_controls" not in control.compliance_mappings
    # assignments cloned, all UNCONFIRMED:
    assert len(control.assignments) == 3
    assert all(a.confirmed_by_user_at is None for a in control.assignments)


@pytest.mark.asyncio
async def test_adopt_copies_effectiveness_values_verbatim(
    db_session: Any,
    seed_org_user: Callable[..., Awaitable[Any]],
) -> None:
    """Value-fidelity regression on the library->control adapter surface.

    A single assignment is seeded with DISTINCT per-field defaults so any
    field cross-swap (e.g. ``coverage=a.reliability_default``) or scaling bug
    on the clone path is detectable field-by-field.
    """
    org, user = await seed_org_user(db_session)
    e = ControlLibraryEntry(
        version=1,
        slug="mfa-vals",
        name="MFA",
        description="a" * 25,
        control_type=ControlType.TECHNICAL,
        nist_csf_subcategories=["PR.AC-7"],
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
            library_entry_id=e.id,
            library_entry_version=1,
            sub_function=F.LEC_PREV_RESISTANCE,
            capability_default=0.61,  # distinct per-field values: swap-detectable
            coverage_default=0.72,
            reliability_default=0.83,
        )
    )
    await db_session.flush()
    control = await adopt_from_library(
        db_session, org_id=org.id, user_id=user.id, entry_id=e.id, version=None
    )
    await db_session.commit()
    await db_session.refresh(control, attribute_names=["assignments"])
    assert len(control.assignments) == 1
    a = control.assignments[0]
    assert a.sub_function == F.LEC_PREV_RESISTANCE
    assert a.capability_value == 0.61  # not swapped with coverage/reliability
    assert a.coverage == 0.72
    assert a.reliability == 0.83
    assert a.confirmed_by_user_at is None


@pytest.mark.asyncio
async def test_adopt_copies_null_capability_verbatim(
    db_session: Any,
    seed_org_user: Callable[..., Awaitable[Any]],
) -> None:
    """A NULL ``capability_default`` (non-PROBABILITY unit sub_function) clones
    to a NULL ``capability_value`` rather than coalescing to a default."""
    org, user = await seed_org_user(db_session)
    e = ControlLibraryEntry(
        version=1,
        slug="mfa-null-cap",
        name="MTTI control",
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
            library_entry_id=e.id,
            library_entry_version=1,
            sub_function=F.LEC_DET_MONITORING,  # ELAPSED_TIME unit
            capability_default=None,
            coverage_default=0.55,
            reliability_default=0.66,
        )
    )
    await db_session.flush()
    control = await adopt_from_library(
        db_session, org_id=org.id, user_id=user.id, entry_id=e.id, version=None
    )
    await db_session.commit()
    await db_session.refresh(control, attribute_names=["assignments"])
    assert len(control.assignments) == 1
    a = control.assignments[0]
    assert a.sub_function == F.LEC_DET_MONITORING
    assert a.capability_value is None  # NULL preserved, not coalesced
    assert a.coverage == 0.55
    assert a.reliability == 0.66


@pytest.mark.asyncio
async def test_adopt_unpublished_raises(
    db_session: Any,
    seed_org_user: Callable[..., Awaitable[Any]],
) -> None:
    org, user = await seed_org_user(db_session)
    e = await _published_entry(db_session, slug="draft")
    e.status = "draft"
    await db_session.flush()
    with pytest.raises(LibraryEntryNotFoundError):
        await adopt_from_library(
            db_session, org_id=org.id, user_id=user.id, entry_id=e.id, version=None
        )
