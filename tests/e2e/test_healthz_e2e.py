"""E2E smoke test — Playwright talks to a real running uvicorn."""

from __future__ import annotations

import pytest
from playwright.async_api import async_playwright


@pytest.mark.e2e
async def test_healthz_via_browser(live_server_url: str) -> None:
    """A real browser can reach /healthz."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        response = await page.goto(f"{live_server_url}/healthz")
        assert response is not None
        assert response.status == 200
        body = await response.text()
        assert '"status":"ok"' in body

        await browser.close()
