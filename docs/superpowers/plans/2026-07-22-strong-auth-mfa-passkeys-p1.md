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
- Produces: `Settings.webauthn_rp_id: str`, `Settings.webauthn_rp_name: str`, `Settings.webauthn_origins: str`, `Settings.webauthn_origin_list -> list[str]` (property), `Settings.auth_mfa_policy: Literal["required","optional"]`, `Settings.totp_issuer: str`, `Settings.mfa_encryption_key: str | None`.

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
    assert s.webauthn_origin_list == ["https://idraa.fly.dev", "https://idraa.app"]
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
    webauthn_origins: str = "https://idraa.fly.dev,https://idraa.app"
    auth_mfa_policy: Literal["required", "optional"] = "required"
    totp_issuer: str = "Idraa"
    mfa_encryption_key: str | None = None

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
    assert cred.sign_count == 0
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

- [ ] **Step 4: Add `mfa_enrolled_at` to `User`.** In `src/idraa/models/user.py`, add after `last_login_at`:

```python
    mfa_enrolled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
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
Open the generated file: confirm it `create_table`s `webauthn_credentials`, `user_totp`, `recovery_codes` (+ the unique index on `credential_id`, the FK indexes) and `op.add_column("users", sa.Column("mfa_enrolled_at", sa.DateTime(timezone=True), nullable=True))`. `mfa_enrolled_at` is nullable so no `server_default` is needed. Convert the header to the modern style if the template emitted the legacy `Union` import.

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


def test_encryption_key_override_changes_ciphertext(monkeypatch) -> None:
    _reset(monkeypatch, ENVIRONMENT="test", SESSION_SECRET="s" * 32, MFA_ENCRYPTION_KEY="k" * 32)
    ct = mfa_crypto.encrypt_totp_secret("SECRETSECRET")
    # decrypt under the same key works
    assert mfa_crypto.decrypt_totp_secret(ct) == "SECRETSECRET"


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
```

(`BadData` is already imported in `auth.py`. `URLSafeTimedSerializer.loads(..., max_age=)` raises `SignatureExpired`, a `BadData` subclass, on expiry — caught by the same `except`.)

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
  - `verify_registration(credential_json: str, challenge_b64url: str) -> RegisteredCredential` (dataclass: `credential_id: bytes`, `public_key: bytes`, `sign_count: int`, `aaguid: str`, `transports: str | None`).
  - `authentication_options() -> tuple[str, str]` → `(options_json, challenge_b64url)` (usernameless).
  - `verify_authentication(credential_json: str, challenge_b64url: str, public_key: bytes, current_sign_count: int) -> int` → `new_sign_count`.
  - `sign_count_ok(stored: int, new: int) -> bool` (pure).
  - `parse_raw_id(credential_json: str) -> bytes`.

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


