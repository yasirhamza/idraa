"""Migration regression: control_library_entries ids must be no-hyphen hex.

Fourth recurrence of the raw-text-seed UUID-format foot-gun (the scenario
library hit it three times — see ``test_library_uuid_format_fix.py``). The
control-library seed ``d4f6a2b9c8e1_seed_control_library.py`` inserted
``str(uuid.uuid4())`` (36-char hyphenated) directly via raw ``sa.text()``,
bypassing the ``Uuid(as_uuid=True)`` column type. SQLAlchemy's ``Uuid`` on
SQLite binds/stores UUID parameters as 32-char no-hyphen hex, so every
id-based ORM query silently returned ``None``:

- ``list_browseable`` worked (filters by status/version, never binds an id),
  so the browse cards rendered.
- ``get_published(entry_id)`` — the ``adopt_from_library`` path — bound the
  id as 32-char no-hyphen vs the stored 36-char hyphenated row and matched
  0 rows, surfacing as ``404 {"detail":"Library entry not available"}`` when
  the user clicked "Add to My Controls".

The fix migration ``b3e9c1a47d52_fix_control_library_uuid_format.py`` runs
``REPLACE(id, '-', '')`` on the entry id, the assignment id, and the
assignment ``library_entry_id`` FK. After it, every id is 32-char no-hyphen
hex and ORM queries via ``Uuid`` find the rows.
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import Engine


def test_control_library_entry_ids_no_hyphen_through_head(
    alembic_config: Config,
    alembic_engine: Engine,
) -> None:
    """Run the full chain through head; assert every control-library id
    (entry id, assignment id, assignment FK) is 32-char no-hyphen hex."""
    command.upgrade(alembic_config, "head")

    with alembic_engine.connect() as conn:
        entry_ids = conn.execute(sa.text("SELECT id FROM control_library_entries")).scalars().all()
        assign_rows = conn.execute(
            sa.text("SELECT id, library_entry_id FROM control_library_entry_assignments")
        ).all()

    assert entry_ids, "seed migration left no control_library_entries — unexpected"
    bad_entries = [i for i in entry_ids if len(i) != 32 or "-" in i]
    assert not bad_entries, (
        f"{len(bad_entries)} control_library_entries id(s) are not 32-char "
        f"no-hyphen hex: {bad_entries!r}. A raw-text seed insert reintroduced "
        f"hyphenated str(uuid.uuid4()) ids — ORM queries via Uuid bind 32-char "
        f"no-hyphen and will 404 these rows."
    )
    bad_assign = [
        (aid, lid)
        for aid, lid in assign_rows
        if len(aid) != 32 or "-" in aid or len(lid) != 32 or "-" in lid
    ]
    assert not bad_assign, (
        f"{len(bad_assign)} assignment id/library_entry_id value(s) are not "
        f"32-char no-hyphen hex: {bad_assign!r}. The FK must match the "
        f"normalised entry id or adopt copies zero assignments."
    )


def test_control_library_get_published_finds_browsed_entry(
    alembic_config: Config,
    alembic_engine: Engine,
) -> None:
    """End-to-end of the user-visible breakage path: pick a seeded id and run
    the exact ORM WHERE-clause ``adopt_from_library`` uses (``get_published``).

    Without the fix this returns None — the 404 the user reported.
    """
    from idraa.models.control_library import ControlLibraryEntry

    command.upgrade(alembic_config, "head")

    with alembic_engine.connect() as conn:
        # Raw read bypasses the type adapter — gets the stored string verbatim.
        first_id = conn.execute(
            sa.text("SELECT id FROM control_library_entries LIMIT 1")
        ).scalar_one()

    # Wrap exactly as the route handler does (path param -> uuid.UUID).
    entry_uuid = uuid.UUID(first_id)

    with alembic_engine.connect() as conn:
        # Mirror get_published's WHERE: id == :entry_id AND status == 'published'.
        result = conn.execute(
            sa.select(ControlLibraryEntry.id).where(
                ControlLibraryEntry.id == entry_uuid,
                ControlLibraryEntry.status == "published",
            )
        ).first()
        assert result is not None, (
            f"ORM query for seeded control-library id {first_id!r} returned "
            f"None — the UUID format mismatch is back. SQLAlchemy bound the "
            f"parameter as no-hyphen but the row's id is hyphenated. This is "
            f"the 404 {{'detail':'Library entry not available'}} on adopt."
        )
