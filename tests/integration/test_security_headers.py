"""Assert every response carries the app-layer hardening headers.

Companion to the reverse-proxy-layer hardening (which will eventually add
HSTS over HTTPS). These app-layer headers are safe to emit unconditionally
and defend against the OWASP "bread-and-butter" browser attacks: MIME
sniffing (A05:2021), clickjacking (A05), XSS via inline script injection
(A03), and referrer leakage.

Every front-end asset is self-hosted, so the CSP grants no external origin
in any directive; :func:`test_csp_grants_no_external_origin` pins the policy
and ``base.html`` together so a new CDN tag or origin grant fails loudly
instead of silently breaking in the browser (violations fail closed).
"""

from __future__ import annotations

from importlib.resources import files

from httpx import AsyncClient

from idraa.middleware.security_headers import CSP_POLICY

# Headers the middleware must set on every response, with their expected values.
EXPECTED_HEADERS: dict[str, str] = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
}


async def test_security_headers_on_dashboard(client: AsyncClient) -> None:
    # With no users seeded, Task 1.1.5's setup-guard redirects GET / to
    # /setup. Hit /setup directly instead — it renders through the same
    # middleware stack and is a typical HTML page, which is what this test
    # actually cares about.
    r = await client.get("/setup")
    assert r.status_code == 200
    for name, value in EXPECTED_HEADERS.items():
        assert r.headers.get(name) == value, f"{name} missing/wrong on GET /setup"
    # Value-equality (not presence-only) closes the ``setdefault`` silent-override
    # window: if any downstream handler sets ``content-security-policy`` (the
    # case-insensitive lookup would match) with a different value, this fails.
    assert r.headers["content-security-policy"] == CSP_POLICY, "CSP drift on GET /setup"


async def test_security_headers_on_healthz(client: AsyncClient) -> None:
    r = await client.get("/healthz")
    assert r.status_code == 200
    for name, value in EXPECTED_HEADERS.items():
        assert r.headers.get(name) == value, f"{name} missing/wrong on GET /healthz"
    assert r.headers["content-security-policy"] == CSP_POLICY, "CSP drift on GET /healthz"


async def test_security_headers_on_static_asset(client: AsyncClient) -> None:
    """Static files are served via a mounted sub-app; middleware must still fire."""
    r = await client.get("/static/css/app.css")
    assert r.status_code == 200
    for name, value in EXPECTED_HEADERS.items():
        assert r.headers.get(name) == value, f"{name} missing/wrong on static asset"
    assert r.headers["content-security-policy"] == CSP_POLICY


async def test_security_headers_on_404(client: AsyncClient) -> None:
    """404 responses must still carry the hardening headers.

    Uses ``/setup/no-such-route`` because Task 1.1.5's setup-guard would
    307-redirect any non-allowlisted unknown path to /setup before it had
    a chance to 404. ``/setup/*`` is allowlisted (segment-aware prefix in
    ``_ALLOW_DIR_PREFIXES``), so FastAPI's own 404 handler runs as intended.
    ``/api`` is no longer in the allowlist (removed in 1.1.5.a FIX 2 —
    pre-setup visitors must not see Swagger UI / OpenAPI schema).
    """
    r = await client.get("/setup/no-such-route")
    assert r.status_code == 404
    for name, value in EXPECTED_HEADERS.items():
        assert r.headers.get(name) == value, f"{name} missing/wrong on 404"
    assert r.headers["content-security-policy"] == CSP_POLICY


async def test_csp_frame_ancestors_none(client: AsyncClient) -> None:
    """Clickjacking defense: page must not be embeddable in any iframe."""
    # /setup is allowlisted by the setup-guard; CSP comes from SecurityHeaders
    # middleware regardless of which path is hit, so this still exercises
    # the same policy.
    r = await client.get("/setup")
    csp = r.headers["Content-Security-Policy"]
    assert "frame-ancestors 'none'" in csp


