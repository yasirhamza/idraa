# Strong authentication — MFA + passkeys (WebAuthn/FIDO2)

**Status:** approved by owner (brainstorm 2026-07-22); pending plan-gate
**Owner decisions (brainstorm):**
1. Auth model = **passkey-primary, password+TOTP+recovery fallback** (passwordless-*capable*, not password-*removed*).
2. Enforcement = **required for all**, operator-configurable (`AUTH_MFA_POLICY`, default `required`), blocking enrollment interstitial.
3. Recovery = **one-time recovery codes (self-service) + audited admin reset + CLI backstop**.
4. Step-up = **broad**: own-credential changes + admin user-management + destructive deletes + bulk exports, 10-min freshness.
5. Fold in = **all three** sweep findings: idraa#81 (throttle/lockout), idraa#80 L13 (revoke-on-deactivation), idraa#82 (HSTS/headers).
6. UAT gate = **keep, make retirement-ready**; retire later via ops flip (`fly secrets unset UAT_BASIC_AUTH_PASSWORD`), no code change.
7. Data model = **separate per-factor tables**.
8. TOTP secret = **encrypted at rest** (not plaintext).

**Review ceremony (owner override, this feature):** 3-reviewer — **security-auditor + architect + code-quality** — at BOTH the plan-gate AND the final PR-gate, iterated to 0/0. The code-quality reviewer additionally carries a **spec-adherence** check. Methodology reviewer is deliberately dropped: MFA/passkeys touches no FAIR math, calibration, or derivations, so the persona the CLAUDE.md 4-reviewer default exists to force has no surface here. This override is recorded here and supersedes the milestone default for this feature only.

**Related:** idraa#81, idraa#80 (L13), idraa#82 (folded in); riskflow#565 (sweep master report); idraa#487 / riskflow#6 (`unsafe-eval` CSP — NOT touched by this feature; WebAuthn needs no eval). Depends on nothing; blocks eventual UAT-gate retirement.

---

## Why

Login today (`routes/auth.py`) is single-step: `verify_user_password` (Argon2, timing-safe) → `create_session` → signed cookie. There is no second factor, no login throttle, and `User` carries zero MFA state. A phished or reused password is full account access. This feature makes the primary credential a **phishing-resistant passkey**, keeps a hardened password path for unsupported browsers and recovery, and requires every user to hold a strong factor. It is a self-hostable, config-driven design — the WebAuthn RP-ID and origins are never hardcoded to the owner's domains.

This ELEVATES auth beyond phase-1's "session-based password auth; SSO deferred". Passkeys are not SSO; SSO/SAML/OIDC remains out of scope.

## Factor semantics

- **Passkey** — a WebAuthn credential registered with `userVerification: "required"` and **discoverable/resident** key (`residentKey: "required"`), enabling usernameless "tap to sign in". UV-required makes the passkey genuinely two-factor (possession of the authenticator + user verification via biometric/PIN); this is what licenses passkey-*alone* login.
- **Password** — stays on every account as bootstrap + a recovery path. Phase 1 is passwordless-capable, never password-removed; users cannot delete their password.
- **Second factor on the password path** — TOTP (if enrolled) **or** a one-time recovery code. A passkey-only user who loses the device therefore falls back to password + recovery code, or admin reset.
- **Enrollment requirement** — ≥1 strong factor (a passkey, OR TOTP) **plus** saved recovery codes. Password alone never satisfies the requirement once `AUTH_MFA_POLICY=required`.

## Data model

Separate, well-bounded tables keyed by `user_id` — matching the `AuthSession` precedent, which scopes via the user and carries no `organization_id` (auth tables inherit org through the user). All FKs `ondelete="CASCADE"` from `users`. New Alembic migration.

