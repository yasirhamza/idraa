"""Help routes: index + per-article, full-page + HX-partial + 404 (design 2026-06-13)."""

from __future__ import annotations

import pytest
from markupsafe import escape

from idraa.help_content import HELP_ARTICLES


@pytest.mark.asyncio
async def test_index_renders_with_all_article_titles(authed_analyst):
    client, _ = authed_analyst
    r = await client.get("/help")
    assert r.status_code == 200
    for a in HELP_ARTICLES:
        assert str(escape(a.title)) in r.text


@pytest.mark.asyncio
@pytest.mark.parametrize("slug", [a.slug for a in HELP_ARTICLES])
async def test_article_full_page_renders(authed_analyst, slug):
    client, _ = authed_analyst
    r = await client.get(f"/help/{slug}")
    assert r.status_code == 200
    assert "<main" in r.text  # full page extends base.html


@pytest.mark.asyncio
@pytest.mark.parametrize("slug", [a.slug for a in HELP_ARTICLES])
async def test_article_hx_partial_renders(authed_analyst, slug):
    client, _ = authed_analyst
    r = await client.get(f"/help/{slug}", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "<main" not in r.text  # partial, not full page
    assert "<article" in r.text


@pytest.mark.asyncio
async def test_unknown_slug_full_page_404(authed_analyst):
    client, _ = authed_analyst
    r = await client.get("/help/nope")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_unknown_slug_hx_returns_drawer_safe_404(authed_analyst):
    client, _ = authed_analyst
    r = await client.get("/help/nope", headers={"HX-Request": "true"})
    assert r.status_code == 404
    assert "<main" not in r.text  # drawer-shaped, not the full styled error page
    assert "doesn't exist" in r.text


@pytest.mark.asyncio
async def test_article_requires_login(anonymous_client, admin_user, db_session):
    await db_session.commit()
    r = await anonymous_client.get("/help/getting-started", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith("/login")


@pytest.mark.asyncio
async def test_index_groups_by_cluster_and_links_each_article(authed_analyst):
    client, _ = authed_analyst
    r = await client.get("/help")
    body = r.text
    # cluster headings present (assert autoescaped form — autoescape is ON)
    for cluster in [
        "Getting started",
        "Core flow",
        "Methodology",
        "Libraries & data",
        "Outputs & configuration",
    ]:
        assert str(escape(cluster)) in body
    # each article linked to its URL
    for a in HELP_ARTICLES:
        assert f'href="/help/{a.slug}"' in body


@pytest.mark.asyncio
async def test_base_includes_drawer_and_script(authed_analyst):
    client, _ = authed_analyst
    r = await client.get("/help")  # any page extending base.html
    body = r.text
    assert 'id="help-drawer-body"' in body
    assert "help_drawer.js" in body


@pytest.mark.asyncio
async def test_help_nav_active_on_article_page(authed_analyst):
    import re

    client, _ = authed_analyst
    r = await client.get("/help/getting-started")
    # The Help SIDEBAR anchor (href="/help") must carry aria-current even on a
    # sub-article page. Assert co-occurrence on the same anchor tag — NOT a bare
    # global count (the breadcrumb's terminal crumb also emits aria-current, so
    # a count>=1 would false-pass; SC-N1).
    anchor = re.compile(
        r'<a[^>]*href="/help"[^>]*aria-current="page"'
        r'|<a[^>]*aria-current="page"[^>]*href="/help"'
    )
    assert anchor.search(r.text), "Help sidebar anchor not marked aria-current"
