"""Arch-19 R2: probe SQLAlchemy `sqlite_where=` BEFORE committing to
partial-unique semantics. If this probe fails, the SME schema must fall
back to application-level uniqueness checks."""

import datetime

import pytest
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError


def test_sqlite_partial_unique_index_works():
    engine = sa.create_engine("sqlite:///:memory:")
    metadata = sa.MetaData()
    t = sa.Table(
        "t",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("email", sa.String),
        sa.Column("archived_at", sa.DateTime, nullable=True),
        sa.Index(
            "ux_t_email_live",
            "email",
            unique=True,
            sqlite_where=text("email IS NOT NULL AND archived_at IS NULL"),
        ),
    )
    metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(t.insert(), {"email": "a@x.com", "archived_at": None})
        # SQLAlchemy's SQLite DateTime adapter rejects strings — pass a real
        # datetime so the partial-index semantics (not type coercion) are
        # what's actually under test.
        conn.execute(
            t.insert(),
            {"email": "a@x.com", "archived_at": datetime.datetime(2024, 1, 1)},
        )
        with pytest.raises(IntegrityError):
            conn.execute(t.insert(), {"email": "a@x.com", "archived_at": None})
