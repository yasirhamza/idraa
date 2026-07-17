"""Library-bundle export route tests — JSON bundle download (Task 5).

Two new routes (both ``require_user`` — read of the public catalog, NOT admin):

  GET /library/export                      → full published catalog as a JSON bundle
  GET /library/entries/{entry_id}/export   → one entry (latest published version)

The bundle is the EXACT ``LibraryEntrySeed`` shape, so the downloaded bytes
re-import cleanly through ``parse_bundle`` → ``_validate_entries`` (cross-instance
round-trip pinned here).

The integration DB is built via ``Base.metadata.create_all`` (NOT Alembic), so it
is NOT pre-seeded with the library entries — each test inserts a published
seed-source row first, then asserts it appears in the export.

A route-resolution test pins that ``/library/export`` and ``/library/import`` are
NOT swallowed by the typed ``/library/entries/{entry_id}`` param or any catch-all.
"""

from __future__ import annotations

import json
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.enums import AssetClass, ThreatActorType, ThreatCategory
from idraa.models.scenario_library import ScenarioLibraryEntry
from idraa.services.library_bundle_import import _validate_entries, parse_bundle


async def _insert_entry(
    db_session: AsyncSession,
    *,
    slug: str = "exp-route-a",
    name: str = "ExpRouteA",
) -> ScenarioLibraryEntry:
    entry = ScenarioLibraryEntry(
        id=uuid.uuid4(),
        version=1,
        slug=slug,
        name=name,
        status="published",
        source="seed",
        threat_event_type=ThreatCategory.RANSOMWARE,
        threat_actor_type=ThreatActorType.CYBERCRIMINALS,
        asset_class=AssetClass.SYSTEMS,
        tags=["t"],
        description="An exportable published entry with a long-enough description.",
        canonical_fair_gap="An exportable published entry FAIR gap note (20+).",
        source_citations=[],
        threat_event_frequency={"distribution": "PERT", "low": 1, "mode": 2, "high": 3},
        vulnerability={"distribution": "PERT", "low": 0.1, "mode": 0.2, "high": 0.3},
        primary_loss={"distribution": "PERT", "low": 100000, "mode": 1000000, "high": 5000000},
        suggested_control_ids=[],
    )
    db_session.add(entry)
    await db_session.commit()
    await db_session.refresh(entry)
    return entry


# ---- GET /library/export (full catalog) -------------------------------


@pytest.mark.asyncio
async def test_export_bundle_returns_json_array_with_slug(
    analyst_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    await _insert_entry(db_session, slug="exp-full-a", name="ExpFullA")
    r = await analyst_client.get("/library/export")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    assert "attachment" in r.headers.get("content-disposition", "")
    body = json.loads(r.content)
    assert isinstance(body, list)
    slugs = {e["slug"] for e in body}
    assert "exp-full-a" in slugs


@pytest.mark.asyncio
async def test_export_bundle_viewer_allowed(
    viewer_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Export is require_user — any authenticated user (viewer+) may download."""
    await _insert_entry(db_session, slug="exp-viewer", name="ExpViewer")
    r = await viewer_client.get("/library/export")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_export_bundle_unauthenticated_blocked(
    anonymous_client: AsyncClient,
) -> None:
    r = await anonymous_client.get("/library/export")
    assert r.status_code in (302, 303, 307, 401, 403)


# ---- GET /library/entries/{entry_id}/export (single) ------------------


@pytest.mark.asyncio
async def test_export_single_entry_returns_one(
    analyst_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    entry = await _insert_entry(db_session, slug="exp-one", name="ExpOne")
    r = await analyst_client.get(f"/library/entries/{entry.id}/export")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    body = json.loads(r.content)
    assert isinstance(body, list)
    assert len(body) == 1
    assert body[0]["slug"] == "exp-one"
    # DB-managed fields are NOT in the exported obj.
    assert "id" not in body[0]
    assert "source" not in body[0]


@pytest.mark.asyncio
async def test_export_single_entry_unknown_id_404(
    analyst_client: AsyncClient,
) -> None:
    r = await analyst_client.get(f"/library/entries/{uuid.uuid4()}/export")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_export_single_entry_unauthenticated_blocked(
    anonymous_client: AsyncClient,
) -> None:
    r = await anonymous_client.get(f"/library/entries/{uuid.uuid4()}/export")
    assert r.status_code in (302, 303, 307, 401, 403)


# ---- cross-instance round-trip ----------------------------------------


@pytest.mark.asyncio
async def test_exported_bytes_reimport_cleanly(
    analyst_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Export the catalog, then re-import its bytes into an EMPTY library
    (existing_slugs=set()) — every entry must validate as ``add``."""
    await _insert_entry(db_session, slug="rt-route-a", name="RtRouteA")
    await _insert_entry(db_session, slug="rt-route-b", name="RtRouteB")
    r = await analyst_client.get("/library/export")
    assert r.status_code == 200

    pairs, hard_stop = parse_bundle(r.content)
    assert hard_stop == []
    assert pairs is not None
    assert len(pairs) >= 2

    preview, errors, seeds = _validate_entries(pairs, existing_slugs=set())
    assert errors == []
    assert all(p["action"] == "add" for p in preview)
    assert all(s is not None for s in seeds)


# ---- route resolution -------------------------------------------------


@pytest.mark.asyncio
async def test_literal_paths_not_swallowed_by_typed_entry_param(
    admin_client: AsyncClient,
) -> None:
    """`/library/export` (export bundle) and `/library/import` (import form) must
    resolve to their own handlers, NOT to GET /library/entries/{entry_id:uuid}
    (which would 422 on uuid-parse) nor a catch-all.

    A 422 with a uuid_parsing error would prove the literal path was captured by
    the typed param — assert that does NOT happen.
    """
    export = await admin_client.get("/library/export")
    assert export.status_code == 200
    assert export.headers["content-type"].startswith("application/json")

    imp = await admin_client.get("/library/import")
    assert imp.status_code == 200
    assert "import" in imp.text.lower()

    # The CSV summary export must still work and be distinct from the JSON bundle.
    csv = await admin_client.get("/library/export.csv")
    assert csv.status_code == 200
    assert "text/csv" in csv.headers["content-type"]
