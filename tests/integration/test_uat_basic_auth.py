"""UAT basic-auth pre-gate middleware tests.

Verifies the middleware:
- no-ops when no password is configured (dev/test default)
- rejects missing / malformed / wrong / non-Basic / non-base64 / no-colon
  credentials with 401 + WWW-Authenticate
- accepts correct credentials and is case-insensitive on the scheme
- exempts /healthz unconditionally so the platform health probe always passes
- uses constant-time compare WITHOUT short-circuiting between user+password
- rejects empty-string user (configuration error guardrail)
"""

from __future__ import annotations

import base64
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from idraa.middleware.uat_basic_auth import uat_basic_auth_factory


def _basic(user: str, password: str) -> str:
    raw = f"{user}:{password}".encode("utf-8")
    return f"Basic {base64.b64encode(raw).decode('ascii')}"


def _build_app(*, user: str | None, password: str | None) -> FastAPI:
    app = FastAPI()
    app.middleware("http")(uat_basic_auth_factory(user=user, password=password))

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/scenarios")
    async def scenarios() -> dict[str, int]:
        return {"x": 1}

    return app


def test_middleware_off_when_password_unset() -> None:
    """Dev/test: no password → middleware is a no-op (200 without auth)."""
    client = TestClient(_build_app(user=None, password=None))
    assert client.get("/scenarios").status_code == 200


def test_middleware_rejects_missing_auth() -> None:
    client = TestClient(_build_app(user="admin", password="hunter2"))
    r = client.get("/scenarios")
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == 'Basic realm="Idraa UAT"'


def test_middleware_rejects_wrong_password() -> None:
    client = TestClient(_build_app(user="admin", password="hunter2"))
    r = client.get("/scenarios", headers={"Authorization": _basic("admin", "wrong")})
    assert r.status_code == 401


def test_middleware_rejects_wrong_user() -> None:
    client = TestClient(_build_app(user="admin", password="hunter2"))
    r = client.get("/scenarios", headers={"Authorization": _basic("evil", "hunter2")})
    assert r.status_code == 401


def test_middleware_accepts_correct_creds() -> None:
    client = TestClient(_build_app(user="admin", password="hunter2"))
    r = client.get("/scenarios", headers={"Authorization": _basic("admin", "hunter2")})
    assert r.status_code == 200


def test_middleware_accepts_lowercase_basic_scheme() -> None:
    """RFC 7235: scheme is case-insensitive. `basic` and `BASIC` must both work."""
    raw = "admin:hunter2".encode("utf-8")
    encoded = base64.b64encode(raw).decode("ascii")
    client = TestClient(_build_app(user="admin", password="hunter2"))
    assert (
        client.get("/scenarios", headers={"Authorization": f"basic {encoded}"}).status_code == 200
    )
    assert (
        client.get("/scenarios", headers={"Authorization": f"BASIC {encoded}"}).status_code == 200
    )


def test_middleware_rejects_non_basic_scheme() -> None:
    client = TestClient(_build_app(user="admin", password="hunter2"))
    r = client.get("/scenarios", headers={"Authorization": "Bearer abc.def.ghi"})
    assert r.status_code == 401


def test_middleware_rejects_malformed_base64() -> None:
    client = TestClient(_build_app(user="admin", password="hunter2"))
    r = client.get("/scenarios", headers={"Authorization": "Basic !!!notbase64!!!"})
    assert r.status_code == 401


def test_middleware_rejects_payload_without_colon() -> None:
    """`base64('adminhunter2')` decodes fine but has no `:` separator."""
    encoded = base64.b64encode(b"adminhunter2").decode("ascii")
    client = TestClient(_build_app(user="admin", password="hunter2"))
    r = client.get("/scenarios", headers={"Authorization": f"Basic {encoded}"})
    assert r.status_code == 401


def test_middleware_exempts_healthz_unconditionally() -> None:
    """Platform health probe runs without credentials. /healthz must always 200."""
    client = TestClient(_build_app(user="admin", password="hunter2"))
    assert client.get("/healthz").status_code == 200


def test_middleware_rejects_empty_user_configuration() -> None:
    """Empty-string user is a misconfiguration trap.

    Without this guard, `eff_user or ""` would compare every submitted user
    against `""` — anyone submitting `Basic OmhvbnRlcjI=` (`":hunter2"`)
    would authenticate. Fail closed: empty user rejects all requests.
    """
    client = TestClient(_build_app(user="", password="hunter2"))
    r = client.get("/scenarios", headers={"Authorization": _basic("", "hunter2")})
    assert r.status_code == 401


def test_middleware_compare_does_not_short_circuit_on_user_mismatch() -> None:
    """Short-circuiting `compare_digest(user) AND compare_digest(pw)` leaks
    which field failed via timing. Both compares must run on every attempt.

    We patch `secrets.compare_digest` and assert it's called exactly twice
    even when the user is wrong.
    """
    with patch(
        "idraa.middleware.uat_basic_auth.secrets.compare_digest",
        wraps=__import__("secrets").compare_digest,
    ) as mock_cmp:
        client = TestClient(_build_app(user="admin", password="hunter2"))
        r = client.get(
            "/scenarios",
            headers={"Authorization": _basic("evil", "hunter2")},
        )
        assert r.status_code == 401
        # Two calls: one for user, one for password. Both must run.
        assert mock_cmp.call_count == 2, (
            f"compare_digest called {mock_cmp.call_count}× — must be called "
            "twice (user + password) on every auth attempt to prevent "
            "timing-leak via short-circuit"
        )


def test_pwa_install_assets_exempt_without_auth() -> None:
    """M0.1 (Samsung install fix): browser install pipelines fetch the
    manifest/icons/worker in credentials modes that may omit HTTP-auth
    entries — the enumerated PWA install assets pass the gate without
    credentials. EXACT paths only; anything else stays 401."""
    from idraa.middleware.uat_basic_auth import EXEMPT_PATHS

    app = _build_app(user="admin", password="hunter2")

    @app.get("/sw.js")
    async def sw() -> dict[str, str]:
        return {"js": "shim"}

    @app.get("/static/manifest.webmanifest")
    async def manifest() -> dict[str, str]:
        return {"name": "Idraa"}

    client = TestClient(app)
    assert client.get("/sw.js").status_code == 200
    assert client.get("/static/manifest.webmanifest").status_code == 200
    # every enumerated install asset stays exempt (S-1: dropping one would
    # silently re-break install on Samsung/Safari) — 404 here means the
    # exemption matched and routing proceeded (no route in this mini-app);
    # 401 would mean the gate swallowed it.
    for p in EXEMPT_PATHS - {"/healthz"}:
        assert client.get(p).status_code != 401, p
    # every enumerated exemption is exact-path (no trailing-slash prefixes)
    assert all(not p.endswith("/") for p in EXEMPT_PATHS)
    # read-only verbs only (S-2): a POST to an exempt path stays gated
    assert client.post("/sw.js").status_code == 401
    # a sibling static path is NOT exempt — the bypass surface is enumerable
    assert client.get("/static/css/app.css").status_code == 401
    # and the app proper stays gated
    assert client.get("/scenarios").status_code == 401
