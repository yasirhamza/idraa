"""Guard: ``Settings`` must refuse to boot in prod without MFA_ENCRYPTION_KEY.

Failure mode we are defending against: an operator deploys to prod without
setting ``MFA_ENCRYPTION_KEY``. ``services/mfa_crypto.py`` silently derives
the TOTP-secret encryption key from ``SESSION_SECRET`` instead — a
key-separation violation — and a later, routine ``SESSION_SECRET`` rotation
then permanently bricks every already-stored TOTP secret.
"""

from __future__ import annotations

import pytest

from idraa.config import Settings


def _settings(**env: str) -> Settings:
    return Settings(_env_file=None, **env)  # type: ignore[arg-type,call-arg]


def test_prod_rejects_empty_mfa_key() -> None:
    """Bare prod Settings (no MFA_ENCRYPTION_KEY) must refuse to boot, naming
    the var — same real-domain WebAuthn kwargs as
    tests/unit/test_config_webauthn.py::test_prod_boots_with_real_rp_id_and_origins
    so this test isolates the MFA-key guard rather than tripping the
    WebAuthn hardening guard first."""
    with pytest.raises(ValueError, match="MFA_ENCRYPTION_KEY") as exc:
        _settings(
            environment="prod",
            session_secret="x" * 40,
            webauthn_rp_id="risk.example.com",
            webauthn_origins="https://risk.example.com",
        )
    msg = str(exc.value)
    assert "MFA_ENCRYPTION_KEY" in msg


def test_prod_boots_with_mfa_key_set() -> None:
    s = _settings(
        environment="prod",
        session_secret="x" * 40,
        webauthn_rp_id="risk.example.com",
        webauthn_origins="https://risk.example.com",
        mfa_encryption_key="k" * 32,
    )
    assert s.mfa_encryption_key == "k" * 32


def test_dev_boots_with_empty_mfa_key() -> None:
    """dev keeps the SESSION_SECRET-derived fallback (services/mfa_crypto.py:32)."""
    s = _settings(environment="dev", session_secret="x" * 16)
    assert s.mfa_encryption_key is None


def test_prod_rejects_short_mfa_key() -> None:
    """prod must refuse a too-short MFA_ENCRYPTION_KEY, mirroring the
    SESSION_SECRET length floor in _check_secret_hardening — both are
    Fernet/HKDF key material and a short value is weak key material
    regardless of which var it lives in."""
    with pytest.raises(ValueError, match="32") as exc:
        _settings(
            environment="prod",
            session_secret="x" * 40,
            webauthn_rp_id="risk.example.com",
            webauthn_origins="https://risk.example.com",
            mfa_encryption_key="k" * 8,
        )
    msg = str(exc.value)
    assert "MFA_ENCRYPTION_KEY" in msg
    assert "32" in msg


def test_prod_rejects_mfa_key_identical_to_session_secret() -> None:
    """prod must refuse MFA_ENCRYPTION_KEY == SESSION_SECRET even when both
    independently satisfy the length floor — reusing the session secret
    re-creates the exact rotation trap this guard exists to prevent:
    rotating SESSION_SECRET would then silently re-derive (rather than keep
    stable) the Fernet key protecting stored TOTP secrets, bricking them."""
    shared = "s" * 40
    with pytest.raises(ValueError, match="DISTINCT") as exc:
        _settings(
            environment="prod",
            session_secret=shared,
            webauthn_rp_id="risk.example.com",
            webauthn_origins="https://risk.example.com",
            mfa_encryption_key=shared,
        )
    msg = str(exc.value)
    assert "MFA_ENCRYPTION_KEY" in msg
    assert "SESSION_SECRET" in msg
    assert "DISTINCT" in msg
