"""E2E — virtual-authenticator passkey register + usernameless login.

Drives the full ceremony through a REAL headless Chromium against a
dedicated localhost server (see ``passkey_server_url`` in conftest.py):
first-run /setup -> register a passkey via ``navigator.credentials.create``
-> regenerate recovery codes -> log out -> usernameless passkey sign-in via
``navigator.credentials.get``.

The WebAuthn ceremonies are satisfied by Chrome's CDP *virtual authenticator*
(``WebAuthn.addVirtualAuthenticator`` with ``automaticPresenceSimulation``),
so no hardware/biometric prompt is ever needed — required on a headless/
remote box where OS permission dialogs can't be approved.

Selectors below were verified against the CURRENT rendered templates
(``templates/setup/wizard.html``, ``templates/account/security.html``,
``templates/auth/login.html``, ``templates/layouts/_sidebar.html``) and the
flow was hand-walked with a throwaway Playwright script against a live
server before being encoded here — notably:

- ``base.html`` sets ``hx-boost="true"`` on ``<body>``, so the plain
  ``<form method="post">`` submits (recovery-codes regenerate, logout) are
  AJAX'd by htmx. htmx updates the address bar to the POST target (and,
  after a 303, to the redirect's final URL) — so ``page.wait_for_url``
  works for those too, confirmed empirically.
- "Add a passkey" / "Sign in with a passkey" are plain
  ``<button type="button" onclick="idraaWebAuthn...">`` elements (NOT
  forms) that end in ``window.location.assign(...)`` — a real navigation,
  not an htmx swap.
"""

from __future__ import annotations

import re

import pytest
from playwright.async_api import async_playwright


@pytest.mark.e2e
async def test_passkey_register_then_usernameless_login(passkey_server_url: str) -> None:
    base = passkey_server_url
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(base_url=base)
        page = await context.new_page()

        # Enable a virtual authenticator (CTAP2, internal, UV=true) via raw
        # CDP so the ceremony auto-approves with zero OS prompt.
        cdp = await context.new_cdp_session(page)
        await cdp.send("WebAuthn.enable")
        auth_result = await cdp.send(
            "WebAuthn.addVirtualAuthenticator",
            {
                "options": {
                    "protocol": "ctap2",
                    "transport": "internal",
                    "hasResidentKey": True,
                    "hasUserVerification": True,
                    "isUserVerified": True,
                    "automaticPresenceSimulation": True,
                }
            },
        )
        authenticator_id = auth_result.get("authenticatorId")
        assert authenticator_id

        # --- Bootstrap the first admin via /setup (also logs the browser in). ---
        await page.goto("/setup")
        await page.fill('input[name="org_name"]', "E2E Org")
        # industry/size are enum selects with no blank/placeholder option —
        # any valid index works; index=1 matches what was hand-verified.
        await page.select_option('select[name="industry_type"]', index=1)
        await page.select_option('select[name="organization_size"]', index=1)
        await page.fill('input[name="email"]', "e2e@example.test")
        await page.fill('input[name="full_name"]', "E2E Admin")
        await page.fill('input[name="password"]', "pw-12345678")
        await page.click('button[type="submit"]')
        await page.wait_for_url(re.compile(rf"^{re.escape(base)}/?$"), timeout=10_000)

        # --- Register a passkey. ---
        await page.goto("/account/security")
        # Nickname comes from the inline #passkey-nickname input (NOT a native
        # prompt() — that dialog steals document focus and iOS WebKit then
        # rejects credentials.create() with "The document is not focused").
        await page.fill("#passkey-nickname", "My Passkey")
        await page.click("text=Add a passkey")
        # "Passkeys" (plural, the section heading) is present even with zero
        # credentials — wait for the per-credential "Remove" button instead,
        # which only renders once the registration round-trip completed.
        await page.wait_for_selector("#passkey-section button:has-text('Remove')", timeout=10_000)
        content = await page.content()
        assert "My Passkey" in content

        # --- Regenerate recovery codes (stamps mfa_enrolled_at alongside the
        # passkey; also exercises a second boosted-form round-trip). ---
        await page.goto("/account/security")
        await page.click("text=Regenerate recovery codes")
        await page.wait_for_selector("text=Save your recovery codes", timeout=10_000)

        # --- Log out. ---
        await page.goto("/account/security")
        await page.click('form[action="/logout"] button')
        await page.wait_for_url(re.compile(r".*/login$"), timeout=10_000)

        # --- Usernameless passkey sign-in. ---
        await page.click("text=Sign in with a passkey")
        await page.wait_for_url(re.compile(rf"^{re.escape(base)}/?$"), timeout=10_000)
        assert "/login" not in page.url
        content = await page.content()
        assert "Sign out" in content
        assert "e2e@example.test" in content

        await browser.close()
