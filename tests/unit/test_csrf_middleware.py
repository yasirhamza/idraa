"""Unit tests for CSRF token helpers — session-bound double-submit signed cookie.

Covers pure functions only; the middleware + request/response behavior live in
``tests/integration/test_csrf_integration.py``. The property-based nonce swap
check below is the closest unit-level approximation of "uses hmac.compare_digest"
that does not monkey-patch or grep the module source: it exercises the invariant
that only the exact (nonce, sig) pairing verifies, which is what constant-time
comparison guarantees for correctness (timing is not observable from a unit
test).

GHSA-46jj-823j-mjj9 B4 (session binding): the token now signs
``nonce || binding`` under a KEY DERIVED from ``session_secret``
(``derive_csrf_key``), not the raw secret — see ``csrf_binding`` for how the
binding is computed from a request's session cookie. These tests build
``binding`` directly via ``csrf_binding`` against minimal Starlette
``Request`` objects (headers-only scope — ``Request.cookies`` only reads
``self.headers``, so no method/path/etc. is needed) rather than duplicating
the hashing logic inline.
"""

from __future__ import annotations

import hashlib
import hmac
import re

import pytest
from starlette.requests import Request

from idraa.middleware.csrf import (
    csrf_binding,
    derive_csrf_key,
    generate_csrf_token,
    verify_csrf_token,
)

SECRET = "a" * 32
SESSION_COOKIE_NAME = "idraa_session"


def _request_with_cookie_header(raw: bytes | None) -> Request:
    """Build a minimal Request carrying (or omitting) a raw Cookie header."""
    headers = [(b"cookie", raw)] if raw is not None else []
    return Request({"type": "http", "headers": headers})


def _binding_for(raw_cookie_header: bytes | None) -> bytes:
    return csrf_binding(_request_with_cookie_header(raw_cookie_header), SESSION_COOKIE_NAME)


KEY = derive_csrf_key(SECRET)
ANON_BINDING = _binding_for(None)


# --- csrf_binding: the accessor itself (Sec-I1, Arch-I3.iv) ---


def test_binding_absent_cookie_is_anon() -> None:
    assert _binding_for(None) == ANON_BINDING


def test_binding_empty_cookie_is_anon() -> None:
    """Mirrors session.py:45's `if signed:` truthy check — an empty value
    binds exactly like no cookie at all."""
    assert _binding_for(b"idraa_session=") == ANON_BINDING


def test_binding_present_cookie_differs_from_anon() -> None:
    assert _binding_for(b"idraa_session=abc") != ANON_BINDING


def test_binding_is_deterministic_hash_of_raw_cookie_value() -> None:
    b1 = _binding_for(b"idraa_session=abc")
    b2 = _binding_for(b"idraa_session=abc")
    assert b1 == b2
    b3 = _binding_for(b"idraa_session=xyz")
    assert b1 != b3


def test_binding_is_32_bytes_either_way() -> None:
    assert len(ANON_BINDING) == 32
    assert len(_binding_for(b"idraa_session=abc")) == 32


def test_binding_duplicate_cookies_take_last_starlette_semantics() -> None:
    """The SAME accessor SessionMiddleware authenticates with
    (``request.cookies.get(name)``, ``session.py:44``) is last-wins on a
    duplicate ``Cookie`` header — pin that here at the unit level; the
    integration-level regression (a real duplicate-cookie request) lives in
    ``test_csrf_integration.py``."""
    dup = _binding_for(b"idraa_session=A; idraa_session=B")
    last = _binding_for(b"idraa_session=B")
    first = _binding_for(b"idraa_session=A")
    assert dup == last
    assert dup != first


# --- generate/verify: token shape + binding enforcement ---


def test_token_format_is_nonce_dot_sig() -> None:
    token = generate_csrf_token(KEY, ANON_BINDING)
    # Shape: "<nonce_hex>.<sig_hex>". 32 random bytes hex -> 64 chars, HMAC-SHA256
    # digest hex -> 64 chars. Exact lengths prevent silent drift of the scheme.
    assert re.fullmatch(r"[0-9a-f]{64}\.[0-9a-f]{64}", token), token


def test_verify_accepts_freshly_generated_token_under_its_own_binding() -> None:
    token = generate_csrf_token(KEY, ANON_BINDING)
    assert verify_csrf_token(token, KEY, ANON_BINDING) is True


def test_verify_rejects_same_token_under_a_different_binding() -> None:
    """The B4 fix itself: a token is only valid for the binding it was
    minted under."""
    token = generate_csrf_token(KEY, ANON_BINDING)
    other_binding = _binding_for(b"idraa_session=someone-elses-session")
    assert verify_csrf_token(token, KEY, other_binding) is False


def test_anon_bound_token_only_verifies_under_anon_binding() -> None:
    token = generate_csrf_token(KEY, ANON_BINDING)
    session_binding = _binding_for(b"idraa_session=real-session-cookie")
    assert verify_csrf_token(token, KEY, ANON_BINDING) is True
    assert verify_csrf_token(token, KEY, session_binding) is False


