"""MFA secret handling: TOTP-secret encryption (Fernet) + recovery codes.

TOTP verification needs the plaintext secret, so it cannot be hashed — it is
symmetric-encrypted at rest with a key derived (HKDF-SHA256) from
MFA_ENCRYPTION_KEY, falling back to SESSION_SECRET. Recovery codes ARE
one-way (Argon2, via the shared password context).

OPERATOR NOTE (plan-gate N3): if MFA_ENCRYPTION_KEY is unset, the key derives
from SESSION_SECRET — so rotating SESSION_SECRET makes every stored TOTP secret
undecryptable and locks all TOTP users out of that factor. Deployments that
rotate SESSION_SECRET MUST set a distinct, stable MFA_ENCRYPTION_KEY. (Key
versioning via MultiFernet + a key-id prefix is a documented post-P1 follow-up.)
"""

from __future__ import annotations

import base64
import secrets

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from idraa.config import get_settings
from idraa.services.auth import hash_password, verify_password

_HKDF_INFO = b"idraa-mfa-totp-secret-v1"


def _fernet() -> Fernet:
    settings = get_settings()
    key_material = (settings.mfa_encryption_key or settings.session_secret).encode("utf-8")
    # Fresh HKDF per call — an HKDF instance is single-use (derive() once).
    derived = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=_HKDF_INFO).derive(
        key_material
    )
    return Fernet(base64.urlsafe_b64encode(derived))


def encrypt_totp_secret(plain: str) -> str:
    return _fernet().encrypt(plain.encode("utf-8")).decode("ascii")


def decrypt_totp_secret(ciphertext: str) -> str:
    return _fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")


def generate_recovery_codes(n: int = 10) -> list[str]:
    """Return n unique one-time codes formatted xxxxx-xxxxx (hex, unambiguous)."""
    codes: set[str] = set()
    while len(codes) < n:
        raw = secrets.token_hex(5)  # 10 hex chars
        codes.add(f"{raw[:5]}-{raw[5:]}")
    return sorted(codes)


def hash_recovery_code(code: str) -> str:
    return hash_password(code)


def verify_recovery_code(code: str, code_hash: str) -> bool:
    return verify_password(code, code_hash)
