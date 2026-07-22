from __future__ import annotations

import pytest

from idraa.config import Settings


def _settings(**env: str) -> Settings:
    return Settings(_env_file=None, **env)  # type: ignore[arg-type,call-arg]


def test_webauthn_defaults_are_owner_deployment() -> None:
    s = _settings(environment="dev", session_secret="x" * 16)
    assert s.webauthn_rp_id == "idraa.fly.dev"
    # The default is one coherent example; RP-ID/origins are per-deployment config.
    # (A single RP-ID binds to one registrable domain — a WebAuthn protocol rule —
    # and the default must be self-consistent or the prod validator rejects it.)
    assert s.webauthn_origin_list == ["https://idraa.fly.dev"]
    assert s.auth_mfa_policy == "required"


def test_webauthn_origins_split_and_strip() -> None:
    s = _settings(
        environment="dev",
        session_secret="x" * 16,
        webauthn_origins=" https://a.example ,https://b.example , ",
    )
    assert s.webauthn_origin_list == ["https://a.example", "https://b.example"]


def test_prod_rejects_placeholder_rp_id() -> None:
    with pytest.raises(ValueError, match="WEBAUTHN_RP_ID"):
        _settings(
            environment="prod",
            session_secret="y" * 40,
            webauthn_rp_id="",
        )


def test_prod_rejects_origin_not_covering_rp_id() -> None:
    with pytest.raises(ValueError, match="WEBAUTHN_ORIGINS"):
        _settings(
            environment="prod",
            session_secret="y" * 40,
            webauthn_rp_id="idraa.fly.dev",
            webauthn_origins="https://evil.example",
        )


def test_prod_accepts_subdomain_origin() -> None:
    s = _settings(
        environment="prod",
        session_secret="y" * 40,
        webauthn_rp_id="example.com",
        webauthn_origins="https://app.example.com,https://example.com",
    )
    assert s.webauthn_rp_id == "example.com"
