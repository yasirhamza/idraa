from __future__ import annotations

import pytest

from idraa.config import Settings


def _settings(**env: str) -> Settings:
    return Settings(_env_file=None, **env)  # type: ignore[arg-type,call-arg]


def test_webauthn_defaults_are_localhost() -> None:
    """OSS rule (readme-selfhost-rewrite, 2026-07-22): no deployment domain may
    ship as a code default. localhost works for dev/compose evaluation out of
    the box; prod boot refuses this default (see
    test_prod_refuses_localhost_webauthn_default below)."""
    s = _settings(environment="dev", session_secret="x" * 16)
    assert s.webauthn_rp_id == "localhost"
    # The default is one coherent example; RP-ID/origins are per-deployment config.
    # (A single RP-ID binds to one registrable domain — a WebAuthn protocol rule —
    # and the default must be self-consistent or the prod validator rejects it.)
    assert s.webauthn_origin_list == ["http://localhost:8000"]
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
    # mfa_encryption_key set so this isolates the WebAuthn origin-matching
    # guard being exercised here — a bare prod Settings() now also trips
    # _check_mfa_key_hardening (security-audit wave, 2026-07-22), covered
    # separately in tests/unit/test_config_mfa_key.py.
    s = _settings(
        environment="prod",
        session_secret="y" * 40,
        webauthn_rp_id="example.com",
        webauthn_origins="https://app.example.com,https://example.com",
        mfa_encryption_key="k" * 32,
    )
    assert s.webauthn_rp_id == "example.com"


# --- readme-selfhost-rewrite Task 1: localhost defaults + explicit prod guard ---


def test_prod_refuses_localhost_webauthn_default() -> None:
    """Self-hoster forgets WEBAUTHN_* in prod -> loud boot failure naming both
    vars, not silently broken passkeys bound to a wrong RP-ID."""
    with pytest.raises(ValueError) as exc:
        _settings(environment="prod", session_secret="x" * 40)
    msg = str(exc.value)
    assert "WEBAUTHN_RP_ID" in msg
    assert "WEBAUTHN_ORIGINS" in msg


def test_prod_boots_with_real_rp_id_and_origins() -> None:
    # mfa_encryption_key set — a bare prod Settings() now also trips
    # _check_mfa_key_hardening (security-audit wave, 2026-07-22); see
    # tests/unit/test_config_mfa_key.py for that guard in isolation.
    s = _settings(
        environment="prod",
        session_secret="x" * 40,
        webauthn_rp_id="risk.example.com",
        webauthn_origins="https://risk.example.com",
        mfa_encryption_key="k" * 32,
    )
    assert s.webauthn_rp_id == "risk.example.com"


def test_dev_and_test_accept_localhost_defaults() -> None:
    for env in ("dev", "test"):
        s = _settings(environment=env, session_secret="y" * 20)
        assert s.webauthn_rp_id == "localhost"
        assert s.webauthn_origins == "http://localhost:8000"
