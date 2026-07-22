# tests/contracts/test_mfa_field_sync.py
from __future__ import annotations

from typing import Any

from sqlalchemy import inspect
from sqlalchemy.orm import Mapper

from idraa.models.mfa import RecoveryCode, UserTotp, WebAuthnCredential


def _cols(model: type) -> set[str]:
    # `inspect(model)` on a bare `type` arg can't disambiguate sqlalchemy's
    # overloaded `inspect()` (mirrors the same-shaped call in
    # tests/contracts/test_orm_sme_columns_subset_of_dto_fields.py, which
    # works because it inspects a concrete class literal, not a generic
    # `type` parameter) — explicit annotation resolves mypy's
    # "Need type annotation" strict-mode error.
    insp: Mapper[Any] = inspect(model)
    return {c.key for c in insp.columns}


def test_webauthn_credential_columns_are_expected() -> None:
    assert _cols(WebAuthnCredential) == {
        "id",
        "user_id",
        "credential_id",
        "public_key",
        "sign_count",
        "transports",
        "aaguid",
        "nickname",
        "last_used_at",
        "created_at",
        "updated_at",
    }


def test_user_totp_columns_are_expected() -> None:
    assert _cols(UserTotp) == {"user_id", "secret_encrypted", "confirmed_at", "created_at"}


def test_recovery_code_columns_are_expected() -> None:
    assert _cols(RecoveryCode) == {"id", "user_id", "code_hash", "used_at", "created_at"}
