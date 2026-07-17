"""Alpha-tester feedback — in-app /help user guide page.

Spec: docs/superpowers/specs/2026-06-05-help-user-guide-design.md
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.user import User


@pytest.mark.asyncio
async def test_help_requires_login(
    anonymous_client: AsyncClient,
    admin_user: User,
    db_session: AsyncSession,
):
    """Unauthenticated GET /help redirects to the login page (303).

    admin_user seeds a user so setup_guard does not 307→/setup; the route
    then runs require_user → 401 → _auth_redirect_handler → 303 /login.
    Commit explicitly so the client's separate engine can observe the User row.
    """
    await db_session.commit()
    r = await anonymous_client.get("/help", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith("/login")


@pytest.mark.asyncio
async def test_help_renders_for_analyst(authed_analyst):
    """An authenticated analyst gets the help page (200).

    authed_analyst starts with a fresh org and no scenarios/controls/runs, so
    this also covers the cold-start render (the page has no data dependency)."""
    client, _ = authed_analyst
    r = await client.get("/help")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_help_renders_for_reviewer(authed_reviewer):
    """Reviewers are read-only but MUST be able to read the guide (200)."""
    client, _ = authed_reviewer
    r = await client.get("/help")
    assert r.status_code == 200


# test_help_spells_out_key_acronyms → migrated to
#   tests/routes/test_help_articles.py::test_methodology_primer_glossary_and_nodes
# test_help_frames_vulnerability_as_inherent → migrated to
#   tests/routes/test_help_articles.py::test_build_a_scenario_inherent_vuln_and_event_conditional_loss
#   + ::test_methodology_primer_glossary_and_nodes


@pytest.mark.asyncio
async def test_sidebar_links_to_help(authed_analyst):
    """The Help page must be reachable from the global sidebar nav."""
    client, _ = authed_analyst
    r = await client.get("/")  # dashboard renders the shared sidebar
    assert r.status_code == 200
    assert 'href="/help"' in r.text
