"""WebAuthn challenge single-use claim (advisory GHSA-46jj-823j-mjj9 B5).

Challenges are stateless itsdangerous cookies (``services/auth.py``); nothing
previously marked one consumed, so a captured ``(assertion, challenge
cookie)`` pair replayed within the TTL window. ``consume_challenge`` is the
atomic claim primitive: an ``INSERT ... ON CONFLICT (challenge_digest) DO
NOTHING`` dialect-dispatched exactly like ``services/retention.py``'s
``_seed_system_state`` / ``services/login_throttle.py``'s atomic increment.
No SAVEPOINT + IntegrityError (rejected in design rev 1 — dialect-asymmetric
durability: a SAVEPOINT RELEASE commits early on some drivers, masking
unrelated constraint bugs). The claim's durability is the caller's request
transaction, on both SQLite and Postgres.

``webauthn_service.py`` stays pure crypto — this module owns the persistence
side of the ceremony (A6).

Call-site contract (S3): claim BEFORE any ORM mutation in the caller's
session — no sign_count / last_used_at / session / reauthenticated_at /
credential mutation may happen before the claim wins. ``consume_challenge``
defensively refuses a session that already carries pending new/dirty objects,
since a caller that mutated first could observe the claim result out of
order with its own rollback semantics.
"""

from __future__ import annotations

import hashlib
from datetime import timedelta
from typing import Any, cast

from sqlalchemy import CursorResult, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.expression import Executable

from idraa.models._types import now_utc
from idraa.models.enums import WebAuthnChallengePurpose
from idraa.models.webauthn_challenge_consumed import WebAuthnChallengeConsumed
from idraa.services.auth import WEBAUTHN_CHALLENGE_MAX_AGE

# Retention headroom above the challenge TTL itself (A7/S7): the sweep in
# services/run_reaper.py::sweep_expired_webauthn_challenges deletes rows past
# expires_at, so a claim row outlives the cookie's own max_age by this much —
# there is no legitimate reason to look a challenge up after its cookie could
# possibly still validate.
_RETENTION_SLACK_SECONDS = 60


def _digest(challenge_b64url: str) -> str:
    return hashlib.sha256(challenge_b64url.encode("ascii")).hexdigest()


async def consume_challenge(
    db: AsyncSession, challenge_b64url: str, purpose: WebAuthnChallengePurpose
) -> bool:
    """Atomically claim a challenge. True exactly once per challenge; False on replay.

    Matches only the unique-key conflict (a NOT NULL or other constraint bug
    still raises) — no exception-flow control path.
    """
    if db.new or db.dirty:
        raise RuntimeError(
            "consume_challenge() called with a dirty session — the challenge "
            "must be claimed BEFORE any ORM mutation (S3); see "
            "services/webauthn_challenge.py module docstring"
        )
    now = now_utc()
    dialect = db.bind.dialect.name if db.bind is not None else "sqlite"
    stmt: Executable
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as _insert
    else:
        from sqlalchemy.dialects.sqlite import insert as _insert  # type: ignore[assignment]
    stmt = (
        _insert(WebAuthnChallengeConsumed)
        .values(
            challenge_digest=_digest(challenge_b64url),
            purpose=purpose,
            consumed_at=now,
            expires_at=now
            + timedelta(seconds=WEBAUTHN_CHALLENGE_MAX_AGE + _RETENTION_SLACK_SECONDS),
        )
        .on_conflict_do_nothing(index_elements=["challenge_digest"])
        .returning(WebAuthnChallengeConsumed.id)
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none() is not None


async def audit_replay_once(db: AsyncSession, challenge_b64url: str) -> bool:
    """Guarded UPDATE: at most ONE replay-audit row is ever emitted per
    challenge (mirrors ``second_factor._claim_recovery_code``'s guarded-UPDATE
    + rowcount atomicity, and ``recovery_code_claim_lost``'s self-limiting
    audit shape — S1). Returns True the first time a replay is observed for
    this challenge (caller should then write the forensic AuditLog row);
    False on every subsequent replay of the same challenge.

    Assumes the row already exists (only meaningful to call after
    ``consume_challenge`` returned False for this same challenge) — matches 0
    rows, and returns False, if it does not.
    """
    stmt = (
        update(WebAuthnChallengeConsumed)
        .where(
            WebAuthnChallengeConsumed.challenge_digest == _digest(challenge_b64url),
            WebAuthnChallengeConsumed.replay_audited.is_(False),
        )
        .values(replay_audited=True)
    )
    result = cast(CursorResult[Any], await db.execute(stmt))
    return result.rowcount == 1
