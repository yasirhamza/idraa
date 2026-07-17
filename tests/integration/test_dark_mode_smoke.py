"""F24 plan-gate SC-14: top-level pages must not carry hard-coded light-only
color literals in their server-rendered HTML. These would bypass the design-
system token layer and break dark mode regardless of the theme cookie sent by
the browser.

Note: this is a light-literal regression gate, NOT a dark-mode render test.
Dark-mode rendering is applied client-side by the JS in localize_time.js and
the data-theme attribute; the server-side theme cookie is decorative from the
server's perspective. The meaningful assertion here is the absence of forbidden
color literals in the HTML body.
"""

from __future__ import annotations

import re
import uuid

import pytest
from httpx import AsyncClient

# Forbidden literals — anything that hard-codes light-mode color and bypasses the token layer.
FORBIDDEN_LITERALS = [
    re.compile(r"\bbg-white\b"),
    re.compile(r"\btext-black\b"),
    re.compile(r"\bbg-gray-\d{2,3}\b"),
    re.compile(r"\btext-gray-\d{2,3}\b"),
    re.compile(r"#[Ff][Ff][Ff](?:[Ff]{3})?\b"),  # #fff or #ffffff
    re.compile(r"#000(?:000)?\b"),  # #000 or #000000
    re.compile(r"rgb\(\s*255\s*,\s*255\s*,\s*255\s*\)"),
    re.compile(r"rgb\(\s*0\s*,\s*0\s*,\s*0\s*\)"),
]

# Exclude inline JS theme blocks that legitimately carry hex literals.
EXCLUDE_BLOCKS = [
    re.compile(r"window\.tailwind\.config.*?\};", re.DOTALL),
    re.compile(r"THEME_LAYOUT\s*=.*?\};", re.DOTALL),
]


@pytest.mark.parametrize(
    "path",
    ["/", "/controls", "/scenarios", "/reports", "/library", "/organization", "/users"],
)
async def test_top_level_pages_have_no_hard_coded_light_literals(
    authed_admin: tuple[AsyncClient, uuid.UUID],
    path: str,
) -> None:
    """Set the idraa.theme=dark cookie alongside the session cookie, then
    request each top-level page and assert no hard-coded light-only color
    literals appear in the response body.

    The theme cookie is set via the client's cookie jar (not the ``headers``
    kwarg, which would shadow the session cookie and cause a 303 redirect to
    /login). The backend does not strictly require the theme cookie — actual
    dark-mode rendering happens client-side via JS — but the assertion against
    hard-coded color literals is meaningful: it catches token-bypass regressions
    in server-rendered HTML that would break dark mode for any user.

    A 302 or 303 response indicates a real misroute (e.g. auth guard firing
    unexpectedly) and is surfaced as a test failure, not silently accepted."""
    client, _ = authed_admin
    client.cookies.set("idraa.theme", "dark")
    resp = await client.get(path)
    # Remove the theme cookie so it doesn't leak to other test parameters
    client.cookies.delete("idraa.theme")
    if resp.status_code == 404:
        pytest.skip(f"{path} not mounted")
    assert resp.status_code == 200, f"{path} returned {resp.status_code}"

    body = resp.text
    for blk in EXCLUDE_BLOCKS:
        body = blk.sub("", body)
    offenders = []
    for pat in FORBIDDEN_LITERALS:
        for m in pat.finditer(body):
            offenders.append((pat.pattern, m.group(0)))
    assert not offenders, (
        f"Page {path} carries hard-coded light-only color literals that bypass the token layer:\n  "
        + "\n  ".join(f"{p}: {found}" for p, found in offenders)
    )
