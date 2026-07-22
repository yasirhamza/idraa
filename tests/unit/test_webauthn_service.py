from __future__ import annotations

import json
import uuid

from webauthn.helpers import bytes_to_base64url

import idraa.config as config
from idraa.services import webauthn_service as ws


def _reset(monkeypatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("SESSION_SECRET", "s" * 32)
    monkeypatch.setenv("WEBAUTHN_RP_ID", "localhost")
    monkeypatch.setenv("WEBAUTHN_ORIGINS", "http://localhost")
    config.reset_for_tests()


def test_registration_options_require_uv_and_resident_key(monkeypatch) -> None:
    _reset(monkeypatch)
    options_json, challenge = ws.registration_options(
        uuid.uuid4(), "a@b.c", "A B", existing_credential_ids=[]
    )
    opts = json.loads(options_json)
    assert opts["rp"]["id"] == "localhost"
    assert opts["authenticatorSelection"]["userVerification"] == "required"
    assert opts["authenticatorSelection"]["residentKey"] == "required"
    assert isinstance(challenge, str) and len(challenge) > 0


def test_registration_options_excludes_existing(monkeypatch) -> None:
    _reset(monkeypatch)
    existing = b"\x01\x02\x03\x04"
    options_json, _ = ws.registration_options(
        uuid.uuid4(), "a@b.c", "A B", existing_credential_ids=[existing]
    )
    opts = json.loads(options_json)
    ids = {c["id"] for c in opts.get("excludeCredentials", [])}
    assert bytes_to_base64url(existing) in ids


def test_authentication_options_usernameless(monkeypatch) -> None:
    _reset(monkeypatch)
    options_json, challenge = ws.authentication_options()
    opts = json.loads(options_json)
    assert opts["userVerification"] == "required"
    assert opts.get("allowCredentials", []) == []
    assert len(challenge) > 0


def test_sign_count_ok() -> None:
    assert ws.sign_count_ok(5, 6) is True
    assert ws.sign_count_ok(5, 5) is False  # non-increasing → cloned
    assert ws.sign_count_ok(5, 4) is False
    assert ws.sign_count_ok(0, 0) is True  # authenticator that never counts


def test_parse_raw_id() -> None:
    raw = b"\xde\xad\xbe\xef"
    body = json.dumps({"rawId": bytes_to_base64url(raw), "id": bytes_to_base64url(raw)})
    assert ws.parse_raw_id(body) == raw
