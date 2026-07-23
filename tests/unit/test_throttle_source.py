# tests/unit/test_throttle_source.py
from unittest.mock import patch

import pytest
from starlette.requests import Request

from idraa.config import Settings
from idraa.routes.deps import resolve_throttle_source


def _req(headers: dict[str, str], peer: str | None = "10.0.0.9") -> Request:
    return Request(
        {
            "type": "http",
            "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
            "client": (peer, 1234) if peer else None,
        }
    )


def _s(**kw) -> Settings:
    return Settings(environment="test", session_secret="x" * 40, **kw)


@pytest.mark.parametrize(
    "headers,cfg,expected",
    [
        # unconfigured -> None (throttle no-ops); NEVER the peer
        ({"x-forwarded-for": "1.2.3.4"}, {}, None),
        # shape 1: dedicated header, forged XFF ignored, surface-prefixed
        (
            {"cf-connecting-ip": "9.9.9.9", "x-forwarded-for": "1.1.1.1"},
            {"trusted_client_ip_header": "CF-Connecting-IP"},
            "login:9.9.9.9",
        ),
        # shape 1: configured header ABSENT -> None (not the peer)
        ({"x-forwarded-for": "1.1.1.1"}, {"trusted_client_ip_header": "CF-Connecting-IP"}, None),
        # shape 2: single trusted proxy -> parts[len-1]; forged left entry ignored
        ({"x-forwarded-for": "6.6.6.6, 5.5.5.5"}, {"trusted_proxy_count": 1}, "login:5.5.5.5"),
        # shape 2: two trusted proxies -> parts[len-2]
        (
            {"x-forwarded-for": "evil, 7.7.7.7, 5.5.5.5, 4.4.4.4"},
            {"trusted_proxy_count": 2},
            "login:5.5.5.5",
        ),
        # shape 2: too few entries -> None (fail safe)
        ({"x-forwarded-for": "5.5.5.5"}, {"trusted_proxy_count": 3}, None),
        # IPv6 normalized to /64
        (
            {"cf-connecting-ip": "2001:db8:1:2:aaaa:bbbb:cccc:dddd"},
            {"trusted_client_ip_header": "CF-Connecting-IP"},
            "login:2001:db8:1:2::",
        ),
    ],
)
def test_resolve_login_surface(headers, cfg, expected):
    with patch("idraa.routes.deps.get_settings", return_value=_s(**cfg)):
        assert resolve_throttle_source(_req(headers), surface="login") == expected


def test_single_proxy_forged_prefix_returns_trusted_not_leftmost():
    # THE security regression: a forged leftmost must NOT become the key.
    with patch("idraa.routes.deps.get_settings", return_value=_s(trusted_proxy_count=1)):
        r = _req({"x-forwarded-for": "66.66.66.66, 5.5.5.5"})  # attacker prepended 66.*
        assert resolve_throttle_source(r, surface="login") == "login:5.5.5.5"


def test_surface_namespacing():
    with patch(
        "idraa.routes.deps.get_settings",
        return_value=_s(trusted_client_ip_header="CF-Connecting-IP"),
    ):
        r = _req({"cf-connecting-ip": "9.9.9.9"})
        assert resolve_throttle_source(r, surface="stepup") == "stepup:9.9.9.9"
