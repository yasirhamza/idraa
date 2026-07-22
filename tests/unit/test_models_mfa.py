# tests/unit/test_models_mfa.py
from __future__ import annotations

import uuid

from idraa.models.mfa import RecoveryCode, UserTotp, WebAuthnCredential
from idraa.models.user import User


def test_webauthn_credential_construct_sets_defaults() -> None:
    uid = uuid.uuid4()
    cred = WebAuthnCredential(
        user_id=uid,
        credential_id=b"\x01\x02\x03",
        public_key=b"\xaa\xbb",
        nickname="YubiKey",
    )
    assert cred.id is not None  # IdMixin populated in __init__
    # sign_count default=0 is a FLUSH-time SQLAlchemy default, NOT populated at
    # __init__ (only IdMixin/TimestampMixin fields are, via the instrument_class
    # init hook). On an unflushed instance cred.sign_count is None — assert the
    # column default instead.
    assert WebAuthnCredential.__table__.c.sign_count.default.arg == 0
    assert cred.created_at is not None  # TimestampMixin populated in __init__
    assert cred.last_used_at is None


def test_user_totp_and_recovery_code_shape() -> None:
    uid = uuid.uuid4()
    totp = UserTotp(user_id=uid, secret_encrypted="enc")
    assert totp.confirmed_at is None
    rc = RecoveryCode(user_id=uid, code_hash="h")
    assert rc.id is not None
    assert rc.used_at is None


def test_user_has_nullable_mfa_enrolled_at() -> None:
    assert "mfa_enrolled_at" in User.__table__.columns
    assert User.__table__.columns["mfa_enrolled_at"].nullable is True
    assert "failed_login_count" in User.__table__.columns
    assert "locked_until" in User.__table__.columns
