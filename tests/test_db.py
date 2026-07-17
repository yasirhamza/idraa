"""Tests for the SQLAlchemy base and session factory."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

import idraa.db as db
from idraa.db import Base, get_engine, get_session


def test_base_metadata_exists() -> None:
    """Base is the declarative base for all ORM models.

    Post-Task-1.1.2 the metadata carries the first round of domain tables
    (organizations, users, auth_sessions, audit_log). The assertion isn't
    the exact set — that would churn on every schema change — but that
    the core auth/audit tables are registered. If a later task renames
    one of these, update this list.
    """
    assert Base.metadata is not None
    assert {"organizations", "users", "auth_sessions", "audit_log"}.issubset(
        set(Base.metadata.tables)
    )


async def test_session_roundtrip() -> None:
    """Can open an async session, execute a trivial SELECT, and close cleanly."""
    from sqlalchemy import text

    # This test binds the global db engine/sessionmaker singletons directly
    # (without the `client` fixture). It MUST reset them afterward, or the
    # next test to call get_engine()/get_session() before the `client`
    # fixture rebinds (e.g. a setup_guard DB roundtrip on the first authed
    # HTTP request collected after this file) inherits this stale binding and
    # queries the wrong DB. Restore the "fresh singletons for the next test"
    # contract the client fixture relies on.
    try:
        engine = get_engine()
        async with get_session() as session:
            assert isinstance(session, AsyncSession)
            result = await session.execute(text("SELECT 1"))
            assert result.scalar() == 1
        await engine.dispose()
    finally:
        db.reset_for_tests()
