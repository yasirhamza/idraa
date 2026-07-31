"""Last-admin lockout race guards (idraa#83).

The pre-write count checks in ``routes/users.py`` (edit_post, set_active_post)
and ``services/users.py::delete_user`` are check-then-write: two concurrent
requests each disarming a *different* admin can both observe count==2, both
pass, and both commit — leaving zero active admins (in-app-irrecoverable).

The fix moves the invariant INTO the write: ``guarded_admin_disarm`` (and the
guarded DELETE inside ``delete_user``) re-verify "another active admin
remains" in the same statement that flips the row.

Coverage strategy (the true cross-transaction race cannot be replayed
deterministically in one test session — a single session always sees its own
uncommitted writes, so ANY re-count implementation, atomic or not, refuses
the second disarm):

- The *_schedule_* tests pin the guard's call-time semantics (refuse when no
  other active cover remains; inactive admins are not cover).
- ``test_disarm_update_carries_guard_predicate`` /
  ``test_delete_carries_guard_predicate`` pin ATOMICITY structurally: they
  capture the emitted SQL and assert the guard subquery lives in the WHERE of
  the disarming UPDATE/DELETE itself. A future count-then-write refactor
  (the racy shape) emits a bare UPDATE/DELETE and fails these.
- ``test_delete_write_guard_survives_stale_precheck`` forces the
  pre-check/write divergence by pinning ``_is_last_admin`` to the stale
  answer — the only black-box way to simulate the race in one session.
- The *_refuse_branch_* route tests cover the route-level refusal rendering,
  which is sequentially unreachable (the guarded predicate is a strict
  subset of the friendly pre-check, so only a genuine race reaches it) —
  exercised via a refusing ``guarded_admin_disarm`` stub.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession

import idraa.services.users as users_service
from idraa.errors import UserDeleteError
from idraa.models.enums import UserRole
from idraa.models.organization import Organization
from idraa.models.user import User
from idraa.services.users import delete_user, guarded_admin_disarm
from tests.conftest import csrf_post
from tests.factories import create_user


async def _two_active_admins(db: AsyncSession, org: Organization) -> tuple[User, User]:
    x = await create_user(db, org, email="admin-x@test.local", role=UserRole.ADMIN)
    y = await create_user(db, org, email="admin-y@test.local", role=UserRole.ADMIN)
    return x, y


async def test_race_schedule_second_deactivation_refused(
    db_session: AsyncSession, organization: Organization
) -> None:
    """Call-time guard semantics: after one disarm, the second must refuse.

    Atomicity itself is pinned by test_disarm_update_carries_guard_predicate
    (see module docstring — this collapsed schedule cannot distinguish an
    atomic guard from a re-count).
    """
    x, y = await _two_active_admins(db_session, organization)

    # Both racing requests already passed the naive count check (count==2).
    # Request A's write disarms X — allowed, Y still covers the org.
    applied_a = await guarded_admin_disarm(
        db_session,
        user_id=x.id,
        org_id=organization.id,
        new_role=UserRole.ADMIN,
        new_active=False,
    )
    assert applied_a is True

    # Request B's write disarms Y — MUST refuse: X is no longer active cover.
    applied_b = await guarded_admin_disarm(
        db_session,
        user_id=y.id,
        org_id=organization.id,
        new_role=UserRole.ADMIN,
        new_active=False,
    )
    assert applied_b is False

    await db_session.refresh(x)
    await db_session.refresh(y)
    assert x.is_active is False
    assert y.is_active is True  # the org keeps one active admin
    assert y.role == UserRole.ADMIN


async def test_guarded_disarm_demote_only(
    db_session: AsyncSession, organization: Organization
) -> None:
    """Demotion (role change, still active) goes through the same guard."""
    x, y = await _two_active_admins(db_session, organization)

    applied = await guarded_admin_disarm(
        db_session,
        user_id=x.id,
        org_id=organization.id,
        new_role=UserRole.ANALYST,
        new_active=True,
    )
    assert applied is True
    await db_session.refresh(x)
    assert x.role == UserRole.ANALYST
    assert x.is_active is True

    # Y is now the sole active admin — demoting them must refuse.
    applied = await guarded_admin_disarm(
        db_session,
        user_id=y.id,
        org_id=organization.id,
        new_role=UserRole.ANALYST,
        new_active=True,
    )
    assert applied is False
    await db_session.refresh(y)
    assert y.role == UserRole.ADMIN


async def test_guarded_disarm_inactive_admin_is_not_cover(
    db_session: AsyncSession, organization: Organization
) -> None:
    """An INACTIVE admin row does not count as remaining admin cover."""
    x = await create_user(db_session, organization, email="admin-x@test.local", role=UserRole.ADMIN)
    z = await create_user(db_session, organization, email="admin-z@test.local", role=UserRole.ADMIN)
    z.is_active = False
    await db_session.flush()

    applied = await guarded_admin_disarm(
        db_session,
        user_id=x.id,
        org_id=organization.id,
        new_role=UserRole.ADMIN,
        new_active=False,
    )
    assert applied is False
    await db_session.refresh(x)
    assert x.is_active is True


async def test_delete_write_guard_survives_stale_precheck(
    db_session: AsyncSession,
    organization: Organization,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """delete_user's write-time guard holds even when the pre-check read stale data.

    Simulates the racy interleaving by pinning ``_is_last_admin`` to the stale
    answer (False) that a concurrent request would have observed before the
    other admin was removed. The conditional DELETE must still refuse.
    """
    x = await create_user(db_session, organization, email="admin-x@test.local", role=UserRole.ADMIN)
    actor = await create_user(
        db_session, organization, email="admin-actor@test.local", role=UserRole.ADMIN
    )
    actor.is_active = False  # X is the sole ACTIVE admin
    await db_session.flush()

    async def _stale_false(db: AsyncSession, org_id: object) -> bool:
        return False  # what the racing request saw before the world changed

    monkeypatch.setattr(users_service, "_is_last_admin", _stale_false)

    with pytest.raises(UserDeleteError):
        await delete_user(db_session, user_id=x.id, actor_id=actor.id, org_id=organization.id)

    survivor = (await db_session.execute(select(User).where(User.id == x.id))).scalar_one_or_none()
    assert survivor is not None
    assert survivor.is_active is True


async def test_disarm_update_carries_guard_predicate(
    db_session: AsyncSession, organization: Organization
) -> None:
    """ATOMICITY PIN: the guard subquery must live in the UPDATE's own WHERE.

    Captures the SQL emitted by guarded_admin_disarm and asserts the
    "another active admin remains" count subquery is part of the UPDATE
    statement itself — the property that makes the guard race-proof. A
    count-then-write refactor (separate SELECT, then a bare UPDATE) passes
    every sequential behavior test but fails this one.
    """
    x, _y = await _two_active_admins(db_session, organization)

    captured: list[str] = []

    def _capture(conn: object, cursor: object, statement: str, *args: object) -> None:
        captured.append(statement)

    sync_engine = db_session.bind.sync_engine  # type: ignore[union-attr]
    event.listen(sync_engine, "before_cursor_execute", _capture)
    try:
        applied = await guarded_admin_disarm(
            db_session,
            user_id=x.id,
            org_id=organization.id,
            new_role=UserRole.ADMIN,
            new_active=False,
        )
    finally:
        event.remove(sync_engine, "before_cursor_execute", _capture)

    assert applied is True
    updates = [s for s in captured if s.lstrip().upper().startswith("UPDATE")]
    assert len(updates) == 1, f"expected exactly one UPDATE, got: {updates}"
    stmt = updates[0].lower()
    assert "select count" in stmt, "guard subquery missing from the UPDATE statement"
    assert "users.id !=" in stmt, "guard subquery must exclude the target row"


async def test_delete_carries_guard_predicate(
    db_session: AsyncSession, organization: Organization
) -> None:
    """ATOMICITY PIN (delete path): guard subquery inside the DELETE itself."""
    x, y = await _two_active_admins(db_session, organization)

    captured: list[str] = []

    def _capture(conn: object, cursor: object, statement: str, *args: object) -> None:
        captured.append(statement)

    sync_engine = db_session.bind.sync_engine  # type: ignore[union-attr]
    event.listen(sync_engine, "before_cursor_execute", _capture)
    try:
        deleted = await delete_user(db_session, user_id=x.id, actor_id=y.id, org_id=organization.id)
    finally:
        event.remove(sync_engine, "before_cursor_execute", _capture)

    assert deleted is True
    deletes = [s for s in captured if s.lstrip().upper().startswith("DELETE")]
    assert len(deletes) == 1, f"expected exactly one DELETE, got: {deletes}"
    stmt = deletes[0].lower()
    assert "select count" in stmt, "guard subquery missing from the DELETE statement"
    assert "users.id !=" in stmt, "guard subquery must exclude the target row"


async def test_edit_post_refuse_branch_renders_form_error(
    authed_admin: tuple[AsyncClient, object],
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Route-level refusal rendering for edit_post (sequentially unreachable).

    The guarded predicate is a strict subset of the friendly pre-check, so
    only a genuine cross-transaction race reaches this branch — a refusing
    stub stands in for the lost race.
    """
    client, _ = authed_admin
    await csrf_post(
        client,
        "/users/invite",
        {
            "email": "race-loser@test.local",
            "full_name": "Race Loser",
            "role": "admin",
            "password": "pw-12345678",
        },
    )
    target = (
        await db_session.execute(select(User).where(User.email == "race-loser@test.local"))
    ).scalar_one()

    async def _refuse(*args: object, **kwargs: object) -> bool:
        return False

    monkeypatch.setattr("idraa.routes.users.guarded_admin_disarm", _refuse)

    r = await csrf_post(
        client,
        f"/users/{target.id}/edit",
        {"role": "analyst", "is_active": "on"},
        follow_redirects=False,
    )
    assert r.status_code == 400
    assert "Cannot demote or deactivate the last active admin" in r.text
    await db_session.refresh(target)
    assert target.role == UserRole.ADMIN  # nothing was written
    assert target.is_active is True


