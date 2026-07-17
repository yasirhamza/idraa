"""Probe the base layout + static assets + root route."""

from __future__ import annotations

import re

import pytest
from httpx import AsyncClient


async def test_root_returns_html_with_layout(client: AsyncClient) -> None:
    # With no users seeded, the setup-guard middleware (Task 1.1.5) redirects
    # any non-allowlisted path to /setup. The base layout itself is covered
    # by the /setup-based tests below — this test only asserts the guard's
    # 307 behaviour for the root path.
    r = await client.get("/", follow_redirects=False)
    assert r.status_code == 307
    assert r.headers["location"] == "/setup"


async def test_static_served(client: AsyncClient) -> None:
    r = await client.get("/static/css/app.css")
    assert r.status_code == 200
    assert "text/css" in r.headers["content-type"]


async def test_no_external_asset_origins(client: AsyncClient) -> None:
    """base.html must load every script/stylesheet same-origin — zero CDN tags.

    Successor to the SRI-count guard: with HTMX + Alpine vendored (the last
    two CDN tags), the invariant is no longer "every CDN tag carries SRI" but
    "there are no CDN tags at all". The app must render + function fully on
    air-gapped / CDN-blocked networks, and CSP script-src grants only 'self'.
    A new external ``<script src>``/``<link href>`` fails loudly here — the
    asset should be vendored instead (see the /static/vendor/ pattern).

    Hits ``/setup`` rather than ``/`` because Task 1.1.5's setup-guard
    redirects ``/`` to ``/setup`` when the DB has no users (empty body on
    the 307). Both templates extend the same ``base.html``.
    """
    r = await client.get("/setup")
    body = r.text
    external_assets = re.findall(r'<(?:script|link)\b[^>]*(?:src|href)="(https?://[^"]*)"', body)
    assert external_assets == [], (
        f"base.html loads assets from external origins: {external_assets} — "
        "vendor them under /static/vendor/ instead (air-gap + CSP 'self' invariant)."
    )
    # With zero cross-origin tags, no SRI attributes should remain either.
    assert body.count('integrity="sha384-') == 0
    assert body.count('crossorigin="anonymous"') == 0


async def test_daisyui_is_self_hosted(client: AsyncClient) -> None:
    """DaisyUI CSS must load same-origin (vendored), not from cdn.jsdelivr.net —
    so styling survives CDN-blocked / air-gapped networks and drops the CSP
    grant. Regression guard for the self-host fix."""
    body = (await client.get("/setup")).text
    assert "/static/vendor/daisyui-4.12.10.min.css" in body, (
        "DaisyUI is not self-hosted in base.html"
    )
    assert "cdn.jsdelivr.net" not in body, "base.html still references cdn.jsdelivr.net"
    # The vendored asset is actually served.
    r = await client.get("/static/vendor/daisyui-4.12.10.min.css")
    assert r.status_code == 200
    assert "text/css" in r.headers["content-type"].lower()


async def test_tailwind_css_is_served(client: AsyncClient) -> None:
    """Self-hosted tailwind.css must be referenced in base.html and served."""
    body = (await client.get("/setup")).text
    assert "/static/css/tailwind.css" in body, "tailwind.css not referenced in base.html"
    assert "cdn.tailwindcss.com" not in body, "base.html still references the Tailwind Play CDN"
    r = await client.get("/static/css/tailwind.css")
    assert r.status_code == 200
    assert "text/css" in r.headers["content-type"].lower()


@pytest.mark.parametrize(
    ("name", "vendored_path"),
    [
        ("HTMX", "/static/vendor/htmx-1.9.12.min.js"),
        ("Alpine", "/static/vendor/alpinejs-3.14.1.min.js"),
    ],
)
async def test_htmx_and_alpine_are_self_hosted(
    client: AsyncClient, name: str, vendored_path: str
) -> None:
    """HTMX + Alpine must load same-origin (vendored), not from unpkg.com — so
    core interactivity (HTMX swaps, Alpine components) survives CDN-blocked /
    air-gapped networks and CSP script-src drops the unpkg.com grant.
    Regression guard mirroring ``test_daisyui_is_self_hosted``. Vendored bytes
    were sha384-verified against the prior SRI integrity values at vendor time.
    """
    body = (await client.get("/setup")).text
    assert vendored_path in body, f"{name} is not self-hosted in base.html"
    assert "unpkg.com" not in body, "base.html still references unpkg.com"
    # The vendored asset is actually served.
    r = await client.get(vendored_path)
    assert r.status_code == 200
    assert "javascript" in r.headers["content-type"].lower()
