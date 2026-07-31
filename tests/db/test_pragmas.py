import pytest
from sqlalchemy import text


@pytest.mark.asyncio
async def test_foreign_keys_on_and_wal(db_session):
    fk = (await db_session.execute(text("PRAGMA foreign_keys"))).scalar()
    jm = (await db_session.execute(text("PRAGMA journal_mode"))).scalar()
    bt = (await db_session.execute(text("PRAGMA busy_timeout"))).scalar()
    assert int(fk) == 1
    assert str(jm).lower() == "wal"
    assert int(bt) >= 1


@pytest.mark.asyncio
async def test_synchronous_full_durability(db_session):
    """Durability decision (whole-project eval): WAL + synchronous=FULL.

    NORMAL skips the per-commit WAL fsync — an unplanned shutdown (host
    crash, kernel panic) can silently lose the most recent committed
    transactions. FULL fsyncs the WAL on every commit; at this app's write
    throughput (form saves + run completions, single team) the per-commit
    fsync cost is immaterial. PRAGMA synchronous: 2 == FULL.
    """
    sync = (await db_session.execute(text("PRAGMA synchronous"))).scalar()
    assert int(sync) == 2  # FULL


@pytest.mark.asyncio
async def test_busy_timeout_follows_settings(db_session):
    """idraa#72 (fix 3): busy_timeout is settings-driven, default 30s.

    The 5s hardcoded timeout meant any writer hold >5s (retention VACUUM,
    long seed transactions) turned concurrent writes into instant
    "database is locked" 500s. 30s rides out realistic holds; the value is
    an env knob (SQLITE_BUSY_TIMEOUT_MS) per the no-hardcoded-deploy-values
    convention.
    """
    from idraa.config import get_settings

    bt = (await db_session.execute(text("PRAGMA busy_timeout"))).scalar()
    assert int(bt) == get_settings().sqlite_busy_timeout_ms
    assert get_settings().sqlite_busy_timeout_ms == 30_000  # raised default
