"""Shared TOTP / recovery-code verification for login-MFA and step-up.

Extracted from routes/auth.py::login_mfa_post (P2) so the step-up verify
endpoint cannot drift from the login second-factor semantics: same TOTP
window, same recovery-shape short-circuit (a wrong 6-digit guess must never
pay the Argon2 cost of the recovery loop), same burn + audit on recovery use.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime
from typing import cast

from sqlalchemy import CursorResult, select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models._types import now_utc
from idraa.models.mfa import RecoveryCode, UserTotp
from idraa.models.user import User
from idraa.services import totp as totp_service
from idraa.services.audit import AuditWriter
from idraa.services.auth import _hash_offload
from idraa.services.mfa_crypto import decrypt_totp_secret, verify_recovery_code

logger = logging.getLogger(__name__)

_RECOVERY_SHAPE = re.compile(r"[0-9a-f]{5}-[0-9a-f]{5}")


def _match_recovery_code(code: str, pairs: list[tuple[uuid.UUID, str]]) -> uuid.UUID | None:
    """Return the id of the first recovery code whose hash matches ``code``.

    Argon2-bound (up to one verify per candidate); the caller offloads it off
    the event loop. A given input hash-matches at most one stored code, so the
    first match is the only match.
    """
    for rc_id, code_hash in pairs:
        if verify_recovery_code(code, code_hash):
            return rc_id
    return None


async def _claim_recovery_code(db: AsyncSession, rc_id: uuid.UUID, now: datetime) -> bool:
    """Atomically flip ``used_at`` NULL->``now`` for one recovery code.

    Returns True iff THIS caller won the single-use claim. Mirrors the atomic
    TOTP-step claim in verify_totp_or_recovery: two concurrent redemptions of
    the same code both read it as unused, but only the request whose guarded
    UPDATE actually flips the row (rowcount == 1) wins; the loser gets rowcount
    0 and is rejected. The DB evaluates ``used_at IS NULL`` atomically under its
    write lock (SQLite WAL single-writer / Postgres row lock), so this guarded
    UPDATE is the atomicity primitive — no schema constraint maps to lost-update
    prevention (see the design doc's DB-backstop analysis).

    An OperationalError (a Postgres serialization failure at SERIALIZABLE, or a
    future SQLite isolation change turning the read-then-write into
    SQLITE_BUSY_SNAPSHOT) fails CLOSED as a loser rather than surfacing a 500 —
    the UPDATE did not commit, so the code is not burned and the legitimate user
    can retry. Logged (mirroring login_throttle's swallowed-error posture) so a
    real DB outage is not a silent stream of "invalid code".
    """
    try:
        res = cast(
            CursorResult[object],
            await db.execute(
                update(RecoveryCode)
                .where(RecoveryCode.id == rc_id, RecoveryCode.used_at.is_(None))
                .values(used_at=now)
                .execution_options(synchronize_session=False)
            ),
        )
    except OperationalError:
        logger.warning("recovery-code claim failed with OperationalError; rejecting", exc_info=True)
        return False
    return res.rowcount == 1


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
    if totp:
        step = totp_service.verify_totp_step(
            decrypt_totp_secret(totp.secret_encrypted), code, after_step=totp.last_used_step
        )
        if step is not None:
            # N4 (idraa#81): claim the step atomically so two concurrent
            # verifies (e.g. Postgres under load) can't both accept it —
            # only the request whose guarded UPDATE actually flips the row
            # wins and returns "totp"; the loser falls through to reject.
            res = cast(
                CursorResult[object],
                await db.execute(
                    update(UserTotp)
                    .where(
                        UserTotp.user_id == user.id,
                        (UserTotp.last_used_step.is_(None)) | (UserTotp.last_used_step < step),
                    )
                    .values(last_used_step=step)
                ),
            )
            if res.rowcount == 1:  # we won the claim
                return "totp"
            # lost the race (already consumed this step) -> fall through to reject
    # Only walk the recovery Argon2 loop when the input is recovery-code-shaped
    # — a wrong TOTP guess must NOT cost up to 10 Argon2 verifies (CPU-DoS
    # amplifier).
    if _RECOVERY_SHAPE.fullmatch(code):
        rows = (
            (
                await db.execute(
                    select(RecoveryCode).where(
                        RecoveryCode.user_id == user.id, RecoveryCode.used_at.is_(None)
                    )
                )
            )
            .scalars()
            .all()
        )
        # ONE offloaded thread hop over all candidate hashes (bounded pool),
        # not one per code — keeps the up-to-10 Argon2 verifies off the event
        # loop and cuts 10 pool round-trips to 1.
        matched_id = await _hash_offload(
            _match_recovery_code, code, [(rc.id, rc.code_hash) for rc in rows]
        )
        if matched_id is not None:
            now = now_utc()
            if await _claim_recovery_code(db, matched_id, now):
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
            # Matched a real code but lost the atomic claim -> it was already
            # burned by a concurrent request. High-signal event (a valid,
            # already-consumed code was submitted): audit distinctly so the
            # bypass attempt is not indistinguishable from a typo, then reject.
            await AuditWriter(db).log(
                organization_id=user.organization_id,
                entity_type="user",
                entity_id=user.id,
                action="user.recovery_code_claim_lost",
                changes={},
                user_id=user.id,
                ip_address=ip_address,
            )
            return None
    return None
