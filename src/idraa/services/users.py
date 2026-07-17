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

from sqlalchemy import func, select
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
    await AuditWriter(db).log(
        organization_id=org_id,
        user_id=actor_id,
        action="user.delete",
        entity_type="user",
        entity_id=user_id,
        changes={"email_redacted": redact_email(user.email), "role": [user.role.value, None]},
    )
    await db.delete(user)
    await db.commit()
    return True
