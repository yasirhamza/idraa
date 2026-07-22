# Strong Auth P2 — Step-up ("sudo mode") + Recovery Ops Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Sensitive actions require a fresh re-authentication (≤10 min); admins (and a host CLI) can reset a locked-out user's factors without ever holding a usable credential; deactivation and factor-reset revoke live sessions (idraa#80 L13).

**Architecture:** `AuthSession` gains `reauthenticated_at`; a `require_recent_auth` route dependency raises `StepUpRequired`, which an app-level exception handler turns into the `/auth/step-up` challenge (full-page 303, HTMX `HX-Redirect`, or JSON `{error, redirect}` for fetch callers). Verify endpoints re-check the strongest factor the user holds (passkey / TOTP / recovery code; password ONLY for users with no strong factor) and stamp the current session fresh. A shared `reset_user_mfa` service backs both the admin route and a new `python -m idraa auth reset-mfa` CLI; `revoke_user_sessions` backs reset + deactivation.

**Tech Stack:** FastAPI + SQLAlchemy 2.x async + Alembic; itsdangerous signed cookies (P1 conventions); py_webauthn via `services/webauthn_service.py`; Jinja2 + HTMX; pytest + httpx.

**Design doc:** `docs/superpowers/specs/2026-07-22-strong-auth-mfa-passkeys-design.md` (§Step-up, §Recovery, §Folded sweep findings #80 L13, §Audit events). Epic: idraa#85. This is milestone P2 of 3 (P1 merged as PR #89, squash `3001505`).

## Global Constraints

- **3-reviewer ceremony** (security-auditor + architect + code-quality-with-spec-adherence) at plan-gate AND PR-gate, iterated to 0/0 — owner override recorded in the design doc; methodology reviewer deliberately dropped (no FAIR surface).
- **Never hardcode deployment domains** — WebAuthn RP-ID/origins stay `Settings`-driven; nothing in this plan may reference `idraa.app`/`idraa.fly.dev` in code.
- **Transaction commit is owned by the `get_db` dependency** (`routes/deps.py::get_db`); handlers do NOT call `await db.commit()` (exceptions: mid-request visibility, documented inline).
- **Detached-instance rebind:** `request.state.user` / `request.state.session` were loaded by `SessionMiddleware`'s own (now-closed) session. Any handler that MUTATES them must rebind first: `user = await db.get(User, user.id) or user` (P1 convention, see `routes/mfa.py::totp_enroll_post`).
- **aiosqlite tz-strip:** `DateTime(timezone=True)` values read cross-connection may come back naive; re-attach UTC before comparing (`services/auth.py::is_locked` pattern).
- **Audit everything:** every auth decision writes an `AuditLog` row via `services/audit.py::AuditWriter` (`await AuditWriter(db).log(organization_id=..., entity_type=..., entity_id=..., action=..., changes=..., user_id=..., ip_address=client_ip(request))`).
- **Signed-payload salt convention** (`services/auth.py`): every new signed-token type gets its own itsdangerous salt. P2 adds ONE new type: the step-up WebAuthn challenge (`rf-webauthn-stepup` salt, `rf_webauthn_stepup` cookie, Task 2) — a challenge minted for the ANONYMOUS login ceremony must never validate for an authenticated step-up re-verify, per the convention's purpose-separation rule (plan-gate Sec-N1).
- **Tests default `AUTH_MFA_POLICY=optional`** (the root `client` fixture sets it); tests that exercise the interstitial set `required` + `config.reset_for_tests()` themselves.
- **Python 3.11+, ruff + ruff-format + mypy clean, fast gate green** (`uv run python scripts/run_local_gate.py` locally ≈ CI's `gate` job). E2E (`-m e2e`) runs OUTSIDE the fast gate — run explicitly when `webauthn.js`/templates change.
- **Alembic:** current head is `2fa98364de58`. This plan adds exactly ONE migration (Task 1). `uv run alembic heads` must show a single head afterward.
- **Audit `changes` values** follow the `[prev, new]` pair convention for field mutations; event-shaped rows may use flat values (P1 precedent: `login_mfa` used `{"method": method}`).

---

### Task 1: Step-up core — `reauthenticated_at`, `require_recent_auth`, challenge + TOTP/recovery/password verify

**Files:**
- Modify: `src/idraa/config.py` (after `auth_lockout_seconds`, ~line 302)
- Modify: `src/idraa/models/session.py`
- Create: `alembic/versions/<generated>_strong_auth_p2_step_up_reauthenticated_at.py`
- Modify: `src/idraa/services/auth.py`
- Create: `src/idraa/services/second_factor.py`
- Modify: `src/idraa/routes/auth.py` (use shared helpers; no behavior change)
- Modify: `src/idraa/errors.py`
- Modify: `src/idraa/routes/deps.py`
- Create: `src/idraa/routes/step_up.py`
- Create: `src/idraa/templates/auth/step_up.html`
- Modify: `src/idraa/app.py` (router + exception handler)
- Modify: `src/idraa/middleware/enrollment_guard.py`
- Test: `tests/unit/test_step_up_freshness.py`
- Test: `tests/integration/test_step_up_flow.py`

**Interfaces:**
- Consumes: `AuthSession` (`models/session.py`), `create_session` / `is_locked` / `register_failed_login` / `reset_login_throttle` / `verify_user_password` (`services/auth.py`), `user_has_strong_factor` (`services/mfa_enrollment.py`), `verify_totp` + `decrypt_totp_secret` + `verify_recovery_code` (P1 services), `AuditWriter`, `client_ip`, `templates`.
- Produces (later tasks rely on these EXACT names):
  - `Settings.auth_step_up_max_age_seconds: int` (default 600; `0` disables step-up — mirrors `auth_max_failed_logins`'s 0-disables convention)
  - `AuthSession.reauthenticated_at: Mapped[datetime | None]`
  - `services/auth.py::is_step_up_fresh(sess: AuthSession) -> bool`
  - `services/second_factor.py::verify_totp_or_recovery(db: AsyncSession, user: User, code: str, *, ip_address: str | None) -> str | None` (returns `"totp"` / `"recovery"` / `None`; burns + audits a used recovery code)
  - `errors.py::StepUpRequired(next_url: str)` with attribute `.next_url`
  - `routes/deps.py::safe_next(raw: str | None) -> str` (relocated from `routes/auth.py::_safe_next`)
  - `routes/deps.py::require_recent_auth(request, user=Depends(current_user), sess=Depends(current_session)) -> None` — wire as `dependencies=[Depends(require_recent_auth)]`
  - Routes: `GET /auth/step-up?next=`, `POST /auth/step-up/verify`
  - Audit actions: `user.step_up` (entity_type `session`, changes `{"method": method}`), `user.step_up_failed` (entity_type `user`)

**Semantics locked by this task (plan-gate-reviewed decisions):**
1. `reauthenticated_at` is NULLABLE; `None` (pre-P2 rows) ⇒ stale ⇒ challenge once. Fail-closed, no backfill dance.
2. `max_age == 0` disables step-up entirely (operator escape hatch, same convention as lockout's `auth_max_failed_logins=0`).
3. Users WITH a strong factor re-verify via TOTP/recovery (or passkey, Task 2) — NEVER via password (a phished password must not satisfy step-up). Users WITHOUT any strong factor (policy=optional migration window, or mid-enrollment) re-verify via password — this also prevents an enrollment deadlock: enrollment endpoints are themselves step-up-gated (Task 3), and a factor-less user must be able to pass the challenge to enroll their first factor.
4. Interrupted POST actions are NOT replayed. The challenge's `next` is the original URL for GETs and the sanitized Referer for POSTs; after verify the user lands back and re-triggers the action, which now passes (window 600 s).
5. Failed code/password step-up attempts audit `user.step_up_failed` AND count toward the per-account lockout (`register_failed_login`) — same B1 discipline as `/login/mfa`. Success resets the throttle. An ALREADY-locked user short-circuits to the same generic "Invalid code or password" 400 with NO audit row and NO counter bump (mirrors `/login/mfa`'s locked path; prevents unbounded append-only audit growth from a hostile session-holder — Sec-N3). No lockout oracle either way.
6. `/auth/step-up` is added to the enrollment-guard allowlist — without it, a `required`-policy un-enrolled user with a stale session loops forever between the interstitial and the challenge.
7. TOTP replay-within-window (pyotp `valid_window=1`, no last-used-step column) is a P1-shipped property that step-up inherits via the shared verifier — acknowledged, NOT fixed here; it belongs to the same P3 "strict single-use" backlog item as the design's `mfa_pending` replay acceptance (Sec-N4).

- [ ] **Step 1: Write the failing unit tests**

`tests/unit/test_step_up_freshness.py`:

```python
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

import idraa.config as config
from idraa.models.session import AuthSession
from idraa.services.auth import is_step_up_fresh


def _sess(reauth: datetime | None) -> AuthSession:
    now = datetime.now(UTC)
    return AuthSession(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        created_at=now,
        last_seen_at=now,
        expires_at=now + timedelta(days=14),
        reauthenticated_at=reauth,
    )


def test_fresh_within_window() -> None:
    assert is_step_up_fresh(_sess(datetime.now(UTC) - timedelta(seconds=30))) is True


def test_stale_beyond_window() -> None:
    assert is_step_up_fresh(_sess(datetime.now(UTC) - timedelta(seconds=601))) is False


def test_none_is_stale() -> None:
    # Pre-P2 session rows have no reauthenticated_at — fail closed.
    assert is_step_up_fresh(_sess(None)) is False


def test_naive_datetime_reattaches_utc() -> None:
    # aiosqlite strips tzinfo on cross-connection reads; a naive value is
    # known-UTC (create_session's invariant) and must not raise.
    naive = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=30)
    assert is_step_up_fresh(_sess(naive)) is True


def test_zero_disables_step_up(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_STEP_UP_MAX_AGE_SECONDS", "0")
    config.reset_for_tests()
    try:
        assert is_step_up_fresh(_sess(None)) is True
    finally:
        monkeypatch.delenv("AUTH_STEP_UP_MAX_AGE_SECONDS")
        config.reset_for_tests()


def test_model_has_reauthenticated_at_column() -> None:
    # Pins column existence only (AttributeError if dropped). The actual
    # create_session stamping is covered by the DB-backed integration tests.
    assert AuthSession.reauthenticated_at is not None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_step_up_freshness.py -v`
Expected: FAIL — `ImportError: cannot import name 'is_step_up_fresh'` (and `reauthenticated_at` unexpected keyword).

- [ ] **Step 3: Implement config + model + migration + service helpers**

`src/idraa/config.py` — add directly after `auth_lockout_seconds` (~line 302):

```python
    # Step-up ("sudo mode") freshness window — P2. Sensitive actions require a
    # re-auth within this many seconds. 0 disables step-up entirely (mirrors
    # auth_max_failed_logins' 0-disables convention).
    auth_step_up_max_age_seconds: int = Field(default=600, ge=0)
```

`src/idraa/models/session.py` — add after `last_seen_at`:

```python
    # P2 step-up: stamped at login and on every successful step-up re-verify.
    # NULLABLE: rows created before the P2 migration have no value; readers
    # treat None as stale (fail closed — one challenge, then stamped).
    reauthenticated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=now_utc
    )
```

Generate the migration: `uv run alembic revision -m "strong auth p2 step-up reauthenticated_at"`, then fill the generated file (down_revision must be `"2fa98364de58"`):

```python
"""strong auth p2 step-up reauthenticated_at

Revision ID: <keep-generated-id>
Revises: 2fa98364de58
Create Date: <keep-generated>
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "<keep-generated-id>"
down_revision = "2fa98364de58"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Nullable, no server default, no backfill: None reads as "stale" so
    # pre-P2 sessions simply re-verify once (fail-closed by design).
    op.add_column(
        "auth_sessions",
        sa.Column("reauthenticated_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("auth_sessions", "reauthenticated_at")
```

`src/idraa/services/auth.py` — in `create_session`, add the stamp (same `now` as `created_at`):

```python
    sess = AuthSession(
        id=uuid.uuid4(),
        user_id=user_id,
        created_at=now,
        last_seen_at=now,
        expires_at=now + SESSION_TTL,
        ip_address=ip,
        reauthenticated_at=now,  # login IS a re-auth (design §Step-up)
    )
```

`src/idraa/services/auth.py` — add after `reset_login_throttle`:

```python
def is_step_up_fresh(sess: AuthSession) -> bool:
    """True when the session's last re-auth is inside the step-up window.

    max_age == 0 disables step-up (operator opt-out, mirrors
    auth_max_failed_logins). A NULL reauthenticated_at (pre-P2 row) is
    stale — fail closed, the user re-verifies once and gets stamped.
    """
    max_age = get_settings().auth_step_up_max_age_seconds
    if max_age == 0:
        return True
    ra = sess.reauthenticated_at
    if ra is None:
        return False
    if ra.tzinfo is None:  # aiosqlite may strip tzinfo on cross-connection read
        ra = ra.replace(tzinfo=UTC)
    return datetime.now(UTC) - ra <= timedelta(seconds=max_age)
```

Create `src/idraa/services/second_factor.py` (extracted from `routes/auth.py::login_mfa_post` so login and step-up share ONE verifier):

```python
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
```

`src/idraa/routes/auth.py` — replace the inline TOTP+recovery block in `login_mfa_post` (the `code = code.strip()` line through the recovery `for` loop, currently lines ~242-284) with:

```python
    method = await verify_totp_or_recovery(db, user, code, ip_address=client_ip(request))
```

and add the import `from idraa.services.second_factor import verify_totp_or_recovery`; drop the now-unused imports: `re`, `totp_service` (its only use is the extracted line — plan-gate CQ-I3), `decrypt_totp_secret` / `verify_recovery_code`, and the `UserTotp` / `RecoveryCode` model imports IF nothing else in the module uses them (check before deleting — `select` and `now_utc` STAY, still used by the passkey login path). The existing `tests/integration/test_login_mfa_flow.py` (241 lines) is the regression net for this refactor — zero behavior change expected.

- [ ] **Step 4: Run the unit tests + the login regression suite**

Run: `uv run pytest tests/unit/test_step_up_freshness.py tests/integration/test_login_mfa_flow.py -v`
Expected: ALL PASS. Also run `uv run alembic heads` → exactly one head (the new revision).

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat(auth): reauthenticated_at + step-up freshness + shared second-factor verifier (P2 Task 1a)"
```

- [ ] **Step 6: Write the failing integration tests for the challenge flow**

`tests/integration/test_step_up_flow.py`:

```python
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pyotp
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models._types import now_utc
from idraa.models.mfa import UserTotp
from idraa.models.session import AuthSession
from idraa.models.user import User
from idraa.services.auth import SESSION_COOKIE, unsign_session_id
from idraa.services.mfa_crypto import encrypt_totp_secret
from tests.conftest import csrf_post

# Fixture note: the root conftest's DB fixture is `db_session` (same db_url as
# the HTTP client fixtures — the authed_admin + db_session pairing is proven by
# tests/integration/test_mfa_passkey_routes.py::test_passkey_delete_removes_row).
# Fixture users are created by tests/factories.py::create_user with default
# password "pw-12345678".


async def _client_session(db_session: AsyncSession, client: AsyncClient) -> AuthSession:
    signed = client.cookies.get(SESSION_COOKIE)
    assert signed is not None
    sid = unsign_session_id(signed)
    assert sid is not None
    sess = await db_session.get(AuthSession, sid)
    assert sess is not None
    return sess


async def _make_stale(db_session: AsyncSession, client: AsyncClient) -> None:
    sess = await _client_session(db_session, client)
    sess.reauthenticated_at = datetime.now(UTC) - timedelta(seconds=999)
    await db_session.commit()


async def _enroll_totp(db_session: AsyncSession, client: AsyncClient) -> str:
    """Attach a confirmed TOTP to the client's user; return the secret."""
    sess = await _client_session(db_session, client)
    secret = pyotp.random_base32()
    db_session.add(
        UserTotp(
            user_id=sess.user_id,
            secret_encrypted=encrypt_totp_secret(secret),
            confirmed_at=now_utc(),
        )
    )
    user = await db_session.get(User, sess.user_id)
    assert user is not None
    user.mfa_enrolled_at = now_utc()
    await db_session.commit()
    return secret


async def test_step_up_page_renders_code_form_for_totp_user(
    db_session: AsyncSession, admin_client: AsyncClient
) -> None:
    await _enroll_totp(db_session, admin_client)
    r = await admin_client.get("/auth/step-up?next=/users", follow_redirects=False)
    assert r.status_code == 200
    assert 'action="/auth/step-up/verify"' in r.text
    assert 'name="code"' in r.text
    assert 'name="password"' not in r.text  # strong-factor users never see password


async def test_step_up_page_renders_password_form_for_factorless_user(
    admin_client: AsyncClient,
) -> None:
    r = await admin_client.get("/auth/step-up", follow_redirects=False)
    assert r.status_code == 200
    assert 'name="password"' in r.text
    assert 'name="code"' not in r.text


async def test_step_up_verify_totp_stamps_session_and_redirects(
    db_session: AsyncSession, admin_client: AsyncClient
) -> None:
    secret = await _enroll_totp(db_session, admin_client)
    await _make_stale(db_session, admin_client)
    r = await csrf_post(
        admin_client,
        "/auth/step-up/verify",
        {"code": pyotp.TOTP(secret).now(), "next": "/users"},
        bootstrap_url="/auth/step-up",
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/users"
    sess = await _client_session(db_session, admin_client)
    await db_session.refresh(sess)
    ra = sess.reauthenticated_at
    assert ra is not None
    if ra.tzinfo is None:
        ra = ra.replace(tzinfo=UTC)
    assert datetime.now(UTC) - ra < timedelta(seconds=30)


async def test_step_up_verify_password_for_factorless_user(
    db_session: AsyncSession, admin_client: AsyncClient
) -> None:
    # "pw-12345678" is tests/factories.py::create_user's default password.
    await _make_stale(db_session, admin_client)
    r = await csrf_post(
        admin_client,
        "/auth/step-up/verify",
        {"password": "pw-12345678", "next": "/"},
        bootstrap_url="/auth/step-up",
        follow_redirects=False,
    )
    assert r.status_code == 303


async def test_step_up_verify_password_refused_for_strong_factor_user(
    db_session: AsyncSession, admin_client: AsyncClient
) -> None:
    await _enroll_totp(db_session, admin_client)
    await _make_stale(db_session, admin_client)
    r = await csrf_post(
        admin_client,
        "/auth/step-up/verify",
        {"password": "pw-12345678", "next": "/"},
        bootstrap_url="/auth/step-up",
        follow_redirects=False,
    )
    assert r.status_code == 400  # password never satisfies a strong-factor account


async def test_step_up_wrong_code_400_and_counts_toward_lockout(
    db_session: AsyncSession, admin_client: AsyncClient
) -> None:
    await _enroll_totp(db_session, admin_client)
    await _make_stale(db_session, admin_client)
    r = await csrf_post(
        admin_client,
        "/auth/step-up/verify",
        {"code": "000000", "next": "/"},
        bootstrap_url="/auth/step-up",
        follow_redirects=False,
    )
    assert r.status_code == 400
    sess = await _client_session(db_session, admin_client)
    user = await db_session.get(User, sess.user_id)
    assert user is not None
    await db_session.refresh(user)
    assert user.failed_login_count == 1


async def test_anonymous_step_up_page_bounces_to_login(
    anonymous_client: AsyncClient,
) -> None:
    r = await anonymous_client.get("/auth/step-up", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith("/login")
```

- [ ] **Step 7: Run them to verify they fail**

Run: `uv run pytest tests/integration/test_step_up_flow.py -v`
Expected: FAIL — 404 on `/auth/step-up` (router not registered yet).

- [ ] **Step 8: Implement exception, dependency, routes, template, handler, allowlist**

`src/idraa/errors.py` — append:

```python
class StepUpRequired(IdraaError):
    """Sensitive action attempted with a stale session (step-up / sudo mode).

    Raised by routes/deps.py::require_recent_auth; translated by
    app.py::_step_up_handler into the /auth/step-up challenge. Carries the
    URL to return to after re-verification.
    """

    def __init__(self, next_url: str) -> None:
        super().__init__(next_url)
        self.next_url = next_url
```

`src/idraa/routes/deps.py` — relocate `_safe_next` here as `safe_next` (public, shared by auth + step_up routers) and add the step-up dependency. Add imports `from urllib.parse import urlsplit` and `from idraa.errors import StepUpRequired`, `from idraa.services.auth import is_step_up_fresh`:

```python
def safe_next(raw: str | None) -> str:
    """Sanitize a ``?next=`` redirect target (relocated from routes/auth.py).

    Returns ``raw`` only when it is a same-origin absolute path: must start
    with a single ``/`` and NOT with ``//`` or ``/\\`` (browsers normalize a
    leading backslash to a forward slash for special schemes, so ``/\\evil``
    is an equivalent protocol-relative open-redirect vector to ``//evil``).
    Anything else falls back to ``/``.
    """
    if raw and raw.startswith("/") and raw[1:2] not in ("/", "\\"):
        return raw
    return "/"


def _step_up_next(request: Request) -> str:
    """The URL the user should land on after passing the step-up challenge.

    GET targets round-trip themselves (path + query — safe to re-issue).
    POST targets cannot be replayed by a redirect, so fall back to the
    same-origin Referer path (the page holding the form/button); the user
    re-triggers the action, which then passes the fresh check.
    """
    if request.method == "GET":
        target = request.url.path
        if request.url.query:
            target = f"{target}?{request.url.query}"
        return safe_next(target)
    ref = request.headers.get("referer", "")
    parts = urlsplit(ref)
    if parts.netloc and parts.netloc != request.url.netloc:
        return "/"
    target = parts.path or "/"
    if parts.query:
        target = f"{target}?{parts.query}"
    return safe_next(target)


def require_recent_auth(
    request: Request,
    user: User | None = Depends(current_user),
    sess: AuthSession | None = Depends(current_session),
) -> None:
    """Step-up ("sudo mode") gate for sensitive actions.

    Wire as a ROUTE-DECORATOR dependency so it runs before handler params::

        @router.post("/x/delete", dependencies=[Depends(require_recent_auth)])

    Anonymous callers get the same 401 as require_user (-> /login redirect
    via _auth_redirect_handler). Stale sessions raise StepUpRequired, which
    app.py::_step_up_handler turns into the /auth/step-up challenge.
    """
    if user is None or sess is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    if not is_step_up_fresh(sess):
        raise StepUpRequired(next_url=_step_up_next(request))
```

`src/idraa/routes/auth.py` — delete the module-level `_safe_next` definition and import the relocated helper under the SAME private name: `from idraa.routes.deps import safe_next as _safe_next`. Call sites stay UNTOUCHED (there are 3: ~lines 116, 134, 235). Do NOT rename them: two call sites assign a LOCAL variable already named `safe_next` (`safe_next = _safe_next(...)`), so renaming the callable to bare `safe_next` would make Python compile the name as a function-local self-reference and raise `UnboundLocalError` on every `POST /login` / `POST /login/mfa` (plan-gate Arch-I1 / CQ-B2). Keep behavior identical.

Create `src/idraa/routes/step_up.py`:

```python
"""Step-up ("sudo mode") challenge + verify routes (P2).

GET /auth/step-up renders the challenge; POST /auth/step-up/verify accepts a
TOTP code or recovery code for strong-factor users, or the account password
for users with NO strong factor yet. Password is NEVER offered to a
strong-factor account — that would collapse step-up assurance to
knowledge-only, which is exactly what a phished-password attacker holds.
The factor-less password path also prevents an enrollment deadlock: the
enrollment endpoints are themselves step-up-gated.

On success the CURRENT AuthSession.reauthenticated_at is stamped and the
user is 303'd back to `next`. Interrupted POST actions are NOT replayed —
the user re-triggers them inside the fresh window.

Passkey step-up is the ceremony pair /auth/step-up/passkey/* (Task 2).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.app import templates
from idraa.models._types import now_utc
from idraa.models.mfa import WebAuthnCredential
from idraa.models.session import AuthSession
from idraa.models.user import User
from idraa.routes.deps import (
    client_ip,
    current_session,
    get_db,
    require_user,
    safe_next,
)
from idraa.services.audit import AuditWriter
from idraa.services.auth import (
    is_locked,
    register_failed_login,
    reset_login_throttle,
    verify_user_password,
)
from idraa.services.mfa_enrollment import user_has_strong_factor
from idraa.services.second_factor import verify_totp_or_recovery

router = APIRouter()


async def _challenge_context(
    db: AsyncSession, user: User, next_url: str, error: str | None
) -> dict[str, object]:
    passkey_count = await db.scalar(
        select(func.count())
        .select_from(WebAuthnCredential)
        .where(WebAuthnCredential.user_id == user.id)
    )
    return {
        "current_user": user,
        "error": error,
        "next": next_url,
        "has_strong_factor": await user_has_strong_factor(db, user.id),
        "has_passkeys": bool(passkey_count),
    }


@router.get("/auth/step-up", response_class=HTMLResponse)
async def step_up_get(
    request: Request,
    next: str | None = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_user),
) -> HTMLResponse:
    ctx = await _challenge_context(db, user, safe_next(next), error=None)
    return templates.TemplateResponse(request, "auth/step_up.html", ctx)


@router.post("/auth/step-up/verify")
async def step_up_verify(
    request: Request,
    code: str = Form(default="", max_length=32),
    password: str = Form(default="", max_length=1024),
    next: str | None = Form(default=None, max_length=2048),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_user),
    sess: AuthSession | None = Depends(current_session),
) -> Response:
    target = safe_next(next)
    # Rebind BOTH state objects into THIS handler's db session before any
    # mutation (P1 detached-instance convention — see routes/mfa.py).
    user = await db.get(User, user.id) or user
    live_sess = await db.get(AuthSession, sess.id) if sess is not None else None
    if live_sess is None:
        return RedirectResponse("/login", status_code=303)

    async def _render_error() -> Response:
        # ONE generic body for wrong-code, wrong-password, AND locked —
        # no lockout oracle (mirrors /login's anti-enumeration posture).
        ctx = await _challenge_context(db, user, target, error="Invalid code or password")
        return templates.TemplateResponse(
            request, "auth/step_up.html", ctx, status_code=400
        )

    async def _fail() -> Response:
        await AuditWriter(db).log(
            organization_id=user.organization_id,
            entity_type="user",
            entity_id=user.id,
            action="user.step_up_failed",
            changes={},
            user_id=user.id,
            ip_address=client_ip(request),
        )
        register_failed_login(user)
        if is_locked(user):  # this miss just tripped the lock -> audit
            await AuditWriter(db).log(
                organization_id=user.organization_id,
                entity_type="user",
                entity_id=user.id,
                action="user.login_locked_out",
                changes={},
                user_id=user.id,
                ip_address=client_ip(request),
            )
        return await _render_error()

    if is_locked(user):
        # Already locked: generic bounce with NO audit row and NO counter
        # bump — mirrors /login/mfa's locked short-circuit. Auditing here
        # would let a hostile session-holder grow the append-only audit_log
        # without bound (plan-gate Sec-N3; the 2026-06-29 outage was
        # SQLite-volume exhaustion).
        return await _render_error()

    method: str | None = None
    if await user_has_strong_factor(db, user.id):
        if code:
            method = await verify_totp_or_recovery(
                db, user, code, ip_address=client_ip(request)
            )
        # NOTE: password deliberately ignored for strong-factor accounts.
    elif password:
        if verify_user_password(user, password):
            method = "password"

    if method is None:
        return await _fail()

    reset_login_throttle(user)
    live_sess.reauthenticated_at = now_utc()
    await AuditWriter(db).log(
        organization_id=user.organization_id,
        entity_type="session",
        entity_id=live_sess.id,
        action="user.step_up",
        changes={"method": method},
        user_id=user.id,
        ip_address=client_ip(request),
    )
    return RedirectResponse(target, status_code=303)
```

Create `src/idraa/templates/auth/step_up.html` (modeled on `auth/mfa_challenge.html`):

```html
{% extends "base.html" %}
{% block title %}Confirm it's you{% endblock %}
{% block container_class %}max-w-md mx-auto{% endblock %}
{% block content %}
<div class="pt-8 space-y-4">
  {# pl-16 below md clears the fixed ☰ (see macros/page_header.html) #}
  <h1 class="text-display text-ink-1 pl-16 md:pl-0">Confirm it's you</h1>
  {% if error %}<div class="alert alert-error">{{ error }}</div>{% endif %}
  <p class="text-sm text-ink-2">This action is sensitive, so we need you to re-verify.</p>
  {% if has_strong_factor %}
  <form method="post" action="/auth/step-up/verify" class="space-y-4">
    {{ csrf_field() }}
    <input type="hidden" name="next" value="{{ next | default('/') }}">
    <input name="code" inputmode="text" autocomplete="one-time-code" maxlength="32"
           class="input input-bordered w-full" placeholder="123456" required autofocus>
    <button class="btn btn-primary btn-sm" type="submit">Verify code</button>
  </form>
  <p class="text-xs text-ink-3">Enter the 6-digit code from your authenticator, or a recovery code.</p>
  {% else %}
  <form method="post" action="/auth/step-up/verify" class="space-y-4">
    {{ csrf_field() }}
    <input type="hidden" name="next" value="{{ next | default('/') }}">
    <input type="password" name="password" autocomplete="current-password" maxlength="1024"
           class="input input-bordered w-full" placeholder="Your password" required autofocus>
    <button class="btn btn-primary btn-sm" type="submit">Verify password</button>
  </form>
  {% endif %}
</div>
{% endblock %}
```

`src/idraa/app.py` — three edits:

1. Import + register the router next to the other auth routers (after `mfa_router`):
```python
    app.include_router(step_up_router.router)
```
(with the deferred import alongside the others: `from idraa.routes import step_up as step_up_router`.)

2. Add the handler near `_auth_redirect_handler` (import `StepUpRequired` from `idraa.errors`, `quote` from `urllib.parse`):
```python
async def _step_up_handler(request: StarletteRequest, exc: StepUpRequired) -> Response:
    """StepUpRequired -> the /auth/step-up challenge.

    Browsers get a 303; HTMX callers get 204 + HX-Redirect (mirrors
    EnrollmentGuardMiddleware); fetch/JSON callers (Accept:
    application/json — webauthn.js sets it) get a structured 401 whose
    `redirect` the client follows. The next target inside `dest` was
    sanitized by routes/deps.py::_step_up_next before it reached the
    exception.
    """
    dest = f"/auth/step-up?next={quote(exc.next_url, safe='/')}"
    if request.headers.get("HX-Request") == "true":
        return Response(status_code=204, headers={"HX-Redirect": dest})
    if "application/json" in request.headers.get("accept", ""):
        return JSONResponse({"error": "step_up_required", "redirect": dest}, status_code=401)
    return RedirectResponse(dest, status_code=303)
```

3. Register it beside the existing handlers:
```python
    app.add_exception_handler(StepUpRequired, _step_up_handler)  # type: ignore[arg-type]
```

`src/idraa/middleware/enrollment_guard.py` — extend the allowlist (deadlock guard, semantics note #6):

```python
_ALLOWLIST = (
    "/account/security",
    "/auth/step-up",
    "/login",
    "/logout",
    "/setup",
    "/healthz",
    "/static",
)
```

- [ ] **Step 9: Run the integration tests + neighbors**

Run: `uv run pytest tests/integration/test_step_up_flow.py tests/integration/test_login_mfa_flow.py tests/integration/test_enrollment_interstitial.py tests/unit/test_step_up_freshness.py tests/unit/test_app_middleware_order.py -v`
Expected: ALL PASS.

- [ ] **Step 10: Run the fast gate**

Run: `uv run python scripts/run_local_gate.py`
Expected: ruff + format + mypy + fast pytest all green.

- [ ] **Step 11: Commit**

```bash
git add -A
git commit -m "feat(auth): step-up challenge + require_recent_auth dependency (P2 Task 1)"
```

---

### Task 2: Passkey step-up ceremony

**Files:**
- Modify: `src/idraa/services/webauthn_service.py` (`authentication_options` gains `allow_credential_ids`)
- Modify: `src/idraa/services/auth.py` (step-up challenge cookie helpers, DISTINCT salt — Sec-N1)
- Modify: `src/idraa/routes/step_up.py` (ceremony endpoints)
- Modify: `src/idraa/static/js/webauthn.js` (shared assertion helper + `stepUp` + step-up-aware `post()`)
- Modify: `src/idraa/templates/auth/step_up.html` (passkey button)
- Test: `tests/unit/test_webauthn_service.py` (extend)
- Test: `tests/integration/test_step_up_passkey.py`
- Test: `tests/e2e/test_passkey_e2e.py` (extend — virtual authenticator step-up)

**Interfaces:**
- Consumes: Task 1's `is_step_up_fresh` semantics, `_challenge_context`, `webauthn_service.verify_authentication` / `sign_count_ok` / `parse_raw_id`.
- Produces:
  - `webauthn_service.authentication_options(allow_credential_ids: list[bytes] | None = None) -> tuple[str, str]` (default `None` ⇒ empty list ⇒ usernameless; EXISTING callers unchanged)
  - `services/auth.py`: `sign_stepup_challenge(challenge_b64url: str) -> str`, `load_stepup_challenge(token: str, max_age: int = 300) -> str | None`, `set_stepup_challenge_cookie(response, challenge_b64url) -> None`, `clear_stepup_challenge_cookie(response) -> None` — salt `rf-webauthn-stepup`, cookie `rf_webauthn_stepup` (Sec-N1: the login/registration challenge and the step-up challenge are different PURPOSES and must not be interchangeable)
  - Routes: `POST /auth/step-up/passkey/options`, `POST /auth/step-up/passkey/verify` (JSON: `{"credential": ..., "next": ...}` → `{"next": "<safe>"}`)
  - `window.idraaWebAuthn.stepUp(next)` in `webauthn.js`
  - `post()` in `webauthn.js` sends `Accept: application/json` and follows a 401 `{"error":"step_up_required","redirect":...}` by navigating — this is what makes Task 3's gating of the ENROLLMENT fetch endpoints degrade gracefully.

**Security invariants:**
1. The step-up verify must ONLY accept a credential whose `user_id` equals the CURRENT session's user — another user's valid passkey must be rejected before signature verification is even attempted.
2. Sec-I1: the forensically-meaningful failure branches (`unknown credential` — hijacker probing with a foreign passkey; `verification failed`; `counter` — cloned-authenticator signal) each write a `user.step_up_failed` audit row with `{"method": "passkey", "reason": <branch>}`. Pre-crypto shape/staleness branches (`challenge expired`, `malformed credential`, `no session`) are NOT audited — indistinguishable from benign client bugs. Passkey failures deliberately do NOT call `register_failed_login`: there is no guessable secret space to throttle (matches P1's lockout-exempt passkey login).

- [ ] **Step 1: Write the failing unit test for the options extension**

Append to `tests/unit/test_webauthn_service.py` — mirror the file's existing `_reset(monkeypatch)` env-pinning helper so the settings singleton is deterministic (plan-gate CQ-N3), and do NOT add a default-usernameless test (the existing `test_authentication_options_usernameless` already pins that; the extension leaves the default byte-identical):

```python
def test_authentication_options_scopes_allow_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import json

    from idraa.services import webauthn_service

    _reset(monkeypatch)  # the file's existing settings-pinning helper
    options_json, _challenge = webauthn_service.authentication_options(
        allow_credential_ids=[b"cred-one", b"cred-two"]
    )
    parsed = json.loads(options_json)
    assert len(parsed["allowCredentials"]) == 2
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/test_webauthn_service.py -v`
Expected: FAIL — `authentication_options() got an unexpected keyword argument`.

- [ ] **Step 3: Implement the service extension**

`src/idraa/services/webauthn_service.py` — replace `authentication_options`:

```python
def authentication_options(
    allow_credential_ids: list[bytes] | None = None,
) -> tuple[str, str]:
    """Login (usernameless, default) or step-up (scoped) assertion options.

    An empty allow-list means discoverable/usernameless (the login flow).
    Step-up passes the CURRENT user's credential ids so the browser only
    offers that user's passkeys.
    """
    s = get_settings()
    options = generate_authentication_options(
        rp_id=s.webauthn_rp_id,
        allow_credentials=[
            PublicKeyCredentialDescriptor(id=cid) for cid in (allow_credential_ids or [])
        ],
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    return options_to_json(options), bytes_to_base64url(options.challenge)
```

- [ ] **Step 4: Run unit tests**

Run: `uv run pytest tests/unit/test_webauthn_service.py -v`
Expected: PASS (including the pre-existing login-path tests — default unchanged).

- [ ] **Step 5: Write the failing integration tests**

`tests/integration/test_step_up_passkey.py` (JSON+CSRF idiom mirrors `tests/integration/test_mfa_passkey_routes.py`: bootstrap-GET for the `csrf_token` cookie, then POST with the `X-CSRF-Token` header):

```python
from __future__ import annotations

import base64
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.mfa import WebAuthnCredential
from idraa.models.session import AuthSession
from idraa.services.auth import SESSION_COOKIE, unsign_session_id


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


async def _client_session(db_session: AsyncSession, client: AsyncClient) -> AuthSession:
    signed = client.cookies.get(SESSION_COOKIE)
    assert signed is not None
    sid = unsign_session_id(signed)
    assert sid is not None
    sess = await db_session.get(AuthSession, sid)
    assert sess is not None
    return sess


async def _csrf_token(client: AsyncClient) -> str:
    await client.get("/auth/step-up")
    token = client.cookies.get("csrf_token")
    assert token is not None
    return token


async def _attach_passkey(
    db_session: AsyncSession, user_id: uuid.UUID, cred_id: bytes = b"test-cred"
) -> WebAuthnCredential:
    cred = WebAuthnCredential(
        user_id=user_id,
        credential_id=cred_id,
        public_key=b"unused-in-these-tests",
        sign_count=0,
        nickname="Test key",
    )
    db_session.add(cred)
    await db_session.commit()
    return cred


async def test_options_scoped_to_own_credentials(
    db_session: AsyncSession, admin_client: AsyncClient
) -> None:
    sess = await _client_session(db_session, admin_client)
    await _attach_passkey(db_session, sess.user_id)
    token = await _csrf_token(admin_client)
    r = await admin_client.post(
        "/auth/step-up/passkey/options", headers={"X-CSRF-Token": token}
    )
    assert r.status_code == 200
    assert len(r.json()["allowCredentials"]) == 1


async def test_options_without_passkeys_is_400(admin_client: AsyncClient) -> None:
    token = await _csrf_token(admin_client)
    r = await admin_client.post(
        "/auth/step-up/passkey/options", headers={"X-CSRF-Token": token}
    )
    assert r.status_code == 400
    assert "no passkeys" in r.json()["error"]


async def test_verify_rejects_other_users_credential(
    db_session: AsyncSession, admin_client: AsyncClient
) -> None:
    # ANOTHER user owns the target passkey; the admin tries to step up with
    # it. Seed the other user directly — the authed_* fixtures all share one
    # underlying AsyncClient, so a second HTTP client is a cookie-clobber trap.
    from tests.factories import create_org, create_user

    other_org = await create_org(db_session, name="Other Org")
    other = await create_user(db_session, other_org, email="other@test.local")
    await _attach_passkey(db_session, other.id, cred_id=b"other-cred")
    admin_sess = await _client_session(db_session, admin_client)
    await _attach_passkey(db_session, admin_sess.user_id, cred_id=b"admin-cred")
    token = await _csrf_token(admin_client)
    r = await admin_client.post(  # prime the challenge cookie
        "/auth/step-up/passkey/options", headers={"X-CSRF-Token": token}
    )
    assert r.status_code == 200
    r = await admin_client.post(
        "/auth/step-up/passkey/verify",
        json={"credential": {"rawId": _b64url(b"other-cred")}, "next": "/"},
        headers={"X-CSRF-Token": token},
    )
    assert r.status_code == 400
    assert "unknown credential" in r.json()["error"]


async def test_verify_success_stamps_session_and_sanitizes_next(
    db_session: AsyncSession, admin_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Full success path with the crypto verification monkeypatched (a real
    assertion needs a real authenticator — the e2e covers that; this pins the
    session-stamping + next-sanitizing behavior). Pattern from
    test_mfa_passkey_routes.py::test_passkey_verify_duplicate_credential_id."""
    from idraa.services import webauthn_service

    sess = await _client_session(db_session, admin_client)
    await _attach_passkey(db_session, sess.user_id, cred_id=b"admin-cred")
    # Backdate so the stamp is observable.
    sess.reauthenticated_at = datetime.now(UTC) - timedelta(seconds=999)
    await db_session.commit()

    monkeypatch.setattr(
        webauthn_service, "verify_authentication", lambda *a, **k: 1
    )
    token = await _csrf_token(admin_client)
    r = await admin_client.post(
        "/auth/step-up/passkey/options", headers={"X-CSRF-Token": token}
    )
    assert r.status_code == 200
    r = await admin_client.post(
        "/auth/step-up/passkey/verify",
        json={
            "credential": {"rawId": _b64url(b"admin-cred")},
            "next": "https://evil.example/phish",  # not a local path -> "/"
        },
        headers={"X-CSRF-Token": token},
    )
    assert r.status_code == 200
    assert r.json()["next"] == "/"
    await db_session.refresh(sess)
    ra = sess.reauthenticated_at
    assert ra is not None
    if ra.tzinfo is None:
        ra = ra.replace(tzinfo=UTC)
    assert datetime.now(UTC) - ra < timedelta(seconds=30)
```

- [ ] **Step 6: Run to verify failure**

Run: `uv run pytest tests/integration/test_step_up_passkey.py -v`
Expected: FAIL — 404 (endpoints missing).

- [ ] **Step 7: Implement the ceremony endpoints**

First append the step-up challenge cookie helpers to `src/idraa/services/auth.py`, directly after `clear_totp_pending_cookie` (Sec-N1 — distinct salt per the module's own convention; payload identical to the login challenge, purpose is not):

```python
# --- Step-up WebAuthn challenge: DISTINCT salt + cookie from the login/
# registration ceremony (P2 plan-gate Sec-N1). Same payload shape, different
# PURPOSE: a challenge minted for the anonymous login ceremony must never
# validate for an authenticated step-up re-verify, per the salt convention
# above _serializer. ---
def _webauthn_stepup_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_settings().session_secret, salt="rf-webauthn-stepup")


def sign_stepup_challenge(challenge_b64url: str) -> str:
    return _webauthn_stepup_serializer().dumps(challenge_b64url)


def load_stepup_challenge(token: str, max_age: int = 300) -> str | None:
    try:
        value: str = _webauthn_stepup_serializer().loads(token, max_age=max_age)
    except BadData:
        return None
    return value


def set_stepup_challenge_cookie(response: Response, challenge_b64url: str) -> None:
    response.set_cookie(
        "rf_webauthn_stepup",
        sign_stepup_challenge(challenge_b64url),
        max_age=300,
        httponly=True,
        samesite="lax",
        secure=_secure(),
        path="/",
    )


def clear_stepup_challenge_cookie(response: Response) -> None:
    response.delete_cookie("rf_webauthn_stepup", path="/")
```

Then append to `src/idraa/routes/step_up.py` (add imports: `Any` from typing, `Body` from fastapi, `json`, `webauthn_service` from idraa.services, `set_stepup_challenge_cookie` / `load_stepup_challenge` / `clear_stepup_challenge_cookie` from idraa.services.auth):

```python
def _json_err(msg: str) -> Response:
    return Response(
        content=json.dumps({"error": msg}), status_code=400, media_type="application/json"
    )


@router.post("/auth/step-up/passkey/options")
async def step_up_passkey_options(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_user),
) -> Response:
    creds = (
        (
            await db.execute(
                select(WebAuthnCredential).where(WebAuthnCredential.user_id == user.id)
            )
        )
        .scalars()
        .all()
    )
    if not creds:
        return _json_err("no passkeys enrolled")
    options_json, challenge = webauthn_service.authentication_options(
        allow_credential_ids=[c.credential_id for c in creds]
    )
    resp = Response(content=options_json, media_type="application/json")
    set_stepup_challenge_cookie(resp, challenge)
    return resp


@router.post("/auth/step-up/passkey/verify")
async def step_up_passkey_verify(
    request: Request,
    payload: dict[str, Any] = Body(...),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_user),
    sess: AuthSession | None = Depends(current_session),
) -> Response:
    async def _audit_failure(reason: str) -> None:
        # Sec-I1: unknown-credential (hijacker probing with a foreign
        # passkey) and counter (cloned-authenticator signal) are this
        # feature's highest-value forensic events. No register_failed_login
        # for passkey misses — no guessable secret space to throttle
        # (deliberate; matches P1's lockout-exempt passkey login).
        await AuditWriter(db).log(
            organization_id=user.organization_id,
            entity_type="user",
            entity_id=user.id,
            action="user.step_up_failed",
            changes={"method": "passkey", "reason": reason},
            user_id=user.id,
            ip_address=client_ip(request),
        )

    signed = request.cookies.get("rf_webauthn_stepup")
    challenge = load_stepup_challenge(signed) if signed else None
    if challenge is None:
        return _json_err("challenge expired")
    credential = payload.get("credential")
    if not isinstance(credential, dict) or not credential.get("rawId"):
        return _json_err("malformed credential")
    raw_id = webauthn_service.parse_raw_id(credential)
    # OWNERSHIP CHECK (security invariant #1): only the CURRENT user's
    # credential may satisfy step-up — filter by user_id in the query so
    # another user's passkey is "unknown" before any crypto runs.
    cred = (
        (
            await db.execute(
                select(WebAuthnCredential).where(
                    WebAuthnCredential.credential_id == raw_id,
                    WebAuthnCredential.user_id == user.id,
                )
            )
        )
        .scalars()
        .first()
    )
    if cred is None:
        await _audit_failure("unknown credential")
        return _json_err("unknown credential")
    try:
        new_count = webauthn_service.verify_authentication(
            credential, challenge, cred.public_key, cred.sign_count
        )
    except Exception as exc:  # any bad/tampered assertion -> 400, not 500
        await _audit_failure(f"verification failed: {type(exc).__name__}")
        return _json_err(f"verification failed: {type(exc).__name__}")
    if not webauthn_service.sign_count_ok(cred.sign_count, new_count):
        await _audit_failure("counter")
        return _json_err("counter")
    cred.sign_count = new_count
    cred.last_used_at = now_utc()

    user = await db.get(User, user.id) or user  # rebind before throttle reset
    live_sess = await db.get(AuthSession, sess.id) if sess is not None else None
    if live_sess is None:
        return _json_err("no session")
    reset_login_throttle(user)
    live_sess.reauthenticated_at = now_utc()
    await AuditWriter(db).log(
        organization_id=user.organization_id,
        entity_type="session",
        entity_id=live_sess.id,
        action="user.step_up",
        changes={"method": "passkey"},
        user_id=user.id,
        ip_address=client_ip(request),
    )
    target = safe_next(payload.get("next") if isinstance(payload.get("next"), str) else None)
    resp = Response(
        content=json.dumps({"next": target}), media_type="application/json"
    )
    clear_stepup_challenge_cookie(resp)
    return resp
```

- [ ] **Step 8: Wire the browser side**

`src/idraa/static/js/webauthn.js` — three changes, keeping the file eval-free and build-free:

1. `post()` gains an Accept header + step-up redirect handling:

```js
  function post(url, body) {
    var opts = {
      method: "POST",
      headers: { "X-CSRF-Token": csrf(), "Accept": "application/json" },
      credentials: "same-origin",
    };
    if (body !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    return fetch(url, opts).then(function (resp) {
      if (resp.status !== 401) return resp;
      return resp.clone().json().then(function (data) {
        if (data && data.error === "step_up_required" && data.redirect) {
          window.location.assign(data.redirect);
          return new Promise(function () {}); // navigation takes over
        }
        return resp;
      }, function () { return resp; });
    });
  }
```

2. Extract the shared assertion ceremony and rewrite `authenticate` on top of it:

```js
  async function assertionCeremony(optionsUrl, verifyUrl, extra) {
    var optsResp = await post(optionsUrl);
    if (!optsResp.ok) throw new Error("options request failed");
    var options = await optsResp.json();
    options.challenge = b64urlToBuf(options.challenge);
    (options.allowCredentials || []).forEach(function (c) { c.id = b64urlToBuf(c.id); });
    var cred = await navigator.credentials.get({ publicKey: options });
    var body = Object.assign({ credential: encodeAssertion(cred) }, extra || {});
    var verifyResp = await post(verifyUrl, body);
    if (!verifyResp.ok) throw new Error("verification failed");
    return verifyResp.json();
  }
  async function authenticate() {
    var data = await assertionCeremony("/login/passkey/options", "/login/passkey/verify");
    window.location.assign(data.next || "/");
  }
  async function stepUp(next) {
    var data = await assertionCeremony(
      "/auth/step-up/passkey/options", "/auth/step-up/passkey/verify", { next: next || "/" });
    window.location.assign(data.next || "/");
  }
```

3. Export: `window.idraaWebAuthn = { register: register, authenticate: authenticate, stepUp: stepUp };`

`src/idraa/templates/auth/step_up.html` — inside the `{% if has_strong_factor %}` branch, ABOVE the code form (mirrors `auth/login.html`'s button conventions exactly):

```html
  {% if has_passkeys %}
  {# SINGLE-quoted onclick: |tojson emits raw double-quotes, which would
     terminate a double-quoted attribute (repo precedent: the Sec-B1 note in
     macros/action_menu.html and analyses/new.html — plan-gate CQ-B1). #}
  <button class="btn btn-outline btn-sm w-full" type="button"
          onclick='idraaWebAuthn.stepUp({{ next | default("/") | tojson }}).catch(function(e){alert(e.message)})'
          x-show="!!window.PublicKeyCredential">Use your passkey</button>
  <div class="divider text-xs text-ink-3">or</div>
  {% endif %}
```

- [ ] **Step 9: Run integration + JS-adjacent suites**

Run: `uv run pytest tests/integration/test_step_up_passkey.py tests/integration/test_mfa_passkey_routes.py tests/integration/test_login_mfa_flow.py -v`
Expected: ALL PASS.

- [ ] **Step 10: Extend the e2e (virtual authenticator)**

First, in `tests/e2e/conftest.py`, refactor `passkey_server_url` so the DB path is reachable: extract the body into a module-scoped fixture `passkey_server` yielding `tuple[str, Path]` (`(url, db_file)`), and keep `passkey_server_url` as a thin alias returning `passkey_server[0]`. No env changes — same `WEBAUTHN_RP_ID=localhost` / `AUTH_MFA_POLICY=optional` block.

Then EXTEND the EXISTING `test_passkey_register_then_usernameless_login` in `tests/e2e/test_passkey_e2e.py` — do NOT write a separate test. Plan-gate CQ-B3: the CDP virtual authenticator (and its resident private key) dies with the browser at the end of the existing test, so a new test's fresh authenticator has NO discoverable credential and "Sign in with a passkey" would `NotAllowedError`. The step-up leg must run inside the SAME browser/authenticator session, right after the usernameless sign-in assertions (this also removes any file-order coupling — Arch-N3).

1. Change the test's fixture parameter from `passkey_server_url: str` to `passkey_server: "tuple[str, Path]"` and unpack at the top: `base, db_file = passkey_server`. Add `import sqlite3` and `from pathlib import Path` to the file's imports.

2. Append INSIDE the `async with` block, after the final `assert "e2e@example.test" in content` and before `await browser.close()`:

```python
        # --- Step-up ("sudo mode") re-verify with the SAME passkey. ---
        # Backdate every session (only ours exists) past the 600 s window by
        # writing the server's SQLite directly. datetime('now') is UTC and
        # naive — is_step_up_fresh reattaches UTC on read.
        conn = sqlite3.connect(db_file)
        try:
            conn.execute(
                "UPDATE auth_sessions SET reauthenticated_at = datetime('now', '-1 hour')"
            )
            conn.commit()
        finally:
            conn.close()

        # Drive the challenge page directly (the integration catalog test
        # covers the redirect-into-it path; this pins the browser ceremony).
        await page.goto("/auth/step-up?next=/account/security")
        await page.click("text=Use your passkey")
        await page.wait_for_url(re.compile(r".*/account/security$"), timeout=10_000)
```

Run: `uv run pytest tests/e2e/test_passkey_e2e.py -m e2e -v`
Expected: PASS (run explicitly — the fast gate skips e2e).

- [ ] **Step 11: Commit**

```bash
git add -A
git commit -m "feat(auth): passkey step-up ceremony + webauthn.js stepUp (P2 Task 2)"
```

---

### Task 3: Wire `require_recent_auth` across the sensitive-action catalog

**Files (Modify only — one decorator kwarg + one import per file):**
- `src/idraa/routes/mfa.py` — `totp_enroll_get` (:85), `totp_enroll_post` (:122), `recovery_codes_generate` (:172), `passkey_register_options` (:207), `passkey_register_verify` (:231), `passkey_delete` (:281)
- `src/idraa/routes/users.py` — `users_export_csv` (:105), `invite_post` (:155), `edit_post` (:240), `set_active_post` (:320), `delete_post` (:381)
- `src/idraa/routes/scenarios.py` — `scenarios_export` (:574), `delete_scenario` (:1096)
- `src/idraa/routes/runs.py` — `post_delete_run` (:629), `post_purge_run_samples` (:674), `analyses_export_csv` (:857)
- `src/idraa/routes/library_overrides.py` — `delete_override` (:314)
- `src/idraa/routes/library.py` — `library_export_csv` (:126), `library_export` (:179), `delete_library_entry` (:299)
- `src/idraa/routes/controls.py` — `controls_export_csv` (:760), `control_delete` (:1019)
- `src/idraa/routes/qualitative_bands.py` — `delete_band` (:282)
- `src/idraa/routes/register_import.py` — `delete_profile` (:789)
- `src/idraa/routes/control_library.py` — `control_library_export_csv` (:141)
- `src/idraa/routes/reports.py` — `reports_export_csv` (:110), `download_run_pdf` (:150), `download_verification_workbook` (:274)
- `src/idraa/routes/overlays.py` — `overlays_export_csv` (:215), `overlay_deactivate` (:592 — plan-gate Sec-N2: overlays have no delete route; deactivate is their lifecycle-terminal destructive action and the un-gated sibling of the gated override delete)
- Test: `tests/integration/test_step_up_catalog.py`

**Interfaces:**
- Consumes: `require_recent_auth` from `idraa.routes.deps` (Task 1), `_make_stale`-style session backdating (Task 1's test helper — duplicate the small helper locally, tests must stay readable standalone).
- Produces: nothing new — pure wiring. Every route above gains `dependencies=[Depends(require_recent_auth)]` in its decorator.

**Wiring notes (all deliberate, for the reviewers):**
1. The catalog covers the design's four named delete targets AND the remaining delete endpoints (library entry, control soft-delete, qualitative band, register-import profile) — a default-deny uniform sweep; leaving siblings ungated is the inconsistency a security review would flag. Trimming is a plan-gate decision, not an implementation choice.
2. GET endpoints in the catalog are bulk exports (`log_bulk_export` funnel, design: "bulk exports") — a challenge redirect on a GET download is safe and returns the user to the file after verify.
3. `totp_enroll_get` and `passkey_register_options` are gated so the challenge fires BEFORE a QR is displayed / a browser ceremony starts, not after the user has scanned/tapped (flow coherence; options endpoints are POST, the QR page is GET).
4. Existing tests stay green because `create_session` stamps `reauthenticated_at = now` (Task 1) — every fixture-made session is fresh inside the 600 s window.
5. Decorator dependencies run before handler-parameter dependencies, so a stale session is challenged before `require_role` runs. A wrong-role user therefore steps up first and only then sees 403 — no information leak beyond endpoint existence (which 403 leaks anyway).
6. Single exports (`/library/entries/{id}/export`, `/scenarios/{id}/export`, `/runs/{id}/control-matrix.csv`) and the static `/overlays/template.csv` are NOT bulk egress and stay ungated (matches the design's "bulk exports" scope and `log_bulk_export`'s own boundary).
7. The design catalog's "change password" AND "disable TOTP" entries are satisfied VACUOUSLY: neither route exists in the app (P1 built TOTP enroll but no disable; there is no change-password route — grep-verified). When either is built, it MUST take `require_recent_auth` — record as a design note, not a P2 task (plan-gate Spec-I1).

- [ ] **Step 1: Write the failing table-driven integration test**

`tests/integration/test_step_up_catalog.py`:

```python
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.session import AuthSession
from idraa.services.auth import SESSION_COOKIE, unsign_session_id
from tests.conftest import csrf_post

_UUID = str(uuid.uuid4())

# Route inventory under step-up. The dependency fires BEFORE handler-level
# 404s, so nonexistent entity ids are fine — a stale session must be
# challenged regardless.
GET_TARGETS = [
    "/users/export.csv",
    "/scenarios/export",
    "/analyses/export.csv",
    "/library/export.csv",
    "/library/export",
    "/controls/export.csv",
    "/controls/library/export.csv",
    "/reports/export.csv",
    f"/reports/run/{_UUID}",
    f"/reports/run/{_UUID}/verification.xlsx",
    "/overlays/export.csv",
    "/account/security/totp/enroll",
]
POST_TARGETS = [
    "/users/invite",
    f"/users/{_UUID}/edit",
    f"/users/{_UUID}/set-active",
    f"/users/{_UUID}/delete",
    f"/scenarios/{_UUID}/delete",
    f"/runs/{_UUID}/delete",
    f"/runs/{_UUID}/purge-samples",
    f"/library/overrides/{_UUID}/delete",
    f"/library/entries/{_UUID}/delete",
    f"/controls/{_UUID}/delete",
    f"/qualitative-bands/{_UUID}/delete",
    f"/register-import/profiles/{_UUID}/delete",
    f"/overlays/{_UUID}/deactivate",
    "/account/security/totp/enroll",
    "/account/security/recovery-codes/generate",
    "/account/security/passkey/options",
    "/account/security/passkey/verify",
    f"/account/security/passkey/{_UUID}/delete",
]
# Task 4 appends f"/users/{_UUID}/reset-mfa" here when the route lands
# (plan-gate Arch-I2) — the table is the default-deny regression net.


async def _make_stale(db_session: AsyncSession, client: AsyncClient) -> None:
    signed = client.cookies.get(SESSION_COOKIE)
    assert signed is not None
    sid = unsign_session_id(signed)
    assert sid is not None
    sess = await db_session.get(AuthSession, sid)
    assert sess is not None
    sess.reauthenticated_at = datetime.now(UTC) - timedelta(seconds=999)
    await db_session.commit()


@pytest.mark.parametrize("url", GET_TARGETS)
async def test_stale_get_targets_are_challenged(
    url: str, db_session: AsyncSession, admin_client: AsyncClient
) -> None:
    await _make_stale(db_session, admin_client)
    r = await admin_client.get(url, follow_redirects=False)
    assert r.status_code == 303, url
    assert r.headers["location"].startswith("/auth/step-up?next="), url


@pytest.mark.parametrize("url", POST_TARGETS)
async def test_stale_post_targets_are_challenged(
    url: str, db_session: AsyncSession, admin_client: AsyncClient
) -> None:
    await _make_stale(db_session, admin_client)
    r = await csrf_post(
        admin_client, url, {}, bootstrap_url="/auth/step-up", follow_redirects=False
    )
    assert r.status_code == 303, url
    assert r.headers["location"].startswith("/auth/step-up?next="), url


async def test_fresh_session_passes_a_gated_route(
    admin_client: AsyncClient,
) -> None:
    # Fixture sessions are freshly stamped by create_session — no challenge.
    r = await admin_client.get("/users/export.csv", follow_redirects=False)
    assert r.status_code == 200


async def test_htmx_stale_gets_hx_redirect(
    db_session: AsyncSession, admin_client: AsyncClient
) -> None:
    await _make_stale(db_session, admin_client)
    r = await admin_client.get(
        "/users/export.csv", headers={"HX-Request": "true"}, follow_redirects=False
    )
    assert r.status_code == 204
    assert r.headers["HX-Redirect"].startswith("/auth/step-up?next=")
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/integration/test_step_up_catalog.py -v`
Expected: FAIL — stale requests reach handlers (200/303-to-elsewhere/404) because nothing is wired.

- [ ] **Step 3: Wire the catalog**

In EACH file listed above: add `require_recent_auth` to the existing `from idraa.routes.deps import ...` line, then add `dependencies=[Depends(require_recent_auth)]` to each listed route decorator. Example (`src/idraa/routes/users.py:105`):

```python
@router.get("/users/export.csv", dependencies=[Depends(require_recent_auth)])
async def users_export_csv(
```

Repeat mechanically for every route in the Files list. NO other changes — do not touch handler bodies, do not reorder parameters.

- [ ] **Step 4: Run the catalog test + the full fast pytest slice for the touched routers**

Run: `uv run pytest tests/integration/test_step_up_catalog.py -v`
Expected: ALL PASS.
Run: `uv run pytest tests/ -m "not e2e" -q`
Expected: no regressions (existing route suites stay green — fixture sessions are fresh).

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat(auth): step-up gate across sensitive-action catalog (P2 Task 3)"
```

---

### Task 4: Recovery ops — session revocation, admin factor-reset, revoke-on-deactivation (#80 L13), CLI backstop

**Files:**
- Modify: `src/idraa/services/auth.py` (`revoke_user_sessions`)
- Modify: `src/idraa/services/mfa_enrollment.py` (`reset_user_mfa`)
- Modify: `src/idraa/routes/users.py` (reset route + deactivation revocation in `edit_post` AND `set_active_post`)
- Modify: `src/idraa/templates/users/edit.html` (Reset MFA button)
- Create: `src/idraa/__main__.py`
- Test: `tests/unit/test_cli_reset_mfa.py`
- Test: `tests/integration/test_mfa_admin_reset.py`
- Test: `tests/integration/test_revoke_on_deactivation.py`

**Interfaces:**
- Consumes: `require_recent_auth` (Task 1), `get_user` (`services/users.py`), `AuditWriter`, `maybe_stamp_enrolled` conventions, `load_user_by_email` (`services/auth.py`), `get_session` (`idraa.db` — auto-commits on context exit).
- Produces:
  - `services/auth.py::revoke_user_sessions(db: AsyncSession, user_id: uuid.UUID) -> int` (rows deleted)
  - `services/mfa_enrollment.py::reset_user_mfa(db: AsyncSession, user: User) -> dict[str, int]` (keys: `"passkeys"`, `"totp"`, `"recovery_codes"`; clears `mfa_enrolled_at`; does NOT revoke sessions — callers pair it with `revoke_user_sessions`)
  - Route: `POST /users/{user_id}/reset-mfa` (ADMIN + step-up + `confirm`)
  - CLI: `python -m idraa auth reset-mfa <email>` (module `src/idraa/__main__.py::main`)
  - Audit actions: `user.mfa_admin_reset` (changes `{"factors_cleared": counts, "via": "ui" | "cli"}`), `user.sessions_revoked` (changes `{"count": n, "via": ...}`)

**Semantics locked by this task:**
1. Admin reset NEVER yields the admin a usable credential (design §Recovery): it only deletes factor rows + clears `mfa_enrolled_at` + revokes the target's sessions. Under `required` policy the target's next password login lands in the enrollment interstitial.
2. Admin SELF-reset is allowed (an admin may rotate their own factors); it revokes their own sessions too — the response redirect lands on /login. Coherent, documented in the route docstring.
3. Deactivation (either route) revokes sessions AND audits `user.sessions_revoked`; REactivation does not touch sessions. `delete_post` needs nothing — the FK `ondelete="CASCADE"` already removes session rows with the user.
4. The CLI is an OPERATIONAL entry point (`python -m idraa …`), deliberately distinct from the dev task runner (`python -m idraa.tasks …`). It writes the same two audit rows with `user_id=None` (no acting web user) and `"via": "cli"`.

- [ ] **Step 1: Write the failing service + route tests**

`tests/integration/test_mfa_admin_reset.py`:

```python
from __future__ import annotations

import uuid

import pyotp
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models._types import now_utc
from idraa.models.audit_log import AuditLog
from idraa.models.mfa import RecoveryCode, UserTotp, WebAuthnCredential
from idraa.models.organization import Organization
from idraa.models.session import AuthSession
from idraa.models.user import User
from idraa.services.mfa_crypto import encrypt_totp_secret, hash_recovery_code
from tests.conftest import csrf_post


async def _target_user_with_factors(
    db_session: AsyncSession, org_id: uuid.UUID
) -> User:
    from tests.factories import create_user

    org = await db_session.get(Organization, org_id)
    assert org is not None
    user = await create_user(db_session, org, email="target@test.local")
    db_session.add(
        UserTotp(
            user_id=user.id,
            secret_encrypted=encrypt_totp_secret(pyotp.random_base32()),
            confirmed_at=now_utc(),
        )
    )
    db_session.add(
        WebAuthnCredential(
            user_id=user.id,
            credential_id=b"c1",
            public_key=b"pk",
            sign_count=0,
            nickname="k",
        )
    )
    db_session.add(RecoveryCode(user_id=user.id, code_hash=hash_recovery_code("aaaaa-bbbbb")))
    user.mfa_enrolled_at = now_utc()
    await db_session.commit()
    return user


async def test_admin_reset_clears_factors_revokes_sessions_audits(
    db_session: AsyncSession, authed_admin: tuple[AsyncClient, uuid.UUID]
) -> None:
    client, org_id = authed_admin
    target = await _target_user_with_factors(db_session, org_id)
    from idraa.services.auth import create_session

    await create_session(db_session, target.id, ip=None)
    await db_session.commit()

    r = await csrf_post(
        client,
        f"/users/{target.id}/reset-mfa",
        {"confirm": "1"},
        bootstrap_url="/users",
        follow_redirects=False,
    )
    assert r.status_code == 303

    assert (
        await db_session.scalar(select(UserTotp).where(UserTotp.user_id == target.id))
    ) is None
    assert (
        await db_session.scalar(
            select(WebAuthnCredential).where(WebAuthnCredential.user_id == target.id)
        )
    ) is None
    assert (
        await db_session.scalar(
            select(RecoveryCode).where(RecoveryCode.user_id == target.id)
        )
    ) is None
    await db_session.refresh(target)
    assert target.mfa_enrolled_at is None
    assert (
        await db_session.scalar(
            select(AuthSession).where(AuthSession.user_id == target.id)
        )
    ) is None
    actions = {
        row.action
        for row in (
            await db_session.execute(
                select(AuditLog).where(AuditLog.entity_id == target.id)
            )
        ).scalars()
    }
    assert "user.mfa_admin_reset" in actions
    assert "user.sessions_revoked" in actions


async def test_reset_requires_confirm(
    db_session: AsyncSession, authed_admin: tuple[AsyncClient, uuid.UUID]
) -> None:
    client, org_id = authed_admin
    target = await _target_user_with_factors(db_session, org_id)
    r = await csrf_post(
        client, f"/users/{target.id}/reset-mfa", {}, bootstrap_url="/users",
        follow_redirects=False,
    )
    assert r.status_code == 400


async def test_reset_is_admin_only(
    db_session: AsyncSession, authed_analyst: tuple[AsyncClient, uuid.UUID]
) -> None:
    # Single authed fixture only — the authed_* fixtures share one underlying
    # AsyncClient, so mixing admin+analyst clients in a test cookie-clobbers.
    client, org_id = authed_analyst
    target = await _target_user_with_factors(db_session, org_id)
    r = await csrf_post(
        client, f"/users/{target.id}/reset-mfa", {"confirm": "1"},
        bootstrap_url="/", follow_redirects=False,
    )
    assert r.status_code == 403
```

`tests/integration/test_revoke_on_deactivation.py`:

```python
from __future__ import annotations

import uuid

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.audit_log import AuditLog
from idraa.models.organization import Organization
from idraa.models.session import AuthSession
from idraa.models.user import User
from tests.conftest import csrf_post


async def _target_with_session(db_session: AsyncSession, org_id: uuid.UUID) -> User:
    from idraa.services.auth import create_session
    from tests.factories import create_user

    org = await db_session.get(Organization, org_id)
    assert org is not None
    user = await create_user(db_session, org, email="victim@test.local")
    await create_session(db_session, user.id, ip=None)
    await db_session.commit()
    return user


async def test_set_active_deactivation_revokes_sessions(
    db_session: AsyncSession, authed_admin: tuple[AsyncClient, uuid.UUID]
) -> None:
    client, org_id = authed_admin
    target = await _target_with_session(db_session, org_id)
    r = await csrf_post(
        client, f"/users/{target.id}/set-active", {"active": "0"},
        bootstrap_url="/users", follow_redirects=False,
    )
    assert r.status_code == 303
    assert (
        await db_session.scalar(
            select(AuthSession).where(AuthSession.user_id == target.id)
        )
    ) is None
    actions = {
        row.action
        for row in (
            await db_session.execute(
                select(AuditLog).where(AuditLog.entity_id == target.id)
            )
        ).scalars()
    }
    assert "user.sessions_revoked" in actions


async def test_edit_post_deactivation_revokes_sessions(
    db_session: AsyncSession, authed_admin: tuple[AsyncClient, uuid.UUID]
) -> None:
    client, org_id = authed_admin
    target = await _target_with_session(db_session, org_id)
    r = await csrf_post(
        client, f"/users/{target.id}/edit", {"role": target.role.value},
        bootstrap_url="/users", follow_redirects=False,
    )  # is_active checkbox omitted == deactivate (checkbox semantics)
    assert r.status_code == 303
    assert (
        await db_session.scalar(
            select(AuthSession).where(AuthSession.user_id == target.id)
        )
    ) is None


async def test_reactivation_does_not_touch_sessions(
    db_session: AsyncSession, authed_admin: tuple[AsyncClient, uuid.UUID]
) -> None:
    client, org_id = authed_admin
    target = await _target_with_session(db_session, org_id)
    await csrf_post(
        client, f"/users/{target.id}/set-active", {"active": "0"},
        bootstrap_url="/users", follow_redirects=False,
    )
    r = await csrf_post(
        client, f"/users/{target.id}/set-active", {"active": "1"},
        bootstrap_url="/users", follow_redirects=False,
    )
    assert r.status_code == 303  # no error; nothing to revoke on reactivate
```

`tests/unit/test_cli_reset_mfa.py`:

```python
from __future__ import annotations

import pytest

import idraa.__main__ as cli


def test_cli_requires_command() -> None:
    with pytest.raises(SystemExit):
        cli.main([])


def test_cli_dispatches_reset_mfa(monkeypatch: pytest.MonkeyPatch) -> None:
    called: dict[str, str] = {}

    async def _fake(email: str) -> int:
        called["email"] = email
        return 0

    monkeypatch.setattr(cli, "_reset_mfa", _fake)
    assert cli.main(["auth", "reset-mfa", "user@example.com"]) == 0
    assert called["email"] == "user@example.com"


def test_cli_unknown_subcommand_exits() -> None:
    with pytest.raises(SystemExit):
        cli.main(["auth", "frobnicate"])
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/integration/test_mfa_admin_reset.py tests/integration/test_revoke_on_deactivation.py tests/unit/test_cli_reset_mfa.py -v`
Expected: FAIL — 404 on the route, `ModuleNotFoundError: idraa.__main__`, missing services.

- [ ] **Step 3: Implement the services**

`src/idraa/services/auth.py` — add `delete` to the sqlalchemy import (`from sqlalchemy import delete, select`) and append:

```python
async def revoke_user_sessions(db: AsyncSession, user_id: uuid.UUID) -> int:
    """Delete every AuthSession row for user_id (idraa#80 L13). Returns count.

    Effective immediately: SessionMiddleware resolves the cookie against the
    DB on every request, so a deleted row means the very next request is
    anonymous. Callers audit `user.sessions_revoked` with the count.
    """
    result = await db.execute(delete(AuthSession).where(AuthSession.user_id == user_id))
    return int(result.rowcount or 0)
```

`src/idraa/services/mfa_enrollment.py` — add `delete` to the sqlalchemy import and append:

```python
async def reset_user_mfa(db: AsyncSession, user: User) -> dict[str, int]:
    """Clear ALL of a user's strong-auth state (admin/CLI factor reset).

    Deletes passkeys, TOTP, and recovery codes and clears mfa_enrolled_at so
    the enrollment interstitial re-traps at next login (policy=required).
    NEVER yields the caller a usable credential (design §Recovery). Session
    revocation is deliberately NOT done here — callers pair this with
    services.auth.revoke_user_sessions so each side is audited separately.
    """
    counts: dict[str, int] = {}
    result = await db.execute(
        delete(WebAuthnCredential).where(WebAuthnCredential.user_id == user.id)
    )
    counts["passkeys"] = int(result.rowcount or 0)
    result = await db.execute(delete(UserTotp).where(UserTotp.user_id == user.id))
    counts["totp"] = int(result.rowcount or 0)
    result = await db.execute(delete(RecoveryCode).where(RecoveryCode.user_id == user.id))
    counts["recovery_codes"] = int(result.rowcount or 0)
    user.mfa_enrolled_at = None
    return counts
```

- [ ] **Step 4: Implement the route + deactivation wiring + template button**

`src/idraa/routes/users.py` — add imports (`require_recent_auth` from deps, `revoke_user_sessions` from `idraa.services.auth`, `reset_user_mfa` from `idraa.services.mfa_enrollment`) and append the route:

```python
@router.post("/users/{user_id}/reset-mfa", dependencies=[Depends(require_recent_auth)])
async def reset_mfa_post(
    user_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    me: User = Depends(require_role(UserRole.ADMIN)),
    confirm: str | None = Form(default=None),
) -> Response:
    """Audited admin factor-reset (design §Recovery).

    Clears the target's passkeys + TOTP + recovery codes, clears
    mfa_enrolled_at (interstitial re-traps at next login under
    policy=required), and revokes the target's live sessions. Never
    authenticates the admin as the target. SELF-reset is allowed — it
    revokes the admin's own sessions too, landing them on /login to
    re-enroll.
    """
    if confirm is None or confirm in ("", "0", "false", "False"):
        raise HTTPException(status_code=400, detail="confirm: missing or falsey")
    user = await get_user(db, user_id, me.organization_id)
    if user is None:
        raise HTTPException(404)
    counts = await reset_user_mfa(db, user)
    revoked = await revoke_user_sessions(db, user.id)
    await AuditWriter(db).log(
        organization_id=user.organization_id,
        entity_type="user",
        entity_id=user.id,
        action="user.mfa_admin_reset",
        changes={"factors_cleared": counts, "via": "ui"},
        user_id=me.id,
        ip_address=client_ip(request),
    )
    await AuditWriter(db).log(
        organization_id=user.organization_id,
        entity_type="user",
        entity_id=user.id,
        action="user.sessions_revoked",
        changes={"count": revoked, "via": "ui"},
        user_id=me.id,
        ip_address=client_ip(request),
    )
    return RedirectResponse(f"/users/{user.id}/edit?mfa_reset=1", status_code=303)
```

`src/idraa/routes/users.py::edit_post` — inside the existing `if user.is_active != new_active:` block (after `user.is_active = new_active`), add:

```python
        if not new_active:  # idraa#80 L13 — deactivation kills live sessions
            revoked = await revoke_user_sessions(db, user.id)
            await AuditWriter(db).log(
                organization_id=user.organization_id,
                entity_type="user",
                entity_id=user.id,
                action="user.sessions_revoked",
                changes={"count": revoked, "via": "ui"},
                user_id=me.id,
                ip_address=client_ip(request),
            )
```

`src/idraa/routes/users.py::set_active_post` — after `user.is_active = new_active` (before the existing audit call), add the same block verbatim.

`tests/integration/test_step_up_catalog.py` — append the new route to the default-deny table (plan-gate Arch-I2; the dependency fires before the handler's 404, so the random UUID works like every other row):

```python
    f"/users/{_UUID}/reset-mfa",
```

`src/idraa/routes/users.py::edit_get` — consume the redirect's `?mfa_reset=1` so the admin gets feedback after the destructive action (plan-gate CQ-I4). Add to the template context dict:

```python
            "mfa_reset_done": request.query_params.get("mfa_reset") == "1",
```

`src/idraa/templates/users/edit.html` — two additions. Placement (plan-gate CQ-N4): the reset form goes AFTER the main edit form's closing `</form>` tag — do NOT nest it inside (browsers reparent nested forms and both break). The confirm banner goes at the top of the content block:

```html
{% if mfa_reset_done %}
<div class="alert alert-success">MFA reset — the user must re-enroll at their next sign-in.</div>
{% endif %}
```

```html
{# SINGLE-quoted onsubmit wrapping a double-quoted JS string with |tojson
   interpolation — autoescape turns a raw ' in an email into &#39;, which the
   HTML parser hands back to JS as ' and breaks a single-quoted literal
   (plan-gate CQ-I5). Mirrors overlays/view.html:97. #}
<form method="post" action="/users/{{ user.id }}/reset-mfa"
      onsubmit='return confirm("Reset MFA for " + {{ user.email | tojson }} + "? All passkeys, authenticator apps and recovery codes are removed and their sessions are signed out.");'>
  {{ csrf_field() }}
  <input type="hidden" name="confirm" value="1">
  <button class="btn btn-outline btn-sm" type="submit">Reset MFA</button>
</form>
```

- [ ] **Step 5: Implement the CLI**

Create `src/idraa/__main__.py`:

```python
"""Operational CLI: ``python -m idraa <command>``.

Deliberately DISTINCT from the dev task runner (``python -m idraa.tasks`` —
lint/test/ci). NOTE the adjacency trap: the installed console script
``idraa`` maps to the TASK runner (pyproject ``[project.scripts]``), so
``idraa auth reset-mfa`` fails loudly with "invalid choice" — operational
commands must be invoked as ``python -m idraa ...`` (Arch-N2). Commands here
are app-level operations run on the host against the live DB
(``DATABASE_URL``), for corners the web UI cannot reach. First command: the
sole-admin-locked-out backstop from the strong-auth design (§Recovery):

    python -m idraa auth reset-mfa <email>
"""

from __future__ import annotations

import argparse
import asyncio
import sys


async def _reset_mfa(email: str) -> int:
    # Imports deferred so ``--help`` never touches DB/config.
    from idraa.db import get_session
    from idraa.services.audit import AuditWriter
    from idraa.services.auth import load_user_by_email, revoke_user_sessions
    from idraa.services.mfa_enrollment import reset_user_mfa

    async with get_session() as db:
        user = await load_user_by_email(db, email)
        if user is None:
            print(f"error: no user with email {email!r}", file=sys.stderr)
            return 1
        counts = await reset_user_mfa(db, user)
        revoked = await revoke_user_sessions(db, user.id)
        await AuditWriter(db).log(
            organization_id=user.organization_id,
            entity_type="user",
            entity_id=user.id,
            action="user.mfa_admin_reset",
            changes={"factors_cleared": counts, "via": "cli"},
            user_id=None,  # host operator, no web actor
            ip_address=None,
        )
        await AuditWriter(db).log(
            organization_id=user.organization_id,
            entity_type="user",
            entity_id=user.id,
            action="user.sessions_revoked",
            changes={"count": revoked, "via": "cli"},
            user_id=None,
            ip_address=None,
        )
    # get_session auto-commits on clean context exit (db.py convention).
    print(f"reset MFA for {email}: {counts}; sessions revoked: {revoked}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m idraa")
    sub = parser.add_subparsers(dest="command", required=True)
    auth = sub.add_parser("auth", help="authentication operations")
    auth_sub = auth.add_subparsers(dest="auth_command", required=True)
    reset = auth_sub.add_parser(
        "reset-mfa",
        help="clear a user's MFA factors and sessions (forces re-enrollment)",
    )
    reset.add_argument("email")
    args = parser.parse_args(argv)
    # Both subparser levels are required=True, so parse_args only returns for
    # the sole leaf command (auth reset-mfa) — no fallthrough branch exists.
    # (A parser.error() tail here would be mypy-unreachable under
    # warn_unreachable=true — plan-gate CQ-I1/Arch-N1.)
    return asyncio.run(_reset_mfa(args.email))


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 6: Run all Task 4 tests + the users route suite**

Run: `uv run pytest tests/integration/test_mfa_admin_reset.py tests/integration/test_revoke_on_deactivation.py tests/unit/test_cli_reset_mfa.py tests/routes -k user -q`
Expected: ALL PASS.

- [ ] **Step 7: Run the full fast gate**

Run: `uv run python scripts/run_local_gate.py`
Expected: green.

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "feat(auth): admin factor-reset + revoke-on-deactivation + CLI backstop (P2 Task 4)"
```

---

## Final verification (before PR)

- [ ] `uv run alembic heads` → single head (the Task 1 revision).
- [ ] `uv run pytest tests/ -m "not e2e" -q` → all green.
- [ ] `uv run pytest tests/e2e/test_passkey_e2e.py -m e2e -v` → green (webauthn.js changed — the fast gate does NOT cover this; run explicitly per the chart-e2e lesson).
- [ ] `uv run python scripts/run_local_gate.py` → green.
- [ ] Grep sanity: `grep -rnE "https://idraa\.app|idraa\.fly\.dev" src/idraa/` → no NEW hits from this branch (P1's config-default examples are the only allowed occurrences; the pattern is domain-anchored so `from idraa.app import templates` lines don't false-positive — plan-gate CQ-N6).
- [ ] 3-reviewer PR-gate (security-auditor + architect + code-quality/spec-adherence) on the whole branch, iterate to 0/0, then push and open the PR (CI `ci-success` is the merge authority).

## Scope budget

- **target_task_count:** 4 (design budget: ~4 ✓)
- **target_loc_delta:** ~1,100 LOC including tests (design's P2 share of the ~3,000 epic budget)
- **Out of scope (P3):** full idraa#81 (management UI, admin unlock, per-IP throttle), idraa#82 HSTS/headers, strict single-use `mfa_pending` nonce table, remember-this-device.

## Scope drift log (P2 plan-writing)

1. **Shared second-factor verifier** (`services/second_factor.py`). Direction: +added refactor — `login_mfa_post`'s TOTP/recovery block extracted so step-up cannot drift from login semantics. Behavior-neutral; guarded by `test_login_mfa_flow.py`.
2. **Catalog widened beyond the design's four named deletes** (library entry, control soft-delete, qualitative band, register-import profile, run purge-samples, user delete, overlay deactivate). Direction: +added — default-deny uniformity (purge-samples/user-delete trace to the design's destructive/admin categories — Spec-N1; overlay deactivate is the delete-less overlay family's terminal action — Sec-N2); plan-gate may trim.
3. **Password fallback for factor-less users at step-up.** Direction: ↔resolved — the design left the factor-less case implicit; without it, step-up-gated enrollment endpoints deadlock un-enrolled users. Password is never offered to strong-factor accounts.
4. **`user.step_up_failed` audit action.** Direction: +added — mirrors P1's `user.login_mfa_failed` detection-signal rationale; failures also count toward the B1 lockout.
5. **`safe_next` relocated** from `routes/auth.py` (private) to `routes/deps.py` (shared). Direction: ↔refactor — avoids a cross-module private import; call sites renamed.
6. **POST `next` = sanitized Referer, actions not replayed.** Direction: ↔resolved — the design says "lets the action proceed"; a redirect cannot replay a POST body, so the user re-triggers inside the fresh window (GitHub-sudo-style). Recorded as the intended UX.
7. **Distinct `rf-webauthn-stepup` salt** (plan-gate Sec-N1). Direction: +added — P2 DOES add one signed-token type, honoring P1's purpose-separation salt convention instead of waiving it by fiat.
8. **Passkey step-up failure audits + locked-user audit silence** (plan-gate Sec-I1 + Sec-N3). Direction: +added/↔aligned — `user.step_up_failed {method: passkey, reason}` on the forensic branches; already-locked users bounce un-audited on the code/password path, mirroring `/login/mfa` and bounding append-only audit growth.