```
users  (new columns)
  mfa_enrolled_at     DateTime(tz)  nullable   -- null ⇒ trapped in enrollment interstitial when policy=required
  failed_login_count  int  default 0  not null  -- idraa#81
  locked_until        DateTime(tz)  nullable    -- idraa#81

webauthn_credential                (N per user)
  id                uuid  PK
  user_id           uuid  FK->users cascade, indexed
  credential_id     LargeBinary  unique, not null   -- raw credential id from the authenticator
  public_key        LargeBinary  not null           -- COSE public key
  sign_count        int  not null default 0
  transports        String|null                     -- csv, advisory
  aaguid            String|null                      -- authenticator model id, for future allowlisting
  nickname          String  not null                -- user-facing label ("YubiKey 5", "iPhone")
  created_at        DateTime(tz)  not null
  last_used_at      DateTime(tz)  nullable

user_totp                          (0..1 per user)
  user_id           uuid  PK/FK->users cascade
  secret_encrypted  String  not null               -- Fernet ciphertext (see "Secret handling")
  confirmed_at      DateTime(tz)  nullable          -- null = enrollment started but not verified

recovery_code                      (N per user)
  id                uuid  PK
  user_id           uuid  FK->users cascade, indexed
  code_hash         String  not null                -- Argon2 hash of the one-time code
  used_at           DateTime(tz)  nullable
  created_at        DateTime(tz)  not null
```

**Data-contract tests** (per the project-wide policy, `tests/contracts/`):
- Adapter iteration test: `list[webauthn_credential] → DTO list` preserves all N (N≥3).
- ORM⊆DTO field-sync tests for each new entity pair, with an explicit internal-only allowlist (`secret_encrypted`, `code_hash`, `public_key`).

## Configuration (`src/idraa/config.py::Settings`, all env-overridable)

```
WEBAUTHN_RP_ID                str          default "idraa.fly.dev"
WEBAUTHN_RP_NAME              str          default "Idraa"
WEBAUTHN_ORIGINS             csv->list[str] default "https://idraa.fly.dev"   (single registrable domain — see note)
AUTH_MFA_POLICY              required|optional  default "required"
AUTH_MAX_FAILED_LOGINS       int          default 5      (0 disables lockout)
AUTH_LOCKOUT_SECONDS         int          default 900
AUTH_STEP_UP_MAX_AGE_SECONDS int          default 600
TOTP_ISSUER                  str          default = app_name ("Idraa")
MFA_ENCRYPTION_KEY           str|null     default None → derive from SESSION_SECRET via HKDF
```

