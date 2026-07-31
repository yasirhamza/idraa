"""User admin service.

Phase-1 invite flow is simplified: an admin types email + full name + role
+ initial password. No SMTP, no token email — the full token-email invite
lands in phase 2 per design Section 6.

Email normalization uses ``.lower().strip()`` everywhere a write enters
the DB (same invariant applied in ``services/auth.py::load_user_by_email``,
``routes/setup.py::setup_post``, and ``tests/factories.py::create_user``).
Without ``.strip()``, a trailing-space email stored here would never match
a lookup because lookups strip — the user would be silently unable to log
in. Normalize on write; normalize on read; stay consistent.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import ColumnElement, delete, func, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.errors import UserDeleteError, UserHasHistoryError
from idraa.models.control import Control
from idraa.models.enums import UserRole
from idraa.models.risk_analysis_run import RiskAnalysisRun
from idraa.models.scenario import Scenario
from idraa.models.user import User
from idraa.services.audit import AuditWriter, redact_email
from idraa.services.auth import hash_password


async def list_users(db: AsyncSession, org_id: uuid.UUID) -> list[User]:
    rows = await db.execute(
        select(User).where(User.organization_id == org_id).order_by(User.created_at)
    )
    return list(rows.scalars().all())


async def invite_user(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    email: str,
    full_name: str,
    role: UserRole,
    password: str,
) -> User:
    user = User(
        organization_id=org_id,
        email=email.lower().strip(),
        full_name=full_name,
        role=role,
        password_hash=hash_password(password),
        is_active=True,
    )
    db.add(user)
    await db.flush()
    return user


async def get_user(
    db: AsyncSession, user_id: uuid.UUID, org_id: uuid.UUID | None = None
) -> User | None:
    """Fetch a user by id, org-scoped when ``org_id`` is passed.

    The ``org_id``-less call (legacy) does a bare primary-key lookup. The
    org-scoped call filters ``organization_id == org_id`` and returns ``None``
    for a cross-org id — this closes a latent IDOR on the admin user routes
    (#296). New callers MUST pass ``org_id``; the optional default exists only
    so the signature stays backward-compatible until all call sites migrate.
    """
    if org_id is None:
        return await db.get(User, user_id)
    return await _get_user_for_org(db, user_id, org_id)


async def _get_user_for_org(db: AsyncSession, user_id: uuid.UUID, org_id: uuid.UUID) -> User | None:
    """Org-scoped single-user fetch — ``None`` for cross-org / missing id."""
    row = await db.execute(select(User).where(User.id == user_id, User.organization_id == org_id))
    return row.scalar_one_or_none()


async def _is_last_admin(db: AsyncSession, org_id: uuid.UUID) -> bool:
    """True when the org has at most one *active* admin.

    Mirrors the last-admin query in ``routes/users.py::edit_post`` (count
    active admins in the org). Used by ``delete_user`` to refuse deleting the
    sole active admin — which would leave the org with no one who can manage
    users.
    """
    active_admin_count = await db.scalar(
        select(func.count())
        .select_from(User)
        .where(
            User.organization_id == org_id,
            User.role == UserRole.ADMIN,
            User.is_active == True,  # noqa: E712 — SQLAlchemy column comparison requires ==
        )
    )
    return active_admin_count is not None and active_admin_count <= 1


def _other_active_admins_remain(org_id: uuid.UUID, user_id: uuid.UUID) -> ColumnElement[bool]:
    """Guard clause: at least one ACTIVE admin other than ``user_id`` exists.

    Embedded in the WHERE of the disarming write itself (idraa#83) so the
    last-admin invariant is re-verified atomically WITH the write — a stale
    pre-check count can no longer produce a zero-admin lockout.
    """
    return (
        select(func.count())
        .select_from(User)
        .where(
            User.organization_id == org_id,
            User.role == UserRole.ADMIN,
            User.is_active == True,  # noqa: E712 — SQLAlchemy column comparison requires ==
            User.id != user_id,
        )
        .scalar_subquery()
        >= 1
    )


async def _lock_active_admin_rows(db: AsyncSession, org_id: uuid.UUID) -> None:
    """Serialize concurrent admin-disarm writes on row-locking backends.

    On Postgres (READ COMMITTED — the stated migration target) two concurrent
    conditional UPDATEs on *different* admin rows would each see the other's
    uncommitted row as still-active (write skew), so the subquery guard alone
    is insufficient there. FOR UPDATE on the org's active-admin rows makes the
    second writer block until the first commits. SQLite ignores FOR UPDATE —
    its single-writer model already serializes the conditional writes.

    LOAD-BEARING: the lock must stay a SEPARATE statement issued BEFORE the
    guarded write. It works because after the blocker commits, the caller's
    NEXT statement (the guarded UPDATE/DELETE) gets a fresh READ-COMMITTED
    snapshot whose guard subquery sees the committed disarm. Folding the lock
    into the write itself (CTE, or deleting this "unused" SELECT) silently
    reintroduces the skew: Postgres's EvalPlanQual recheck re-evaluates only
    the ROW qual against the updated row — sub-SELECTs in the qual still run
    against the ORIGINAL snapshot. The discarded result below is not dead
    code; executing the SELECT *is* the point.
    """
    await db.execute(
        select(User.id)
        .where(
            User.organization_id == org_id,
            User.role == UserRole.ADMIN,
            User.is_active == True,  # noqa: E712 — SQLAlchemy column comparison requires ==
        )
        .with_for_update()
    )


async def guarded_admin_disarm(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    org_id: uuid.UUID,
    new_role: UserRole,
    new_active: bool,
) -> bool:
    """Atomically demote/deactivate an ACTIVE admin, refusing a last-admin lockout.

    The check-then-write guards in ``routes/users.py`` are racy (idraa#83):
    two concurrent requests each disarming a *different* admin both count 2
    active admins, both pass, and both commit — leaving the org with zero
    active admins (in-app-irrecoverable). This helper moves the invariant into
    the write: the UPDATE's WHERE re-verifies that another active admin
    remains, so at most one of the racing writes can apply.

    Returns ``True`` when the write applied (rowcount 1). ``False`` means the
    guard refused: no other active admin remains — or the target stopped being
    an active admin concurrently, in which case refusing is also safe (the
    disarm already happened).
    """
    await _lock_active_admin_rows(db, org_id)
    result: CursorResult[Any] = await db.execute(  # type: ignore[assignment]
        update(User)
        .where(
            User.id == user_id,
            User.organization_id == org_id,
            User.role == UserRole.ADMIN,
            User.is_active == True,  # noqa: E712 — SQLAlchemy column comparison requires ==
            _other_active_admins_remain(org_id, user_id),
        )
        .values(role=new_role, is_active=new_active)
        .execution_options(synchronize_session="fetch")
    )
    return int(result.rowcount or 0) == 1


async def _authored_count(db: AsyncSession, user_id: uuid.UUID, org_id: uuid.UUID) -> int:
    """Count business entities authored by ``user_id`` within ``org_id``.

    Sums rows across the three authored-entity tables (runs, scenarios,
    controls) where ``created_by == user_id``. Org-scoped so a cross-org
    authorship (shouldn't happen given org isolation, but defensive) doesn't
    block a legitimate delete.
    """
    total = 0
    for model in (RiskAnalysisRun, Scenario, Control):
        count = await db.scalar(
            select(func.count())
            .select_from(model)
            .where(model.created_by == user_id, model.organization_id == org_id)
        )
        total += int(count or 0)
    return total


async def delete_user(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    actor_id: uuid.UUID,
    org_id: uuid.UUID,
) -> bool:
    """Conditional hard-delete a user (#296).

    A user may be hard-deleted ONLY if they authored no business entities
    (runs, scenarios, controls). Guards, in order:

      1. Org-scoped fetch — ``None`` (cross-org / missing) -> return ``False``
         so the route maps to 404.
      2. Self-delete -> :class:`UserDeleteError` (409).
      3. Last active admin -> :class:`UserDeleteError` (409).
      4. Authored entities present -> :class:`UserHasHistoryError` (409); the
         admin must deactivate (``is_active=False``) instead.

    TOCTOU (accepted): the authored-count -> delete window is racy; the FK
    ``ON DELETE SET NULL`` backstop (``foreign_keys=ON``) degrades a lost race
    to NULL attribution, not a dangling FK. Acceptable for single-org
    small-team.

    TOCTOU (guarded, idraa#83): the last-admin check is NOT left racy — when
    the target is an active admin, the DELETE itself carries the
    "another active admin remains" predicate (same guard as
    :func:`guarded_admin_disarm`), so two concurrent deletes of different
    admins cannot leave zero active admins. Core DELETE is equivalent to
    ``db.delete(user)`` here: ``User`` has no ORM relationships; all
    referential behavior is DB-side (SET NULL / CASCADE, foreign_keys=ON).

    Audit: emits a ``user.delete`` row with a REDACTED email (local part
    stripped) per the audit no-raw-email contract.

    Commit ownership: this service ends with ``db.commit()`` (NOT ``flush()``)
    so the delete + audit row land atomically as one transaction — a partial
    flush could leave an orphaned audit row if the request later errored.
    (This deliberately differs from ``ScenarioService.delete``, which ends in
    ``flush()`` and defers the commit to ``get_db``.) Committing here leaves
    the session clean, so ``get_db``'s teardown auto-commit is a harmless
    no-op for this path.
    """
    user = await _get_user_for_org(db, user_id, org_id)
    if user is None:
        return False  # route -> 404
    if user.id == actor_id:
        raise UserDeleteError("cannot delete yourself")
    if user.role == UserRole.ADMIN and await _is_last_admin(db, org_id):
        raise UserDeleteError("cannot delete the last admin")
    if await _authored_count(db, user_id, org_id) > 0:
        raise UserHasHistoryError(
            "user authored entities (runs / scenarios / controls) — deactivate instead"
        )
    # Capture audit values BEFORE the delete; the row may be gone after.
    email_redacted = redact_email(user.email)
    role_value = user.role.value

    # Delete FIRST, audit after: if the guarded DELETE refuses, no audit row
    # has been flushed for a delete that never happened.
    if user.role == UserRole.ADMIN and user.is_active:
        await _lock_active_admin_rows(db, org_id)
        result: CursorResult[Any] = await db.execute(  # type: ignore[assignment]
            delete(User)
            .where(
                User.id == user.id,
                User.organization_id == org_id,
                _other_active_admins_remain(org_id, user.id),
            )
            .execution_options(synchronize_session="fetch")
        )
        if int(result.rowcount or 0) == 0:
            raise UserDeleteError("cannot delete the last admin")
    else:
        await db.delete(user)

    await AuditWriter(db).log(
        organization_id=org_id,
        user_id=actor_id,
        action="user.delete",
        entity_type="user",
        entity_id=user_id,
        changes={"email_redacted": email_redacted, "role": [role_value, None]},
    )
    await db.commit()
    return True
