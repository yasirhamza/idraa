"""TOTP (RFC 6238) provisioning + verification + server-rendered QR (SVG)."""

from __future__ import annotations

import io

import pyotp
import segno


def provision_secret() -> str:
    return str(pyotp.random_base32())


def totp_uri(secret: str, account_name: str, issuer: str) -> str:
    return str(pyotp.TOTP(secret).provisioning_uri(name=account_name, issuer_name=issuer))


def verify_totp(secret: str, code: str, valid_window: int = 1) -> bool:
    """Verify a 6-digit code, tolerating +/- valid_window 30s steps for clock skew."""
    return bool(pyotp.TOTP(secret).verify(code.strip(), valid_window=valid_window))


def totp_qr_svg(uri: str) -> str:
    """Render the otpauth URI as an inline SVG string (no JS QR lib, no PNG)."""
    buf = io.BytesIO()
    segno.make(uri).save(buf, kind="svg", scale=5, border=2)
    return buf.getvalue().decode("utf-8")
