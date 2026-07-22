# tests/unit/test_auth_mfa_tokens.py
from __future__ import annotations

import time
import uuid

import idraa.config as config
from idraa.services import auth


def _reset(monkeypatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("SESSION_SECRET", "s" * 32)
    config.reset_for_tests()


def test_mfa_pending_round_trip(monkeypatch) -> None:
    _reset(monkeypatch)
    uid = uuid.uuid4()
    token = auth.sign_mfa_pending(uid)
    assert auth.load_mfa_pending(token) == uid


def test_mfa_pending_expires(monkeypatch) -> None:
    _reset(monkeypatch)
    token = auth.sign_mfa_pending(uuid.uuid4())
    time.sleep(1)
    assert auth.load_mfa_pending(token, max_age=0) is None


def test_mfa_pending_rejects_tampered(monkeypatch) -> None:
    _reset(monkeypatch)
    assert auth.load_mfa_pending("garbage.token.value") is None


def test_webauthn_challenge_round_trip(monkeypatch) -> None:
    _reset(monkeypatch)
    token = auth.sign_webauthn_challenge("Y2hhbGxlbmdl")
    assert auth.load_webauthn_challenge(token) == "Y2hhbGxlbmdl"


def test_pending_and_challenge_salts_are_not_interchangeable(monkeypatch) -> None:
    _reset(monkeypatch)
    uid = uuid.uuid4()
    pending = auth.sign_mfa_pending(uid)
    # A pending token must NOT validate as a challenge token (distinct salts).
    assert auth.load_webauthn_challenge(pending) is None