A prod-gated `model_validator` (mirroring `_check_secret_hardening`) enforces in `environment=="prod"`: `WEBAUTHN_RP_ID` is set and not a placeholder; every entry in `WEBAUTHN_ORIGINS` is an `https://` origin whose host equals or is a subdomain of `WEBAUTHN_RP_ID` (WebAuthn's RP-ID/origin relationship). `dev`/`test` accept defaults. This keeps the software self-hostable — a different deployment sets its own RP-ID/origins and nothing is baked in.

> **Single-RP-ID limitation (plan-gate 2026-07-22).** A WebAuthn credential is bound to ONE RP-ID, which must be a registrable-domain suffix of the origin. `idraa.fly.dev` and `idraa.app` are two distinct registrable domains (neither is a suffix of the other), so **one `WEBAUTHN_RP_ID` cannot serve passkeys on both** — the earlier owner assumption of "RP = idraa.fly.dev + idraa.app" is infeasible under a single static RP-ID, and the old two-origin default would have crash-failed the prod validator. P1 therefore ships a single-domain default (`idraa.fly.dev`). Serving passkeys on a second product domain later requires either its own deployment/RP-ID or WebAuthn **Related Origin Requests** (a `/.well-known/webauthn` file) — tracked as a post-P1 follow-up. **Owner decision needed:** which single domain is canonical for passkeys.

## Secret handling

- **TOTP secret** — symmetric-encrypted at rest with Fernet (`cryptography`). Key = `MFA_ENCRYPTION_KEY` if set, else derived from `SESSION_SECRET` via HKDF-SHA256 (distinct info string, so it is not the cookie-signing key). Decrypted only in-process at verify time. Rationale: TOTP verification needs the plaintext secret, so hashing is impossible; plaintext-in-DB is the alternative the security-auditor would reject.
- **Recovery codes** — high-entropy (e.g. 10 codes, `secrets.token_hex`), shown once at generation, stored as Argon2 hashes (reuses the existing `passlib` context). A single Argon2 pass is sufficient given the entropy.
- **Signed short-lived tokens** — each new signed-payload type gets its own itsdangerous salt per the convention in `services/auth.py`: `rf-mfa-pending` (second-factor step), `rf-webauthn-challenge` (ceremony challenge). Distinct salts prevent one token type being replayed as another.

## Login state machine

Entry points on the sign-in page:

1. **Passkey (daily driver, usernameless)** — "Sign in with a passkey" → `webauthn.js` calls `navigator.credentials.get()` with empty `allowCredentials` (relies on discoverable credentials) using a server-issued challenge → POST assertion → server verifies signature, origin, RP-ID, and **sign-count regression** (reject a non-increasing counter unless the authenticator always reports 0) → mint `AuthSession` with `reauthenticated_at = now`, update `last_used_at`. No password involved.

2. **Password path** — email+password → timing-safe `verify_user_password` + lockout check (see idraa#81):
   - Verified **and** user has a second factor (TOTP confirmed or recovery codes exist) → issue signed **`mfa_pending`** token (salt `rf-mfa-pending`, TTL 5 min, delivered as an httponly/secure-in-prod/samesite=lax cookie, carries `user_id`+purpose+issued-at; NOT a session) → prompt for TOTP or recovery code → on success mint `AuthSession`. A recovery code is burned on use and flags a re-enroll nudge.
   - Verified **and** user has NO strong factor yet (existing password-only accounts at first login after `required` turns on) → mint a normal `AuthSession`; the **enrollment interstitial** then traps the user until they enroll. No regression versus today's password-only login during the migration window.

The WebAuthn challenge for both registration and authentication is stored in a short-lived signed cookie (`rf-webauthn-challenge`, single ceremony, ~5 min), so anonymous users need no server-side row; verification consumes/clears the cookie.

> **Security-auditor note (flagged for plan-gate):** the `mfa_pending` token is TTL-bounded but stateless, so it is replayable within its 5-min window. If strict single-use is required, back it with a server-side nonce table. Proposed default: accept the bounded window for phase 1; auditor to rule.

New/changed code: `services/mfa.py` (new — ceremony orchestration, TOTP verify, recovery-code verify), `services/webauthn.py` (new — thin `py_webauthn` wrapper: options + verify), `routes/auth.py` (login becomes multi-step; add `/login/verify`, passkey ceremony endpoints), `services/auth.py` (new signed-token helpers + `revoke_user_sessions`).

## Enrollment interstitial

An HTTP **middleware** (`BaseHTTPMiddleware` subclass, registered via `add_middleware` positioned INNER to `SessionMiddleware` so `request.state.user` is populated) — when `AUTH_MFA_POLICY=="required"` and `current_user.mfa_enrolled_at is None`, any route except the enrollment endpoints (`/account/security*`), `/login`, `/logout`, `/setup`, `/static/*`, and `/healthz` returns a 303 (or `HX-Redirect` for HTMX) to `/account/security`. **Middleware, not a dependency** (revised at plan-gate, 2026-07-22, on the architect's ruling): auth is enforced per-route across ~18 routers with no single chokepoint, so a dependency would be a default-*allow* enumeration where any new router silently escapes enforcement; a middleware with a path allowlist is default-*deny*, the correct posture for a security boundary. There is no DB hop — `SessionMiddleware` already pins the loaded `User` (with `expire_on_commit=False`) onto `request.state`, so the guard reads `mfa_enrolled_at` with zero DB access. `mfa_enrolled_at` is stamped once the user has ≥1 confirmed strong factor AND recovery codes; and **cleared when the last strong factor is removed** (so the interstitial re-fires rather than silently downgrading to password-only).

## Step-up ("sudo mode") — broad

`AuthSession` gains `reauthenticated_at DateTime(tz)` (set = `created_at` at login). A dependency `require_recent_auth` checks `now - reauthenticated_at ≤ AUTH_STEP_UP_MAX_AGE_SECONDS` (default 600s); if stale it returns an HTMX step-up challenge (re-tap passkey / re-enter TOTP) that, on success, updates `reauthenticated_at` and lets the action proceed. One re-verify covers a 10-min working burst.

**Sensitive-action catalog** (owner-confirmed, broad tier):
- *Own credentials* — add/remove passkey, enroll/disable TOTP, regenerate recovery codes, change password.
- *Admin user-management* — create user, deactivate/reactivate user, change role, reset another user's factors.
- *Destructive/bulk* — delete scenario / analysis / run / library-override; bulk exports (the endpoints already funnel through `log_bulk_export`).

## Recovery

- **Recovery codes** (self-service, first line) — ~10 one-time codes at enrollment, shown once, Argon2-hashed. Usable as the password-path second factor and as the lost-passkey escape. Using one burns it; the UI nudges re-enrollment and shows remaining count.
- **Admin reset** (backstop) — an admin clears a target user's strong-auth (passkeys + TOTP + recovery codes, sets `mfa_enrolled_at=NULL`). This does NOT authenticate the admin as the user: it forces re-enrollment via the interstitial at the target's next login, **revokes the target's live sessions**, and writes an `mfa_admin_reset` audit row. Lives in `routes/users.py`, `require_role(ADMIN)` + `require_recent_auth`.
- **CLI backstop** (sole-admin-locked-out corner) — `python -m idraa auth reset-mfa <email>` performs the same reset from the host, for the case where the only admin loses their own factor. Consistent with the "Python task runner is authoritative" cross-platform rule.

## Folded sweep findings

- **idraa#81 — throttle/lockout.** `failed_login_count` / `locked_until` on `users`. Failed password verify AND failed TOTP/recovery attempts increment (TOTP is a 6-digit space — must be rate-limited); a success resets the counter. After `AUTH_MAX_FAILED_LOGINS` within the window, set `locked_until = now + AUTH_LOCKOUT_SECONDS`. The locked path returns the SAME generic "invalid email or password" 400 as bad-credentials and still runs the dummy Argon2 verify, preserving the existing anti-enumeration timing property. Audit `login_locked_out`.
- **idraa#80 L13 — revoke-on-deactivation.** The admin deactivate route in `routes/users.py` (and admin factor-reset) calls `revoke_user_sessions(db, user_id)` (deletes the target's `AuthSession` rows) and audits `sessions_revoked`. Closes the gap where a deactivated user keeps a live 14-day cookie (the `SessionMiddleware` `is_active` check already blocks new requests, but this makes revocation explicit + audited and covers the reset case).
- **idraa#82 — HSTS/headers.** `middleware/security_headers.py` adds `Strict-Transport-Security` (prod-gated, since dev/test are http). Audit and tighten the existing header set (`X-Frame-Options`, `X-Content-Type-Options`, `Referrer-Policy`, `Permissions-Policy`) as part of the same change.

## Browser JS + CSP

One vendored `src/idraa/static/js/webauthn.js` (~120 LOC, no build step, no CDN, no `eval`): base64url encode/decode, `navigator.credentials.create/get`, and `fetch` POST of ceremony results. It reads the CSRF token the same way existing forms do (via the `csrf_token()` mechanism / `CSRFMiddleware`-set state) and sends it in the header/field the middleware validates — the exact carrier is confirmed against `middleware/csrf.py` as an implementation task. `unsafe-eval` (idraa#487/riskflow#6) is untouched; the `data:` img-src grant is unaffected. Progressive enhancement: the passkey button is shown only when `window.PublicKeyCredential` exists; otherwise the password path is the visible default.

## Audit events

New actions via the existing `services/audit.py::AuditWriter`: `mfa_enroll_passkey`, `mfa_remove_passkey`, `mfa_enroll_totp`, `mfa_disable_totp`, `recovery_codes_generated`, `recovery_code_used`, `mfa_admin_reset`, `login_mfa`, `login_locked_out`, `step_up`, `sessions_revoked`, `password_changed`. Audit logging is a first-class table (architectural rule) — every credential-lifecycle and auth-decision event is recorded.

## Testing

- **Unit** — `py_webauthn` register/authenticate happy + error paths (bad origin, bad RP-ID, sign-count regression, UV-absent rejection); TOTP verify window/skew + replay-within-step; recovery-code hash + single-use burn; lockout counter increment/reset + locked-path generic response; step-up freshness boundary; RP-ID/origins prod validator; TOTP secret encrypt/decrypt round-trip + key derivation.
- **Integration** — full login state machine (passkey, password+TOTP, password+recovery, migration no-factor path); enrollment interstitial trap + clear; admin reset + session revocation + re-enroll; deactivation revokes sessions.
- **E2e (Playwright)** — CDP `WebAuthn.addVirtualAuthenticator` drives passkey register + usernameless login with **no hardware/biometric prompt** (critical given the remote-Mac-no-TCC-dialogs constraint). Like the chart e2e suite, these run OUTSIDE the fast local gate (`-m "not e2e"`) and MUST be run explicitly before shipping.

## Migration & rollout

1. Alembic migration adds the four tables + `users` columns.
2. **Soft-launch option** — deploy with `AUTH_MFA_POLICY=optional`, let users enroll voluntarily, then flip to `required`. Or deploy straight to `required` and let the interstitial drive enrollment at next login (no lockout — password still works to reach enrollment).
3. **UAT gate** — stays in place during rollout (belt + suspenders while the new flow is least battle-tested). Retiring it after prod validation is `fly secrets unset UAT_BASIC_AUTH_PASSWORD` — no code change, fully reversible. Documented in `docs/runbooks/uat-operations.md`.

## Out of scope (phase N+1, with slot-in sketches)

- **SSO / OIDC / SAML** — a new provider adapter that mints the same `AuthSession`; the session/step-up model is unchanged.
- **WebAuthn as a 2nd factor on the password path** — add passkey to the accepted password-path second factors (today it's TOTP/recovery only, since a passkey user just uses passkey-primary).
- **Passkey attestation / AAGUID allowlisting** — validate `aaguid` at registration against an operator allowlist (enterprise device restriction); the column exists for this.
- **Remember-this-device / adaptive auth** — a per-session trust flag feeding the step-up freshness decision.
- **Per-org MFA policy** — when multi-tenancy lands, `AUTH_MFA_POLICY` moves from a global Setting to an `organization` column.
- **Strict single-use `mfa_pending`** — server-side nonce table if the auditor wants it beyond the TTL bound.

---

## Scope budget

Numeric ceilings against which the implementation plan and PR(s) will be measured.

- **target_task_count:** ~16 implementer tasks for the full epic: config+validator; data model+migration+contract tests; secret handling (TOTP encryption, recovery hashing, signed tokens); `py_webauthn` service wrapper; passkey registration ceremony; passkey authentication + login integration; TOTP enrollment+verify; recovery codes; login state-machine restructure (`mfa_pending`); enrollment interstitial + enroll UI; step-up dependency + catalog wiring; admin reset + CLI + session revocation; idraa#81 throttle; idraa#80 L13 revoke-on-deactivate; idraa#82 HSTS/headers; `webauthn.js` + account-security UI + e2e.
- **RECOMMENDED SUB-SPLIT:** ~16 tasks is too large for one PR. Split into three milestone PRs, EACH with its own 3-reviewer plan-gate + PR-gate (per "every P-series deliverable is a milestone"):
  - **P1 — Core factors + login:** config, data model, secret handling, WebAuthn register/auth, TOTP, recovery codes, login state machine, enrollment interstitial, `webauthn.js`, e2e. (~9–10 tasks)
  - **P2 — Step-up + recovery ops:** step-up dependency + broad catalog wiring, admin reset, CLI backstop, session revocation. (~4 tasks)
  - **P3 — Folded sweep findings:** idraa#81 throttle/lockout — the FULL feature (management UI, admin unlock, per-IP throttle); a MINIMAL per-account lockout slice was pulled into P1 at plan-gate (see drift-log #12) — idraa#80 L13 revoke-on-deactivation (if not already pulled into P2), idraa#82 HSTS/headers. (~3 tasks)
- **target_loc_delta:** ~3000 LOC across the epic (test-heavy: WebAuthn/TOTP/lockout/step-up all need dense unit + integration coverage, plus virtual-authenticator e2e).
- **review_budget:** per owner override — 3-reviewer (**security-auditor + architect + code-quality**, code-quality also carrying spec-adherence) at EACH milestone's plan-gate AND PR-gate, iterated to 0/0. Per-task two-stage review during execution. Methodology reviewer dropped (no FAIR surface).
- **timeline_budget:** 3 heavy sessions, one per sub-split milestone (P1/P2/P3).

If plan-writing produces a task count materially over target for any milestone (>20%), sub-split further or append a `## Scope budget — addendum` with the surprises and explicit user re-approval.

## Scope drift log

Every scope decision/addition/cut relative to the originating prompt. The prompt explicitly left the model, second-factor, enrollment, step-up, recovery, and UAT-gate questions "open to settle (not pre-decided)", so most items below are *resolutions* of intentionally-open questions rather than unplanned creep.

1. **Auth model (prompt: "augment vs replace passwords", open).** Direction: ↔reframed → **passkey-primary, passwordless-capable but not password-removed**. Justification: user choice at brainstorm Q1 ("Passkey-primary, password+TOTP fallback").
2. **Second factor (prompt: "WebAuthn-only vs TOTP fallback", open).** Direction: ↔decided → TOTP **or** recovery code as the password-path second factor; passkey alone (UV-required) suffices. Justification: falls out of Q1 model choice.
3. **Enforcement.** Direction: +added → **required-for-all, operator-configurable** (`AUTH_MFA_POLICY`, default required) + blocking enrollment interstitial + no-regression migration. Justification: user choice at Q2.
4. **Recovery (prompt: "without weakening the factor", open).** Direction: +added → one-time codes + **audited admin reset that never yields the admin a usable credential** + CLI backstop. Justification: user choice at Q3.
5. **Step-up (prompt: open).** Direction: +added → **broad** step-up incl. destructive deletes **and** bulk exports, 10-min freshness. Justification: user choice at Q4 + the step-up-catalog follow-up.
6. **Auth-adjacent sweep findings.** Direction: +added → idraa#81 (throttle), idraa#80 L13 (revoke-on-deactivate), idraa#82 (HSTS) folded into this epic. Justification: user choice at the fold-in-scope question ("All three"); prompt suggested "consider folding in".
7. **UAT gate (prompt: "how MFA interacts with / retires it", open).** Direction: ↔decided → **keep during rollout, retirement-ready**; retire via `fly secrets unset` later, no code change. Justification: user choice at the UAT-gate question.
8. **Review ceremony.** Direction: ↔reframed → **3-reviewer (security + architect + code-quality)** overriding the CLAUDE.md 4-reviewer milestone default; methodology dropped (no FAIR surface), spec-compliance folded into code-quality. Justification: explicit owner instruction after the design was presented.
9. **Data model shape.** Direction: +decided → **separate per-factor tables** (not one polymorphic table). Justification: user choice at the data-model question.
10. **TOTP secret at rest.** Direction: +added → **encrypted at rest** (Fernet, key from `MFA_ENCRYPTION_KEY`/`SESSION_SECRET`-HKDF). Justification: resolved by Claude, owner did not object ("the security-auditor would flag plaintext anyway").
11. **Considered and deferred (no code):** passwordless-only (retire passwords) and password+passkey-as-2nd-factor were both weighed and rejected/deferred at Q1; passkey-as-password-path-2nd-factor sketched in Out-of-scope.
12. **Minimal login throttle pulled P3 → P1.** Direction: +added to P1. Justification: plan-gate security-auditor BLOCKER — the reworked login ships in P1, and a rate-limit-free 6-digit TOTP defeats the phished-password threat the feature exists to counter. A minimal slice of idraa#81 (the `failed_login_count`/`locked_until` columns + lock-on-failed-password/second-factor + non-recovery-shaped short-circuit) lands in P1; the full #81 (UI, admin unlock, per-IP) stays P3.
13. **Interstitial: dependency → middleware.** Direction: ↔reframed. Justification: plan-gate architect ruling — no single authed-router chokepoint, so a dependency is default-allow; a path-allowlisted middleware inner to `SessionMiddleware` is default-deny.
14. **WebAuthn default: two origins → one.** Direction: ↔reframed. Justification: plan-gate (security + architect) — one RP-ID can't span `idraa.fly.dev` + `idraa.app`; the two-origin default failed its own prod validator. See the single-RP-ID note in §Configuration.

---

## End