async def test_csp_grants_no_external_origin(client: AsyncClient) -> None:
    """CSP must grant zero external origins — every asset is self-hosted.

    Successor to the CDN-origin lockstep test: with HTMX + Alpine vendored
    (the last unpkg.com grant dropped), the policy is 'self'-only across all
    directives. base.html must match (no external script/link tags), so the
    template and the policy are still pinned together — a new CDN tag or a
    new CSP origin grant each fail here.
    """
    # ``importlib.resources`` survives a tests-layout reorg (no ``parents[2]``
    # cliff) and locates the packaged template via the installed distribution.
    base_html = (files("idraa") / "templates" / "base.html").read_text(encoding="utf-8")
    assert 'src="https://' not in base_html and 'href="https://' not in base_html, (
        "base.html references an external asset origin — vendor it under "
        "/static/vendor/ instead, or (exceptionally) grant it in CSP + here."
    )

    # Same setup-guard rationale as the other CSP tests — hit an allowlisted
    # HTML page so we get a real 200 response with the CSP header.
    r = await client.get("/setup")
    csp = r.headers["Content-Security-Policy"]
    assert "https://" not in csp, f"CSP unexpectedly grants an external origin: {csp}"


async def test_csp_no_longer_grants_dropped_cdns(client: AsyncClient) -> None:
    """Regression guard: cdn.tailwindcss.com, cdn.jsdelivr.net and unpkg.com
    must be gone from the emitted CSP now that Tailwind + DaisyUI CSS and
    HTMX + Alpine are all self-hosted."""
    r = await client.get("/setup")
    csp = r.headers["Content-Security-Policy"]
    assert "cdn.tailwindcss.com" not in csp, "CSP should no longer grant cdn.tailwindcss.com"
    assert "cdn.jsdelivr.net" not in csp, "CSP should no longer grant cdn.jsdelivr.net"
    assert "unpkg.com" not in csp, "CSP should no longer grant unpkg.com"


async def test_csp_denies_http_origins_implicitly(client: AsyncClient) -> None:
    """Policy must not grant any ``http://`` origin.

    Subsumed by ``test_csp_grants_no_external_origin`` (no origin is granted
    at all), but kept as a distinct guard: if an external origin is ever
    deliberately re-granted, it must be https:// — a plain "http://" scheme
    would be a MITM-able asset load bypassing the site's own TLS.
    """
    r = await client.get("/setup")
    csp = r.headers["Content-Security-Policy"]
    assert "http://" not in csp, "CSP unexpectedly allows insecure http:// origins"


async def test_csp_script_src_shape(client: AsyncClient) -> None:
    """Spot-check script-src has exactly the tokens rendering relies on.

    We do not assert the *exact* directive bytes (allows formatting tweaks)
    but every token we rely on for rendering must be present, and every
    dropped CDN origin must stay gone.
    """
    r = await client.get("/setup")
    csp = r.headers["Content-Security-Policy"]
    # Crude but sufficient: script-src directive is one semicolon-separated
    # slice of the policy string.
    script_src = next(
        (d.strip() for d in csp.split(";") if d.strip().startswith("script-src")),
        None,
    )
    assert script_src is not None, "CSP has no script-src directive"
    for token in (
        "'self'",
        # 'unsafe-eval' is required by the standard Alpine build's expression
        # evaluator; dropping it is #487 (@alpinejs/csp migration). When that
        # lands, move this token to the absence list below.
        "'unsafe-eval'",
        # unpkg.com removed — HTMX + Alpine are self-hosted (served from 'self').
        # cdn.plot.ly removed — the chart vendor is gone entirely (epic #547 P3).
        # cdn.jsdelivr.net / cdn.tailwindcss.com removed — CSS is self-hosted.
    ):
        assert token in script_src, f"script-src missing token {token!r}"
    for dropped in ("unpkg.com", "cdn.plot.ly", "cdn.jsdelivr.net", "cdn.tailwindcss.com"):
        assert dropped not in script_src, (
            f"script-src should no longer grant {dropped} (asset is self-hosted)"
        )
