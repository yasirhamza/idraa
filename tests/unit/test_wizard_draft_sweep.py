"""Wizard-draft TTL sweep (drafts-surfaced spec §4)."""

from __future__ import annotations

import asyncio
import datetime
import uuid
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import idraa.services.run_reaper as run_reaper
from idraa.config import get_settings
from idraa.models._types import now_utc  # DA-10: the import run_reaper.py:66 uses
from idraa.models.wizard_draft import WizardDraft
from idraa.services.wizard_state import WizardStateService

pytestmark = pytest.mark.asyncio


async def _mk_draft(
    db: AsyncSession, user_id: uuid.UUID, org_id: uuid.UUID, age_days: int
) -> uuid.UUID:
    tx = uuid.uuid4()
    draft = WizardDraft(
        user_id=user_id,
        tx_id=tx,
        organization_id=org_id,
        state_json={"tx_id": str(tx), "current_step": 3},
    )
    db.add(draft)
    await db.flush()
    # backdate via direct UPDATE (onupdate would restamp)
    from sqlalchemy import update

    await db.execute(
        update(WizardDraft)
        .where(WizardDraft.tx_id == tx)
        .values(updated_at=now_utc() - datetime.timedelta(days=age_days))
    )
    await db.commit()
    return tx


async def test_ttl_setting_default() -> None:
    assert get_settings().wizard_draft_ttl_days == 30


async def test_cleanup_deletes_old_keeps_recent(
    seed_user, seed_organization, db_session: AsyncSession
) -> None:
    user = seed_user
    old_tx = await _mk_draft(db_session, user.id, user.organization_id, age_days=40)
    new_tx = await _mk_draft(db_session, user.id, user.organization_id, age_days=1)
    svc = WizardStateService(db_session)
    await svc.cleanup_expired(max_age_minutes=30 * 24 * 60)
    await db_session.commit()
    # NO count assertion (DQ-5): cleanup_expired's docstring warns SQLite may
    # report -1 — correctness is the row set, not the count.
    from sqlalchemy import select

    remaining = (await db_session.execute(select(WizardDraft.tx_id))).scalars().all()
    assert new_tx in remaining and old_tx not in remaining


async def test_ttl_zero_disables_sweep(monkeypatch: pytest.MonkeyPatch) -> None:
    """wizard_draft_ttl_days=0 must short-circuit BEFORE opening a session
    (run_reaper.py:206-209's `if ttl_days <= 0: return`, ahead of the
    deferred `from idraa.db import get_session`). Proven by monkeypatching
    ``idraa.db.get_session`` — the module attribute the deferred import
    resolves at call time — to a sentinel that fails the test if invoked."""
    import idraa.db as db_module

    def _fail_if_called(*_args: object, **_kwargs: object) -> Any:
        pytest.fail("sweep_wizard_drafts opened a session despite ttl_days=0")

    monkeypatch.setattr(db_module, "get_session", _fail_if_called)

    settings = get_settings().model_copy(update={"wizard_draft_ttl_days": 0})
    result = await run_reaper.sweep_wizard_drafts(settings)
    assert result is None


async def test_sweep_exception_does_not_kill_reaper_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A wizard-draft sweep failure must not kill periodic_reaper_loop
    (run_reaper.py:246-251's except-and-log clause around the sweep call) —
    the run-orphan reap must keep firing every interval regardless.

    Drives the loop with a tiny interval via a stub settings object (only
    ``run_reaper_interval_seconds`` is read by the loop itself; the
    monkeypatched ``reap_once``/``sweep_wizard_drafts`` never touch the real
    Settings shape), lets it complete >=2 iterations, then cancels and
    asserts clean cancellation."""

    class _StubSettings:
        run_reaper_interval_seconds = 0.01

    reap_once_calls = 0

    async def _counting_reap_once(settings: Any) -> int:
        nonlocal reap_once_calls
        reap_once_calls += 1
        return 0

    async def _raising_sweep(settings: Any) -> None:
        raise RuntimeError("boom — simulated wizard-draft sweep failure")

    monkeypatch.setattr(run_reaper, "reap_once", _counting_reap_once)
    monkeypatch.setattr(run_reaper, "sweep_wizard_drafts", _raising_sweep)

    task = asyncio.create_task(run_reaper.periodic_reaper_loop(_StubSettings()))  # type: ignore[arg-type]
    await asyncio.sleep(0.05)  # >= 2 intervals at 0.01s each
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert task.cancelled()
    assert reap_once_calls >= 2, "reap_once must keep running despite the sweep exception"
