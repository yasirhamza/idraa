"""Advisory C6: the UAT Basic-auth password gets prod boot hardening.

It used to be read from os.environ by the middleware, so a 4-character
password booted silently while SESSION_SECRET / MFA_ENCRYPTION_KEY were
length-checked.
"""

from __future__ import annotations

import pytest

from idraa.config import Settings

_PROD = {
    "environment": "prod",
    "session_secret": "x" * 32,
    "webauthn_rp_id": "example.com",
    "webauthn_origins": "https://example.com",
    "mfa_encryption_key": "k" * 32,
}


def test_prod_short_uat_password_rejected() -> None:
    with pytest.raises(ValueError, match="UAT_BASIC_AUTH_PASSWORD must be at least 16"):
        Settings(**_PROD, uat_basic_auth_user="uat", uat_basic_auth_password="abcd")


def test_prod_uat_password_without_user_rejected() -> None:
    with pytest.raises(ValueError, match="UAT_BASIC_AUTH_USER is empty"):
        Settings(**_PROD, uat_basic_auth_password="p" * 20)


def test_prod_uat_password_equal_to_user_rejected() -> None:
    with pytest.raises(ValueError, match="must differ"):
        Settings(**_PROD, uat_basic_auth_user="u" * 20, uat_basic_auth_password="u" * 20)


def test_prod_strong_uat_pre_gate_ok() -> None:
    s = Settings(**_PROD, uat_basic_auth_user="uat", uat_basic_auth_password="p" * 22)
    assert s.uat_basic_auth_password == "p" * 22


def test_prod_pre_gate_unset_is_allowed() -> None:
    # Self-hosted installs without the pre-gate stay supported.
    assert Settings(**_PROD).uat_basic_auth_password is None


def test_dev_short_uat_password_not_enforced() -> None:
    s = Settings(environment="test", uat_basic_auth_user="u", uat_basic_auth_password="abcd")
    assert s.uat_basic_auth_password == "abcd"


def test_middleware_reads_the_validated_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """The factory's default now comes from Settings, not a raw os.environ read."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from idraa import config
    from idraa.middleware.uat_basic_auth import uat_basic_auth_factory

    monkeypatch.setenv("UAT_BASIC_AUTH_USER", "uat")
    monkeypatch.setenv("UAT_BASIC_AUTH_PASSWORD", "p" * 20)
    config.reset_for_tests()
    try:
        app = FastAPI()
        app.get("/x")(lambda: {"ok": True})
        app.middleware("http")(uat_basic_auth_factory())
        client = TestClient(app)
        assert client.get("/x").status_code == 401
        assert client.get("/x", auth=("uat", "p" * 20)).status_code == 200
    finally:
        config.reset_for_tests()
