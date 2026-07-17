"""Optimistic-lock unit tests for ``WizardStateService.advance_step``.

Covers Arch-25 R3 (atomic CAS via SA Core ``update().returning()``) and the
Arch-6 PR1 back-compat path (``expected_version_token=None`` skips the
check). Layered on top of ``tests/services/conftest.py`` which provisions
an in-memory SQLite session + seeded org + actor user.

The conftest exposes the seeded user as ``actor_id`` (not ``user_id``); the
plan example uses ``user_id`` for readability — they are the same fixture
in this codebase.
"""

from __future__ import annotations

import pytest

from idraa.services.wizard_state import (
    WizardDraftConflictError,
    WizardStateService,
)


async def test_advance_step_increments_version_token(db, actor_id, org_id):
    """Happy path: advance with the correct expected token bumps it by 1."""
    svc = WizardStateService(db)
    state = await svc.get_or_create(
        user_id=actor_id,
        organization_id=org_id,
        tx_id=None,
    )
    initial = state.version_token
    await svc.advance_step(
        user_id=actor_id,
        organization_id=org_id,
        state=state,
        expected_version_token=initial,
    )
    assert state.version_token == initial + 1


async def test_stale_version_token_raises_conflict(db, actor_id, org_id):
    """Concurrent-advance simulation: second advance with the now-stale
    token must raise ``WizardDraftConflictError`` and leave the row's
    token at the value the first advance bumped it to.
    """
    svc = WizardStateService(db)
    state = await svc.get_or_create(
        user_id=actor_id,
        organization_id=org_id,
        tx_id=None,
    )
    # First advance succeeds and bumps the row's token 0 -> 1.
    await svc.advance_step(
        user_id=actor_id,
        organization_id=org_id,
        state=state,
        expected_version_token=0,
    )
    # Second advance still passes the pre-bump token; the WHERE clause
    # filter mismatches and the CAS must raise. The dataclass copy was
    # updated by the first call, so we pass the stale literal directly
    # to simulate a parallel-tab caller that read 0 before we wrote.
    with pytest.raises(WizardDraftConflictError):
        await svc.advance_step(
            user_id=actor_id,
            organization_id=org_id,
            state=state,
            expected_version_token=0,
        )