def verify_registration(credential_json: str, challenge_b64url: str) -> RegisteredCredential:
    s = get_settings()
    v = verify_registration_response(
        credential=credential_json,
        expected_challenge=base64url_to_bytes(challenge_b64url),
        expected_rp_id=s.webauthn_rp_id,
        expected_origin=s.webauthn_origin_list,
        require_user_verification=True,
    )
    transports = None
    parsed = json.loads(credential_json)
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
    credential_json: str,
    challenge_b64url: str,
    public_key: bytes,
    current_sign_count: int,
) -> int:
    s = get_settings()
    v = verify_authentication_response(
        credential=credential_json,
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


def parse_raw_id(credential_json: str) -> bytes:
    return base64url_to_bytes(json.loads(credential_json)["rawId"])
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

import pyotp
from httpx import AsyncClient
from sqlalchemy import select

from idraa.models.mfa import RecoveryCode, UserTotp
from idraa.services.mfa_crypto import decrypt_totp_secret


async def test_totp_enroll_confirm_and_recovery_stamps_enrolled(
    authed_admin: tuple[AsyncClient, object], db_session
) -> None:
    from tests.conftest import csrf_post

    client, _org = authed_admin

    # Begin TOTP enrollment — GET returns the QR + provisions an unconfirmed secret.
    r = await client.get("/account/security/totp/enroll")
    assert r.status_code == 200
    assert "<svg" in r.text

    row = (await db_session.execute(select(UserTotp))).scalars().first()
    assert row is not None and row.confirmed_at is None
    secret = decrypt_totp_secret(row.secret_encrypted)

    # Confirm with a live code.
    code = pyotp.TOTP(secret).now()
    r2 = await csrf_post(
        client, "/account/security/totp/enroll", {"code": code},
        bootstrap_url="/account/security", follow_redirects=False,
    )
    assert r2.status_code == 303

    await db_session.commit()
    confirmed = (await db_session.execute(select(UserTotp))).scalars().first()
    assert confirmed is not None and confirmed.confirmed_at is not None

    # Generate recovery codes → this completes enrollment.
    r3 = await csrf_post(
        client, "/account/security/recovery-codes/generate", {},
        bootstrap_url="/account/security",
    )
    assert r3.status_code == 200
    codes_visible = [ln for ln in r3.text.split() if "-" in ln and len(ln) == 11]
    assert codes_visible, "recovery codes should be shown once"

    await db_session.commit()
    assert (await db_session.execute(select(RecoveryCode))).scalars().first() is not None

    # mfa_enrolled_at is now stamped → interstitial clears (tested in Task 9).
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
from idraa.routes.deps import client_ip, current_user, get_db, require_user
from idraa.services import totp as totp_service
from idraa.services.audit import AuditWriter
from idraa.services.mfa_crypto import (
    decrypt_totp_secret,
    encrypt_totp_secret,
    generate_recovery_codes,
    hash_recovery_code,
)
from idraa.services.mfa_enrollment import maybe_stamp_enrolled
from idraa.config import get_settings
from idraa.models._types import now_utc

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
) -> HTMLResponse:
    # Provision (or re-provision) an unconfirmed secret and show the QR.
    existing = (
        await db.execute(select(UserTotp).where(UserTotp.user_id == user.id))
    ).scalars().first()
    secret = totp_service.provision_secret()
    if existing is None:
        db.add(UserTotp(user_id=user.id, secret_encrypted=encrypt_totp_secret(secret)))
    elif existing.confirmed_at is None:
        existing.secret_encrypted = encrypt_totp_secret(secret)
    else:
        # Already confirmed — don't overwrite; render the page instead.
        return templates.TemplateResponse(
            request, "account/_totp.html", {"already": True, "qr_svg": "", "current_user": user}
        )
    uri = totp_service.totp_uri(secret, user.email, get_settings().totp_issuer)
    return templates.TemplateResponse(
        request,
        "account/_totp.html",
        {"already": False, "qr_svg": totp_service.totp_qr_svg(uri), "secret": secret,
         "current_user": user},
    )


@router.post("/account/security/totp/enroll")
async def totp_enroll_post(
    request: Request,
    code: str = Form(..., max_length=10),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_user),
) -> Response:
    row = (
        await db.execute(select(UserTotp).where(UserTotp.user_id == user.id))
    ).scalars().first()
    if row is None:
        return RedirectResponse("/account/security/totp/enroll", status_code=303)
    secret = decrypt_totp_secret(row.secret_encrypted)
    if not totp_service.verify_totp(secret, code):
        ctx = await _security_context(db, user)
        ctx["error"] = "That code didn't match. Try again."
        return templates.TemplateResponse(request, "account/security.html", ctx, status_code=400)
    row.confirmed_at = now_utc()
    await AuditWriter(db).log(
        organization_id=user.organization_id, entity_type="user", entity_id=user.id,
        action="user.mfa_totp_enroll", changes={}, user_id=user.id, ip_address=client_ip(request),
    )
    await maybe_stamp_enrolled(db, user)
    return RedirectResponse("/account/security", status_code=303)


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

import json

from httpx import AsyncClient
from sqlalchemy import select

from idraa.models.mfa import WebAuthnCredential


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
    me = (await db_session.execute(select(__import__("idraa.models.user", fromlist=["User"]).User))).scalars().first()
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

- [ ] **Step 4: Add passkey endpoints** to `src/idraa/routes/mfa.py`. Add imports (`uuid`, `Response` already present; `from fastapi import Body`; `from idraa.services import webauthn_service`; `from idraa.services.auth import sign_webauthn_challenge, load_webauthn_challenge`) and:

