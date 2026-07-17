"""Synth UUIDs in sidecar `sme_ids` are NOT FK-resolvable against subject_matter_experts.

Pinned per the 2026-05-25 SME free-text design: free-text estimate rows
get a deterministic synth UUID derived from the casefolded name via
``uuid5(NAMESPACE_DNS, "freetext:" + name.casefold())``. The synth UUID
lets `sme_ids: list[str]` keep its UUID-shaped type without a downstream
schema change. But a maintainer who tries to JOIN sme_ids back against
subject_matter_experts will silently get empty results — this test makes
that loud.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.sme import SubjectMatterExpert
from idraa.services.wizard_finalize import row_identity_uuid


def test_freetext_synth_uuid_format_is_uuid5() -> None:
    """Helper output for free-text rows is a UUID5 of the expected derivation."""
    from uuid import NAMESPACE_DNS, uuid5

    row = {"sme_id": None, "sme_name": "Alice Chen"}
    expected = uuid5(NAMESPACE_DNS, "freetext:alice chen")
    assert row_identity_uuid(row) == expected


@pytest.mark.asyncio
async def test_freetext_synth_uuid_does_not_join_to_subject_matter_experts(
    db_session: AsyncSession,
    seed_organization,
) -> None:
    """A synth UUID derived from a free-text name never matches a real SME row.

    Defensive: even if a future SME were inserted with an id that *happened* to
    collide with a uuid5 derivation, the test would still catch the regression
    via the "freetext:" namespace prefix being unique.
    """
    row = {"sme_id": None, "sme_name": "Definitely Not A Real SME"}
    synth_id = row_identity_uuid(row)
    result = await db_session.execute(
        select(SubjectMatterExpert).where(SubjectMatterExpert.id == synth_id)
    )
    assert result.first() is None, (
        f"Synth UUID {synth_id} resolved to a real SubjectMatterExpert row — "
        "either uuid5 derivation collided with a real id (vanishingly improbable) "
        "or someone is using freetext synth UUIDs as real FKs. Both are bugs."
    )
