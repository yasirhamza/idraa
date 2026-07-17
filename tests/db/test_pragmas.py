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

    NORMAL skips the per-commit WAL fsync — an unplanned shutdown (Fly host
    crash, kernel panic) can silently lose the most recent committed
    transactions. FULL fsyncs the WAL on every commit; at this app's write
    throughput (form saves + run completions, single team) the per-commit
    fsync cost is immaterial. PRAGMA synchronous: 2 == FULL.
    """
    sync = (await db_session.execute(text("PRAGMA synchronous"))).scalar()
    assert int(sync) == 2  # FULL