def test_verify_rejects_tampered_signature() -> None:
    token = generate_csrf_token(KEY, ANON_BINDING)
    nonce, sig = token.split(".")
    # Flip the first hex character of the signature deterministically.
    flipped = ("0" if sig[0] != "0" else "1") + sig[1:]
    assert verify_csrf_token(f"{nonce}.{flipped}", KEY, ANON_BINDING) is False


def test_verify_rejects_tampered_nonce() -> None:
    token = generate_csrf_token(KEY, ANON_BINDING)
    nonce, sig = token.split(".")
    flipped_nonce = ("0" if nonce[0] != "0" else "1") + nonce[1:]
    assert verify_csrf_token(f"{flipped_nonce}.{sig}", KEY, ANON_BINDING) is False


def test_verify_rejects_malformed_missing_dot() -> None:
    assert verify_csrf_token("nodothere", KEY, ANON_BINDING) is False


def test_verify_rejects_malformed_empty_parts() -> None:
    assert verify_csrf_token(".", KEY, ANON_BINDING) is False
    assert verify_csrf_token("abc.", KEY, ANON_BINDING) is False
    assert verify_csrf_token(".abc", KEY, ANON_BINDING) is False


def test_verify_rejects_empty_token() -> None:
    assert verify_csrf_token("", KEY, ANON_BINDING) is False


def test_verify_rejects_non_hex_parts() -> None:
    # Valid-shape length but non-hex — ValueError in bytes.fromhex must be caught.
    bad = ("z" * 64) + "." + ("f" * 64)
    assert verify_csrf_token(bad, KEY, ANON_BINDING) is False


def test_verify_rejects_nonce_not_32_bytes() -> None:
    """Design: nonce must be exactly 32 bytes; verify rejects any other
    length even when the hex is well-formed and the signature is computed
    correctly over that (wrong-length) nonce."""
    short_hex = "ab" * 16  # 16 bytes
    short_sig = hmac.new(KEY, bytes.fromhex(short_hex) + ANON_BINDING, hashlib.sha256).hexdigest()
    assert verify_csrf_token(f"{short_hex}.{short_sig}", KEY, ANON_BINDING) is False

    long_hex = "ab" * 48  # 48 bytes
    long_sig = hmac.new(KEY, bytes.fromhex(long_hex) + ANON_BINDING, hashlib.sha256).hexdigest()
    assert verify_csrf_token(f"{long_hex}.{long_sig}", KEY, ANON_BINDING) is False


def test_verify_rejects_wrong_key() -> None:
    token = generate_csrf_token(KEY, ANON_BINDING)
    other_key = derive_csrf_key("b" * 32)
    assert verify_csrf_token(token, other_key, ANON_BINDING) is False


def test_old_format_token_raw_secret_no_binding_rejected() -> None:
    """Key separation (Spec-N-c/Arch-I3.v): a token signed the OLD (pre-B4)
    way — HMAC-SHA256(raw secret, nonce) directly, no derive_csrf_key, no
    binding component — must never verify under the new scheme, even for a
    correctly-shaped 32-byte nonce signed with the SAME underlying secret.
    ``derive_csrf_key``'s HMAC-based key separation makes this true by
    construction; this test pins it as a named regression.
    """
    nonce_hex = "ab" * 32
    nonce_bytes = bytes.fromhex(nonce_hex)
    old_sig = hmac.new(SECRET.encode("utf-8"), nonce_bytes, hashlib.sha256).hexdigest()
    old_token = f"{nonce_hex}.{old_sig}"
    assert verify_csrf_token(old_token, KEY, ANON_BINDING) is False


def test_cross_nonce_signature_does_not_validate() -> None:
    """A signature computed for nonce A must not verify against nonce B.

    Indirect check of "HMAC is actually per-nonce", which is the correctness
    half of the hmac.compare_digest contract. (The constant-time half is a
    timing property unit tests cannot observe.)
    """
    token_a = generate_csrf_token(KEY, ANON_BINDING)
    token_b = generate_csrf_token(KEY, ANON_BINDING)
    nonce_a, _sig_a = token_a.split(".")
    _nonce_b, sig_b = token_b.split(".")
    # Splice: nonce from A, signature from B. Must not verify.
    spliced = f"{nonce_a}.{sig_b}"
    assert verify_csrf_token(spliced, KEY, ANON_BINDING) is False


def test_generated_tokens_are_unique() -> None:
    tokens = {generate_csrf_token(KEY, ANON_BINDING) for _ in range(50)}
    # Nonce is 32 random bytes — collisions in 50 samples would indicate a
    # broken RNG. Probability of accidental failure is ~0.
    assert len(tokens) == 50


def test_derive_csrf_key_rejects_empty_secret() -> None:
    """Defense in depth: callers that bypass ``Settings`` (direct construction
    in scripts/tests) must not be able to silently sign with an empty key."""
    with pytest.raises(ValueError, match=r"non-empty session_secret"):
        derive_csrf_key("")


def test_derive_csrf_key_rejects_whitespace_only_secret() -> None:
    with pytest.raises(ValueError, match=r"non-empty session_secret"):
        derive_csrf_key("   \t\n")


def test_derive_csrf_key_is_deterministic_and_secret_specific() -> None:
    assert derive_csrf_key(SECRET) == derive_csrf_key(SECRET)
    assert derive_csrf_key(SECRET) != derive_csrf_key("b" * 32)
