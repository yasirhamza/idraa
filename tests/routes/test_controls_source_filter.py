"""Task 9 (P2b): GET /controls?source=... provenance badge + filter.

The control list renders a "Source" column distinguishing Custom controls
(manually created / arbitrary-CSV import) from From-library controls (adopted
clone-snapshots from the catalog). The route accepts an optional `?source=`
query param validated against `ControlSource`, returning a generic 400 (NOT
echoing user input) on bad values — mirroring the existing `?domain=` filter.

The repo has no `make_control` factory, so rows are inline-created.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.control import Control
from idraa.models.enums import ControlSource, ControlType, EntityStatus


def _make_control(
    org_id: uuid.UUID,
    *,
    name: str,
    source: ControlSource,
    library_pin: dict | None = None,
) -> Control:
    return Control(
        organization_id=org_id,
        name=name,
        type=ControlType.TECHNICAL,
        annual_cost=Decimal("0"),
        status=EntityStatus.ACTIVE,
        version="1.0",
        source=source,
        library_pin=library_pin,
    )


@pytest.mark.asyncio
async def test_source_filter_partitions(
    authed_admin: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """`?source=library_derived` returns only adopted controls; no filter shows both."""
    client, org_id = authed_admin

    custom = _make_control(org_id, name="Custom1", source=ControlSource.CUSTOM)
    adopted = _make_control(
        org_id,
        name="Adopted1",
        source=ControlSource.LIBRARY_DERIVED,
        library_pin={"entry_id": "x", "version": 1},
    )
    db_session.add_all([custom, adopted])
    await db_session.commit()

    r_all = await client.get("/controls")
    assert r_all.status_code == 200, r_all.text[:300]
    assert "Custom1" in r_all.text
    assert "Adopted1" in r_all.text

    r_lib = await client.get("/controls?source=library_derived")
    assert r_lib.status_code == 200, r_lib.text[:300]
    assert "Adopted1" in r_lib.text
    assert "Custom1" not in r_lib.text

    r_custom = await client.get("/controls?source=custom")
    assert r_custom.status_code == 200, r_custom.text[:300]
    assert "Custom1" in r_custom.text
    assert "Adopted1" not in r_custom.text


@pytest.mark.asyncio
async def test_source_badge_labels_rendered(
    authed_admin: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """The list renders human-readable source labels, not raw enum values."""
    client, org_id = authed_admin

    custom = _make_control(org_id, name="CustomBadge", source=ControlSource.CUSTOM)
    adopted = _make_control(
        org_id,
        name="AdoptedBadge",
        source=ControlSource.LIBRARY_DERIVED,
        library_pin={"entry_id": "y", "version": 2},
    )
    db_session.add_all([custom, adopted])
    await db_session.commit()

    r = await client.get("/controls")
    assert r.status_code == 200, r.text[:300]
    assert "From library" in r.text
    assert "Custom" in r.text


@pytest.mark.asyncio
async def test_unknown_source_returns_generic_400_no_echo(
    authed_admin: tuple[AsyncClient, uuid.UUID],
) -> None:
    """Invalid `?source=` returns a generic 400 — body must NOT echo the input."""
    client, _org_id = authed_admin

    payload = "<script>alert(1)</script>"
    r = await client.get(f"/controls?source={payload}")
    assert r.status_code == 400
    assert payload not in r.text, (
        "400 response echoed the offending user input — XSS / log-injection risk"
    )
    assert "unknown source" in r.text
