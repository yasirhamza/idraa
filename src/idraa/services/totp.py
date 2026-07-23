"""TOTP (RFC 6238) provisioning + verification + server-rendered QR (SVG)."""

from __future__ import annotations

import hmac
import io
import time

import pyotp
import segno


def provision_secret() -> str:
    return pyotp.random_base32()


def totp_uri(secret: str, account_name: str, issuer: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=account_name, issuer_name=issuer)


def verify_totp_step(
    secret: str,
    code: str,
    *,
    valid_window: int = 1,
    after_step: int | None = None,
    for_time: float | None = None,
) -> int | None:
    """Return the matched 30s step counter, or None (no match, or step <= after_step).

    N4 (idraa#81): callers use ``after_step`` (``UserTotp.last_used_step``) to
    reject replay-within-window — a previously-accepted code (or an earlier
    step) must never verify again, even though it is still inside pyotp's
    +/- valid_window tolerance.
    """
    code = code.strip()
    t = time.time() if for_time is None else for_time
    totp = pyotp.TOTP(secret)
    current = int(t // 30)
    for offset in range(-valid_window, valid_window + 1):
        step = current + offset
        # Encode to bytes: hmac.compare_digest raises TypeError on non-ASCII str
        # operands. str.encode never raises, keeps the compare constant-time, and
        # a non-ASCII code simply doesn't match -> None -> clean 400 (not a 500).
        if hmac.compare_digest(totp.generate_otp(step).encode("utf-8"), code.encode("utf-8")):
            if after_step is not None and step <= after_step:
                return None
            return step
    return None


def verify_totp(secret: str, code: str, valid_window: int = 1) -> bool:
    """Verify a 6-digit code (+/- valid_window steps). Delegates to
    verify_totp_step so there is a single acceptance-window definition."""
    return verify_totp_step(secret, code, valid_window=valid_window) is not None


def totp_qr_svg(uri: str) -> str:
    """Render the otpauth URI as an inline SVG string (no JS QR lib, no PNG)."""
    buf = io.BytesIO()
    segno.make(uri).save(buf, kind="svg", scale=5, border=2)
    return buf.getvalue().decode("utf-8")
