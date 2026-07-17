"""UtcDateTime TypeDecorator: stores datetimes as timezone-aware UTC,
normalises on read."""

from __future__ import annotations

import datetime

import pytest
import sqlalchemy.exc
from sqlalchemy import Column, Integer, MetaData, Table, create_engine
from sqlalchemy.orm import Session

from idraa.models._types import UtcDateTime, now_utc


def _make_table(metadata: MetaData) -> Table:
    return Table(
        "_t",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("ts", UtcDateTime, nullable=True),
    )


def test_now_utc_returns_aware_utc():
    """now_utc() returns timezone-aware UTC datetime."""
    ts = now_utc()
    assert ts.tzinfo is datetime.UTC
    # Ordering invariant: two consecutive calls are monotonically non-decreasing
    later = now_utc()
    assert later >= ts


def test_utc_datetime_round_trip():
    """Aware UTC datetime round-trips equal across the SQLAlchemy boundary."""
    engine = create_engine("sqlite:///:memory:")
    metadata = MetaData()
    t = _make_table(metadata)
    metadata.create_all(engine)

    aware_utc = datetime.datetime(2026, 4, 27, 12, 30, 45, 123456, tzinfo=datetime.UTC)
    with Session(engine) as session:
        session.execute(t.insert().values(id=1, ts=aware_utc))
        session.commit()
        row = session.execute(t.select().where(t.c.id == 1)).first()

    assert row is not None
    assert row.ts == aware_utc
    assert row.ts.tzinfo is datetime.UTC


def test_utc_datetime_naive_input_rejected():
    """Storing a naive datetime is a programming error. We reject explicitly.

    SQLAlchemy wraps the ValueError in a StatementError; the original
    ValueError is accessible via __cause__ and its message contains "naive".
    """
    engine = create_engine("sqlite:///:memory:")
    metadata = MetaData()
    t = _make_table(metadata)
    metadata.create_all(engine)

    naive = datetime.datetime(2026, 4, 27, 12, 30, 45, 123456)
    with Session(engine) as session, pytest.raises(sqlalchemy.exc.StatementError) as exc_info:
        session.execute(t.insert().values(id=1, ts=naive))
        session.commit()
    cause = exc_info.value.__cause__
    assert isinstance(cause, ValueError)
    assert "naive" in str(cause)


def test_utc_datetime_non_utc_input_normalised():
    """Non-UTC tz-aware datetime is normalised to UTC on store, round-trips equal."""
    engine = create_engine("sqlite:///:memory:")
    metadata = MetaData()
    t = _make_table(metadata)
    metadata.create_all(engine)

    eastern = datetime.timezone(datetime.timedelta(hours=-5))
    aware_eastern = datetime.datetime(2026, 4, 27, 7, 30, 45, 123456, tzinfo=eastern)
    expected_utc = aware_eastern.astimezone(datetime.UTC)
    with Session(engine) as session:
        session.execute(t.insert().values(id=1, ts=aware_eastern))
        session.commit()
        row = session.execute(t.select().where(t.c.id == 1)).first()

    assert row is not None
    assert row.ts == expected_utc
    assert row.ts.tzinfo is datetime.UTC


def test_utc_datetime_null_round_trip():
    """None round-trips to None — null path through process_bind_param/process_result_value."""
    engine = create_engine("sqlite:///:memory:")
    metadata = MetaData()
    t = _make_table(metadata)
    metadata.create_all(engine)

    with Session(engine) as session:
        session.execute(t.insert().values(id=1, ts=None))
        session.commit()
        row = session.execute(t.select().where(t.c.id == 1)).first()

    assert row is not None
    assert row.ts is None
