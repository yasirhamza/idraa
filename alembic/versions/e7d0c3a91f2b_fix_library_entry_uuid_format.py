"""fix_library_entry_uuid_format

The seed migration ``c1d2e3f4a5b6_seed_library_entries.py`` inserted
``str(uuid.uuid4())`` (36-char hyphenated) directly via raw ``sa.text("INSERT
... VALUES ...")``. SQLAlchemy ORM queries via the column's ``UuidType
(as_uuid=True)`` adapter bind UUID parameters as 32-char no-hyphen hex.

The format mismatch silently broke every id-based ORM query against
``scenario_library_entries``: the rows existed in the DB, ``list_browseable``
worked because it iterated by status/version filters (no id comparison),
but ``ScenarioLibraryService._get_entry_by_id`` and ``get_by_id_version``
returned ``None`` for valid ids — surfacing as ``LibraryEntryNotFoundError``
on the wizard's step-1 → step-2 advance.

This migration normalises every existing ``scenario_library_entries.id``
to the no-hyphen hex format. Idempotent: ``REPLACE(id, '-', '')`` on a
row that's already no-hyphen is a no-op. Safe to run on a fresh DB
seeded after this migration lands.

Revision ID: e7d0c3a91f2b
Revises: ae67f3cda318
Create Date: 2026-05-08 19:50:00.000000

"""

from alembic import op

revision = "e7d0c3a91f2b"
down_revision = "ae67f3cda318"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("UPDATE scenario_library_entries SET id = REPLACE(id, '-', '')")


def downgrade() -> None:
    # No reverse: re-inserting hyphens would require remembering the
    # original character positions (8-4-4-4-12). For a downgrade safety
    # net, callers can re-run the seed migration on a fresh DB.
    pass
