"""Shared TOTP / recovery-code verification for login-MFA and step-up.

Extracted from routes/auth.py::login_mfa_post (P2) so the step-up verify
endpoint cannot drift from the login second-factor semantics: same TOTP
window, same recovery-shape short-circuit (a wrong 6-digit guess must never
pay the Argon2 cost of the recovery loop), same burn + audit on recovery use.
"""

from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models._types import now_utc
from idraa.models.mfa import RecoveryCode, UserTotp
from idraa.models.user import User
from idraa.services import totp as totp_service
from idraa.services.audit import AuditWriter
from idraa.services.mfa_crypto import decrypt_totp_secret, verify_recovery_code

_RECOVERY_SHAPE = re.compile(r"[0-9a-f]{5}-[0-9a-f]{5}")


async def verify_totp_or_recovery(
    db: AsyncSession, user: User, code: str, *, ip_address: str | None
) -> str | None:
    """Verify a second-factor input. Returns "totp", "recovery", or None.

    A matched recovery code is burned (used_at stamped) and audited
    (user.recovery_code_used) HERE — callers must not double-audit.
    """
    code = code.strip()
    totp = (
        (
            await db.execute(
                select(UserTotp).where(
                    UserTotp.user_id == user.id, UserTotp.confirmed_at.is_not(None)
                )
            )
        )
        .scalars()
        .first()
    )
    if totp and totp_service.verify_totp(decrypt_totp_secret(totp.secret_encrypted), code):
        return "totp"
    # Only walk the recovery Argon2 loop when the input is recovery-code-shaped
    # — a wrong TOTP guess must NOT cost up to 10 Argon2 verifies (CPU-DoS
    # amplifier).
    if _RECOVERY_SHAPE.fullmatch(code):
        for rc in (
            (
                await db.execute(
                    select(RecoveryCode).where(
                        RecoveryCode.user_id == user.id, RecoveryCode.used_at.is_(None)
                    )
                )
            )
            .scalars()
            .all()
        ):
            if verify_recovery_code(code, rc.code_hash):
                rc.used_at = now_utc()
                await AuditWriter(db).log(
                    organization_id=user.organization_id,
                    entity_type="user",
                    entity_id=user.id,
                    action="user.recovery_code_used",
                    changes={},
                    user_id=user.id,
                    ip_address=ip_address,
                )
                return "recovery"
    return None
