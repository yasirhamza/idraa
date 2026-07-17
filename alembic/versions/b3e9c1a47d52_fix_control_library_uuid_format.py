"""fix_control_library_uuid_format

The seed migration ``d4f6a2b9c8e1_seed_control_library.py`` inserted
``str(uuid.uuid4())`` (36-char hyphenated) directly via raw ``sa.text("INSERT
... VALUES ...")`` for the entry id, the assignment id, and the assignment's
``library_entry_id`` FK. SQLAlchemy ORM queries via the column's
``Uuid(as_uuid=True)`` type bind UUID parameters as 32-char no-hyphen hex on
SQLite.

The format mismatch silently broke every id-based ORM query against the
control library: the rows existed, ``ControlLibraryService.list_browseable``
worked (it filters by status/version, never compares an id), so the browse
cards rendered — but ``get_published(entry_id)`` (the ``adopt_from_library``
path) bound the id as 32-char no-hyphen vs the stored 36-char hyphenated row
and matched 0 rows. That surfaced as ``404 {"detail":"Library entry not
available"}`` when the user clicked "Add to My Controls".

This is the fourth recurrence of the foot-gun (the scenario library hit it
three times; see ``e7d0c3a91f2b`` and the durable guard in
``test_library_uuid_format_fix.py``). This migration normalises every
existing control-library id to no-hyphen hex on all three columns. The
assignment ``library_entry_id`` is stripped in lockstep so it keeps matching
its parent entry id (otherwise adopt would copy zero assignments). Idempotent:
``REPLACE(id, '-', '')`` on an already-no-hyphen row is a no-op, so it's safe
on a fresh DB seeded after this migration lands.

Revision ID: b3e9c1a47d52
Revises: 0897a0ff350e
Create Date: 2026-06-03 00:00:00.000000

"""

from alembic import op

revision = "b3e9c1a47d52"
down_revision = "0897a0ff350e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Strip hyphens on all three id columns. Parent id and the child FK that
    # references it are normalised identically, so the composite FK
    # (library_entry_id, library_entry_version) stays consistent. SQLite runs
    # Alembic migrations with foreign_keys off, so update order is immaterial.
    op.execute("UPDATE control_library_entries SET id = REPLACE(id, '-', '')")
    op.execute(
        "UPDATE control_library_entry_assignments "
        "SET library_entry_id = REPLACE(library_entry_id, '-', '')"
    )
    op.execute("UPDATE control_library_entry_assignments SET id = REPLACE(id, '-', '')")


def downgrade() -> None:
    # No reverse: re-inserting hyphens would require remembering the original
    # 8-4-4-4-12 character positions. For a downgrade safety net, re-run the
    # seed migration on a fresh DB.
    pass