```python
_CHALLENGE_COOKIE = "rf_webauthn_challenge"


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
    resp.set_cookie(
        _CHALLENGE_COOKIE, sign_webauthn_challenge(challenge), max_age=300, httponly=True,
        samesite="lax", secure=(get_settings().environment == "prod"), path="/",
    )
    return resp


@router.post("/account/security/passkey/verify")
async def passkey_register_verify(
    request: Request,
    payload: dict = Body(...),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_user),
) -> Response:
    signed = request.cookies.get(_CHALLENGE_COOKIE)
    challenge = load_webauthn_challenge(signed) if signed else None
    if challenge is None:
        return Response(content='{"error":"challenge expired"}', status_code=400,
                        media_type="application/json")
    import json as _json
    reg = webauthn_service.verify_registration(_json.dumps(payload["credential"]), challenge)
    nickname = (payload.get("nickname") or "Passkey")[:64]
    db.add(WebAuthnCredential(
        user_id=user.id, credential_id=reg.credential_id, public_key=reg.public_key,
        sign_count=reg.sign_count, aaguid=reg.aaguid, transports=reg.transports, nickname=nickname,
    ))
    await AuditWriter(db).log(
        organization_id=user.organization_id, entity_type="webauthn_credential",
        entity_id=user.id, action="webauthn_credential.create", changes={"nickname": nickname},
        user_id=user.id, ip_address=client_ip(request),
    )
    await maybe_stamp_enrolled(db, user)
    resp = Response(content='{"ok":true}', media_type="application/json")
    resp.delete_cookie(_CHALLENGE_COOKIE, path="/")
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
        await db.delete(cred)
        await AuditWriter(db).log(
            organization_id=user.organization_id, entity_type="webauthn_credential",
            entity_id=user.id, action="webauthn_credential.delete", changes={}, user_id=user.id,
            ip_address=client_ip(request),
        )
    return RedirectResponse("/account/security", status_code=303)
```

Add `import uuid` at the top of the module. (Verify `verify_registration` accepts a JSON string — it does; we re-serialize the parsed `credential` sub-object.)

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
```

- [ ] **Step 2: Run — expect fail.** `uv run pytest tests/integration/test_login_mfa_flow.py -q`

- [ ] **Step 3: Rework `POST /login` in `src/idraa/routes/auth.py`.** After the successful `verify_user_password` branch, replace the direct session mint with:

```python
    from idraa.services.mfa_enrollment import user_has_strong_factor
    from idraa.services.auth import sign_mfa_pending

    if await user_has_strong_factor(db, user.id):
        # Hold in the pending-2FA state — NOT a session yet.
        resp = templates.TemplateResponse(
            request, "auth/mfa_challenge.html",
            {"current_user": None, "error": None, "next": safe_next},
        )
        resp.set_cookie(
            "rf_mfa_pending", sign_mfa_pending(user.id), max_age=300, httponly=True,
            samesite="lax", secure=(get_settings().environment == "prod"), path="/",
        )
        return resp
    # No strong factor yet (pre-enrollment) → mint a session; interstitial traps.
    # ... existing create_session + audit + set_session_cookie code unchanged ...
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
    from idraa.services.auth import load_mfa_pending
    from idraa.services import totp as totp_service
    from idraa.services.mfa_crypto import verify_recovery_code, decrypt_totp_secret
    from idraa.models.mfa import UserTotp, RecoveryCode
    from idraa.models._types import now_utc
    from sqlalchemy import select

    signed = request.cookies.get("rf_mfa_pending")
    user_id = load_mfa_pending(signed) if signed else None
    safe_next = _safe_next(next or request.query_params.get("next"))
    if user_id is None:
        return RedirectResponse("/login", status_code=303)
    user = await db.get(User, user_id)
    if user is None or not user.is_active:
        return RedirectResponse("/login", status_code=303)

    ok = False
    totp = (await db.execute(select(UserTotp).where(
        UserTotp.user_id == user.id, UserTotp.confirmed_at.is_not(None)))).scalars().first()
    if totp and totp_service.verify_totp(decrypt_totp_secret(totp.secret_encrypted), code):
        ok = True
    if not ok:
        for rc in (await db.execute(select(RecoveryCode).where(
                RecoveryCode.user_id == user.id, RecoveryCode.used_at.is_(None)))).scalars().all():
            if verify_recovery_code(code.strip(), rc.code_hash):
                rc.used_at = now_utc()
                ok = True
                break
    if not ok:
        resp = templates.TemplateResponse(
            request, "auth/mfa_challenge.html",
            {"current_user": None, "error": "Invalid code", "next": safe_next}, status_code=400)
        return resp

    sess = await create_session(db, user.id, ip=client_ip(request))
    user.last_login_at = datetime.now(UTC)
    await AuditWriter(db).log(
        organization_id=user.organization_id, entity_type="session", entity_id=sess.id,
        action="user.login_mfa", changes={}, user_id=user.id, ip_address=client_ip(request))
    resp = RedirectResponse(safe_next, status_code=303)
    set_session_cookie(resp, sess.id)
    resp.delete_cookie("rf_mfa_pending", path="/")
    return resp
```

Add the passkey login endpoints:

```python
@router.post("/login/passkey/options")
async def login_passkey_options(request: Request) -> Response:
    from idraa.services import webauthn_service
    from idraa.services.auth import sign_webauthn_challenge
    options_json, challenge = webauthn_service.authentication_options()
    resp = Response(content=options_json, media_type="application/json")
    resp.set_cookie("rf_webauthn_challenge", sign_webauthn_challenge(challenge), max_age=300,
                    httponly=True, samesite="lax",
                    secure=(get_settings().environment == "prod"), path="/")
    return resp


