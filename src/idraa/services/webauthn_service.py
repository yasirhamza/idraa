"""Thin wrapper over py_webauthn for the passkey ceremonies.

RP-ID / RP-name / origins come from Settings (config-driven, never hardcoded).
Challenges are returned base64url-encoded so callers can stash them in a
signed cookie; they are handed back to verify_* on completion.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any

from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from idraa.config import get_settings


@dataclass(frozen=True)
class RegisteredCredential:
    credential_id: bytes
    public_key: bytes
    sign_count: int
    aaguid: str
    transports: str | None


def registration_options(
    user_id: uuid.UUID,
    user_email: str,
    user_display_name: str,
    existing_credential_ids: list[bytes],
) -> tuple[str, str]:
    s = get_settings()
    options = generate_registration_options(
        rp_id=s.webauthn_rp_id,
        rp_name=s.webauthn_rp_name,
        user_id=user_id.bytes,
        user_name=user_email,
        user_display_name=user_display_name,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.REQUIRED,
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
        exclude_credentials=[
            PublicKeyCredentialDescriptor(id=cid) for cid in existing_credential_ids
        ],
    )
    return options_to_json(options), bytes_to_base64url(options.challenge)


def verify_registration(
    credential: dict[str, Any] | str, challenge_b64url: str
) -> RegisteredCredential:
    s = get_settings()
    raw = credential if isinstance(credential, str) else json.dumps(credential)
    parsed = json.loads(credential) if isinstance(credential, str) else credential
    v = verify_registration_response(
        credential=raw,
        expected_challenge=base64url_to_bytes(challenge_b64url),
        expected_rp_id=s.webauthn_rp_id,
        expected_origin=s.webauthn_origin_list,
        require_user_verification=True,
    )
    transports = None
    t = parsed.get("response", {}).get("transports")
    if isinstance(t, list) and t:
        transports = ",".join(str(x) for x in t)
    return RegisteredCredential(
        credential_id=v.credential_id,
        public_key=v.credential_public_key,
        sign_count=v.sign_count,
        aaguid=v.aaguid,
        transports=transports,
    )


def authentication_options(
    allow_credential_ids: list[bytes] | None = None,
) -> tuple[str, str]:
    """Login (usernameless, default) or step-up (scoped) assertion options.

    An empty allow-list means discoverable/usernameless (the login flow).
    Step-up passes the CURRENT user's credential ids so the browser only
    offers that user's passkeys.
    """
    s = get_settings()
    options = generate_authentication_options(
        rp_id=s.webauthn_rp_id,
        allow_credentials=[
            PublicKeyCredentialDescriptor(id=cid) for cid in (allow_credential_ids or [])
        ],
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    return options_to_json(options), bytes_to_base64url(options.challenge)


def verify_authentication(
    credential: dict[str, Any] | str,
    challenge_b64url: str,
    public_key: bytes,
    current_sign_count: int,
) -> int:
    s = get_settings()
    raw = credential if isinstance(credential, str) else json.dumps(credential)
    v = verify_authentication_response(
        credential=raw,
        expected_challenge=base64url_to_bytes(challenge_b64url),
        expected_rp_id=s.webauthn_rp_id,
        expected_origin=s.webauthn_origin_list,
        credential_public_key=public_key,
        credential_current_sign_count=current_sign_count,
        require_user_verification=True,
    )
    return v.new_sign_count


def sign_count_ok(stored: int, new: int) -> bool:
    """Reject a non-increasing counter (cloned authenticator). The 0/0 case is
    an authenticator that never increments — allowed."""
    if stored == 0 and new == 0:
        return True
    return new > stored


def parse_raw_id(credential: dict[str, Any] | str) -> bytes:
    parsed = json.loads(credential) if isinstance(credential, str) else credential
    return base64url_to_bytes(parsed["rawId"])
