"""Unit tests for CSRF token helpers — stateless double-submit signed-cookie.

Covers pure functions only; the middleware + request/response behavior live in
``tests/integration/test_csrf_integration.py``. The property-based nonce swap
check below is the closest unit-level approximation of "uses hmac.compare_digest"
that does not monkey-patch or grep the module source: it exercises the invariant
that only the exact (nonce, sig) pairing verifies, which is what constant-time
comparison guarantees for correctness (timing is not observable from a unit
test).
"""

from __future__ import annotations

import re

import pytest

from idraa.middleware.csrf import (
    generate_csrf_token,
    verify_csrf_token,
)

SECRET = "a" * 32


def test_token_format_is_nonce_dot_sig() -> None:
    token = generate_csrf_token(SECRET)
    # Shape: "<nonce_hex>.<sig_hex>". 32 random bytes hex -> 64 chars, HMAC-SHA256
    # digest hex -> 64 chars. Exact lengths prevent silent drift of the scheme.
    assert re.fullmatch(r"[0-9a-f]{64}\.[0-9a-f]{64}", token), token


def test_verify_accepts_freshly_generated_token() -> None:
    token = generate_csrf_token(SECRET)
    assert verify_csrf_token(token, SECRET) is True


def test_verify_rejects_tampered_signature() -> None:
    token = generate_csrf_token(SECRET)
    nonce, sig = token.split(".")
    # Flip the first hex character of the signature deterministically.
    flipped = ("0" if sig[0] != "0" else "1") + sig[1:]
    assert verify_csrf_token(f"{nonce}.{flipped}", SECRET) is False


def test_verify_rejects_tampered_nonce() -> None:
    token = generate_csrf_token(SECRET)
    nonce, sig = token.split(".")
    flipped_nonce = ("0" if nonce[0] != "0" else "1") + nonce[1:]
    assert verify_csrf_token(f"{flipped_nonce}.{sig}", SECRET) is False


def test_verify_rejects_malformed_missing_dot() -> None:
    assert verify_csrf_token("nodothere", SECRET) is False


def test_verify_rejects_malformed_empty_parts() -> None:
    assert verify_csrf_token(".", SECRET) is False
    assert verify_csrf_token("abc.", SECRET) is False
    assert verify_csrf_token(".abc", SECRET) is False


def test_verify_rejects_empty_token() -> None:
    assert verify_csrf_token("", SECRET) is False


def test_verify_rejects_non_hex_parts() -> None:
    # Valid-shape length but non-hex — ValueError in bytes.fromhex must be caught.
    bad = ("z" * 64) + "." + ("f" * 64)
    assert verify_csrf_token(bad, SECRET) is False


def test_verify_rejects_wrong_secret() -> None:
    token = generate_csrf_token(SECRET)
    assert verify_csrf_token(token, SECRET + "different") is False


def test_cross_nonce_signature_does_not_validate() -> None:
    """A signature computed for nonce A must not verify against nonce B.

    Indirect check of "HMAC is actually per-nonce", which is the correctness
    half of the hmac.compare_digest contract. (The constant-time half is a
    timing property unit tests cannot observe.)
    """
    token_a = generate_csrf_token(SECRET)
    token_b = generate_csrf_token(SECRET)
    nonce_a, _sig_a = token_a.split(".")
    _nonce_b, sig_b = token_b.split(".")
    # Splice: nonce from A, signature from B. Must not verify.
    spliced = f"{nonce_a}.{sig_b}"
    assert verify_csrf_token(spliced, SECRET) is False


def test_generated_tokens_are_unique() -> None:
    tokens = {generate_csrf_token(SECRET) for _ in range(50)}
    # Nonce is 32 random bytes — collisions in 50 samples would indicate a
    # broken RNG. Probability of accidental failure is ~0.
    assert len(tokens) == 50


def test_generate_rejects_empty_secret() -> None:
    """Defense in depth: callers that bypass ``Settings`` (direct construction
    in scripts/tests) must not be able to silently sign with an empty key."""
    with pytest.raises(ValueError, match=r"non-empty session_secret"):
        generate_csrf_token("")


def test_generate_rejects_whitespace_only_secret() -> None:
    with pytest.raises(ValueError, match=r"non-empty session_secret"):
        generate_csrf_token("   \t\n")
