"""SQLAlchemy TypeDecorator for timezone-aware UTC datetimes.

Closes GH #7. Used everywhere a model column stores a timestamp that
must round-trip identically across SQLite (dev) and Postgres (prod).
"""

from __future__ import annotations

import datetime

from sqlalchemy import DateTime
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator


def now_utc() -> datetime.datetime:
    """Return current time as timezone-aware UTC.

    Python's datetime.now() is already microsecond-precise (no nanosecond
    drift); no rounding needed. Centralised so audit log timestamps are
    consistent across all callers.
    """
    return datetime.datetime.now(datetime.UTC)


class UtcDateTime(TypeDecorator[datetime.datetime]):
    """DateTime column that:
    - rejects naive datetimes (raises ValueError),
    - normalises non-UTC aware datetimes to UTC on store,
    - returns timezone-aware UTC datetimes on read.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(
        self, value: datetime.datetime | None, dialect: Dialect
    ) -> datetime.datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError(
                "UtcDateTime received a naive datetime; pass a "
                "timezone-aware UTC datetime (use idraa.models._types.now_utc)"
            )
        if value.tzinfo != datetime.UTC:
            value = value.astimezone(datetime.UTC)
        return value

    def process_result_value(
        self, value: datetime.datetime | None, dialect: Dialect
    ) -> datetime.datetime | None:
        if value is None:
            return None
        # SQLite returns naive; Postgres returns aware. Normalise.
        if value.tzinfo is None:
            return value.replace(tzinfo=datetime.UTC)
        return value.astimezone(datetime.UTC)