@router.post("/login/passkey/verify")
async def login_passkey_verify(
    request: Request, payload: dict = Body(...), db: AsyncSession = Depends(get_db)
) -> Response:
    import json as _json
    from sqlalchemy import select
    from idraa.services import webauthn_service
    from idraa.services.auth import load_webauthn_challenge
    from idraa.models.mfa import WebAuthnCredential
    from idraa.models._types import now_utc

    signed = request.cookies.get("rf_webauthn_challenge")
    challenge = load_webauthn_challenge(signed) if signed else None
    if challenge is None:
        return Response('{"error":"challenge expired"}', status_code=400,
                        media_type="application/json")
    cred_json = _json.dumps(payload["credential"])
    raw_id = webauthn_service.parse_raw_id(cred_json)
    cred = (await db.execute(select(WebAuthnCredential).where(
        WebAuthnCredential.credential_id == raw_id))).scalars().first()
    if cred is None:
        return Response('{"error":"unknown credential"}', status_code=400,
                        media_type="application/json")
    new_count = webauthn_service.verify_authentication(
        cred_json, challenge, cred.public_key, cred.sign_count)
    if not webauthn_service.sign_count_ok(cred.sign_count, new_count):
        return Response('{"error":"counter"}', status_code=400, media_type="application/json")
    cred.sign_count = new_count
    cred.last_used_at = now_utc()
    user = await db.get(User, cred.user_id)
    if user is None or not user.is_active:
        return Response('{"error":"inactive"}', status_code=400, media_type="application/json")
    sess = await create_session(db, user.id, ip=client_ip(request))
    user.last_login_at = datetime.now(UTC)
    await AuditWriter(db).log(
        organization_id=user.organization_id, entity_type="session", entity_id=sess.id,
        action="user.login_passkey", changes={}, user_id=user.id, ip_address=client_ip(request))
    resp = Response('{"next":"/"}', media_type="application/json")
    set_session_cookie(resp, sess.id)
    resp.delete_cookie("rf_webauthn_challenge", path="/")
    return resp
```

Add imports at the top of `routes/auth.py`: `from fastapi import Body`, `from idraa.config import get_settings`, `from idraa.services.audit import AuditWriter` (verify — some already imported). `set_session_cookie`/`create_session`/`client_ip` are already imported.

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
git add src/idraa/routes/auth.py src/idraa/templates/auth/ src/idraa/static/css/tailwind.css tests/integration/test_login_mfa_flow.py
git commit -m "feat(auth): login state machine — passkey + password/TOTP/recovery second factor"
```

---

## Task 9: Enrollment interstitial dependency

**Files:**
- Modify: `src/idraa/routes/deps.py` (`require_enrolled`)
- Modify: `src/idraa/app.py` (apply to authed routers or add a middleware)
- Test: `tests/integration/test_enrollment_interstitial.py`

**Interfaces:**
- Produces: `require_enrolled(request, user, db) -> User` FastAPI dependency — when `AUTH_MFA_POLICY=="required"` and `user.mfa_enrolled_at is None`, raises a redirect to `/account/security` (except on the enroll/logout/static/health allowlist).

- [ ] **Step 1: Write the test.**

```python
# tests/integration/test_enrollment_interstitial.py
from __future__ import annotations

from httpx import AsyncClient


async def test_unenrolled_required_user_is_redirected_to_security(
    admin_client: AsyncClient
) -> None:
    # authed fixtures create users with mfa_enrolled_at = None; policy defaults to required.
    r = await admin_client.get("/", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/account/security"


async def test_security_page_itself_is_reachable_while_unenrolled(
    admin_client: AsyncClient
) -> None:
    r = await admin_client.get("/account/security", follow_redirects=False)
    assert r.status_code == 200


async def test_logout_reachable_while_unenrolled(admin_client: AsyncClient) -> None:
    from tests.conftest import csrf_post
    r = await csrf_post(admin_client, "/logout", {}, bootstrap_url="/account/security",
                        follow_redirects=False)
    assert r.status_code == 303
```

- [ ] **Step 2: Run — expect fail** (GET `/` returns 200, not a redirect).

Run: `uv run pytest tests/integration/test_enrollment_interstitial.py -q`

- [ ] **Step 3: Implement as a middleware** in `src/idraa/app.py` (a middleware covers every router uniformly; place it just inside `SessionMiddleware` so `request.state.user` is populated). Add after the `MaintenanceBadgeCountMiddleware` add-line — remember LIFO, so to run AFTER Session it must be added BEFORE Session:

