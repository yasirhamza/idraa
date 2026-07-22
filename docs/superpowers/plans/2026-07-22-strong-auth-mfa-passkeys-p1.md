# Strong Authentication P1 — Core Factors + Login — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship passkey-primary authentication with a hardened password + TOTP + recovery-code fallback, plus a blocking enrollment interstitial — the P1 slice of `docs/superpowers/specs/2026-07-22-strong-auth-mfa-passkeys-design.md`.

**Architecture:** Insert an MFA stage into today's single-step login. New per-factor tables (`webauthn_credentials`, `user_totp`, `recovery_codes`) scope via `user_id` like `AuthSession`. WebAuthn ceremonies run through `py_webauthn` server-side with a signed short-lived challenge cookie; a ~120-line vendored `webauthn.js` drives `navigator.credentials`. TOTP via `pyotp`, QR as server-rendered SVG via `segno`, TOTP secret encrypted at rest with Fernet. Login becomes a state machine: passkey → session, or password → signed `mfa_pending` token → TOTP/recovery → session. A `require_enrolled` dependency traps un-enrolled users on the enroll page when `AUTH_MFA_POLICY=required`.

**Tech Stack:** FastAPI, SQLAlchemy 2.x (async) + Alembic, Jinja2 + HTMX + Alpine (no build step), pytest + httpx + Playwright, `webauthn` (py_webauthn), `pyotp`, `segno`, `cryptography` (Fernet), `itsdangerous`, `passlib[argon2]`.

## Global Constraints

Every task's requirements implicitly include this section. Values are verbatim from the spec + verified against the current codebase (`main` @ `b7d3f1a9c4e2`).

