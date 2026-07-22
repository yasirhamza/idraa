"""Enrollment state helpers: what counts as 'enrolled', and stamping it."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models._types import now_utc
from idraa.models.mfa import RecoveryCode, UserTotp, WebAuthnCredential
from idraa.models.user import User


async def user_has_strong_factor(db: AsyncSession, user_id: uuid.UUID) -> bool:
    passkeys = await db.scalar(
        select(func.count())
        .select_from(WebAuthnCredential)
        .where(WebAuthnCredential.user_id == user_id)
    )
    if passkeys:
        return True
    totp = await db.scalar(
        select(UserTotp).where(UserTotp.user_id == user_id, UserTotp.confirmed_at.is_not(None))
    )
    return totp is not None


async def user_has_recovery_codes(db: AsyncSession, user_id: uuid.UUID) -> bool:
    n = await db.scalar(
        select(func.count()).select_from(RecoveryCode).where(RecoveryCode.user_id == user_id)
    )
    return bool(n)


async def maybe_stamp_enrolled(db: AsyncSession, user: User) -> None:
    """Set mfa_enrolled_at once the user has >=1 strong factor AND recovery codes."""
    if user.mfa_enrolled_at is not None:
        return
    if await user_has_strong_factor(db, user.id) and await user_has_recovery_codes(db, user.id):
        user.mfa_enrolled_at = now_utc()


async def maybe_unstamp_enrolled(db: AsyncSession, user: User) -> None:
    """Clear mfa_enrolled_at when the user no longer has ANY strong factor.

    Plan-gate I4: without this, deleting the last passkey leaves mfa_enrolled_at
    set, so the next password login takes the 'no strong factor' branch and the
    interstitial never re-traps — silently downgrading a required account to
    password-only. Must be called AFTER the delete has been flushed/visible.
    """
    if user.mfa_enrolled_at is None:
        return
    if not await user_has_strong_factor(db, user.id):
        user.mfa_enrolled_at = None


async def reset_user_mfa(db: AsyncSession, user: User) -> dict[str, int]:
    """Clear ALL of a user's strong-auth state (admin/CLI factor reset).

    Deletes passkeys, TOTP, and recovery codes and clears mfa_enrolled_at so
    the enrollment interstitial re-traps at next login (policy=required).
    NEVER yields the caller a usable credential (design §Recovery). Session
    revocation is deliberately NOT done here — callers pair this with
    services.auth.revoke_user_sessions so each side is audited separately.
    """
    counts: dict[str, int] = {}
    result: CursorResult[Any] = await db.execute(  # type: ignore[assignment]
        delete(WebAuthnCredential).where(WebAuthnCredential.user_id == user.id)
    )
    counts["passkeys"] = int(result.rowcount or 0)
    result = await db.execute(  # type: ignore[assignment]
        delete(UserTotp).where(UserTotp.user_id == user.id)
    )
    counts["totp"] = int(result.rowcount or 0)
    result = await db.execute(  # type: ignore[assignment]
        delete(RecoveryCode).where(RecoveryCode.user_id == user.id)
    )
    counts["recovery_codes"] = int(result.rowcount or 0)
    user.mfa_enrolled_at = None
    return counts


def credential_views(creds: Sequence[WebAuthnCredential]) -> list[dict[str, object]]:
    """Map passkey ORM rows to template-safe view dicts. Preserves order + count."""
    return [
        {
            "id": str(c.id),
            "nickname": c.nickname,
            "last_used_at": c.last_used_at,
            "created_at": c.created_at,
        }
        for c in creds
    ]
