from __future__ import annotations

import uuid

from sqlalchemy import text

from idraa.models.threat_community import (
    CANONICAL_THREAT_COMMUNITY_SLUGS,
    THREAT_COMMUNITY_NAMESPACE,
    canonical_threat_community_id,
)


def test_canonical_ids_are_deterministic_uuid5() -> None:
    assert canonical_threat_community_id("nation_state") == uuid.uuid5(
        THREAT_COMMUNITY_NAMESPACE, "nation_state"
    )
    assert len({canonical_threat_community_id(s) for s in CANONICAL_THREAT_COMMUNITY_SLUGS}) == 9


async def test_schema_seeding_inserts_nine_rows_visible_to_app_engine(
    seed_threat_communities, db_session, client
) -> None:
    """Rows are committed by _create_schema, so the app's own engine sees them (no write-lock hang)."""
    assert set(seed_threat_communities) == set(CANONICAL_THREAT_COMMUNITY_SLUGS)
    assert (await client.get("/setup")).status_code in (200, 303)
    nulls = (
        (
            await db_session.execute(
                text("SELECT slug FROM threat_communities WHERE tcap_landmark IS NULL")
            )
        )
        .scalars()
        .all()
    )
    assert nulls == ["insider_accidental"]  # SQL NULL, not the JSON text 'null' (none_as_null=True)
