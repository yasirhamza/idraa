"""Password hashing smoke tests — Task 1.1.3."""

from __future__ import annotations

import uuid

from idraa.services.auth import (
    hash_password,
    sign_session_id,
    unsign_session_id,
    verify_password,
)


def test_hash_and_verify() -> None:
    h = hash_password("correct horse battery staple")
    assert h != "correct horse battery staple"
    assert verify_password("correct horse battery staple", h)
    assert not verify_password("wrong", h)


def test_hash_is_argon2() -> None:
    h = hash_password("x")
    assert h.startswith("$argon2")


def test_sign_unsign_round_trip() -> None:
    sid = uuid.uuid4()
    assert unsign_session_id(sign_session_id(sid)) == sid


def test_unsign_rejects_tampered_cookie() -> None:
    assert unsign_session_id("not-a-valid-signed-payload") is None
