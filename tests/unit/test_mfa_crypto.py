# tests/unit/test_mfa_crypto.py
from __future__ import annotations

import pytest
from cryptography.fernet import InvalidToken

import idraa.config as config
from idraa.services import mfa_crypto


def _reset(monkeypatch, **env):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    config.reset_for_tests()


def test_totp_secret_round_trip(monkeypatch) -> None:
    _reset(monkeypatch, ENVIRONMENT="test", SESSION_SECRET="s" * 32)
    ct = mfa_crypto.encrypt_totp_secret("JBSWY3DPEHPK3PXP")
    assert ct != "JBSWY3DPEHPK3PXP"  # actually encrypted
    assert mfa_crypto.decrypt_totp_secret(ct) == "JBSWY3DPEHPK3PXP"


def test_encryption_is_key_isolated(monkeypatch) -> None:
    _reset(monkeypatch, ENVIRONMENT="test", SESSION_SECRET="s" * 32, MFA_ENCRYPTION_KEY="k" * 32)
    ct = mfa_crypto.encrypt_totp_secret("SECRETSECRET")
    assert mfa_crypto.decrypt_totp_secret(ct) == "SECRETSECRET"  # same key round-trips
    # A DIFFERENT key cannot decrypt it (proves the key actually gates decryption).
    _reset(monkeypatch, ENVIRONMENT="test", SESSION_SECRET="s" * 32, MFA_ENCRYPTION_KEY="j" * 32)
    with pytest.raises(InvalidToken):
        mfa_crypto.decrypt_totp_secret(ct)


def test_recovery_codes_generated_hashed_and_verified() -> None:
    codes = mfa_crypto.generate_recovery_codes()
    assert len(codes) == 10
    assert len(set(codes)) == 10  # unique
    h = mfa_crypto.hash_recovery_code(codes[0])
    assert h != codes[0]  # hashed, not plaintext
    assert mfa_crypto.verify_recovery_code(codes[0], h) is True
    assert mfa_crypto.verify_recovery_code("wrong-code", h) is False
