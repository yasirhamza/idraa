# tests/unit/test_totp_service.py
from __future__ import annotations

import pyotp

from idraa.services import totp


def test_provision_and_verify_current_code() -> None:
    secret = totp.provision_secret()
    assert len(secret) >= 16
    code = pyotp.TOTP(secret).now()
    assert totp.verify_totp(secret, code) is True


def test_verify_rejects_wrong_code() -> None:
    secret = totp.provision_secret()
    assert totp.verify_totp(secret, "000000") is False


def test_uri_contains_issuer_and_account() -> None:
    uri = totp.totp_uri("JBSWY3DPEHPK3PXP", "user@example.com", "Idraa")
    assert uri.startswith("otpauth://totp/")
    assert "issuer=Idraa" in uri
    assert "user%40example.com" in uri or "user@example.com" in uri


def test_qr_svg_renders() -> None:
    svg = totp.totp_qr_svg(
        "otpauth://totp/Idraa:user@example.com?secret=JBSWY3DPEHPK3PXP&issuer=Idraa"
    )
    assert svg.lstrip().startswith("<?xml") or "<svg" in svg
    assert "</svg>" in svg