- **Dependencies** via `uv add <pkg>` (updates `pyproject.toml` + `uv.lock` atomically); never hand-edit the lockfile. Install/refresh with `uv sync --extra dev`. Run every command with `uv run ...`.
- **New ORM models MUST be re-exported in `src/idraa/models/__init__.py`** (import + `__all__`) or Alembic autogenerate and the test schema won't see them.
- **Migrations:** `uv run alembic revision --autogenerate -m "<msg>"`, then hand-audit the generated file (add `server_default` on any `nullable=False` column added to an existing table). Set `down_revision` to the real current head — get it with `uv run alembic heads` (currently `b7d3f1a9c4e2`, but re-check; `main` may have advanced). Verify every migration round-trips: `uv run alembic upgrade head && uv run alembic downgrade -1 && uv run alembic upgrade head`. Modern migration style: `from __future__ import annotations` + `str | Sequence[str] | None`. A pre-commit hook rejects `str(uuid.uuid4())` in migrations — use `.hex`.
- **Enums:** `StrEnum` in `models/enums.py`, stored via `Enum(X, native_enum=False, values_callable=lambda x: [e.value for e in x])`.
- **Timestamps:** timezone-aware UTC via `idraa.models._types.now_utc`; columns are `DateTime(timezone=True)`. Never store naive datetimes.
- **Audit:** `AuditWriter(db).log(*, organization_id, entity_type, entity_id, action, changes, user_id, ip_address=None)` — all keyword-only; commit is the caller's (the `get_db` dep auto-commits). `action` is a free-form `String(64)`, convention `"<entity>.<verb>"`, ≤64 chars. Auth tables have no `organization_id`; get it from the acting `User.organization_id`.
- **CSRF:** cookie `csrf_token` (readable by JS), form field `_csrf`, header `X-CSRF-Token` (checked first). **JSON bodies are NOT parsed for `_csrf`** — every `fetch()` to a JSON endpoint MUST send `X-CSRF-Token`, read from `<meta name="csrf-token">` (already in `base.html:7`). Forms use `{{ csrf_field() }}`.
- **CSP:** `script-src 'self' 'unsafe-inline' 'unsafe-eval'`, `connect-src 'self'` — a same-origin `/static/js/*.js` file and same-origin `fetch()` are allowed as-is; no nonce, no new CSP grant. Do NOT touch the `unsafe-eval` item (idraa#487).
- **Static assets:** hard-coded `/static/...?v={{ static_version }}` in `base.html` (no `url_for`). After adding/altering any template, rebuild the stylesheet: `uv run python -m idraa.tasks.build_css` and commit `tailwind.css` — the gate runs `build_css --check` and fails on a stale sheet.
- **Session:** `SESSION_COOKIE = "idraa_session"`; `create_session(db, user_id, ip)`, `set_session_cookie(response, sess.id)`, `clear_session_cookie(response)` in `services/auth.py`. Sessions are 14-day non-sliding; `is_active` is checked live in `SessionMiddleware`.
- **Signed short-lived tokens:** each new signed-payload type gets its OWN itsdangerous salt (convention documented in `services/auth.py`): `rf-mfa-pending`, `rf-webauthn-challenge`. Use `URLSafeTimedSerializer` for TTL enforcement.
- **Security invariants (spec):** passkeys register + authenticate with `user_verification=REQUIRED` and `resident_key=REQUIRED` (discoverable); reject non-increasing `sign_count` (except authenticators that always report 0); TOTP secret encrypted at rest; recovery codes Argon2-hashed + single-use; `mfa_pending` TTL 300 s. **RP-ID/origins are config, never hardcoded** — the software is self-hostable.
- **Typing:** `mypy` gates `src/idraa`. `pyotp` and `segno` ship no type stubs — add `[[tool.mypy.overrides]]` `ignore_missing_imports = true` entries for them (Task 1).
- **Tests:** `uv run pytest -q` runs the fast suite (`-m "not e2e and not slow and not ci_only"` from `addopts`). E2e run explicitly: `uv run pytest -m e2e tests/e2e/`. Fixtures: `db_session`, `client`, `csrf_post(client, url, data, *, bootstrap_url="/setup")`, `authed_admin`/`authed_analyst`/… (`tuple[AsyncClient, org_id]`), `admin_client`/…, `anonymous_client`; factories `create_org`, `create_user(db, org, *, email, role, password)`, `login_client_as(db, user) -> signed_cookie`. Service-only unit tests use `tests/services/conftest.py`'s self-contained `db`/`org_id`/`actor_id`.
- **Review ceremony (this feature):** 3-reviewer — security-auditor + architect + code-quality (code-quality also carries spec-adherence) — at the plan-gate AND the P1 PR-gate, iterated to 0/0.

---

## File Structure

**Create:**
- `src/idraa/models/mfa.py` — `WebAuthnCredential`, `UserTotp`, `RecoveryCode` ORM.
- `src/idraa/services/mfa_crypto.py` — Fernet TOTP encrypt/decrypt, recovery-code gen/hash/verify.
- `src/idraa/services/totp.py` — provision / URI / verify / QR-SVG.
- `src/idraa/services/webauthn_service.py` — py_webauthn options + verify wrappers + sign-count check.
- `src/idraa/services/mfa_enrollment.py` — enrollment orchestration + `mfa_enrolled_at` stamping + credential view mapper.
- `src/idraa/routes/mfa.py` — `/account/security` page + enrollment endpoints.
- `src/idraa/templates/account/security.html`, `templates/account/_passkeys.html`, `templates/account/_totp.html`, `templates/auth/mfa_challenge.html`, `templates/auth/enroll.html`.
- `src/idraa/static/js/webauthn.js` — browser ceremony driver.
- Alembic migration under `alembic/versions/`.
- Tests under `tests/unit/`, `tests/integration/`, `tests/contracts/`, `tests/e2e/`.

**Modify:**
- `src/idraa/config.py` — WebAuthn/MFA settings + validators.
- `src/idraa/models/user.py` — `mfa_enrolled_at` column.
- `src/idraa/models/__init__.py` — register new models.
- `src/idraa/models/enums.py` — `MfaPolicy` StrEnum (optional; or use `Literal`).
- `src/idraa/services/auth.py` — signed `mfa_pending` + `webauthn_challenge` token helpers.
- `src/idraa/routes/auth.py` — login state machine.
- `src/idraa/routes/deps.py` — `require_enrolled` dependency.
- `src/idraa/app.py` — include `mfa` router; apply `require_enrolled` to the authed surface.
- `src/idraa/templates/auth/login.html`, `templates/base.html` — passkey button, `webauthn.js` include.
- `pyproject.toml` — deps + mypy overrides.

---

## Task 1: Dependencies + configuration

**Files:**
- Modify: `pyproject.toml` (deps, mypy overrides)
- Modify: `src/idraa/config.py`
- Test: `tests/unit/test_config_webauthn.py`

**Interfaces:**
- Produces: `Settings.webauthn_rp_id: str`, `Settings.webauthn_rp_name: str`, `Settings.webauthn_origins: str`, `Settings.webauthn_origin_list -> list[str]` (property), `Settings.auth_mfa_policy: Literal["required","optional"]`, `Settings.totp_issuer: str`, `Settings.mfa_encryption_key: str | None`, `Settings.auth_max_failed_logins: int`, `Settings.auth_lockout_seconds: int`.

- [ ] **Step 1: Add dependencies.**

Run: `uv add webauthn pyotp segno cryptography` then `uv sync --extra dev`.
Expected: `pyproject.toml` `[project] dependencies` gains the four packages; `uv.lock` updated. (`py_webauthn` is imported as `webauthn`; it pulls `cryptography` + `cbor2` transitively, but we add `cryptography` explicitly since Task 3 imports it directly.)

- [ ] **Step 2: Add mypy overrides for untyped deps.**

In `pyproject.toml`, under the existing mypy config, append:

```toml
[[tool.mypy.overrides]]
module = ["pyotp", "segno"]
ignore_missing_imports = true
```

- [ ] **Step 3: Write the failing config test.**

```python
# tests/unit/test_config_webauthn.py
from __future__ import annotations

import pytest

from idraa.config import Settings


def _settings(**env: str) -> Settings:
    return Settings(_env_file=None, **env)  # type: ignore[arg-type]


def test_webauthn_defaults_are_owner_deployment() -> None:
    s = _settings(environment="dev", session_secret="x" * 16)
    assert s.webauthn_rp_id == "idraa.fly.dev"
    # The default is one coherent example; RP-ID/origins are per-deployment config.
    # (A single RP-ID binds to one registrable domain — a WebAuthn protocol rule —
    # and the default must be self-consistent or the prod validator rejects it.)
    assert s.webauthn_origin_list == ["https://idraa.fly.dev"]
    assert s.auth_mfa_policy == "required"


def test_webauthn_origins_split_and_strip() -> None:
    s = _settings(
        environment="dev",
        session_secret="x" * 16,
        webauthn_origins=" https://a.example ,https://b.example , ",
    )
    assert s.webauthn_origin_list == ["https://a.example", "https://b.example"]


def test_prod_rejects_placeholder_rp_id() -> None:
    with pytest.raises(ValueError, match="WEBAUTHN_RP_ID"):
        _settings(
            environment="prod",
            session_secret="y" * 40,
            webauthn_rp_id="",
        )


def test_prod_rejects_origin_not_covering_rp_id() -> None:
    with pytest.raises(ValueError, match="WEBAUTHN_ORIGINS"):
        _settings(
            environment="prod",
            session_secret="y" * 40,
            webauthn_rp_id="idraa.fly.dev",
            webauthn_origins="https://evil.example",
        )


def test_prod_accepts_subdomain_origin() -> None:
    s = _settings(
        environment="prod",
        session_secret="y" * 40,
        webauthn_rp_id="example.com",
        webauthn_origins="https://app.example.com,https://example.com",
    )
    assert s.webauthn_rp_id == "example.com"
```

- [ ] **Step 4: Run — expect fail** (`AttributeError`/`TypeError`, fields don't exist).

Run: `uv run pytest tests/unit/test_config_webauthn.py -q`

- [ ] **Step 5: Implement the settings.** Add to `class Settings` in `src/idraa/config.py` (note: `webauthn_origins` is a plain `str` field + a property — this sidesteps pydantic-settings' JSON-decoding of `list`-typed env vars entirely):

```python
    # --- Strong auth / MFA (2026-07-22 design) ---
    # Config-driven so the software stays self-hostable — never hardcode an
    # operator's domains into WebAuthn. Defaults are the OWNER deployment.
    webauthn_rp_id: str = "idraa.fly.dev"
    webauthn_rp_name: str = "Idraa"
    # Single registrable domain (plan-gate): one RP-ID can't span idraa.fly.dev +
    # idraa.app. A second passkey domain needs its own RP-ID or Related Origin Requests.
    webauthn_origins: str = "https://idraa.fly.dev"
    auth_mfa_policy: Literal["required", "optional"] = "required"
    totp_issuer: str = "Idraa"
    mfa_encryption_key: str | None = None
    # Minimal login throttle — idraa#81 slice pulled into P1 at plan-gate (B1):
    # the reworked login must not ship a rate-limit-free 6-digit second factor.
    auth_max_failed_logins: int = 5  # 0 disables lockout
    auth_lockout_seconds: int = 900

    @property
    def webauthn_origin_list(self) -> list[str]:
        """WEBAUTHN_ORIGINS parsed: comma-split, trimmed, blanks dropped."""
        return [o.strip() for o in self.webauthn_origins.split(",") if o.strip()]
```

Add a prod-gated validator (place after `_check_secret_hardening`):

```python
    @model_validator(mode="after")
    def _check_webauthn_hardening(self) -> Settings:
        """Refuse to boot in prod with an unusable WebAuthn RP-ID / origins.

        A placeholder RP-ID or origins that don't cover it silently breaks
        passkeys for the deployment. dev/test accept the defaults.
        """
        if self.environment != "prod":
            return self
        if not self.webauthn_rp_id.strip():
            raise ValueError(
                "WEBAUTHN_RP_ID must be set to the deployment's domain in "
                f"environment={self.environment!r} (e.g. app.example.com)."
            )
        origins = self.webauthn_origin_list
        if not origins:
            raise ValueError(
                "WEBAUTHN_ORIGINS must list at least one https:// origin in "
                f"environment={self.environment!r} "
                "(e.g. https://app.example.com,https://example.com)."
            )
        rp = self.webauthn_rp_id
        for origin in origins:
            if not origin.startswith("https://"):
                raise ValueError(f"WEBAUTHN_ORIGINS entry {origin!r} must be https://")
            host = origin.removeprefix("https://").split("/", 1)[0].split(":", 1)[0]
            if not (host == rp or host.endswith("." + rp)):
                raise ValueError(
                    f"WEBAUTHN_ORIGINS entry {origin!r} (host {host!r}) is not "
                    f"WEBAUTHN_RP_ID {rp!r} nor a subdomain of it — WebAuthn "
                    "requires every origin's host to equal or be under the RP-ID."
                )
        return self
```

Ensure `from typing import Literal` and `model_validator` are imported (both already are).

- [ ] **Step 6: Run — expect pass.** `uv run pytest tests/unit/test_config_webauthn.py -q`

- [ ] **Step 7: Commit.**

```bash
git add pyproject.toml uv.lock src/idraa/config.py tests/unit/test_config_webauthn.py
git commit -m "feat(auth): MFA/WebAuthn config keys + prod-gated RP-ID/origins validator"
```

---

## Task 2: Data model + migration + contract tests

**Files:**
- Create: `src/idraa/models/mfa.py`
- Modify: `src/idraa/models/user.py` (add `mfa_enrolled_at`)
- Modify: `src/idraa/models/__init__.py` (register)
- Create: `alembic/versions/<rev>_strong_auth_p1_mfa_tables.py` (autogenerated)
- Test: `tests/unit/test_models_mfa.py`, `tests/contracts/test_mfa_field_sync.py`

**Interfaces:**
- Produces: `WebAuthnCredential(id, user_id, credential_id: bytes, public_key: bytes, sign_count: int, transports: str|None, aaguid: str|None, nickname: str, last_used_at: datetime|None, created_at, updated_at)`; `UserTotp(user_id [PK], secret_encrypted: str, confirmed_at: datetime|None, created_at)`; `RecoveryCode(id, user_id, code_hash: str, used_at: datetime|None, created_at)`; `User.mfa_enrolled_at: datetime|None`.

- [ ] **Step 1: Write the model unit test.**

```python
# tests/unit/test_models_mfa.py
from __future__ import annotations

import uuid

from idraa.models.mfa import RecoveryCode, UserTotp, WebAuthnCredential
from idraa.models.user import User


def test_webauthn_credential_construct_sets_defaults() -> None:
    uid = uuid.uuid4()
    cred = WebAuthnCredential(
        user_id=uid,
        credential_id=b"\x01\x02\x03",
        public_key=b"\xAA\xBB",
        nickname="YubiKey",
    )
    assert cred.id is not None            # IdMixin populated in __init__
    # sign_count default=0 is a FLUSH-time SQLAlchemy default, NOT populated at
    # __init__ (only IdMixin/TimestampMixin fields are, via the instrument_class
    # init hook). On an unflushed instance cred.sign_count is None — assert the
    # column default instead.
    assert WebAuthnCredential.__table__.c.sign_count.default.arg == 0
    assert cred.created_at is not None    # TimestampMixin populated in __init__
    assert cred.last_used_at is None


def test_user_totp_and_recovery_code_shape() -> None:
    uid = uuid.uuid4()
    totp = UserTotp(user_id=uid, secret_encrypted="enc")
    assert totp.confirmed_at is None
    rc = RecoveryCode(user_id=uid, code_hash="h")
    assert rc.id is not None
    assert rc.used_at is None


def test_user_has_nullable_mfa_enrolled_at() -> None:
    assert "mfa_enrolled_at" in User.__table__.columns
    assert User.__table__.columns["mfa_enrolled_at"].nullable is True
```

- [ ] **Step 2: Run — expect fail** (`ModuleNotFoundError: idraa.models.mfa`).

Run: `uv run pytest tests/unit/test_models_mfa.py -q`

- [ ] **Step 3: Create `src/idraa/models/mfa.py`.**

```python
"""MFA factor ORM: passkeys, TOTP secret, recovery codes.

Keyed by ``user_id`` and scoped through the user (no ``organization_id``
column) — mirrors ``AuthSession``. FK ``ondelete=CASCADE`` from ``users``:
deleting a user drops their factors.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, LargeBinary, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from idraa.db import Base
from idraa.models._types import now_utc
from idraa.models.mixins import IdMixin, TimestampMixin


class WebAuthnCredential(IdMixin, TimestampMixin, Base):
    __tablename__ = "webauthn_credentials"

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    credential_id: Mapped[bytes] = mapped_column(LargeBinary, unique=True, nullable=False)
    public_key: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    sign_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    transports: Mapped[str | None] = mapped_column(String(255), nullable=True)
    aaguid: Mapped[str | None] = mapped_column(String(64), nullable=True)
    nickname: Mapped[str] = mapped_column(String(64), nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class UserTotp(Base):
    __tablename__ = "user_totp"

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    secret_encrypted: Mapped[str] = mapped_column(String(255), nullable=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, nullable=False
    )


class RecoveryCode(IdMixin, Base):
    __tablename__ = "recovery_codes"

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    code_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, nullable=False
    )
```

- [ ] **Step 4: Add `mfa_enrolled_at` + throttle columns to `User`.** In `src/idraa/models/user.py`, add after `last_login_at` (add `Integer` to the sqlalchemy import):

```python
    mfa_enrolled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Minimal login throttle (idraa#81 slice, plan-gate B1).
    failed_login_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
```

Add a matching column-existence assertion to `test_user_has_nullable_mfa_enrolled_at` (or a sibling test):

```python
    assert "failed_login_count" in User.__table__.columns
    assert "locked_until" in User.__table__.columns
```

- [ ] **Step 5: Register models.** In `src/idraa/models/__init__.py` add the import and three `__all__` entries:

```python
from idraa.models.mfa import RecoveryCode, UserTotp, WebAuthnCredential
```
Add `"RecoveryCode"`, `"UserTotp"`, `"WebAuthnCredential"` to `__all__` (keep alphabetical).

- [ ] **Step 6: Run — expect pass.** `uv run pytest tests/unit/test_models_mfa.py -q`

- [ ] **Step 7: Generate + audit the migration.**

Run: `uv run alembic heads` (confirm the head; use it as `down_revision`).
Run: `uv run alembic revision --autogenerate -m "strong auth p1 mfa tables"`
Open the generated file: confirm it `create_table`s `webauthn_credentials`, `user_totp`, `recovery_codes` (+ the unique index on `credential_id`, the FK indexes) and three `op.add_column("users", ...)` calls: `mfa_enrolled_at` (nullable — no server_default), `locked_until` (nullable — no server_default), and `failed_login_count`. **`failed_login_count` is `nullable=False`** — autogenerate omits the server_default, so HAND-ADD it: `op.add_column("users", sa.Column("failed_login_count", sa.Integer(), nullable=False, server_default="0"))` (backfills existing rows). Convert the header to the modern style if the template emitted the legacy `Union` import.

- [ ] **Step 8: Verify migration round-trips.**

Run: `uv run alembic upgrade head && uv run alembic downgrade -1 && uv run alembic upgrade head`
Expected: all three succeed with no error.

- [ ] **Step 9: Write the field-sync contract test.** (No DTO exists yet in P1; this asserts the ORM columns are the expected set so a later DTO/adapter can be checked against it — mirrors the hand-written `_ALLOWLIST` pattern in `tests/contracts/test_orm_sme_columns_subset_of_dto_fields.py`.)

```python
# tests/contracts/test_mfa_field_sync.py
from __future__ import annotations

from sqlalchemy import inspect

from idraa.models.mfa import RecoveryCode, UserTotp, WebAuthnCredential


def _cols(model: type) -> set[str]:
    return {c.key for c in inspect(model).columns}


def test_webauthn_credential_columns_are_expected() -> None:
    assert _cols(WebAuthnCredential) == {
        "id", "user_id", "credential_id", "public_key", "sign_count",
        "transports", "aaguid", "nickname", "last_used_at", "created_at", "updated_at",
    }


def test_user_totp_columns_are_expected() -> None:
    assert _cols(UserTotp) == {"user_id", "secret_encrypted", "confirmed_at", "created_at"}


def test_recovery_code_columns_are_expected() -> None:
    assert _cols(RecoveryCode) == {"id", "user_id", "code_hash", "used_at", "created_at"}
```

- [ ] **Step 10: Run — expect pass, then commit.**

```bash
uv run pytest tests/unit/test_models_mfa.py tests/contracts/test_mfa_field_sync.py -q
git add src/idraa/models/mfa.py src/idraa/models/user.py src/idraa/models/__init__.py alembic/versions/ tests/unit/test_models_mfa.py tests/contracts/test_mfa_field_sync.py
git commit -m "feat(auth): MFA factor tables + user.mfa_enrolled_at + migration"
```

---

## Task 3: MFA crypto primitives (TOTP-secret encryption, recovery codes, signed tokens)

**Files:**
- Create: `src/idraa/services/mfa_crypto.py`
- Modify: `src/idraa/services/auth.py` (signed `mfa_pending` + `webauthn_challenge` helpers)
- Test: `tests/unit/test_mfa_crypto.py`, `tests/unit/test_auth_mfa_tokens.py`

**Interfaces:**
- Produces (`mfa_crypto`): `encrypt_totp_secret(plain: str) -> str`, `decrypt_totp_secret(ciphertext: str) -> str`, `generate_recovery_codes(n: int = 10) -> list[str]`, `hash_recovery_code(code: str) -> str`, `verify_recovery_code(code: str, code_hash: str) -> bool`.
- Produces (`auth`): `sign_mfa_pending(user_id: uuid.UUID) -> str`, `load_mfa_pending(token: str, max_age: int = 300) -> uuid.UUID | None`, `sign_webauthn_challenge(challenge_b64url: str) -> str`, `load_webauthn_challenge(token: str, max_age: int = 300) -> str | None`.

- [ ] **Step 1: Write the crypto test.**

```python
# tests/unit/test_mfa_crypto.py
from __future__ import annotations

import pytest
from cryptography.fernet import InvalidToken

import idraa.config as config
from idraa.services import mfa_crypto


def _reset(monkeypatch, **env):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    config.reset_for_tests()


def test_totp_secret_round_trip(monkeypatch) -> None:
    _reset(monkeypatch, ENVIRONMENT="test", SESSION_SECRET="s" * 32)
    ct = mfa_crypto.encrypt_totp_secret("JBSWY3DPEHPK3PXP")
    assert ct != "JBSWY3DPEHPK3PXP"          # actually encrypted
    assert mfa_crypto.decrypt_totp_secret(ct) == "JBSWY3DPEHPK3PXP"


def test_encryption_is_key_isolated(monkeypatch) -> None:
    _reset(monkeypatch, ENVIRONMENT="test", SESSION_SECRET="s" * 32, MFA_ENCRYPTION_KEY="k" * 32)
    ct = mfa_crypto.encrypt_totp_secret("SECRETSECRET")
    assert mfa_crypto.decrypt_totp_secret(ct) == "SECRETSECRET"     # same key round-trips
    # A DIFFERENT key cannot decrypt it (proves the key actually gates decryption).
    _reset(monkeypatch, ENVIRONMENT="test", SESSION_SECRET="s" * 32, MFA_ENCRYPTION_KEY="j" * 32)
    with pytest.raises(InvalidToken):
        mfa_crypto.decrypt_totp_secret(ct)


def test_recovery_codes_generated_hashed_and_verified() -> None:
    codes = mfa_crypto.generate_recovery_codes()
    assert len(codes) == 10
    assert len(set(codes)) == 10             # unique
    h = mfa_crypto.hash_recovery_code(codes[0])
    assert h != codes[0]                      # hashed, not plaintext
    assert mfa_crypto.verify_recovery_code(codes[0], h) is True
    assert mfa_crypto.verify_recovery_code("wrong-code", h) is False
```

- [ ] **Step 2: Run — expect fail.** `uv run pytest tests/unit/test_mfa_crypto.py -q`

- [ ] **Step 3: Create `src/idraa/services/mfa_crypto.py`.**

```python
"""MFA secret handling: TOTP-secret encryption (Fernet) + recovery codes.

TOTP verification needs the plaintext secret, so it cannot be hashed — it is
symmetric-encrypted at rest with a key derived (HKDF-SHA256) from
MFA_ENCRYPTION_KEY, falling back to SESSION_SECRET. Recovery codes ARE
one-way (Argon2, via the shared password context).

OPERATOR NOTE (plan-gate N3): if MFA_ENCRYPTION_KEY is unset, the key derives
from SESSION_SECRET — so rotating SESSION_SECRET makes every stored TOTP secret
undecryptable and locks all TOTP users out of that factor. Deployments that
rotate SESSION_SECRET MUST set a distinct, stable MFA_ENCRYPTION_KEY. (Key
versioning via MultiFernet + a key-id prefix is a documented post-P1 follow-up.)
"""

from __future__ import annotations

import base64
import secrets

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from idraa.config import get_settings
from idraa.services.auth import hash_password, verify_password

_HKDF_INFO = b"idraa-mfa-totp-secret-v1"


def _fernet() -> Fernet:
    settings = get_settings()
    key_material = (settings.mfa_encryption_key or settings.session_secret).encode("utf-8")
    # Fresh HKDF per call — an HKDF instance is single-use (derive() once).
    derived = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=_HKDF_INFO).derive(
        key_material
    )
    return Fernet(base64.urlsafe_b64encode(derived))


def encrypt_totp_secret(plain: str) -> str:
    return _fernet().encrypt(plain.encode("utf-8")).decode("ascii")


def decrypt_totp_secret(ciphertext: str) -> str:
    return _fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")


def generate_recovery_codes(n: int = 10) -> list[str]:
    """Return n unique one-time codes formatted xxxxx-xxxxx (hex, unambiguous)."""
    codes: set[str] = set()
    while len(codes) < n:
        raw = secrets.token_hex(5)  # 10 hex chars
        codes.add(f"{raw[:5]}-{raw[5:]}")
    return sorted(codes)


def hash_recovery_code(code: str) -> str:
    return hash_password(code)


def verify_recovery_code(code: str, code_hash: str) -> bool:
    return verify_password(code, code_hash)
```

- [ ] **Step 4: Run — expect pass.** `uv run pytest tests/unit/test_mfa_crypto.py -q`

- [ ] **Step 5: Write the signed-token test.**

```python
# tests/unit/test_auth_mfa_tokens.py
from __future__ import annotations

import time
import uuid

import idraa.config as config
from idraa.services import auth


def _reset(monkeypatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("SESSION_SECRET", "s" * 32)
    config.reset_for_tests()


def test_mfa_pending_round_trip(monkeypatch) -> None:
    _reset(monkeypatch)
    uid = uuid.uuid4()
    token = auth.sign_mfa_pending(uid)
    assert auth.load_mfa_pending(token) == uid


def test_mfa_pending_expires(monkeypatch) -> None:
    _reset(monkeypatch)
    token = auth.sign_mfa_pending(uuid.uuid4())
    time.sleep(1)
    assert auth.load_mfa_pending(token, max_age=0) is None


def test_mfa_pending_rejects_tampered(monkeypatch) -> None:
    _reset(monkeypatch)
    assert auth.load_mfa_pending("garbage.token.value") is None


def test_webauthn_challenge_round_trip(monkeypatch) -> None:
    _reset(monkeypatch)
    token = auth.sign_webauthn_challenge("Y2hhbGxlbmdl")
    assert auth.load_webauthn_challenge(token) == "Y2hhbGxlbmdl"


def test_pending_and_challenge_salts_are_not_interchangeable(monkeypatch) -> None:
    _reset(monkeypatch)
    uid = uuid.uuid4()
    pending = auth.sign_mfa_pending(uid)
    # A pending token must NOT validate as a challenge token (distinct salts).
    assert auth.load_webauthn_challenge(pending) is None
```

- [ ] **Step 6: Run — expect fail.** `uv run pytest tests/unit/test_auth_mfa_tokens.py -q`

- [ ] **Step 7: Add token helpers to `src/idraa/services/auth.py`.** Add the import and helpers (place near `_serializer`):

```python
from itsdangerous import URLSafeTimedSerializer  # add to the itsdangerous import line


def _mfa_pending_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_settings().session_secret, salt="rf-mfa-pending")


def sign_mfa_pending(user_id: uuid.UUID) -> str:
    return _mfa_pending_serializer().dumps(str(user_id))


def load_mfa_pending(token: str, max_age: int = 300) -> uuid.UUID | None:
    try:
        raw: str = _mfa_pending_serializer().loads(token, max_age=max_age)
    except BadData:
        return None
    try:
        return uuid.UUID(raw)
    except ValueError:
        return None


def _webauthn_challenge_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_settings().session_secret, salt="rf-webauthn-challenge")


def sign_webauthn_challenge(challenge_b64url: str) -> str:
    return _webauthn_challenge_serializer().dumps(challenge_b64url)


def load_webauthn_challenge(token: str, max_age: int = 300) -> str | None:
    try:
        value: str = _webauthn_challenge_serializer().loads(token, max_age=max_age)
    except BadData:
        return None
    return value


def _totp_pending_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_settings().session_secret, salt="rf-totp-pending")


def sign_totp_pending(secret: str) -> str:
    return _totp_pending_serializer().dumps(secret)


def load_totp_pending(token: str, max_age: int = 600) -> str | None:
    try:
        value: str = _totp_pending_serializer().loads(token, max_age=max_age)
    except BadData:
        return None
    return value


# --- Short-lived MFA cookie helpers (DRY: one place derives the secure flag,
# mirroring set_session_cookie — plan-gate: prevents a missed secure= flag). ---
def _secure() -> bool:
    return get_settings().environment == "prod"


def set_mfa_pending_cookie(response: Response, user_id: uuid.UUID) -> None:
    response.set_cookie("rf_mfa_pending", sign_mfa_pending(user_id), max_age=300,
                        httponly=True, samesite="lax", secure=_secure(), path="/")


def clear_mfa_pending_cookie(response: Response) -> None:
    response.delete_cookie("rf_mfa_pending", path="/")


def set_webauthn_challenge_cookie(response: Response, challenge_b64url: str) -> None:
    response.set_cookie("rf_webauthn_challenge", sign_webauthn_challenge(challenge_b64url),
                        max_age=300, httponly=True, samesite="lax", secure=_secure(), path="/")


def clear_webauthn_challenge_cookie(response: Response) -> None:
    response.delete_cookie("rf_webauthn_challenge", path="/")


def set_totp_pending_cookie(response: Response, secret: str) -> None:
    response.set_cookie("rf_totp_pending", sign_totp_pending(secret), max_age=600,
                        httponly=True, samesite="lax", secure=_secure(), path="/")


def clear_totp_pending_cookie(response: Response) -> None:
    response.delete_cookie("rf_totp_pending", path="/")
```

(`BadData` is already imported in `auth.py`. `URLSafeTimedSerializer.loads(..., max_age=)` raises `SignatureExpired`, a `BadData` subclass, on expiry — caught by the same `except`. `Response` and `get_settings` are already imported in `auth.py`; add `uuid` if not present.)

**All challenge / `mfa_pending` / `totp_pending` cookies MUST be set/cleared via these helpers** — no hand-rolled `set_cookie` in the routes (guarantees the `secure=` flag can't be forgotten on one path).

- [ ] **Step 8: Run — expect pass, then commit.**

```bash
uv run pytest tests/unit/test_mfa_crypto.py tests/unit/test_auth_mfa_tokens.py -q
git add src/idraa/services/mfa_crypto.py src/idraa/services/auth.py tests/unit/test_mfa_crypto.py tests/unit/test_auth_mfa_tokens.py
git commit -m "feat(auth): MFA crypto (Fernet TOTP secret, recovery codes) + signed pending/challenge tokens"
```

---

## Task 4: TOTP service

**Files:**
- Create: `src/idraa/services/totp.py`
- Test: `tests/unit/test_totp_service.py`

**Interfaces:**
- Produces: `provision_secret() -> str`, `totp_uri(secret: str, account_name: str, issuer: str) -> str`, `verify_totp(secret: str, code: str, valid_window: int = 1) -> bool`, `totp_qr_svg(uri: str) -> str`.

- [ ] **Step 1: Write the test.**

```python
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
    svg = totp.totp_qr_svg("otpauth://totp/Idraa:user@example.com?secret=JBSWY3DPEHPK3PXP&issuer=Idraa")
    assert svg.lstrip().startswith("<?xml") or "<svg" in svg
    assert "</svg>" in svg
```

- [ ] **Step 2: Run — expect fail.** `uv run pytest tests/unit/test_totp_service.py -q`

- [ ] **Step 3: Create `src/idraa/services/totp.py`.**

```python
"""TOTP (RFC 6238) provisioning + verification + server-rendered QR (SVG)."""

from __future__ import annotations

import io

import pyotp
import segno


def provision_secret() -> str:
    return pyotp.random_base32()


def totp_uri(secret: str, account_name: str, issuer: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=account_name, issuer_name=issuer)


def verify_totp(secret: str, code: str, valid_window: int = 1) -> bool:
    """Verify a 6-digit code, tolerating +/- valid_window 30s steps for clock skew."""
    return bool(pyotp.TOTP(secret).verify(code.strip(), valid_window=valid_window))


def totp_qr_svg(uri: str) -> str:
    """Render the otpauth URI as an inline SVG string (no JS QR lib, no PNG)."""
    buf = io.BytesIO()
    segno.make(uri).save(buf, kind="svg", scale=5, border=2)
    return buf.getvalue().decode("utf-8")
```

- [ ] **Step 4: Run — expect pass, then commit.**

```bash
uv run pytest tests/unit/test_totp_service.py -q
git add src/idraa/services/totp.py tests/unit/test_totp_service.py
git commit -m "feat(auth): TOTP service (provision/verify/URI/QR-SVG)"
```

---

## Task 5: WebAuthn service wrapper

**Files:**
- Create: `src/idraa/services/webauthn_service.py`
- Test: `tests/unit/test_webauthn_service.py`

**Interfaces:**
- Produces:
  - `registration_options(user_id: uuid.UUID, user_email: str, user_display_name: str, existing_credential_ids: list[bytes]) -> tuple[str, str]` → `(options_json, challenge_b64url)`.
  - `verify_registration(credential: dict[str, Any] | str, challenge_b64url: str) -> RegisteredCredential` (dataclass: `credential_id: bytes`, `public_key: bytes`, `sign_count: int`, `aaguid: str`, `transports: str | None`).
  - `authentication_options() -> tuple[str, str]` → `(options_json, challenge_b64url)` (usernameless).
  - `verify_authentication(credential: dict[str, Any] | str, challenge_b64url: str, public_key: bytes, current_sign_count: int) -> int` → `new_sign_count`.
  - `sign_count_ok(stored: int, new: int) -> bool` (pure).
  - `parse_raw_id(credential: dict[str, Any] | str) -> bytes`.

- [ ] **Step 1: Write the test.** (Full ceremony verify needs a real authenticator — covered by the e2e virtual-authenticator test in Task 10. Here: options generation is deterministic enough to assert structure, and the sign-count + raw-id helpers are pure.)

```python
# tests/unit/test_webauthn_service.py
from __future__ import annotations

import json
import uuid

import idraa.config as config
from idraa.services import webauthn_service as ws
from webauthn.helpers import bytes_to_base64url


def _reset(monkeypatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("SESSION_SECRET", "s" * 32)
    monkeypatch.setenv("WEBAUTHN_RP_ID", "localhost")
    monkeypatch.setenv("WEBAUTHN_ORIGINS", "http://localhost")
    config.reset_for_tests()


def test_registration_options_require_uv_and_resident_key(monkeypatch) -> None:
    _reset(monkeypatch)
    options_json, challenge = ws.registration_options(
        uuid.uuid4(), "a@b.c", "A B", existing_credential_ids=[]
    )
    opts = json.loads(options_json)
    assert opts["rp"]["id"] == "localhost"
    assert opts["authenticatorSelection"]["userVerification"] == "required"
    assert opts["authenticatorSelection"]["residentKey"] == "required"
    assert isinstance(challenge, str) and len(challenge) > 0


def test_registration_options_excludes_existing(monkeypatch) -> None:
    _reset(monkeypatch)
    existing = b"\x01\x02\x03\x04"
    options_json, _ = ws.registration_options(
        uuid.uuid4(), "a@b.c", "A B", existing_credential_ids=[existing]
    )
    opts = json.loads(options_json)
    ids = {c["id"] for c in opts.get("excludeCredentials", [])}
    assert bytes_to_base64url(existing) in ids


def test_authentication_options_usernameless(monkeypatch) -> None:
    _reset(monkeypatch)
    options_json, challenge = ws.authentication_options()
    opts = json.loads(options_json)
    assert opts["userVerification"] == "required"
    assert opts.get("allowCredentials", []) == []
    assert len(challenge) > 0


def test_sign_count_ok() -> None:
    assert ws.sign_count_ok(5, 6) is True
    assert ws.sign_count_ok(5, 5) is False          # non-increasing → cloned
    assert ws.sign_count_ok(5, 4) is False
    assert ws.sign_count_ok(0, 0) is True           # authenticator that never counts


def test_parse_raw_id() -> None:
    raw = b"\xDE\xAD\xBE\xEF"
    body = json.dumps({"rawId": bytes_to_base64url(raw), "id": bytes_to_base64url(raw)})
    assert ws.parse_raw_id(body) == raw
```

- [ ] **Step 2: Run — expect fail.** `uv run pytest tests/unit/test_webauthn_service.py -q`

- [ ] **Step 3: Create `src/idraa/services/webauthn_service.py`.** (Verify the exact `webauthn` package symbols against the installed version — `uv run python -c "import webauthn, inspect; print(webauthn.__version__)"` — the API below targets py_webauthn 2.x.)

```python
"""Thin wrapper over py_webauthn for the passkey ceremonies.

RP-ID / RP-name / origins come from Settings (config-driven, never hardcoded).
Challenges are returned base64url-encoded so callers can stash them in a
signed cookie; they are handed back to verify_* on completion.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any

from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from idraa.config import get_settings


@dataclass(frozen=True)
class RegisteredCredential:
    credential_id: bytes
    public_key: bytes
    sign_count: int
    aaguid: str
    transports: str | None


def registration_options(
    user_id: uuid.UUID,
    user_email: str,
    user_display_name: str,
    existing_credential_ids: list[bytes],
) -> tuple[str, str]:
    s = get_settings()
    options = generate_registration_options(
        rp_id=s.webauthn_rp_id,
        rp_name=s.webauthn_rp_name,
        user_id=user_id.bytes,
        user_name=user_email,
        user_display_name=user_display_name,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.REQUIRED,
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
        exclude_credentials=[
            PublicKeyCredentialDescriptor(id=cid) for cid in existing_credential_ids
        ],
    )
    return options_to_json(options), bytes_to_base64url(options.challenge)


def verify_registration(
    credential: dict[str, Any] | str, challenge_b64url: str
) -> RegisteredCredential:
    s = get_settings()
    raw = credential if isinstance(credential, str) else json.dumps(credential)
    parsed = json.loads(credential) if isinstance(credential, str) else credential
    v = verify_registration_response(
        credential=raw,
        expected_challenge=base64url_to_bytes(challenge_b64url),
        expected_rp_id=s.webauthn_rp_id,
        expected_origin=s.webauthn_origin_list,
        require_user_verification=True,
    )
    transports = None
    t = parsed.get("response", {}).get("transports")
    if isinstance(t, list) and t:
        transports = ",".join(str(x) for x in t)
    return RegisteredCredential(
        credential_id=v.credential_id,
        public_key=v.credential_public_key,
        sign_count=v.sign_count,
        aaguid=v.aaguid,
        transports=transports,
    )


def authentication_options() -> tuple[str, str]:
    s = get_settings()
    options = generate_authentication_options(
        rp_id=s.webauthn_rp_id,
        allow_credentials=[],  # usernameless / discoverable
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    return options_to_json(options), bytes_to_base64url(options.challenge)


def verify_authentication(
    credential: dict[str, Any] | str,
    challenge_b64url: str,
    public_key: bytes,
    current_sign_count: int,
) -> int:
    s = get_settings()
    raw = credential if isinstance(credential, str) else json.dumps(credential)
    v = verify_authentication_response(
        credential=raw,
        expected_challenge=base64url_to_bytes(challenge_b64url),
        expected_rp_id=s.webauthn_rp_id,
        expected_origin=s.webauthn_origin_list,
        credential_public_key=public_key,
        credential_current_sign_count=current_sign_count,
        require_user_verification=True,
    )
    return v.new_sign_count


def sign_count_ok(stored: int, new: int) -> bool:
    """Reject a non-increasing counter (cloned authenticator). The 0/0 case is
    an authenticator that never increments — allowed."""
    if stored == 0 and new == 0:
        return True
    return new > stored


def parse_raw_id(credential: dict[str, Any] | str) -> bytes:
    parsed = json.loads(credential) if isinstance(credential, str) else credential
    return base64url_to_bytes(parsed["rawId"])
```

- [ ] **Step 4: Run — expect pass, then commit.**

```bash
uv run pytest tests/unit/test_webauthn_service.py -q
git add src/idraa/services/webauthn_service.py tests/unit/test_webauthn_service.py
git commit -m "feat(auth): py_webauthn service wrapper (options + verify + sign-count)"
```

---

## Task 6: TOTP + recovery enrollment routes + `/account/security` page

**Files:**
- Create: `src/idraa/services/mfa_enrollment.py`
- Create: `src/idraa/routes/mfa.py`
- Create: `src/idraa/templates/account/security.html`, `templates/account/_totp.html`
- Modify: `src/idraa/app.py` (include the router)
- Test: `tests/integration/test_mfa_totp_enrollment.py`

**Interfaces:**
- Produces (`mfa_enrollment`): `async def user_has_strong_factor(db, user_id) -> bool`; `async def user_has_recovery_codes(db, user_id) -> bool`; `async def maybe_stamp_enrolled(db, user) -> None` (sets `user.mfa_enrolled_at` when ≥1 strong factor AND recovery codes exist and it's currently null).
- Produces (routes): `GET /account/security`, `GET/POST /account/security/totp/enroll`, `POST /account/security/recovery-codes/generate`.

- [ ] **Step 1: Write the integration test.**

```python
# tests/integration/test_mfa_totp_enrollment.py
from __future__ import annotations

import re

import pyotp
from httpx import AsyncClient
from sqlalchemy import select

from idraa.models.mfa import RecoveryCode, UserTotp


async def test_totp_enroll_confirm_and_recovery_stamps_enrolled(
    authed_admin: tuple[AsyncClient, object], db_session
) -> None:
    from tests.conftest import csrf_post

    client, _org = authed_admin

    # GET returns the QR + the manual key; the unconfirmed secret rides a signed
    # cookie (NO DB write on GET). Extract the secret from the shown manual key.
    r = await client.get("/account/security/totp/enroll")
    assert r.status_code == 200
    assert "<svg" in r.text
    m = re.search(r"Manual key:\s*([A-Z2-7]+)", r.text)
    assert m, "manual key not rendered"
    secret = m.group(1)
    # No UserTotp row exists yet (GET didn't persist).
    assert (await db_session.execute(select(UserTotp))).scalars().first() is None

    # Confirm with a live code (the rf_totp_pending cookie is carried by the client).
    code = pyotp.TOTP(secret).now()
    r2 = await csrf_post(
        client, "/account/security/totp/enroll", {"code": code},
        bootstrap_url="/account/security", follow_redirects=False,
    )
    assert r2.status_code == 303

    await db_session.commit()
    confirmed = (await db_session.execute(select(UserTotp))).scalars().first()
    assert confirmed is not None and confirmed.confirmed_at is not None

    # Generate recovery codes → completes enrollment.
    r3 = await csrf_post(
        client, "/account/security/recovery-codes/generate", {},
        bootstrap_url="/account/security",
    )
    assert r3.status_code == 200
    found = re.findall(r"\b[0-9a-f]{5}-[0-9a-f]{5}\b", r3.text)
    assert len(found) >= 10, "recovery codes should be shown once"

    await db_session.commit()
    assert (await db_session.execute(select(RecoveryCode))).scalars().first() is not None

    r4 = await client.get("/account/security")
    assert "Enrolled" in r4.text or "enrolled" in r4.text


async def test_totp_confirm_rejects_bad_code(
    authed_admin: tuple[AsyncClient, object], db_session
) -> None:
    from tests.conftest import csrf_post

    client, _ = authed_admin
    await client.get("/account/security/totp/enroll")
    r = await csrf_post(
        client, "/account/security/totp/enroll", {"code": "000000"},
        bootstrap_url="/account/security",
    )
    assert r.status_code == 400
```

- [ ] **Step 2: Run — expect fail** (404 — routes don't exist).

Run: `uv run pytest tests/integration/test_mfa_totp_enrollment.py -q`

- [ ] **Step 3: Create `src/idraa/services/mfa_enrollment.py`.**

```python
"""Enrollment state helpers: what counts as 'enrolled', and stamping it."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models._types import now_utc
from idraa.models.mfa import RecoveryCode, UserTotp, WebAuthnCredential
from idraa.models.user import User


async def user_has_strong_factor(db: AsyncSession, user_id: uuid.UUID) -> bool:
    passkeys = await db.scalar(
        select(func.count()).select_from(WebAuthnCredential).where(
            WebAuthnCredential.user_id == user_id
        )
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
```

- [ ] **Step 4: Create `src/idraa/routes/mfa.py`** (TOTP + recovery slice; passkey endpoints added in Task 7).

```python
"""Account security: MFA enrollment + management."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.app import templates
from idraa.models.mfa import RecoveryCode, UserTotp, WebAuthnCredential
from idraa.models.user import User
from idraa.config import get_settings
from idraa.models._types import now_utc
from idraa.routes.deps import client_ip, get_db, require_user
from idraa.services import totp as totp_service
from idraa.services.audit import AuditWriter
from idraa.services.auth import (
    clear_totp_pending_cookie,
    load_totp_pending,
    set_totp_pending_cookie,
)
from idraa.services.mfa_crypto import (
    encrypt_totp_secret,
    generate_recovery_codes,
    hash_recovery_code,
)
from idraa.services.mfa_enrollment import maybe_stamp_enrolled

router = APIRouter()


async def _security_context(db: AsyncSession, user: User) -> dict[str, object]:
    passkeys = (
        await db.execute(select(WebAuthnCredential).where(WebAuthnCredential.user_id == user.id))
    ).scalars().all()
    totp = (
        await db.execute(select(UserTotp).where(UserTotp.user_id == user.id))
    ).scalars().first()
    recovery_remaining = len(
        [
            r
            for r in (
                await db.execute(select(RecoveryCode).where(RecoveryCode.user_id == user.id))
            ).scalars().all()
            if r.used_at is None
        ]
    )
    return {
        "current_user": user,
        "passkeys": passkeys,
        "totp_confirmed": bool(totp and totp.confirmed_at),
        "recovery_remaining": recovery_remaining,
        "enrolled": user.mfa_enrolled_at is not None,
    }


@router.get("/account/security", response_class=HTMLResponse)
async def security_page(
    request: Request, db: AsyncSession = Depends(get_db), user: User = Depends(require_user)
) -> HTMLResponse:
    ctx = await _security_context(db, user)
    return templates.TemplateResponse(request, "account/security.html", ctx)


@router.get("/account/security/totp/enroll", response_class=HTMLResponse)
async def totp_enroll_get(
    request: Request, db: AsyncSession = Depends(get_db), user: User = Depends(require_user)
) -> Response:
    # Already confirmed → just render (no re-provision).
    confirmed = (
        await db.execute(select(UserTotp).where(
            UserTotp.user_id == user.id, UserTotp.confirmed_at.is_not(None)))
    ).scalars().first()
    if confirmed is not None:
        return templates.TemplateResponse(
            request, "account/_totp.html", {"already": True, "qr_svg": "", "current_user": user})
    # Provision a fresh secret; stash it in a SIGNED cookie — NO DB write on GET.
    secret = totp_service.provision_secret()
    uri = totp_service.totp_uri(secret, user.email, get_settings().totp_issuer)
    resp = templates.TemplateResponse(
        request, "account/_totp.html",
        {"already": False, "qr_svg": totp_service.totp_qr_svg(uri), "secret": secret,
         "current_user": user})
    set_totp_pending_cookie(resp, secret)
    return resp


@router.post("/account/security/totp/enroll")
async def totp_enroll_post(
    request: Request,
    code: str = Form(..., max_length=10),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_user),
) -> Response:
    signed = request.cookies.get("rf_totp_pending")
    secret = load_totp_pending(signed) if signed else None
    if secret is None:
        return RedirectResponse("/account/security/totp/enroll", status_code=303)
    if not totp_service.verify_totp(secret, code):
        ctx = await _security_context(db, user)
        ctx["error"] = "That code didn't match. Try again."
        return templates.TemplateResponse(request, "account/security.html", ctx, status_code=400)
    # Confirmed — persist the ENCRYPTED secret now (first TOTP DB write).
    existing = (
        await db.execute(select(UserTotp).where(UserTotp.user_id == user.id))
    ).scalars().first()
    if existing is None:
        db.add(UserTotp(user_id=user.id, secret_encrypted=encrypt_totp_secret(secret),
                        confirmed_at=now_utc()))
    else:
        existing.secret_encrypted = encrypt_totp_secret(secret)
        existing.confirmed_at = now_utc()
    await AuditWriter(db).log(
        organization_id=user.organization_id, entity_type="user", entity_id=user.id,
        action="user.mfa_totp_enroll", changes={}, user_id=user.id, ip_address=client_ip(request),
    )
    await maybe_stamp_enrolled(db, user)
    resp = RedirectResponse("/account/security", status_code=303)
    clear_totp_pending_cookie(resp)
    return resp


@router.post("/account/security/recovery-codes/generate", response_class=HTMLResponse)
async def recovery_codes_generate(
    request: Request, db: AsyncSession = Depends(get_db), user: User = Depends(require_user)
) -> HTMLResponse:
    # Replace any prior codes (regenerate invalidates the old set).
    for old in (
        await db.execute(select(RecoveryCode).where(RecoveryCode.user_id == user.id))
    ).scalars().all():
        await db.delete(old)
    codes = generate_recovery_codes()
    for c in codes:
        db.add(RecoveryCode(user_id=user.id, code_hash=hash_recovery_code(c)))
    await AuditWriter(db).log(
        organization_id=user.organization_id, entity_type="user", entity_id=user.id,
        action="user.recovery_codes_generated", changes={"count": len(codes)},
        user_id=user.id, ip_address=client_ip(request),
    )
    await maybe_stamp_enrolled(db, user)
    return templates.TemplateResponse(
        request, "account/security.html",
        {**(await _security_context(db, user)), "shown_recovery_codes": codes},
    )
```

- [ ] **Step 5: Create the templates.** `src/idraa/templates/account/security.html`:

```html
{% extends "base.html" %}
{% block title %}Account security{% endblock %}
{% block container_class %}max-w-2xl mx-auto{% endblock %}
{% block content %}
<div class="pt-8 space-y-8">
  <h1 class="text-display text-ink-1">Account security</h1>
  {% if error %}<div class="alert alert-error">{{ error }}</div>{% endif %}
  {% if enrolled %}<div class="badge badge-success">Enrolled</div>{% endif %}

  {% if shown_recovery_codes %}
  <section class="card bg-surface-0 p-4">
    <h2 class="font-semibold">Save your recovery codes</h2>
    <p class="text-sm text-ink-2">Shown once. Store them somewhere safe.</p>
    <ul class="font-mono text-sm mt-2">
      {% for c in shown_recovery_codes %}<li>{{ c }}</li>{% endfor %}
    </ul>
  </section>
  {% endif %}

  <section class="card bg-surface-0 p-4" id="totp-section">
    <h2 class="font-semibold">Authenticator app (TOTP)</h2>
    {% if totp_confirmed %}
      <p class="text-sm text-ink-2">Enabled.</p>
    {% else %}
      <a class="btn btn-primary btn-sm" href="/account/security/totp/enroll">Set up authenticator</a>
    {% endif %}
  </section>

  <section class="card bg-surface-0 p-4" id="passkey-section">
    <h2 class="font-semibold">Passkeys</h2>
    <ul>{% for p in passkeys %}<li>{{ p.nickname }}</li>{% endfor %}</ul>
    {# Passkey add/remove UI wired in Task 7. #}
  </section>

  <section class="card bg-surface-0 p-4">
    <h2 class="font-semibold">Recovery codes</h2>
    <p class="text-sm text-ink-2">{{ recovery_remaining }} unused.</p>
    <form method="post" action="/account/security/recovery-codes/generate">
      {{ csrf_field() }}
      <button class="btn btn-sm" type="submit">Regenerate recovery codes</button>
    </form>
  </section>
</div>
{% endblock %}
```

`src/idraa/templates/account/_totp.html`:

```html
{% extends "base.html" %}
{% block title %}Set up authenticator{% endblock %}
{% block container_class %}max-w-md mx-auto{% endblock %}
{% block content %}
<div class="pt-8 space-y-4">
  <h1 class="text-display text-ink-1">Set up authenticator</h1>
  {% if already %}
    <p>An authenticator is already enabled.</p>
  {% else %}
    <p class="text-sm text-ink-2">Scan this with your authenticator app, then enter the 6-digit code.</p>
    <div class="bg-white inline-block p-2">{{ qr_svg | safe }}</div>
    <p class="text-xs font-mono text-ink-2">Manual key: {{ secret }}</p>
    <form method="post" action="/account/security/totp/enroll" class="space-y-3">
      {{ csrf_field() }}
      <input name="code" inputmode="numeric" autocomplete="one-time-code" maxlength="10"
             class="input input-bordered" placeholder="123456" required>
      <button class="btn btn-primary btn-sm" type="submit">Confirm</button>
    </form>
  {% endif %}
</div>
{% endblock %}
```

- [ ] **Step 6: Register the router.** In `src/idraa/app.py`, add to the deferred-import block: `from idraa.routes import mfa as mfa_router`, and after `app.include_router(users_router.router)` add `app.include_router(mfa_router.router)`.

- [ ] **Step 7: Rebuild CSS** (new templates introduce classes): `uv run python -m idraa.tasks.build_css`.

- [ ] **Step 8: Run — expect pass, then commit.**

```bash
uv run pytest tests/integration/test_mfa_totp_enrollment.py -q
git add src/idraa/services/mfa_enrollment.py src/idraa/routes/mfa.py src/idraa/templates/account/ src/idraa/app.py src/idraa/static/css/tailwind.css tests/integration/test_mfa_totp_enrollment.py
git commit -m "feat(auth): TOTP + recovery-code enrollment + /account/security page"
```

---

## Task 7: Passkey enrollment routes + webauthn.js + credential view mapper

**Files:**
- Modify: `src/idraa/routes/mfa.py` (passkey endpoints)
- Modify: `src/idraa/services/mfa_enrollment.py` (`credential_views` mapper)
- Create: `src/idraa/static/js/webauthn.js`
- Modify: `src/idraa/templates/base.html` (script include), `templates/account/security.html` (passkey UI)
- Test: `tests/integration/test_mfa_passkey_routes.py`, `tests/contracts/test_credential_view_iteration.py`

**Interfaces:**
- Consumes: `webauthn_service.registration_options/verify_registration/parse_raw_id`; `auth.sign_webauthn_challenge/load_webauthn_challenge`.
- Produces: `POST /account/security/passkey/options` (JSON options + challenge cookie), `POST /account/security/passkey/verify` (JSON), `POST /account/security/passkey/{cred_id}/delete` (form); `mfa_enrollment.credential_views(creds: list[WebAuthnCredential]) -> list[dict]`; JS globals `window.idraaWebAuthn.register()` / `.authenticate()`.

- [ ] **Step 1: Write the iteration contract + integration tests.**

```python
# tests/contracts/test_credential_view_iteration.py
from __future__ import annotations

import uuid

from idraa.models.mfa import WebAuthnCredential
from idraa.services.mfa_enrollment import credential_views
from tests.contracts.helpers import assert_preserves_list_count


def _build(n: int) -> list[WebAuthnCredential]:
    return [
        WebAuthnCredential(
            user_id=uuid.uuid4(), credential_id=bytes([i]), public_key=b"k",
            nickname=f"key-{i}",
        )
        for i in range(n)
    ]


def test_credential_views_preserves_all() -> None:
    assert_preserves_list_count(credential_views, _build, n=3)
```

```python
# tests/integration/test_mfa_passkey_routes.py
from __future__ import annotations

from httpx import AsyncClient
from sqlalchemy import select

from idraa.models.mfa import WebAuthnCredential
from idraa.models.user import User


async def test_passkey_options_returns_json_and_sets_challenge_cookie(
    authed_admin: tuple[AsyncClient, object]
) -> None:
    client, _ = authed_admin
    # Bootstrap a CSRF token, then call the JSON endpoint with the header.
    await client.get("/account/security")
    token = client.cookies.get("csrf_token")
    r = await client.post("/account/security/passkey/options", headers={"X-CSRF-Token": token})
    assert r.status_code == 200
    body = r.json()
    assert body["authenticatorSelection"]["userVerification"] == "required"
    assert "rf_webauthn_challenge" in r.cookies or any(
        "rf_webauthn_challenge" in h for h in r.headers.get_list("set-cookie")
    )


async def test_passkey_options_without_csrf_header_is_forbidden(
    authed_admin: tuple[AsyncClient, object]
) -> None:
    client, _ = authed_admin
    await client.get("/account/security")
    r = await client.post("/account/security/passkey/options")  # no X-CSRF-Token
    assert r.status_code == 403


async def test_passkey_delete_removes_row(
    authed_admin: tuple[AsyncClient, object], db_session
) -> None:
    from tests.conftest import csrf_post

    client, _ = authed_admin
    me = (await db_session.execute(select(User))).scalars().first()
    cred = WebAuthnCredential(user_id=me.id, credential_id=b"\x09\x09", public_key=b"k", nickname="X")
    db_session.add(cred)
    await db_session.commit()
    r = await csrf_post(
        client, f"/account/security/passkey/{cred.id}/delete", {},
        bootstrap_url="/account/security", follow_redirects=False,
    )
    assert r.status_code == 303
    await db_session.commit()
    remaining = (await db_session.execute(select(WebAuthnCredential))).scalars().all()
    assert all(c.id != cred.id for c in remaining)
```

- [ ] **Step 2: Run — expect fail.** `uv run pytest tests/contracts/test_credential_view_iteration.py tests/integration/test_mfa_passkey_routes.py -q`

- [ ] **Step 3: Add the view mapper** to `src/idraa/services/mfa_enrollment.py`:

```python
def credential_views(creds: list[WebAuthnCredential]) -> list[dict[str, object]]:
    """Map passkey ORM rows to template-safe view dicts. Preserves order + count."""
    return [
        {"id": str(c.id), "nickname": c.nickname, "last_used_at": c.last_used_at,
         "created_at": c.created_at}
        for c in creds
    ]
```

- [ ] **Step 4: Add passkey endpoints** to `src/idraa/routes/mfa.py`. Add at module top: `import json`, `import uuid`, `from typing import Any`, `from fastapi import Body`, `from sqlalchemy.exc import IntegrityError`, `from idraa.services import webauthn_service`, `from idraa.services.auth import (clear_webauthn_challenge_cookie, load_webauthn_challenge, set_webauthn_challenge_cookie)`, and add `maybe_unstamp_enrolled` to the `mfa_enrollment` import. Add a small JSON-error helper and the endpoints:

```python
def _json_error(msg: str, status: int = 400) -> Response:
    return Response(content=json.dumps({"error": msg}), status_code=status,
                    media_type="application/json")


@router.post("/account/security/passkey/options")
async def passkey_register_options(
    request: Request, db: AsyncSession = Depends(get_db), user: User = Depends(require_user)
) -> Response:
    existing = [
        c.credential_id
        for c in (
            await db.execute(select(WebAuthnCredential).where(WebAuthnCredential.user_id == user.id))
        ).scalars().all()
    ]
    options_json, challenge = webauthn_service.registration_options(
        user.id, user.email, user.full_name, existing
    )
    resp = Response(content=options_json, media_type="application/json")
    set_webauthn_challenge_cookie(resp, challenge)
    return resp


@router.post("/account/security/passkey/verify")
async def passkey_register_verify(
    request: Request,
    payload: dict[str, Any] = Body(...),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_user),
) -> Response:
    signed = request.cookies.get("rf_webauthn_challenge")
    challenge = load_webauthn_challenge(signed) if signed else None
    if challenge is None:
        return _json_error("challenge expired")
    try:
        reg = webauthn_service.verify_registration(payload["credential"], challenge)
    except Exception as exc:  # noqa: BLE001 — any bad/tampered ceremony → 400, not 500
        return _json_error(f"verification failed: {type(exc).__name__}")
    nickname = (payload.get("nickname") or "Passkey")[:64]
    cred = WebAuthnCredential(
        user_id=user.id, credential_id=reg.credential_id, public_key=reg.public_key,
        sign_count=reg.sign_count, aaguid=reg.aaguid, transports=reg.transports, nickname=nickname,
    )
    db.add(cred)
    try:
        await db.flush()  # surface a duplicate credential_id as IntegrityError, not a 500
    except IntegrityError:
        await db.rollback()
        return _json_error("credential already registered")
    await AuditWriter(db).log(
        organization_id=user.organization_id, entity_type="webauthn_credential",
        entity_id=cred.id, action="webauthn_credential.create", changes={"nickname": nickname},
        user_id=user.id, ip_address=client_ip(request),
    )
    await maybe_stamp_enrolled(db, user)
    resp = Response(content='{"ok":true}', media_type="application/json")
    clear_webauthn_challenge_cookie(resp)
    return resp


@router.post("/account/security/passkey/{cred_id}/delete")
async def passkey_delete(
    cred_id: uuid.UUID, request: Request,
    db: AsyncSession = Depends(get_db), user: User = Depends(require_user),
) -> Response:
    cred = (
        await db.execute(
            select(WebAuthnCredential).where(
                WebAuthnCredential.id == cred_id, WebAuthnCredential.user_id == user.id
            )
        )
    ).scalars().first()
    if cred is not None:
        cred_pk = cred.id
        await db.delete(cred)
        await db.flush()  # make the delete visible to maybe_unstamp_enrolled's count
        await AuditWriter(db).log(
            organization_id=user.organization_id, entity_type="webauthn_credential",
            entity_id=cred_pk, action="webauthn_credential.delete", changes={}, user_id=user.id,
            ip_address=client_ip(request),
        )
        # I4: if that was the last strong factor, clear enrollment so the
        # interstitial re-fires (don't silently downgrade to password-only).
        await maybe_unstamp_enrolled(db, user)
    return RedirectResponse("/account/security", status_code=303)
```

- [ ] **Step 5: Create `src/idraa/static/js/webauthn.js`.**

```javascript
/* webauthn.js — passkey ceremonies over navigator.credentials. No build step.
 * CSRF: every fetch sends X-CSRF-Token from <meta name="csrf-token">.
 */
(function () {
  "use strict";
  function csrf() {
    var m = document.querySelector('meta[name="csrf-token"]');
    return m ? m.content : "";
  }
  function b64urlToBuf(s) {
    s = s.replace(/-/g, "+").replace(/_/g, "/");
    var pad = s.length % 4 ? "=".repeat(4 - (s.length % 4)) : "";
    var bin = atob(s + pad), buf = new Uint8Array(bin.length);
    for (var i = 0; i < bin.length; i++) buf[i] = bin.charCodeAt(i);
    return buf.buffer;
  }
  function bufToB64url(buf) {
    var bytes = new Uint8Array(buf), bin = "";
    for (var i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
    return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  }
  function post(url, body) {
    var opts = { method: "POST", headers: { "X-CSRF-Token": csrf() }, credentials: "same-origin" };
    if (body !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    return fetch(url, opts);
  }
  function encodeRegistration(cred) {
    return {
      id: cred.id, rawId: bufToB64url(cred.rawId), type: cred.type,
      response: {
        clientDataJSON: bufToB64url(cred.response.clientDataJSON),
        attestationObject: bufToB64url(cred.response.attestationObject),
        transports: cred.response.getTransports ? cred.response.getTransports() : [],
      },
    };
  }
  function encodeAssertion(cred) {
    return {
      id: cred.id, rawId: bufToB64url(cred.rawId), type: cred.type,
      response: {
        clientDataJSON: bufToB64url(cred.response.clientDataJSON),
        authenticatorData: bufToB64url(cred.response.authenticatorData),
        signature: bufToB64url(cred.response.signature),
        userHandle: cred.response.userHandle ? bufToB64url(cred.response.userHandle) : null,
      },
    };
  }
  async function register(nickname) {
    var optsResp = await post("/account/security/passkey/options");
    if (!optsResp.ok) throw new Error("options request failed");
    var options = await optsResp.json();
    options.challenge = b64urlToBuf(options.challenge);
    options.user.id = b64urlToBuf(options.user.id);
    (options.excludeCredentials || []).forEach(function (c) { c.id = b64urlToBuf(c.id); });
    var cred = await navigator.credentials.create({ publicKey: options });
    var verifyResp = await post("/account/security/passkey/verify",
      { credential: encodeRegistration(cred), nickname: nickname || "Passkey" });
    if (!verifyResp.ok) throw new Error("verification failed");
    window.location.assign("/account/security");
  }
  async function authenticate() {
    var optsResp = await post("/login/passkey/options");
    if (!optsResp.ok) throw new Error("options request failed");
    var options = await optsResp.json();
    options.challenge = b64urlToBuf(options.challenge);
    (options.allowCredentials || []).forEach(function (c) { c.id = b64urlToBuf(c.id); });
    var cred = await navigator.credentials.get({ publicKey: options });
    var verifyResp = await post("/login/passkey/verify", { credential: encodeAssertion(cred) });
    if (!verifyResp.ok) throw new Error("passkey sign-in failed");
    var data = await verifyResp.json();
    window.location.assign(data.next || "/");
  }
  window.idraaWebAuthn = { register: register, authenticate: authenticate };
})();
```

- [ ] **Step 6: Wire the script + passkey UI.** In `src/idraa/templates/base.html`, add near the other `/static/js` includes: `<script src="/static/js/webauthn.js?v={{ static_version }}" defer></script>`. In `account/security.html`, replace the passkey-section body with an add button + list:

```html
    <ul>{% for p in passkeys %}<li>{{ p.nickname }}
      <form method="post" action="/account/security/passkey/{{ p.id }}/delete" class="inline">
        {{ csrf_field() }}<button class="btn btn-ghost btn-xs" type="submit">Remove</button>
      </form></li>{% endfor %}</ul>
    <button class="btn btn-primary btn-sm" type="button"
            onclick="idraaWebAuthn.register(prompt('Name this passkey','Passkey')).catch(function(e){alert(e.message)})"
            x-show="!!window.PublicKeyCredential">Add a passkey</button>
```

- [ ] **Step 7: Rebuild CSS + run + commit.**

```bash
uv run python -m idraa.tasks.build_css
uv run pytest tests/contracts/test_credential_view_iteration.py tests/integration/test_mfa_passkey_routes.py -q
git add src/idraa/routes/mfa.py src/idraa/services/mfa_enrollment.py src/idraa/static/js/webauthn.js src/idraa/templates/ src/idraa/static/css/tailwind.css tests/contracts/test_credential_view_iteration.py tests/integration/test_mfa_passkey_routes.py
git commit -m "feat(auth): passkey enrollment endpoints + webauthn.js + credential views"
```

---

## Task 8: Login state machine (passkey login + password → mfa_pending → TOTP/recovery)

**Files:**
- Modify: `src/idraa/routes/auth.py`
- Create: `src/idraa/templates/auth/mfa_challenge.html`
- Modify: `src/idraa/templates/auth/login.html` (passkey button)
- Test: `tests/integration/test_login_mfa_flow.py`

**Interfaces:**
- Consumes: `sign_mfa_pending/load_mfa_pending`, `verify_totp`, `verify_recovery_code`, `webauthn_service.authentication_options/verify_authentication/parse_raw_id/sign_count_ok`, `user_has_strong_factor`.
- Produces: `POST /login` (branches to mfa_challenge), `POST /login/mfa` (TOTP/recovery), `POST /login/passkey/options`, `POST /login/passkey/verify`.

- [ ] **Step 1: Write the integration test.**

```python
# tests/integration/test_login_mfa_flow.py
from __future__ import annotations

import pyotp
from httpx import AsyncClient
from sqlalchemy import select

from idraa.models.mfa import RecoveryCode, UserTotp
from idraa.models.user import User
from idraa.services.mfa_crypto import hash_recovery_code
from idraa.models._types import now_utc


async def _seed_setup(client: AsyncClient) -> None:
    from tests.conftest import csrf_post
    await csrf_post(client, "/setup", {
        "org_name": "A", "industry_type": "information", "organization_size": "small",
        "email": "a@b.c", "full_name": "A", "password": "pw-12345678",
    })
    client.cookies.delete("idraa_session")


async def test_password_login_with_totp_second_factor(client: AsyncClient, db_session) -> None:
    from tests.conftest import csrf_post
    await _seed_setup(client)
    user = (await db_session.execute(select(User))).scalars().first()
    secret = pyotp.random_base32()
    from idraa.services.mfa_crypto import encrypt_totp_secret
    db_session.add(UserTotp(user_id=user.id, secret_encrypted=encrypt_totp_secret(secret),
                            confirmed_at=now_utc()))
    user.mfa_enrolled_at = now_utc()
    await db_session.commit()

    # Step 1: password → mfa challenge (NOT a session yet).
    r = await csrf_post(client, "/login", {"email": "a@b.c", "password": "pw-12345678"},
                        follow_redirects=False)
    assert r.status_code == 200
    assert "idraa_session" not in r.cookies
    assert "code" in r.text.lower()

    # Step 2: TOTP code → session.
    code = pyotp.TOTP(secret).now()
    r2 = await csrf_post(client, "/login/mfa", {"code": code},
                         bootstrap_url="/login", follow_redirects=False)
    assert r2.status_code == 303
    assert "idraa_session" in r2.cookies or any(
        "idraa_session" in h for h in r2.headers.get_list("set-cookie"))


async def test_recovery_code_second_factor_is_single_use(client: AsyncClient, db_session) -> None:
    from tests.conftest import csrf_post
    await _seed_setup(client)
    user = (await db_session.execute(select(User))).scalars().first()
    db_session.add(RecoveryCode(user_id=user.id, code_hash=hash_recovery_code("aaaaa-bbbbb")))
    from idraa.services.mfa_crypto import encrypt_totp_secret
    db_session.add(UserTotp(user_id=user.id, secret_encrypted=encrypt_totp_secret(pyotp.random_base32()),
                            confirmed_at=now_utc()))
    user.mfa_enrolled_at = now_utc()
    await db_session.commit()

    await csrf_post(client, "/login", {"email": "a@b.c", "password": "pw-12345678"},
                    follow_redirects=False)
    r = await csrf_post(client, "/login/mfa", {"code": "aaaaa-bbbbb"},
                        bootstrap_url="/login", follow_redirects=False)
    assert r.status_code == 303
    await db_session.commit()
    used = (await db_session.execute(select(RecoveryCode))).scalars().first()
    assert used.used_at is not None


async def test_unenrolled_user_password_login_gets_session(client: AsyncClient, db_session) -> None:
    # Migration path: no strong factor yet → straight session (interstitial traps later).
    from tests.conftest import csrf_post
    await _seed_setup(client)
    r = await csrf_post(client, "/login", {"email": "a@b.c", "password": "pw-12345678"},
                        follow_redirects=False)
    assert r.status_code == 303
    set_cookie = "".join(r.headers.get_list("set-cookie"))
    assert "idraa_session" in r.cookies or "idraa_session" in set_cookie


async def test_repeated_bad_password_locks_account(client: AsyncClient, db_session) -> None:
    # B1: minimal throttle. AUTH_MAX_FAILED_LOGINS defaults to 5.
    from tests.conftest import csrf_post
    await _seed_setup(client)
    for _ in range(5):
        await csrf_post(client, "/login", {"email": "a@b.c", "password": "wrong-pass"},
                        follow_redirects=False)
    await db_session.commit()
    user = (await db_session.execute(select(User))).scalars().first()
    assert user.locked_until is not None
    # Even the CORRECT password is denied while locked.
    r = await csrf_post(client, "/login", {"email": "a@b.c", "password": "pw-12345678"},
                        follow_redirects=False)
    assert r.status_code == 400


async def _seed_totp_user(client: AsyncClient, db_session) -> None:
    import pyotp
    from idraa.services.mfa_crypto import encrypt_totp_secret
    await _seed_setup(client)
    user = (await db_session.execute(select(User))).scalars().first()
    db_session.add(UserTotp(user_id=user.id, secret_encrypted=encrypt_totp_secret(pyotp.random_base32()),
                            confirmed_at=now_utc()))
    user.mfa_enrolled_at = now_utc()
    await db_session.commit()


async def test_repeated_bad_totp_locks_account(client: AsyncClient, db_session) -> None:
    # B1: failed second-factor attempts count toward lockout, within one window.
    from tests.conftest import csrf_post
    await _seed_totp_user(client, db_session)
    await csrf_post(client, "/login", {"email": "a@b.c", "password": "pw-12345678"},
                    follow_redirects=False)                 # ONE password login → mfa_pending
    for _ in range(5):
        await csrf_post(client, "/login/mfa", {"code": "000000"},
                        bootstrap_url="/login", follow_redirects=False)
    await db_session.commit()
    locked = (await db_session.execute(select(User))).scalars().first()
    assert locked.locked_until is not None


async def test_relogin_does_not_reset_mfa_throttle(client: AsyncClient, db_session) -> None:
    # Regression (plan-gate round 2): a correct password must NOT reset the 2FA
    # failure counter while a second factor is still pending — otherwise an
    # attacker who has the password bypasses the TOTP rate limit by re-POSTing
    # /login before each guess.
    from tests.conftest import csrf_post
    await _seed_totp_user(client, db_session)
    for _ in range(5):
        await csrf_post(client, "/login", {"email": "a@b.c", "password": "pw-12345678"},
                        follow_redirects=False)             # correct password each time
        await csrf_post(client, "/login/mfa", {"code": "000000"},
                        bootstrap_url="/login", follow_redirects=False)
    await db_session.commit()
    locked = (await db_session.execute(select(User))).scalars().first()
    assert locked.locked_until is not None                  # re-login did NOT wipe the counter
```

- [ ] **Step 2: Run — expect fail.** `uv run pytest tests/integration/test_login_mfa_flow.py -q`

- [ ] **Step 3a: Add throttle helpers to `src/idraa/services/auth.py`** (B1 — minimal idraa#81 slice). `datetime`, `UTC`, `timedelta`, `get_settings`, and `User` are already imported there:

```python
def is_locked(user: User) -> bool:
    lu = user.locked_until
    if lu is None:
        return False
    if lu.tzinfo is None:            # aiosqlite may strip tzinfo on cross-connection read
        lu = lu.replace(tzinfo=UTC)
    return lu > datetime.now(UTC)


def register_failed_login(user: User) -> None:
    settings = get_settings()
    user.failed_login_count += 1
    if settings.auth_max_failed_logins and user.failed_login_count >= settings.auth_max_failed_logins:
        user.locked_until = datetime.now(UTC) + timedelta(seconds=settings.auth_lockout_seconds)


def reset_login_throttle(user: User) -> None:
    user.failed_login_count = 0
    user.locked_until = None
```

- [ ] **Step 3b: Harden `_safe_next` in `src/idraa/routes/auth.py`** (I3 — backslash open-redirect, now on the auth-critical path):

```python
def _safe_next(raw: str | None) -> str:
    """Same-origin absolute path only. Rejects //evil AND /\\evil (browsers
    normalize backslash to slash for special schemes → protocol-relative)."""
    if raw and raw.startswith("/") and raw[1:2] not in ("/", "\\"):
        return raw
    return "/"
```

- [ ] **Step 3c: Hoist imports to module top + rework `POST /login`.** Add at the top of `src/idraa/routes/auth.py`: `from fastapi import Body`, `from typing import Any`, `from idraa.config import get_settings`, `from idraa.services.auth import (is_locked, load_mfa_pending, register_failed_login, reset_login_throttle, set_mfa_pending_cookie, clear_mfa_pending_cookie, set_webauthn_challenge_cookie, load_webauthn_challenge)`, `from idraa.services.mfa_enrollment import user_has_strong_factor`, `from idraa.services import webauthn_service, totp as totp_service`, `from idraa.services.mfa_crypto import decrypt_totp_secret, verify_recovery_code`, `from idraa.models.mfa import UserTotp, RecoveryCode, WebAuthnCredential`. `AuditWriter`, `create_session`, `set_session_cookie`, `client_ip` are already imported. Replace `login_post` with the throttled version (the bad-credentials 400 block is unchanged from today; the added lines are the lock check + throttle counters + the MFA branch):

```python
    user = await load_user_by_email(db, email)
    password_ok = verify_user_password(user, password)  # always one Argon2 (timing-safe)
    if user is None or not password_ok:
        if user is not None and not is_locked(user):
            register_failed_login(user)                  # count only a real, unlocked user's miss
            if is_locked(user):                          # this miss just tripped the lock → audit
                await AuditWriter(db).log(
                    organization_id=user.organization_id, entity_type="user", entity_id=user.id,
                    action="user.login_locked_out", changes={}, user_id=user.id,
                    ip_address=client_ip(request))
        return templates.TemplateResponse(
            request, "auth/login.html",
            {"current_user": None, "flash": None, "form": {"email": email},
             "error": "Invalid email or password", "next": safe_next}, status_code=400)
    if is_locked(user):                                  # correct password but locked → still deny
        return templates.TemplateResponse(
            request, "auth/login.html",
            {"current_user": None, "flash": None, "form": {"email": email},
             "error": "Invalid email or password", "next": safe_next}, status_code=400)

    if await user_has_strong_factor(db, user.id):
        # Correct password but a 2nd factor is still required. Do NOT reset the
        # throttle here — password-verify is NOT full auth, and resetting would let
        # an attacker who has the password wipe the /login/mfa rate limit at will
        # (the exact hole B1 closes). Reset happens only on full-auth success.
        resp = templates.TemplateResponse(
            request, "auth/mfa_challenge.html",
            {"current_user": None, "error": None, "next": safe_next})
        set_mfa_pending_cookie(resp, user.id)
        return resp
    # No strong factor (pre-enrollment / migration) → password IS full auth here;
    # reset the throttle, then mint a session (interstitial traps the user). The
    # existing create_session + audit + set_session_cookie code follows unchanged.
    reset_login_throttle(user)
```

Add `POST /login/mfa`:

```python
@router.post("/login/mfa")
async def login_mfa_post(
    request: Request,
    code: str = Form(..., max_length=32),
    next: str | None = Form(default=None, max_length=2048),
    db: AsyncSession = Depends(get_db),
) -> Response:
    signed = request.cookies.get("rf_mfa_pending")
    user_id = load_mfa_pending(signed) if signed else None
    safe_next = _safe_next(next or request.query_params.get("next"))
    if user_id is None:
        return RedirectResponse("/login", status_code=303)
    user = await db.get(User, user_id)
    if user is None or not user.is_active or is_locked(user):
        return RedirectResponse("/login", status_code=303)

    code = code.strip()
    method: str | None = None
    totp = (await db.execute(select(UserTotp).where(
        UserTotp.user_id == user.id, UserTotp.confirmed_at.is_not(None)))).scalars().first()
    if totp and totp_service.verify_totp(decrypt_totp_secret(totp.secret_encrypted), code):
        method = "totp"
    # Only walk the recovery Argon2 loop when the input is recovery-code-shaped —
    # a wrong TOTP guess must NOT cost up to 10 Argon2 verifies (CPU-DoS amplifier).
    if method is None and re.fullmatch(r"[0-9a-f]{5}-[0-9a-f]{5}", code):
        for rc in (await db.execute(select(RecoveryCode).where(
                RecoveryCode.user_id == user.id, RecoveryCode.used_at.is_(None)))).scalars().all():
            if verify_recovery_code(code, rc.code_hash):
                rc.used_at = now_utc()
                method = "recovery"
                await AuditWriter(db).log(
                    organization_id=user.organization_id, entity_type="user", entity_id=user.id,
                    action="user.recovery_code_used", changes={}, user_id=user.id,
                    ip_address=client_ip(request))
                break

    if method is None:
        register_failed_login(user)                       # counts toward lockout (B1)
        if is_locked(user):                               # this miss just tripped the lock → audit
            await AuditWriter(db).log(
                organization_id=user.organization_id, entity_type="user", entity_id=user.id,
                action="user.login_locked_out", changes={}, user_id=user.id,
                ip_address=client_ip(request))
        return templates.TemplateResponse(
            request, "auth/mfa_challenge.html",
            {"current_user": None, "error": "Invalid code", "next": safe_next}, status_code=400)

    reset_login_throttle(user)
    sess = await create_session(db, user.id, ip=client_ip(request))
    user.last_login_at = datetime.now(UTC)
    await AuditWriter(db).log(
        organization_id=user.organization_id, entity_type="session", entity_id=sess.id,
        action="user.login_mfa", changes={"method": method}, user_id=user.id,
        ip_address=client_ip(request))
    resp = RedirectResponse(safe_next, status_code=303)
    set_session_cookie(resp, sess.id)
    clear_mfa_pending_cookie(resp)
    return resp
```

(Also add `import re` and `from idraa.models._types import now_utc` and `from sqlalchemy import select` to the top of `routes/auth.py` if not already present.)
```

Add the passkey login endpoints:

```python
def _json_err(msg: str) -> Response:
    return Response(content=json.dumps({"error": msg}), status_code=400,
                    media_type="application/json")


@router.post("/login/passkey/options")
async def login_passkey_options(request: Request) -> Response:
    options_json, challenge = webauthn_service.authentication_options()
    resp = Response(content=options_json, media_type="application/json")
    set_webauthn_challenge_cookie(resp, challenge)
    return resp


@router.post("/login/passkey/verify")
async def login_passkey_verify(
    request: Request, payload: dict[str, Any] = Body(...), db: AsyncSession = Depends(get_db)
) -> Response:
    signed = request.cookies.get("rf_webauthn_challenge")
    challenge = load_webauthn_challenge(signed) if signed else None
    if challenge is None:
        return _json_err("challenge expired")
    credential = payload.get("credential")
    if not isinstance(credential, dict):
        return _json_err("malformed credential")
    raw_id = webauthn_service.parse_raw_id(credential)
    cred = (await db.execute(select(WebAuthnCredential).where(
        WebAuthnCredential.credential_id == raw_id))).scalars().first()
    if cred is None:
        return _json_err("unknown credential")
    try:
        new_count = webauthn_service.verify_authentication(
            credential, challenge, cred.public_key, cred.sign_count)
    except Exception as exc:  # noqa: BLE001 — any bad/tampered assertion → 400, not 500
        return _json_err(f"verification failed: {type(exc).__name__}")
    if not webauthn_service.sign_count_ok(cred.sign_count, new_count):
        return _json_err("counter")
    cred.sign_count = new_count
    cred.last_used_at = now_utc()
    user = await db.get(User, cred.user_id)
    if user is None or not user.is_active:
        return _json_err("inactive")
    sess = await create_session(db, user.id, ip=client_ip(request))
    user.last_login_at = datetime.now(UTC)
    await AuditWriter(db).log(
        organization_id=user.organization_id, entity_type="session", entity_id=sess.id,
        action="user.login_passkey", changes={}, user_id=user.id, ip_address=client_ip(request))
    resp = Response(content='{"next":"/"}', media_type="application/json")
    set_session_cookie(resp, sess.id)
    clear_webauthn_challenge_cookie(resp)
    return resp
```

Module-top imports for `routes/auth.py` (consolidated, no in-handler imports): `import json`, `import re`, `from typing import Any`, `from fastapi import Body`, `from sqlalchemy import select`, `from idraa.config import get_settings`, `from idraa.models._types import now_utc`, `from idraa.models.mfa import RecoveryCode, UserTotp, WebAuthnCredential`, `from idraa.services import totp as totp_service, webauthn_service`, `from idraa.services.mfa_crypto import decrypt_totp_secret, verify_recovery_code`, `from idraa.services.mfa_enrollment import user_has_strong_factor`, plus the `auth` helpers listed in Step 3c (`is_locked`, `register_failed_login`, `reset_login_throttle`, `load_mfa_pending`, `set_/clear_mfa_pending_cookie`, `load_webauthn_challenge`, `set_/clear_webauthn_challenge_cookie`). `AuditWriter`, `create_session`, `set_session_cookie`, `client_ip`, `verify_user_password`, `load_user_by_email` are already imported.

- [ ] **Step 4: Create `src/idraa/templates/auth/mfa_challenge.html`.**

```html
{% extends "base.html" %}
{% block title %}Two-step verification{% endblock %}
{% block container_class %}max-w-md mx-auto{% endblock %}
{% block content %}
<div class="pt-8 space-y-4">
  <h1 class="text-display text-ink-1">Enter your code</h1>
  {% if error %}<div class="alert alert-error">{{ error }}</div>{% endif %}
  <p class="text-sm text-ink-2">Enter the 6-digit code from your authenticator, or a recovery code.</p>
  <form method="post" action="/login/mfa" class="space-y-4">
    {{ csrf_field() }}
    <input type="hidden" name="next" value="{{ next | default('/') }}">
    <input name="code" inputmode="text" autocomplete="one-time-code" maxlength="32"
           class="input input-bordered w-full" placeholder="123456" required autofocus>
    <button class="btn btn-primary btn-sm" type="submit">Verify</button>
  </form>
</div>
{% endblock %}
```

- [ ] **Step 5: Add the passkey button to `login.html`** (above the email/password form):

```html
  <button class="btn btn-outline btn-sm w-full mb-4" type="button"
          onclick="idraaWebAuthn.authenticate().catch(function(e){alert(e.message)})"
          x-show="!!window.PublicKeyCredential">Sign in with a passkey</button>
  <div class="divider text-xs text-ink-3">or</div>
```

- [ ] **Step 6: Rebuild CSS + run + commit.**

```bash
uv run python -m idraa.tasks.build_css
uv run pytest tests/integration/test_login_mfa_flow.py -q
git add src/idraa/routes/auth.py src/idraa/services/auth.py src/idraa/templates/auth/ src/idraa/static/css/tailwind.css tests/integration/test_login_mfa_flow.py
git commit -m "feat(auth): login state machine (passkey + password/TOTP/recovery) + minimal login throttle"
```

---

## Task 9: Enrollment interstitial (middleware)

**Files:**
- Create: `src/idraa/middleware/enrollment_guard.py` (`EnrollmentGuardMiddleware`)
- Modify: `src/idraa/app.py` (register it between `MaintenanceBadgeCountMiddleware` and `SessionMiddleware`)
- Modify: `tests/conftest.py` (default `AUTH_MFA_POLICY=optional` for the HTTP suite)
- Test: `tests/integration/test_enrollment_interstitial.py`

**Interfaces:**
- Produces: `EnrollmentGuardMiddleware` — a `BaseHTTPMiddleware` subclass. When `AUTH_MFA_POLICY=="required"` and `request.state.user` is set with `mfa_enrolled_at is None`, it returns a 303 to `/account/security` (or an `HX-Redirect` for HTMX requests) unless the path is on the allowlist (`/account/security*`, `/login`, `/logout`, `/setup`, `/healthz`, `/static/*`).

**Plan-gate ruling (architect):** this is a MIDDLEWARE, not a dependency, and NOT the `app.middleware("http")` decorator form. Rationale: auth is enforced per-route via `Depends(require_user)` across ~18 routers with no single chokepoint — a dependency would be default-*allow* (any new router silently escapes enforcement), whereas a path-allowlisted middleware is default-*deny*. There is NO DB hop: `SessionMiddleware` already pins the loaded `User` onto `request.state` (with `expire_on_commit=False`, so `mfa_enrolled_at` is readable), and the guard must run INNER to `SessionMiddleware` so that pin exists. Registering via `add_middleware` positioned right after `MaintenanceBadgeCountMiddleware` (added 2nd → inner to Session) guarantees that ordering deterministically.

- [ ] **Step 0: Default the HTTP test suite to `AUTH_MFA_POLICY=optional`** (plan-gate BLOCKER — otherwise the required-default guard 303-redirects EVERY authed test off its target page and the whole suite goes red). In `tests/conftest.py`, in the `client` fixture, immediately after `monkeypatch.setenv("DATABASE_URL", db_url)` add:

```python
    monkeypatch.setenv("AUTH_MFA_POLICY", "optional")  # interstitial off by default in tests
```

The interstitial tests below opt back into `required` per-test (the guard reads `get_settings()` per request, so a `setenv` + `config.reset_for_tests()` in the test body takes effect on the next request).

- [ ] **Step 1: Write the test.**

```python
# tests/integration/test_enrollment_interstitial.py
from __future__ import annotations

import idraa.config as config
from httpx import AsyncClient


def _require_mfa(monkeypatch) -> None:
    monkeypatch.setenv("AUTH_MFA_POLICY", "required")
    config.reset_for_tests()


async def test_unenrolled_required_user_redirected_on_dashboard(
    admin_client: AsyncClient, monkeypatch
) -> None:
    _require_mfa(monkeypatch)  # authed fixtures create users with mfa_enrolled_at = None
    r = await admin_client.get("/", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/account/security"


async def test_unenrolled_required_user_redirected_on_non_dashboard_route(
    admin_client: AsyncClient, monkeypatch
) -> None:
    # A non-allowlisted authed route (not just the dashboard) must also be trapped.
    _require_mfa(monkeypatch)
    r = await admin_client.get("/scenarios", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/account/security"


async def test_security_page_reachable_while_unenrolled(
    admin_client: AsyncClient, monkeypatch
) -> None:
    _require_mfa(monkeypatch)
    r = await admin_client.get("/account/security", follow_redirects=False)
    assert r.status_code == 200


async def test_logout_reachable_while_unenrolled(admin_client: AsyncClient, monkeypatch) -> None:
    from tests.conftest import csrf_post
    _require_mfa(monkeypatch)
    r = await csrf_post(admin_client, "/logout", {}, bootstrap_url="/account/security",
                        follow_redirects=False)
    assert r.status_code == 303


def test_enrollment_guard_runs_inner_to_session() -> None:
    # Ordering is the security-critical invariant: the guard MUST run after
    # SessionMiddleware so request.state.user is populated. Higher index in
    # user_middleware = added earlier = inner (runs later inbound).
    from idraa.app import create_app
    from idraa.middleware.enrollment_guard import EnrollmentGuardMiddleware
    from idraa.middleware.session import SessionMiddleware

    app = create_app()
    classes = [m.cls for m in app.user_middleware]
    assert classes.index(EnrollmentGuardMiddleware) > classes.index(SessionMiddleware)
```

- [ ] **Step 2: Run — expect fail** (module `enrollment_guard` doesn't exist; GET `/` returns 200).

Run: `uv run pytest tests/integration/test_enrollment_interstitial.py -q`

- [ ] **Step 3: Create `src/idraa/middleware/enrollment_guard.py`.**

```python
"""Blocking MFA-enrollment interstitial.

When AUTH_MFA_POLICY == "required" and the logged-in user has no strong factor
(mfa_enrolled_at is None), redirect every non-allowlisted request to
/account/security. Runs INNER to SessionMiddleware so request.state.user is
already populated; reads it with zero DB access (Session pinned the loaded
User, expire_on_commit=False).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

from idraa.config import get_settings

_ALLOWLIST = ("/account/security", "/login", "/logout", "/setup", "/healthz", "/static")


def _allowed(path: str) -> bool:
    # Segment-aware: "/static" or "/static/..." match, but "/staticfoo" does NOT
    # (don't regress the repo's anti-prefix-abuse convention).
    return any(path == p or path.startswith(p + "/") for p in _ALLOWLIST)


class EnrollmentGuardMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if get_settings().auth_mfa_policy != "required":
            return await call_next(request)
        user = getattr(request.state, "user", None)
        if (
            user is not None
            and getattr(user, "mfa_enrolled_at", None) is None
            and not _allowed(request.url.path)
        ):
            if request.headers.get("HX-Request") == "true":
                # Tell HTMX to redirect the whole page, not swap a fragment.
                return Response(status_code=204, headers={"HX-Redirect": "/account/security"})
            return RedirectResponse("/account/security", status_code=303)
        return await call_next(request)
```

- [ ] **Step 4: Register it INNER to `SessionMiddleware`.** In `src/idraa/app.py`, add the import (`from idraa.middleware.enrollment_guard import EnrollmentGuardMiddleware`) and insert the add-line BETWEEN `MaintenanceBadgeCountMiddleware` and `SessionMiddleware` (added 2nd → inner to Session, so Session runs first inbound and populates `request.state.user`):

```python
    app.add_middleware(MaintenanceBadgeCountMiddleware)
    app.add_middleware(EnrollmentGuardMiddleware)   # <-- new; inner to Session
    app.add_middleware(SessionMiddleware)
```

- [ ] **Step 5: Run — expect pass, then commit.**

```bash
uv run pytest tests/integration/test_enrollment_interstitial.py -q
# Full auth regression: existing login/session/users tests must stay green (they
# run under AUTH_MFA_POLICY=optional from the conftest default, so the guard no-ops).
uv run pytest tests/integration/test_login_flow.py tests/integration/test_users_admin.py tests/middleware -q
git add src/idraa/middleware/enrollment_guard.py src/idraa/app.py tests/conftest.py tests/integration/test_enrollment_interstitial.py
git commit -m "feat(auth): blocking enrollment interstitial middleware (AUTH_MFA_POLICY=required)"
```

---

## Task 10: E2E — virtual authenticator passkey register + login

**Files:**
- Create: `tests/e2e/test_passkey_e2e.py`
- Modify: `tests/e2e/conftest.py` (real login helper, if needed — currently stubbed)
- Test: the file itself (`-m e2e`)

**Interfaces:**
- Consumes: `live_server_url` fixture (launches uvicorn subprocess); Playwright async API; CDP `WebAuthn.addVirtualAuthenticator`.

**Note:** This repo has NO existing virtual-authenticator or real-login e2e (the `seed_admin_login_e2e` fixture is a `pytest.skip` stub). This task is greenfield: it uses a raw CDP session for the virtual authenticator and bootstraps the account through the live app itself (setup → enroll → login). It runs only under `-m e2e`, outside the fast gate — per the "run full e2e on auth/JS changes" convention, it MUST be run explicitly before shipping. **Critical env requirement:** WebAuthn's origin↔RP-ID check means the server's origin must match `WEBAUTHN_RP_ID`. The default RP-ID (`idraa.fly.dev`) won't match a local server, so this task launches a DEDICATED server bound to `localhost` with `WEBAUTHN_RP_ID=localhost`, `WEBAUTHN_ORIGINS=http://localhost:<port>`, and `AUTH_MFA_POLICY=optional` (so the interstitial doesn't complicate the passkey-only flow), and the browser navigates to `http://localhost:<port>`.

- [ ] **Step 1: Add the passkey e2e server fixture** to `tests/e2e/conftest.py` (schema-migrated, localhost-bound, WebAuthn env set):

```python
@pytest.fixture(scope="module")
def passkey_server_url() -> Iterator[str]:
    import os, socket, subprocess, sys, time
    import httpx
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    url = f"http://localhost:{port}"
    env = {
        **os.environ,
        "ENVIRONMENT": "dev",
        "SESSION_SECRET": "e2e-not-a-real-secret-set-me-in-your-env",  # placeholder, not a credential
        "DATABASE_URL": f"sqlite+aiosqlite:///./e2e-passkey-{port}.db",
        "WEBAUTHN_RP_ID": "localhost",
        "WEBAUTHN_ORIGINS": url,
        "AUTH_MFA_POLICY": "optional",
    }
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], env=env, check=True)
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "idraa.app:app",
         "--host", "localhost", "--port", str(port), "--log-level", "warning"], env=env)
    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            if httpx.get(f"{url}/healthz", timeout=0.5).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.2)
    else:
        proc.terminate(); raise RuntimeError("passkey e2e server did not come up")
    try:
        yield url
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
```

- [ ] **Step 2: Write the e2e test.**

```python
# tests/e2e/test_passkey_e2e.py
from __future__ import annotations

import re

import httpx
import pytest
from playwright.async_api import async_playwright


async def _csrf_and_post(base: str, path: str, data: dict[str, str]) -> httpx.Response:
    """Bootstrap CSRF + POST via httpx against the live server (setup/seed only)."""
    async with httpx.AsyncClient(base_url=base, follow_redirects=False) as c:
        g = await c.get("/setup")
        token = c.cookies.get("csrf_token")
        return await c.post(path, data={**data, "_csrf": token})


@pytest.mark.e2e
async def test_passkey_register_then_usernameless_login(passkey_server_url: str) -> None:
    base = passkey_server_url
    # Bootstrap the first admin via the live /setup so we have a logged-in cookie jar.
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(base_url=base)
        page = await context.new_page()

        # Enable a virtual authenticator (CTAP2, internal, UV=true) via raw CDP.
        cdp = await context.new_cdp_session(page)
        await cdp.send("WebAuthn.enable")
        result = await cdp.send("WebAuthn.addVirtualAuthenticator", {
            "options": {
                "protocol": "ctap2", "transport": "internal",
                "hasResidentKey": True, "hasUserVerification": True,
                "isUserVerified": True, "automaticPresenceSimulation": True,
            }
        })
        assert result.get("authenticatorId")

        # Create the first admin through the setup form (logs the browser in).
        await page.goto("/setup")
        await page.fill('input[name="org_name"]', "E2E Org")
        # industry/size are selects — pick the first real option.
        await page.select_option('select[name="industry_type"]', index=1)
        await page.select_option('select[name="organization_size"]', index=1)
        await page.fill('input[name="email"]', "e2e@example.test")
        await page.fill('input[name="full_name"]', "E2E Admin")
        await page.fill('input[name="password"]', "pw-12345678")
        await page.click('button[type="submit"]')
        await page.wait_for_url(re.compile(r".*/(account/security|)$"))

        # Register a passkey (interstitial sends us to /account/security).
        await page.goto("/account/security")
        page.on("dialog", lambda d: d.accept("My Passkey"))  # prompt() for nickname
        await page.click("text=Add a passkey")
        await page.wait_for_selector("text=Passkey", timeout=10_000)

        # Also enroll TOTP + recovery so mfa_enrolled_at stamps, then log out.
        # (Simplest: just confirm a passkey is enough of a strong factor; recovery
        #  codes still required to clear the interstitial — generate them.)
        await page.click("text=Regenerate recovery codes")
        # Log out.
        await page.goto("/account/security")
        await page.click('form[action="/logout"] button')
        await page.wait_for_url(re.compile(r".*/login$"))

        # Usernameless passkey sign-in.
        await page.click("text=Sign in with a passkey")
        await page.wait_for_url(re.compile(rf"{re.escape(base)}/?$"), timeout=10_000)
        assert "/login" not in page.url

        await browser.close()
```

- [ ] **Step 3: Run the e2e test explicitly.**

Run: `uv run pytest -m e2e tests/e2e/test_passkey_e2e.py -q`
Expected: PASS. If the virtual-authenticator credential doesn't persist across the logout/login (Chromium keeps it on the authenticator, which lives on the CDP session/context — keep the same `context` across the whole flow, as written), debug with `WebAuthn.getCredentials`. The test also navigates to `passkey_server_url` (localhost), which matches `WEBAUTHN_RP_ID=localhost`.

- [ ] **Step 4: Commit.**

```bash
git add tests/e2e/test_passkey_e2e.py tests/e2e/conftest.py
git commit -m "test(auth): e2e passkey register + usernameless login via CDP virtual authenticator"
```

---

## Final P1 verification (before PR-gate)

- [ ] Run the full fast gate: `uv run python scripts/run_local_gate.py` (ruff + format + mypy + css-check + fast pytest). Fix anything red.
- [ ] Run the passkey e2e explicitly (the gate skips e2e): `uv run pytest -m e2e tests/e2e/test_passkey_e2e.py -q`.
- [ ] Manually drive the app once (`uv run uvicorn idraa.app:app --reload`): setup → interstitial → enroll TOTP + passkey + recovery codes → logout → passkey login → password+TOTP login. Confirm audit rows via `/users` or a DB peek.
- [ ] Confirm `AUTH_MFA_POLICY=optional` disables the interstitial (env override) and `WEBAUTHN_*` prod validator fires with a bogus RP-ID in `ENVIRONMENT=prod`.
- [ ] Dispatch the 3-reviewer PR-gate (security-auditor + architect + code-quality/spec-adherence); iterate to 0/0.

---

## Self-review notes (author)

- **Spec coverage:** §Factor semantics → Tasks 4/5/8; §Data model → Task 2; §Config → Task 1; §Secret handling → Task 3; §Login state machine → Task 8; §Enrollment interstitial → Task 9; §Browser JS/CSP → Task 7; §Testing incl. virtual-authenticator → Tasks throughout + 10. **Deferred to P2/P3 (correctly out of this plan):** step-up / `reauthenticated_at` (P2), admin reset + CLI + session-revocation (P2), the FULL throttle/lockout idraa#81 — management UI, admin unlock, per-IP throttle (P3; a MINIMAL per-account lockout slice ships in P1 per the plan-gate addendum, and it emits `login_locked_out`), revoke-on-deactivation idraa#80 L13 (P2/P3), HSTS idraa#82 (P3). The audit-action set here is the P1 subset plus `login_locked_out`; P2/P3 add `mfa_admin_reset`, `sessions_revoked`, `step_up`.
- **Known implementation checkpoints for the implementer** (not placeholders — verify-at-build): (a) exact `py_webauthn` symbol names against the installed version (Task 5 Step 3 note); (b) the enrollment guard is now definitively a middleware inner to `SessionMiddleware` (Task 9), with an ordering-assertion test — no ambiguity remains.
- **Security note carried to the PR-gate:** `rf_mfa_pending` / `rf_webauthn_challenge` are stateless TTL-bounded cookies (replayable within their window; synced passkeys report `sign_count=0`, so counter-replay protection is null for those). Accepted for P1 (replay requires capturing the TLS-protected request); a server-side single-use nonce is the documented post-P1 hardening.

## Plan-gate applied (2026-07-22)

3-reviewer plan-gate (security-auditor + architect + code-quality). Applied to convergence:

- **B1 [BLOCKER] rate-limit-free 2nd factor** → pulled a minimal idraa#81 throttle into P1: `failed_login_count`/`locked_until` on `users` (Task 2), `AUTH_MAX_FAILED_LOGINS`/`AUTH_LOCKOUT_SECONDS` (Task 1), lock + increment/reset on failed password AND `/login/mfa` (Task 8), plus a recovery-code-shape short-circuit so a wrong TOTP can't cost 10 Argon2 verifies. Tests added.
- **[BLOCKER] interstitial** → rewritten as `EnrollmentGuardMiddleware` (Task 9), inner to `SessionMiddleware`, segment-aware allowlist, `HX-Redirect` for HTMX; false ordering rationale + dependency hedge deleted; ordering-assertion + non-dashboard tests added. Design doc corrected.
- **[BLOCKER] suite-wide test breakage** → `conftest.py` defaults `AUTH_MFA_POLICY=optional`; interstitial tests opt into `required` (Task 9 Step 0).
- **[BLOCKER] `dict` `Body` mypy** → `dict[str, Any]` in Tasks 7/8.
- **[IMPORTANT] single-domain WEBAUTHN default** → `WEBAUTHN_ORIGINS="https://idraa.fly.dev"` (Task 1); design notes the one-RP-ID-per-domain limitation + Related Origin Requests follow-up. **Owner input needed: canonical passkey domain.**
- **[IMPORTANT] `_safe_next` backslash open-redirect** → hardened (Task 8 Step 3b).
- **[IMPORTANT] last-factor un-stamp downgrade (I4)** → `maybe_unstamp_enrolled` after passkey delete (Tasks 6/7).
- **[IMPORTANT] missing `recovery_code_used` audit** → emitted on burn (Task 8).
- **[IMPORTANT] two non-passing tests** → Task 2 `sign_count` (flush-time default) + Task 6 recovery-code parser (regex) fixed.
- **[IMPORTANT] duplicated cookie logic** → centralized `set_/clear_*_cookie` helpers in `services/auth.py` (Task 3), used everywhere.
- **NICE applied:** verify wrappers accept `dict|str` (drop triple-JSON); verify wrapped → 400 not 500; `IntegrityError` guard on duplicate credential; `entity_id=cred.id`; inline imports hoisted; dynamic-import test fixed; key-isolation crypto test strengthened; TOTP GET side-effect removed (signed `rf_totp_pending` cookie); `MFA_ENCRYPTION_KEY` rotation note.
- **NICE accepted/deferred:** stateless-nonce for `mfa_pending`/challenge (post-P1, documented); `MultiFernet` key-versioning (post-P1).

**Round 2 (re-gate) — the throttle pull-forward introduced findings, now fixed:**
- **[BLOCKER] `reset_login_throttle` defeated the 2FA lockout** (caught independently by architect + code-quality; the security-auditor's trace missed it) → reset removed from the password gate; it now fires ONLY on full-auth success (`login_mfa_post` success + the no-strong-factor branch of `login_post`). Added a regression test (`test_relogin_does_not_reset_mfa_throttle`) proving re-POSTing `/login` no longer resets the counter, and reshaped the lockout test to one `mfa_pending` window.
- **[IMPORTANT] lockout shipped unaudited** → `login_post` and `login_mfa_post` now emit `user.login_locked_out` when a failed attempt trips a new lock (generic 400 response unchanged).
- **[IMPORTANT] #81 P1/P3 doc contradiction** → reconciled: design Scope-budget P3 line + plan self-review now both say "minimal per-account lockout slice in P1; full idraa#81 in P3."

## Scope budget — addendum (plan-gate)

B1 pulled a **minimal login-throttle slice** from P3 into P1 (the design's own `failed_login_count`/`locked_until` columns + lock-on-failed-attempt). This adds ~1 column-pair, 2 config keys, ~25 LOC of login logic, and 2 tests to P1 — folded into existing Tasks 1/2/8, not a new task. P1 task count stays 10; the full idraa#81 (management UI, admin unlock, per-IP throttle) remains P3. No other scope change.
