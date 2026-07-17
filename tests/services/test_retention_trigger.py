"""Trigger tests for the opportunistic, throttled retention sweep (#297).

``maybe_sweep_opportunistic`` opens its OWN session via the module-level
``get_session()`` (it fires from a FastAPI BackgroundTask AFTER the request
session is closed). The request-scoped ``db_session`` fixture builds a SEPARATE
engine on the same SQLite file, so seeding through it would not be visible to
``get_session()`` unless the singleton engine is wired to the same file.

Reconciliation: this module wires the module-level engine/settings singletons to
the per-test ``db_url`` (same path ``client`` uses), creates the schema on that
singleton engine, and seeds the org + system_state row THROUGH ``get_session()``
itself — the exact same DB the trigger opens. No reliance on ``db_session``.
"""

from __future__ import annotations

import asyncio
import datetime
import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

from idraa import config
from idraa import db as db_module
from idraa.config import Settings
from idraa.db import Base, get_engine, get_session
from idraa.models._types import now_utc
from idraa.models.system_state import SystemState
from idraa.services.retention import maybe_sweep_opportunistic
from tests.factories import create_org


@pytest_asyncio.fixture
async def wired_engine(db_url: str, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[None]:
    """Point the module-level singletons at the per-test SQLite file + create schema.

    Mirrors the ``client`` fixture's singleton management so that
    ``get_session()`` (used by the trigger) resolves to this DB.
    """
    monkeypatch.setenv("DATABASE_URL", db_url)
    config.reset_for_tests()
    db_module.reset_for_tests()
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield
    finally:
        await engine.dispose()
        db_module.reset_for_tests()
        config.reset_for_tests()


@pytest_asyncio.fixture
async def seeded_org_id(wired_engine: None) -> uuid.UUID:
    """Create an org via ``get_session()`` (same DB the trigger opens)."""
    async with get_session() as db:
        org = await create_org(db)
        return org.id


@pytest.mark.asyncio
async def test_parallel_calls_fire_sweep_at_most_once(
    seeded_org_id: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[int] = []

    async def _fake(db, settings):
        calls.append(1)
        return {"purged": 0, "deleted": 0}

    monkeypatch.setattr("idraa.services.retention.sweep_retention", _fake)
    # Pre-seed the throttle row STALE so the very first UPDATE can win — but the
    # self-seed path is also covered by the no-row test below.
    async with get_session() as db:
        db.add(
            SystemState(
                organization_id=seeded_org_id,
                last_retention_sweep_at=now_utc() - datetime.timedelta(hours=24),
            )
        )

    s = Settings(retention_sample_purge_days=90, retention_sweep_interval_hours=6)
    await asyncio.gather(
        maybe_sweep_opportunistic(s, org_id=seeded_org_id),
        maybe_sweep_opportunistic(s, org_id=seeded_org_id),
    )
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_fires_on_first_call_with_no_seeded_row(
    seeded_org_id: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[int] = []

    async def _fake(db, settings):
        calls.append(1)
        return {"purged": 0, "deleted": 0}

    monkeypatch.setattr("idraa.services.retention.sweep_retention", _fake)
    s = Settings(retention_sample_purge_days=90)
    # No system_state row exists for the org — the self-seed must create it and
    # the conditional UPDATE (last_retention_sweep_at IS NULL) must then win.
    await maybe_sweep_opportunistic(s, org_id=seeded_org_id)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_no_op_when_both_phases_disabled(
    seeded_org_id: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[int] = []

    async def _fake(db, settings):
        calls.append(1)
        return {"purged": 0, "deleted": 0}

    monkeypatch.setattr("idraa.services.retention.sweep_retention", _fake)
    s = Settings(retention_sample_purge_days=0, retention_run_delete_days=0)
    await maybe_sweep_opportunistic(s, org_id=seeded_org_id)
    assert calls == []


@pytest.mark.asyncio
async def test_second_call_within_interval_does_not_fire(
    seeded_org_id: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[int] = []

    async def _fake(db, settings):
        calls.append(1)
        return {"purged": 0, "deleted": 0}

    monkeypatch.setattr("idraa.services.retention.sweep_retention", _fake)
    s = Settings(retention_sample_purge_days=90, retention_sweep_interval_hours=6)
    await maybe_sweep_opportunistic(s, org_id=seeded_org_id)
    await maybe_sweep_opportunistic(s, org_id=seeded_org_id)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_sweep_exception_is_swallowed_not_propagated(
    seeded_org_id: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The body is self-guarding: a sweep failure logs but must NOT propagate,
    so unguarded FastAPI BackgroundTask callers don't raise a Starlette
    traceback after the response is sent."""

    async def _boom(db, settings):
        raise RuntimeError("transient sweep failure")

    monkeypatch.setattr("idraa.services.retention.sweep_retention", _boom)
    s = Settings(retention_sample_purge_days=90)
    # Must return None without raising despite the underlying sweep raising.
    assert await maybe_sweep_opportunistic(s, org_id=seeded_org_id) is None
