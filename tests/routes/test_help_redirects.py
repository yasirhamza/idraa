"""Old help slugs 301 to their renamed articles (full page); HX gets content."""

from __future__ import annotations

import pytest

from idraa.help_content import HELP_REDIRECTS


@pytest.mark.asyncio
@pytest.mark.parametrize("old,new", sorted(HELP_REDIRECTS.items()))
async def test_old_slug_redirects_full_page(authed_analyst, old: str, new: str) -> None:
    client, _ = authed_analyst
    r = await client.get(f"/help/{old}", follow_redirects=False)
    assert r.status_code == 301
    assert r.headers["location"] == f"/help/{new}"


@pytest.mark.asyncio
async def test_old_slug_hx_request_serves_new_partial(authed_analyst) -> None:
    client, _ = authed_analyst
    r = await client.get("/help/reports", headers={"HX-Request": "true"})
    assert r.status_code == 200  # partial, not a redirect the drawer can't follow
    assert "Reports" in r.text


@pytest.mark.asyncio
async def test_boosted_navigation_gets_full_page_not_partial(authed_analyst) -> None:
    # hx-boost sends HX-Request too; a boosted nav must NEVER get the bare
    # drawer partial (Arch-B1: it would replace <body> and strip the chrome).
    client, _ = authed_analyst
    r = await client.get(
        "/help/getting-started",
        headers={"HX-Request": "true", "HX-Boosted": "true"},
    )
    assert r.status_code == 200
    assert "<html" in r.text  # full page, chrome intact


@pytest.mark.asyncio
async def test_boosted_old_slug_takes_the_301(authed_analyst) -> None:
    client, _ = authed_analyst
    r = await client.get(
        "/help/reports",
        headers={"HX-Request": "true", "HX-Boosted": "true"},
        follow_redirects=False,
    )
    assert r.status_code == 301  # htmx follows via xhr.responseURL
