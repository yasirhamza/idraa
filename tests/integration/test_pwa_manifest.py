"""PWA M0 acceptance (mobile strategy M0: manifest + icons, NO service worker).

Installability needs manifest + HTTPS only; a service worker is deliberately
absent (session-authed, DB-backed app — offline mode is meaningless and a SW
invites stale-cache bugs alongside static_version busting). If someone adds
one, they own that trade-off consciously — see the base.html head comment.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio

STATIC_DIR = Path(__file__).resolve().parents[2] / "src" / "idraa" / "static"

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


async def test_manifest_served_and_valid(client: AsyncClient) -> None:
    """The web app manifest is served and pins the graphite identity."""
    r = await client.get("/static/manifest.webmanifest")
    assert r.status_code == 200
    m = json.loads(r.text)
    assert m["short_name"] == "Idraa"
    assert m["display"] == "standalone"
    assert m["start_url"] == "/"
    assert m["theme_color"] == "#37464F"  # graphite brand — sync with app.css
    assert m["background_color"] == "#FAFAF9"  # surface-0 light
    purposes = {i["purpose"] for i in m["icons"]}
    assert purposes == {"any", "maskable"}
    assert {i["sizes"] for i in m["icons"]} == {"192x192", "512x512"}


async def test_icons_served_as_png(client: AsyncClient) -> None:
    for name in (
        "icon-192.png",
        "icon-512.png",
        "icon-maskable-512.png",
        "apple-touch-icon.png",
    ):
        r = await client.get(f"/static/icons/{name}")
        assert r.status_code == 200, name
        assert r.content.startswith(_PNG_MAGIC), name


async def test_base_head_carries_pwa_links(client: AsyncClient) -> None:
    """base.html links the manifest, both theme-color metas, and the
    apple-touch-icon — and ships NO service-worker registration."""
    r = await client.get("/login")
    assert r.status_code == 200
    html = r.text
    assert 'rel="manifest"' in html
    assert "/static/manifest.webmanifest" in html
    assert 'crossorigin="use-credentials"' in html  # P-1: UAT basic-auth gate
    assert 'content="#37464F"' in html
    assert 'content="#0A0A0B"' in html
    assert "apple-touch-icon" in html
    assert "serviceWorker" not in html  # M0: deliberately no SW


async def test_no_service_worker_anywhere() -> None:
    """P-2: the no-SW decision covers the whole first-party tree, not just
    the login page's HTML — a register() in a static JS file or any template
    must trip this, so adding a SW is a conscious, reviewed decision."""
    src = Path(__file__).resolve().parents[2] / "src" / "idraa"
    offenders: list[str] = []
    for path in sorted(src.rglob("*")):
        if not path.is_file() or "static/vendor" in str(path).replace("\\", "/"):
            continue
        if path.suffix not in {".js", ".html", ".py"}:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "serviceWorker" in text and "test_pwa" not in path.name:
            offenders.append(str(path.relative_to(src)))
    assert not offenders, f"service-worker references found: {offenders}"


async def test_manifest_theme_matches_tokens() -> None:
    """P-3: the manifest/meta colors anchor to the SPECIFIC app.css token
    declarations (not a loose substring), incl. the dark theme-color."""
    import re

    css = (STATIC_DIR / "css" / "app.css").read_text(encoding="utf-8")
    root, _, dark = css.rpartition('[data-theme="dark"]')
    manifest = json.loads((STATIC_DIR / "manifest.webmanifest").read_text(encoding="utf-8"))
    assert re.search(rf"--color-brand:\s+{manifest['theme_color']}", root, re.IGNORECASE), (
        "manifest theme_color must equal light --color-brand"
    )
    assert re.search(
        rf"--color-surface-0:\s+{manifest['background_color']}", root, re.IGNORECASE
    ), "manifest background_color must equal light --color-surface-0"
    # dark theme-color meta in base.html = dark surface-0
    base = (
        Path(__file__).resolve().parents[2] / "src" / "idraa" / "templates" / "base.html"
    ).read_text(encoding="utf-8")
    dark_meta = re.search(
        r'media="\(prefers-color-scheme: dark\)" content="(#[0-9A-Fa-f]{6})"', base
    )
    assert dark_meta, "dark theme-color meta missing"
    assert re.search(rf"--color-surface-0:\s+{dark_meta.group(1)}", dark, re.IGNORECASE), (
        "dark theme-color meta must equal dark --color-surface-0"
    )