```python
    _ENROLL_ALLOWLIST = ("/account/security", "/logout", "/login", "/setup", "/healthz", "/static")

    async def enrollment_guard(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        settings = get_settings()
        if settings.auth_mfa_policy != "required":
            return await call_next(request)
        user = getattr(request.state, "user", None)
        path = request.url.path
        if (
            user is not None
            and getattr(user, "mfa_enrolled_at", None) is None
            and not any(path == p or path.startswith(p + "/") or path.startswith("/static")
                        for p in _ENROLL_ALLOWLIST)
        ):
            return RedirectResponse("/account/security", status_code=303)
        return await call_next(request)

    app.middleware("http")(enrollment_guard)
```

Because `app.middleware("http")` registrations run OUTERMOST-first relative to `add_middleware` ones, and `enrollment_guard` needs `request.state.user`, verify ordering in a test: `SessionMiddleware` must have run before this guard. If the decorator-registered guard ends up outside `SessionMiddleware`, instead implement `require_enrolled` as a router dependency and attach it via `dependencies=[Depends(require_enrolled)]` on each authed router's `include_router`. (The middleware form is preferred; fall back to the dependency form only if ordering fights you — the test above is the arbiter.)

- [ ] **Step 4: Run — expect pass, then commit.**

```bash
uv run pytest tests/integration/test_enrollment_interstitial.py -q
# Full auth regression: existing login/session/users tests must stay green.
uv run pytest tests/integration/test_login_flow.py tests/integration/test_users_admin.py tests/middleware -q
git add src/idraa/app.py src/idraa/routes/deps.py tests/integration/test_enrollment_interstitial.py
git commit -m "feat(auth): blocking enrollment interstitial when AUTH_MFA_POLICY=required"
```

---

## Task 10: E2E — virtual authenticator passkey register + login

**Files:**
- Create: `tests/e2e/test_passkey_e2e.py`
- Modify: `tests/e2e/conftest.py` (real login helper, if needed — currently stubbed)
- Test: the file itself (`-m e2e`)

**Interfaces:**
- Consumes: `live_server_url` fixture (launches uvicorn subprocess); Playwright async API; CDP `WebAuthn.addVirtualAuthenticator`.

**Note:** This repo has NO existing virtual-authenticator or real-login e2e (the `seed_admin_login_e2e` fixture is a `pytest.skip` stub). This task is greenfield: it uses a raw CDP session for the virtual authenticator and bootstraps the account through the live app itself (setup → enroll → login). It runs only under `-m e2e`, outside the fast gate — per the "run full e2e on auth/JS changes" convention, it MUST be run explicitly before shipping.

- [ ] **Step 1: Write the e2e test.**

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
async def test_passkey_register_then_usernameless_login(live_server_url: str) -> None:
    base = live_server_url
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

- [ ] **Step 2: Run the e2e test explicitly.**

Run: `uv run pytest -m e2e tests/e2e/test_passkey_e2e.py -q`
Expected: PASS. If the virtual-authenticator credential doesn't persist across the logout/login (Chromium keeps it on the authenticator, which lives on the CDP session/context — keep the same `context` across the whole flow, as written), debug with `WebAuthn.getCredentials`.

- [ ] **Step 3: Commit.**

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

- **Spec coverage:** §Factor semantics → Tasks 4/5/8; §Data model → Task 2; §Config → Task 1; §Secret handling → Task 3; §Login state machine → Task 8; §Enrollment interstitial → Task 9; §Browser JS/CSP → Task 7; §Testing incl. virtual-authenticator → Tasks throughout + 10. **Deferred to P2/P3 (correctly out of this plan):** step-up / `reauthenticated_at` (P2), admin reset + CLI + session-revocation (P2), throttle/lockout idraa#81 (P3), revoke-on-deactivation idraa#80 L13 (P2/P3), HSTS idraa#82 (P3). The audit-action set here is the P1 subset; P2/P3 add `mfa_admin_reset`, `sessions_revoked`, `login_locked_out`, `step_up`.
- **Known implementation checkpoints for the implementer** (not placeholders — verify-at-build): (a) exact `py_webauthn` symbol names against the installed version (Task 5 Step 3 note); (b) middleware ordering for the enrollment guard vs `SessionMiddleware` — the Task 9 test is the arbiter.
- **Security note carried to the PR-gate:** `rf_mfa_pending` is a stateless TTL-bounded cookie (replayable within 300 s); the security-auditor decides whether P1 needs a server-side nonce or can defer that to a follow-up.