async def test_set_active_refuse_branch_returns_400(
    authed_admin: tuple[AsyncClient, object],
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Route-level refusal for set_active_post (sequentially unreachable)."""
    client, _ = authed_admin
    await csrf_post(
        client,
        "/users/invite",
        {
            "email": "race-loser-2@test.local",
            "full_name": "Race Loser Two",
            "role": "admin",
            "password": "pw-12345678",
        },
    )
    target = (
        await db_session.execute(select(User).where(User.email == "race-loser-2@test.local"))
    ).scalar_one()

    async def _refuse(*args: object, **kwargs: object) -> bool:
        return False

    monkeypatch.setattr("idraa.routes.users.guarded_admin_disarm", _refuse)

    r = await csrf_post(
        client,
        f"/users/{target.id}/set-active",
        {"active": "0"},
        follow_redirects=False,
    )
    assert r.status_code == 400
    await db_session.refresh(target)
    assert target.is_active is True  # nothing was written


async def test_admin_can_deactivate_other_admin_via_route(
    authed_admin: tuple[AsyncClient, object], db_session: AsyncSession
) -> None:
    """Green path through the rewritten set_active_post guarded write."""
    client, _ = authed_admin
    await csrf_post(
        client,
        "/users/invite",
        {
            "email": "second-admin@test.local",
            "full_name": "Second Admin",
            "role": "admin",
            "password": "pw-12345678",
        },
    )
    target = (
        await db_session.execute(select(User).where(User.email == "second-admin@test.local"))
    ).scalar_one()

    r = await csrf_post(
        client,
        f"/users/{target.id}/set-active",
        {"active": "0"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    await db_session.refresh(target)
    assert target.is_active is False


async def test_admin_can_demote_other_admin_via_route(
    authed_admin: tuple[AsyncClient, object], db_session: AsyncSession
) -> None:
    """Green path through the rewritten edit_post guarded write."""
    client, _ = authed_admin
    await csrf_post(
        client,
        "/users/invite",
        {
            "email": "third-admin@test.local",
            "full_name": "Third Admin",
            "role": "admin",
            "password": "pw-12345678",
        },
    )
    target = (
        await db_session.execute(select(User).where(User.email == "third-admin@test.local"))
    ).scalar_one()

    r = await csrf_post(
        client,
        f"/users/{target.id}/edit",
        {"role": "analyst", "is_active": "on"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    await db_session.refresh(target)
    assert target.role == UserRole.ANALYST
    assert target.is_active is True
