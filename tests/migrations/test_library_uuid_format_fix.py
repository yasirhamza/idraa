"""Migration regression: scenario_library_entries.id must be no-hyphen hex.

The seed migration ``c1d2e3f4a5b6_seed_library_entries.py`` inserted
``str(uuid.uuid4())`` (36-char hyphenated) directly via raw SQL. Combined
with SQLAlchemy ``UuidType(as_uuid=True)`` on SQLite — which binds UUID
parameters as 32-char no-hyphen hex — every id-based ORM query against
``scenario_library_entries`` silently returned ``None``. ``LibraryEntry
NotFoundError`` surfaced on the wizard step-1 → step-2 advance.

The fix migration ``e7d0c3a91f2b_fix_library_entry_uuid_format.py`` runs
``UPDATE scenario_library_entries SET id = REPLACE(id, '-', '')``. After
all migrations, every row's ``id`` is 32-char no-hyphen hex and ORM
queries via ``UuidType`` find them.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import Engine

# Apply through the fix migration specifically and inspect the resulting state.
_FIX_REV = "e7d0c3a91f2b"


def test_library_entry_ids_are_no_hyphen_after_fix(
    alembic_config: Config,
    alembic_engine: Engine,
) -> None:
    """Apply migrations through the fix; assert every seeded library entry
    id is 32-char no-hyphen hex (the format SQLAlchemy ``UuidType`` binds)."""
    command.upgrade(alembic_config, _FIX_REV)

    with alembic_engine.connect() as conn:
        rows = conn.execute(sa.text("SELECT id, length(id) FROM scenario_library_entries")).all()
        assert rows, "seed migration left no library entries — unexpected"
        for id_value, id_len in rows:
            assert id_len == 32, (
                f"library entry id {id_value!r} has length {id_len}; "
                f"expected 32-char no-hyphen hex (the format SQLAlchemy "
                f"``UuidType`` binds for ORM queries)"
            )
            assert "-" not in id_value, (
                f"library entry id {id_value!r} still has hyphens — "
                f"the fix migration didn't normalise it"
            )


def test_library_entry_orm_query_by_id_finds_seeded_row(
    alembic_config: Config,
    alembic_engine: Engine,
) -> None:
    """End-to-end: pick a seeded id, ORM-query for it, assert it's found.

    This is the actual user-visible breakage path — wizard step 1 reads
    the entry id from the DB, the user submits it, the route handler
    parses it as ``uuid.UUID`` and calls ``_get_entry_by_id`` which does
    ``select(...).where(.id == entry_id)``. Without the fix, that
    query binds 32-char no-hyphen vs stored 36-char hyphenated and
    returns 0 rows.
    """
    import uuid

    from idraa.models.scenario_library import ScenarioLibraryEntry

    command.upgrade(alembic_config, _FIX_REV)

    with alembic_engine.connect() as conn:
        # Pick any seeded id (raw — bypasses the type adapter).
        first_id = conn.execute(
            sa.text("SELECT id FROM scenario_library_entries LIMIT 1")
        ).scalar_one()

    # Wrap in a UUID exactly the way the route handler does.
    entry_uuid = uuid.UUID(first_id)

    # ORM query — the path that was silently failing.
    #
    # SELECT only ``id`` (not the full row) so this test stays pinned to the
    # historical schema at ``_HEAD_REV`` without breaking every time a later
    # migration adds an ORM column. The UUID-format-mismatch bug surfaces in
    # the WHERE-clause binding, which this minimal SELECT still exercises.
    with alembic_engine.connect() as conn:
        result = conn.execute(
            sa.select(ScenarioLibraryEntry.id).where(ScenarioLibraryEntry.id == entry_uuid)
        ).first()
        assert result is not None, (
            f"ORM query for seeded id {first_id!r} returned None — "
            f"the UUID format mismatch is back. SQLAlchemy bound the "
            f"parameter as no-hyphen but the row's id is hyphenated."
        )


def test_all_library_entry_ids_no_hyphen_through_head(
    alembic_config: Config,
    alembic_engine: Engine,
) -> None:
    """Durable recurrence-class guard (ARCH-1): run the FULL migration chain
    through ``head`` — which includes the additive extension seeds
    ``0897a0ff350e`` (original 13), ``60ff242180f6`` (38 C-iii-b archetypes),
    ``4b7f9e2a1c83`` (3 WS3b energy/manufacturing third-party-revenue
    scenarios), ``f4a1c2b3d4e5`` (8 D-iii-b attested vertical entries,
    #497), and the attack-coverage insert-if-absent migration (9 new
    entries, #529) — and assert EVERY ``scenario_library_entries.id`` is
    32-char no-hyphen hex.

    The sibling ``test_library_entry_ids_are_no_hyphen_after_fix`` pins to the
    fix revision ``e7d0c3a91f2b``, which runs BEFORE the 13-row extension seed,
    so it never inspects those rows.  This test covers all 102 rows
    (31 base + 13 original extension + 38 C-iii-b batches A/B/C + 3 WS3b + 8
    D-iii-b + 9 attack-coverage) and FAILS if any future raw-text seed insert
    reintroduces a hyphenated ``str(uuid.uuid4())`` id (the foot-gun has
    recurred three times: seed → fix e7d0c3a91f2b → reintroduced in
    0897a0ff350e).

    Count update: pre-WS3b this asserted 82 (31+13+38). WS3b appended 3
    entries to the extension JSON (inserted by 0897a0ff350e which reads the
    whole file) and added migration ``4b7f9e2a1c83`` → 85 total. D-iii-b
    (#497) appended 8 more (inserted by ``f4a1c2b3d4e5`` on an existing DB)
    → 93 total. The attack-coverage gap-fill epic (#529) appended 9 more
    (inserted by the same live-JSON-read mechanism) → 102 total. The
    UUID-format invariant (no hyphens, 32 chars) applies to ALL 102 rows.
    """
    command.upgrade(alembic_config, "head")

    with alembic_engine.connect() as conn:
        ids = conn.execute(sa.text("SELECT id FROM scenario_library_entries")).scalars().all()

    # 31 base + 13 original (#303) + 38 C-iii-b (#335) + 3 WS3b + 8 D-iii-b
    # (#497) + 9 attack-coverage (#529) = 102 total.
    assert len(ids) == 102, (
        f"expected 102 library entries through head "
        f"(31 base + 13 original + 38 C-iii-b + 3 WS3b + 8 D-iii-b + "
        f"9 attack-coverage); got {len(ids)}"
    )
    bad = [i for i in ids if len(i) != 32 or "-" in i]
    assert not bad, (
        f"{len(bad)} library entry id(s) are not 32-char no-hyphen hex: {bad!r}. "
        f"A raw-text seed insert reintroduced hyphenated str(uuid.uuid4()) ids — "
        f"ORM queries via UuidType bind 32-char no-hyphen and will 404 these rows."
    )
