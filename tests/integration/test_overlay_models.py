"""Verifies the overlay ORM models materialize the expected schema.

Drives ``Base.metadata.create_all`` (the path used by the ``db_session``
fixture in tests/conftest.py) and asserts the overlay tables and the
per-org uniqueness constraint exist. Inspection goes through ``run_sync``
because synchronous reflection IO from an async engine raises
``MissingGreenlet``.
"""

from __future__ import annotations

from sqlalchemy import Connection, inspect
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession


async def test_overlay_tables_exist(db_session: AsyncSession) -> None:
    """Both overlay_definitions and overlay_definition_revisions exist."""

    def _table_names(sync_conn: Connection) -> list[str]:
        return list(inspect(sync_conn).get_table_names())

    bind = db_session.bind
    assert isinstance(bind, AsyncEngine), "db_session fixture binds to an AsyncEngine"
    async with bind.connect() as conn:
        table_names = await conn.run_sync(_table_names)

    assert "overlay_definitions" in table_names
    assert "overlay_definition_revisions" in table_names


async def test_overlay_definitions_has_uniqueness_constraint(
    db_session: AsyncSession,
) -> None:
    def _unique_constraint_names(sync_conn: Connection) -> list[str]:
        # ``uc["name"]`` is typed ``str | None`` by SQLAlchemy's inspector;
        # filter Nones out so the return type matches ``list[str]``. Unique
        # constraints emitted by our models all carry an explicit name, so
        # the filter is defensive rather than load-bearing.
        return [
            uc["name"]
            for uc in inspect(sync_conn).get_unique_constraints("overlay_definitions")
            if uc["name"] is not None
        ]

    bind = db_session.bind
    assert isinstance(bind, AsyncEngine), "db_session fixture binds to an AsyncEngine"
    async with bind.connect() as conn:
        constraint_names = await conn.run_sync(_unique_constraint_names)

    assert "uq_overlay_per_org_tag" in constraint_names
