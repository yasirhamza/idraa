"""POST /scenarios/wizard/request-sme returns JSON with id, name, role_title.

Replaces the legacy ORM-object response. The wizard combobox consumes this
JSON client-side to push the new SME into its directory store without a
round-trip page refresh.

Per plan-gate B-Arch-3: there is no ``authenticated_analyst_client``
fixture; the project exposes ``authed_analyst`` which returns
``(AsyncClient, organization_id)``. We use that here.

CSRFMiddleware enforces double-submit on POST: we bootstrap the cookie via
GET /setup (per ``conftest.csrf_post``) and replay the token in the
``X-CSRF-Token`` header — required because this endpoint reads JSON, not
form bodies.
"""

from __future__ import annotations

import uuid
from uuid import UUID

import pytest
from httpx import AsyncClient


async def _csrf_token_for(client: AsyncClient) -> str:
    """Bootstrap CSRFMiddleware's cookie and return the token value.

    GET /setup is allowlisted by setup_guard pre- and post-bootstrap, so
    it's the canonical place to mint a fresh csrf_token cookie (same
    pattern used by ``tests.conftest.csrf_post``).
    """
    r = await client.get("/setup")
    assert r.status_code in (200, 303), f"bootstrap GET /setup returned {r.status_code}"
    token = client.cookies.get("csrf_token")
    assert token, "csrf_token cookie missing after bootstrap GET"
    return token


@pytest.mark.asyncio
async def test_analyst_request_returns_json_shape(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
) -> None:
    """Successful request emits {id: UUID-str, name: str, role_title: str | null}."""
    client, _org_id = authed_analyst
    csrf = await _csrf_token_for(client)
    resp = await client.post(
        "/scenarios/wizard/request-sme",
        json={"name": "Alice Chen", "role_title": "Senior Analyst"},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert set(body.keys()) == {"id", "name", "role_title"}
    assert body["name"] == "Alice Chen"
    assert body["role_title"] == "Senior Analyst"
    # id is a UUID string
    UUID(body["id"])


@pytest.mark.asyncio
async def test_analyst_request_optional_role_title(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
) -> None:
    """role_title is optional; omit-or-null returns null in the JSON body."""
    client, _org_id = authed_analyst
    csrf = await _csrf_token_for(client)
    resp = await client.post(
        "/scenarios/wizard/request-sme",
        json={"name": "Bob Smith"},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["role_title"] is None
