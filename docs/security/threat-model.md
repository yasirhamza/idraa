# Idraa threat model

This document maps the trust boundaries in `src/idraa/` + `fair_cam/` and its
Fly.io deployment, and runs a STRIDE pass over each. It is the artifact the
security-auditor persona (`CLAUDE.md` → Review ceremony) consults and updates
at milestone PR-gates and whenever a change touches a boundary listed below.

**Scope.** The application and its own deployment. Not in scope: the
FAIR-methodology sense of "threat modeling" — i.e. how Idraa helps *users*
characterize threats in their own risk scenarios (MITRE ATT&CK crosswalk,
Threat/Asset/Method/Effect scenario taxonomy). That's a product feature, not
an app-security artifact, and isn't covered here.

**Status.** Drafted 2026-08-05 from a live code sweep (not carried forward
from an earlier doc — none existed). **First full re-audit 2026-08-05, second
2026-10-23** — each time every file:line citation was re-opened and every
counting claim re-derived against the tree by three parallel reviewers, with
findings applied in one consolidated pass (see §13 for what the first caught
and why the periodic pass exists). Every claim below cites a file:line as of
the latest re-audit; re-verify before trusting an old citation, per the
project's own "point-in-time observation" convention.

## 1. System overview

```
Internet ──TLS (ends at the Fly edge)──▶ Fly.io edge/proxy ──(B1)── trusted client-IP header (Fly secret) or XFF hop-count
   │  HTTP, port 8000 only — no other port published (operators reach the VM over Fly's private ssh: §3 factor reset)
   ▼
Idraa FastAPI process (single VM: performance / 2 cpu / 4096mb, fly.toml:91-94)
   │  Request path, outermost first: all 8 middleware (LIFO-registered, app.py:1173-1237), then
   │  per-route dependencies (step-up resolves before role on every gated route); [ ] = no letter.
   │  B0/B3 reject before B2's session lookup; [setup_guard] still reads the user count first.
   ├─ RequestFramingMiddleware (outermost, pure ASGI) ──(B1)── ambiguous framing refused (desync defence)
   ├─ uat_basic_auth (UAT-hosted only) ──(B0)── no credential ↔ shared HTTP Basic credential
   ├─ [setup_guard] — no user yet → 307 /setup, the first-run admin claim (§3; allowlist §6)
   ├─ [SecurityHeadersMiddleware] — CSP, X-Frame-Options, nosniff, HSTS (prod) on responses from below
   ├─ CSRFMiddleware ──(B3)── state-changing request ↔ verified-origin request
   ├─ SessionMiddleware (ASGI-wide, before routing) ──(B2)── unauth ↔ authenticated
   ├─ EnrollmentGuardMiddleware ──(B4)── authenticated ↔ MFA-enrolled
   ├─ [MaintenanceBadgeCountMiddleware] (innermost) — navbar count only, no access decision
   ├─ require_step_up dependency ──(B5)── authenticated ↔ freshly-reauthenticated
   ├─ require_role dependency ──(B6)── role tier (viewer/analyst/reviewer/admin)
   ├─ organization_id scoping ──(B7)── org boundary (single-org today, schema-ready)
   ├─ Jinja2 template rendering ──(B8)── user data as template *variable*, never *source*
   ├─ file import/export paths ──(B9)── external file content ↔ internal data model
   └─ Monte Carlo run executor ──(B10)── request ↔ shared-VM compute/memory budget
   │
   ▼
SQLite (Fly volume, /data/riskflow.db) — local to the VM, not network-reachable
```

Eleven labeled boundaries (B0–B10), each taken in turn below under a STRIDE
pass. **S**poofing / **T**ampering / **R**epudiation / **I**nformation
disclosure / **D**enial of service / **E**levation of privilege — only the
rows with a real control or a real residual risk are listed per boundary;
blank categories means "not a meaningfully distinct risk at this boundary;
covered by another boundary's row."

## 2. B1 — Public internet ↔ Fly edge

- **S**: `request.client.host` is **never** trusted for a security decision
  (`routes/deps.py:62-69,98-99`): behind the edge it is the proxy hop unless
  `FORWARDED_ALLOW_IPS` is set (a `*` makes it an attacker-chosen XFF entry). Two opt-in
  strategies: a Fly-secret header (`trusted_client_ip_header`, `fly.toml:27-31`) or an N-hop XFF
  walk (`trusted_proxy_count`, `config.py:390-397`). Both unset → the per-IP throttle no-ops rather than
  trusting a spoofable value (`routes/deps.py:120-130`); audit logging falls back to
  best-effort `request.client` instead (`routes/deps.py:133-154`) — a deliberate
  forensic-vs-security-critical asymmetry.
- **T (request smuggling / desync, 2026-09)**: the back-end parser
  (uvicorn + httptools) rejects every classic gadget (dual CL, CL.TE, TE.CL,
  obfuscated TE, bare LF — advisory GHSA-46jj-823j-mjj9 dynamic pass). The
  two shapes it accepted — a body on GET/HEAD/OPTIONS and
  `Transfer-Encoding` on HTTP/1.0 — are gadgets only if the Fly edge frames
  them differently, which cannot be tested locally. The outermost
  `RequestFramingMiddleware` (`middleware/request_framing.py:33-49`) refuses
  both with 400 and any method outside GET/HEAD/POST/PUT/PATCH/DELETE/OPTIONS
  with 405 + `Allow`, always with `Connection: close` (`middleware/request_framing.py:78-82`), before any other layer reads the
  request. Live uvicorn probe: GET+body with a pipelined second request →
  one 400, connection closed, the pipelined request never runs; pinned by
  `tests/unit/test_request_framing.py`.
- **I (C5, 2026-09)**: `/healthz` is exempt from the B0 pre-gate and the
  setup guard, so it is liveness-only — `{"status": "ok"}`, no version and no
  security-settings state (`app.py:1358-1366`). The idraa#107 cache-state
  signal renders on the admin-only `/settings/security` page instead.
- **D**: boot-time warning fires in prod if the per-IP login throttle is
  enabled with no trust strategy configured (`app.py:1018-1035`) — misconfig is
  loud, not silent.
- Gap: `fly.toml:4` comments that it's "read on every `fly deploy` (run by
  `.github/workflows/uat-deploy.yml`)" — that workflow does not exist in
  `.github/workflows/` (only `ci.yml`, `dependency-review.yml`). Deploys
  actually run through `./scripts/fly` per project convention; the comment is
  stale, as is line 5's "bootstrap workflow". Low severity (comment, not behavior) — worth a follow-up cleanup.

## 2a. B0 — UAT basic-auth pre-gate (hosted UAT only)

**Omitted from the original 2026-08-05 sweep** — caught by the 2026-08-05
full-doc re-audit. This is the app's outermost **authentication** layer (only
the B1 request-framing guard sits outside it, since 2026-09)
(`middleware/uat_basic_auth.py`, wired at `app.py:1231`; confirmed order
`app.py:1173-1237`) — outside even `setup_guard` (which carries no boundary
letter of its own; see §6), the security headers (so its 401s carry none), B3's CSRF and B2's session auth. A single
shared HTTP Basic credential gating the hosted UAT
deployment, layered ON TOP of the app's normal `/login` session auth (compromising
the edge credential does not equal compromising the app, per the module
docstring).

- **S/E**: when `UAT_BASIC_AUTH_PASSWORD` is unset (dev/test/local docker),
  the middleware is a pure no-op (`uat_basic_auth.py:77-78`) — this boundary
  doesn't exist outside the hosted UAT runtime. When set, every request
  without a valid `Authorization: Basic` header 401s
  (`_check_auth`, `uat_basic_auth.py:103-137`), except the exact paths in
  `EXEMPT_PATHS` (`uat_basic_auth.py:33-48` — `/healthz` plus enumerated PWA install assets),
  and only for `GET`/`HEAD` on those paths (`uat_basic_auth.py:74`) — a guard against a future
  POST handler on one of those paths silently inheriting an unauthenticated
  write.
- **T (misconfiguration)**: an empty-string `user` with a real password set
  fails closed rather than matching any caller (`uat_basic_auth.py:82-83`,
  explicit trap comment).
- **S (weak credential, C6, 2026-09)**: the credential is read from
  `Settings.uat_basic_auth_user` / `uat_basic_auth_password`
  (`config.py:351-352`), not straight from `os.environ`, so it gets boot
  hardening like `SESSION_SECRET`: in prod an enabled pre-gate needs a 16+
  character password, a non-empty user and password ≠ user
  (`_check_uat_basic_auth_hardening`, `config.py:502-527`), else the app
  refuses to boot. Unset stays allowed (self-hosted, no pre-gate).
- **S (timing)**: `secrets.compare_digest` on both user and password,
  assigned to locals and AND-ed at the end rather than short-circuited
  (`uat_basic_auth.py:135-137`) — the docstring explains the short-circuit
  form would leak "wrong user" vs. "wrong password" via response timing.
- **Residual risk**: single shared credential (not per-user), by design for a UAT gate —
  compromise exposes the instance only to the inner (still-intact) session auth. Guesses are not
  throttled and leave only uvicorn's access line, so the strength is C6's 16-character floor.
  Its *presence* and *position* are pinned by
  `tests/unit/test_app_middleware_order.py::test_middleware_wire_order`,
  which asserts the 8-element `app.user_middleware` class list (framing guard
  outermost) and, because `uat_basic_auth` and `setup_guard` are both
  `BaseHTTPMiddleware`, their dispatch names at indices 1–2
  (that test's last assertion), so removing, reordering or swapping them fails;
  a same-named replacement would still pass.
  What nothing checks is whether THIS DOCUMENT still enumerates the real
  middleware stack (see §12 item 6).

## 3. B2 — Unauthenticated ↔ authenticated (session)

- **S/T**: session cookie `idraa_session`, `itsdangerous.URLSafeSerializer`
  signed (`services/auth.py:27,96-101`); cookie attributes — `httponly`,
  `samesite=lax`, `secure` in prod — set in `set_session_cookie`
  (`services/auth.py:314-338`). `SessionMiddleware.dispatch` (`middleware/session.py:33-64`)
  unsigns and loads `AuthSession`+`User` before any route runs, ASGI-wide —
  cannot be bypassed per-route (verified while checking B8/HTMX below: every
  fragment handler still resolves through the same dependency graph).
  Absolute 14-day TTL, does not slide (`services/auth.py:28,295-311,362-378`).
  **CSRF depends on this cookie value never changing mid-session** (GHSA-46jj-823j-mjj9 B4
  binding, §4): today it is a timestamp-free `URLSafeSerializer` signature and
  `SessionMiddleware` never re-issues it. A future sliding re-sign or rotation
  would invalidate every open page's forms (one 403 + reload each) — design it
  with that in mind.
- **S** (credential stuffing): Argon2 password hashing with a precomputed dummy hash, so
  a nonexistent/inactive user pays the same single verify (`_DUMMY_PW_HASH`/`verify_user_password`,
  `services/auth.py:75-89`); a known user's miss also writes the lockout UPDATE and C2 audit row
  (unmeasured timing gap; ≤5 samples per lockout window by default). **A3 (2026-08-15):** the Argon2
  verify — BOTH the real and the dummy branch — now runs via `_hash_offload` on
  a DEDICATED, config-sized `ThreadPoolExecutor` (`_get_hash_pool`,
  `services/auth.py:39-59`; `Settings.argon2_max_threads`, `config.py:367`), isolated
  from the default executor that Monte-Carlo run computes saturate (§10). This
  keeps the single event loop non-blocking (was a measured 13.7× head-of-line
  block) while preserving the timing-equal anti-enumeration property (both
  branches offloaded identically, exactly one verify each).
- **D/brute-force**: two independent DB-backed throttles — per-account lockout (5 attempts/900s,
  `config.py:355-356`; `services/auth.py:384-434`; a store error fails the request) and per-source
  `LoginAttempt` (20/900s/900s, `config.py:401-403`; fail-open, `services/login_throttle.py`).
  `/login` and `/login/mfa` apply both, passkey login only per-source; step-up code/password verify
  only per-account (`routes/step_up.py:124`), step-up passkey only per-source (`routes/step_up.py:217,245`).
  **Both counters are now atomic (2026-08-15).** The per-account counter
  (`register_failed_login`) was a non-atomic read-modify-write that lost
  increments under concurrency (~1 of 5 retained); it now does a guarded
  `UPDATE users SET failed_login_count = failed_login_count + 1` with a `CASE`
  lock and `RETURNING` (reflected via `set_committed_value`), so N concurrent
  misses each count and the lock trips at the true threshold — matching the
  per-source throttle's `ON CONFLICT ... failed_count + 1` atomicity. Closes §12
  gap #8. Side benefit: the `user.login_locked_out` audit, which the lossy
  counter could silently suppress (threshold never crossed under a burst), now
  fires reliably.
- **E**: MFA — TOTP (pyotp, single-use-per-step replay guard,
  `services/totp.py:21-55`) and WebAuthn/passkeys
  (`services/webauthn_service.py:66-142`) both live. **WebAuthn challenge
  single-use (advisory GHSA-46jj-823j-mjj9 B5, 2026-09):** challenges are
  stateless signed cookies, and `sign_count_ok` must allow 0 → 0 (platform
  authenticators never increment), so a captured (assertion, challenge cookie)
  pair used to replay within the 300 s TTL — every replay minted a new login
  session or re-stamped a step-up. Each verify route (login, step-up,
  registration) now claims the challenge atomically AFTER the signature checks
  and BEFORE any side effect: `consume_challenge`
  (`services/webauthn_challenge.py`) is an `INSERT ... ON CONFLICT
  (challenge_digest) DO NOTHING RETURNING` into `webauthn_challenge_consumed`
  (auth-layer table, no `organization_id`, like `login_attempt`); a second use
  of the same challenge gets 400 `challenge already used`. Rows expire at TTL +
  60 s and are swept at boot and by the periodic reaper
  (`services/run_reaper.py::sweep_expired_webauthn_challenges`). The step-up
  challenge cookie is additionally bound to the minting session
  (`services/auth.py::load_webauthn_stepup_challenge` compares `sess.id.hex`),
  so a captured step-up ceremony cannot stamp a different session of the same
  user. `user_has_strong_factor`
  (`services/mfa_enrollment.py:18-29`) accepts either.
  `EnrollmentGuardMiddleware` (`middleware/enrollment_guard.py:36-54`)
  force-redirects unenrolled users to `/account/security` when the effective
  MFA policy is `required`. **Recovery-code single-use (finding B6, 2026-08-15):** a
  matched recovery code is burned by an atomic guarded UPDATE
  (`_claim_recovery_code`, `UPDATE ... WHERE used_at IS NULL` + `rowcount==1`,
  `services/second_factor.py:47-79`), mirroring the TOTP step claim — replacing
  a plain attribute set that let two concurrent redemptions of one code both
  mint a session. The recovery-loop Argon2 (`_match_recovery_code`) is offloaded
  to the same dedicated pool. A lost claim audits `user.recovery_code_claim_lost`
  (§11) and rejects; the caller additionally audits `user.login_mfa_failed` /
  `user.step_up_failed`, so a lost claim emits TWO rows by design.
- **S/E (first-run bootstrap)**: while no user exists, `setup_guard` (§6) sends every
  non-allowlisted request to `/setup`, and `POST /setup` creates the organization, the first
  ADMIN and a logged-in session for whoever submits it first; no credential is checked, by
  design. Residual: from first boot
  until the operator completes `/setup`, any client that reaches the port can claim the
  instance. The hosted UAT closes that window with B0 (`/setup` is not in `EXEMPT_PATHS`); a
  self-hosted install without the pre-gate must finish `/setup` before exposing the port.
- **E/R (factor reset: admin UI and operator CLI, idraa#256)**: both paths run `reset_user_mfa`
  (`services/mfa_enrollment.py:61-84`: deletes the user's passkeys, TOTP and recovery codes and
  clears `mfa_enrolled_at`), then `revoke_user_sessions` (`services/auth.py:472-482`: deletes
  every `AuthSession` row of the user, so the next request is anonymous), and write
  `user.mfa_admin_reset` and `user.sessions_revoked` rows; neither path sets or reveals a
  credential. (1) `POST /users/{user_id}/reset-mfa` (`routes/users.py:512-553`): ADMIN role,
  ADMIN step-up (§5), CSRF, a `confirm` field and an org-scoped `get_user` (cross-org 404); the
  rows name the admin. (2) `python -m idraa auth reset-mfa <email>` (`__main__.py:22-61`), the
  backstop for a locked-out sole admin, has no HTTP entry point and no in-app gate (no session,
  role, step-up or CSRF): whoever can run a process with the app's environment and database runs
  it (on Fly, a `scripts/fly ssh console` shell behind Fly org authentication). That access can
  already write the database directly, so the command adds nothing to host access. It looks the
  email up across every org (`services/auth.py:464-469`, `.first()`; one org today) and writes
  its rows with `user_id` and `ip_address` NULL and `"via": "cli"`: the trail shows that an
  operator acted, not which one. The `idraa` console script is the task runner; only
  `python -m idraa` reaches this command. Deactivating a user runs the same revoke
  (`routes/users.py:349-351`, and the set-active toggle); logout deletes its own row (§6).
  **Residual (both paths):** a reset leaves a password-only account until it re-enrolls. A
  password login then mints a full session (`routes/auth.py:246-252`), the enrollment guard
  (policy `required`) holds it on `/account/security`, and step-up accepts the password from an
  account with no factor (§5), so whoever signs in next with the password enrolls the next
  factor. Invite passwords are chosen by the admin and there is no self-service password change,
  so an admin who resets a user's factors can sign in as that user before they re-enroll; the
  reset row names that admin. Coordinate each reset with its user.

## 4. B3 — CSRF (state-changing request ↔ verified-origin request)

- **T**: session-bound double-submit HMAC pattern (`middleware/csrf.py`).
  Cookie `csrf_token` = `<nonce>.<HMAC-SHA256(key, nonce || binding)>`
  (`csrf.py:151-242`): `key` is derived once from `session_secret`
  (`derive_csrf_key`, info `idraa-csrf-v2` — tokens signed the pre-2026-09 way
  never verify), and `binding` (`csrf_binding`, `csrf.py:172-191`) is
  `sha256` of the raw `idraa_session` cookie value — read through exactly the
  accessor `SessionMiddleware` authenticates with (`request.cookies.get`,
  last-wins on duplicates; empty = absent) — or a fixed 32-byte anon binding
  when there is no session cookie. **Advisory GHSA-46jj-823j-mjj9 B4
  (2026-09):** before this, a token proved only "this server minted it", so an
  anonymously minted token rode an authenticated admin write and a session-A
  token rode session B. `HttpOnly=False` (must be JS-readable),
  `SameSite=Strict`. Validation requires the cookie present and valid **under
  the current binding** and the header-or-form token to
  `hmac.compare_digest`-match it (`csrf.py:374-405`). A cookie that fails the
  current binding is re-minted (every GET after login/logout hands the page a
  fresh token). Rejections are an opaque 403 (`_forbid`, `csrf.py:440-478`):
  the body carries no reason; a present-but-stale cookie gets a fresh
  `Set-Cookie` on the 403 (its presence tells only the cookie's own holder
  that their half was stale — not a cross-session oracle), a missing cookie
  does not (that is the cross-site-POST shape; minting there would let a
  cross-site page reset the victim's cookie), and every 403 to an
  `HX-Request` carries `HX-Refresh: true` so HTMX reloads instead of swapping
  "Forbidden" into the page. **No exemption list** — module docstring and
  inline comments (`routes/register_import.py:45`, `routes/library.py:352-353`)
  state nothing is exempted; fail-closed by design. Non-form JS reads the
  token through `window.idraaCsrfToken()` (`static/js/csrf.js`: the
  `csrf_token` cookie first — last exact-name match, like the server — then
  `<meta name="csrf-token">`, `templates/base.html:7`): `<body hx-boost>`
  swaps only body + title, so the `<head>` meta can hold a pre-login token
  after a boosted login. A test pins that nothing else reads the meta tag.
- **What survives if a lower layer fails (GHSA-46jj-823j-mjj9 B4):**

  | If… | Authenticated writes | Still open |
  |---|---|---|
  | SameSite on the CSRF cookie is relaxed | still need a token bound to the victim's session cookie, which a cross-site page can neither read nor compute | pre-login forms (`/login`, `/login/mfa`, `/login/passkey/*`, `/setup`) share the one anon binding, so an attacker-minted anon token is valid there |
  | a subdomain writes cookies (cookie tossing) | a tossed `csrf_token` is bound to the attacker's session or to anon, so it fails; tossing `idraa_session` too makes the request run AS the attacker (a session swap, not CSRF) | the same pre-login gap: login CSRF is NOT prevented (an earlier design draft's "prevents login CSRF" claim was withdrawn) |
  | a token leaks | usable only with its own session's cookie | for that session's whole life (14-day absolute TTL, no per-token rotation) |

  The open column is §12 items 9–10. Plain (non-HTMX) form POSTs that hit a
  stale token get a 403 whose text tells the user to reload; HTMX requests
  reload automatically.
- **Non-ASCII-token hardening (D1, 2026-08-14)**: `hmac.compare_digest` raises
  `TypeError` (not a clean non-match) when either operand carries a
  non-ASCII character, so an attacker-crafted non-ASCII token used to escape
  as an unhandled 500 rather than the intended 403. Both `compare_digest`
  call sites in this middleware now guard with `.isascii()` first, rejecting as a normal
  verification failure instead: `verify_csrf_token`'s `sig_hex` check
  (`csrf.py:233-239`) covers the cookie-signature compare — this one closes
  an **unauthenticated 500-on-every-request** class, since `verify_csrf_token`
  runs unconditionally near the top of `dispatch` (even on safe GETs) and
  `sig_hex` is never hex-validated before reaching it (unlike `nonce_hex`);
  `dispatch`'s `submitted` check (`csrf.py:391-397`) covers the double-submit
  compare against the form-field-or-header token. Regression:
  `tests/integration/test_csrf_nonascii_token.py`.
- **DoS ceiling (A4, 2026-08-09)**: this middleware buffers the FULL body of
  every unsafe-method request before the route runs (to replay it for
  downstream form parsing — `_CachedRequest.wrapped_receive`). That buffer is
  now size-capped: `_read_body_capped` (`csrf.py:122-148`) reads via
  `request.stream()` under `settings.max_request_body_bytes` (default 8 MB,
  `config.py:384`) and returns a 413 before the whole body is buffered
  (`csrf.py:329-346`). The cap sits ABOVE `MAX_UPLOAD_BYTES` (5 MB,
  `routes/deps.py:23`) + multipart framing so legitimate imports pass; a test
  pins that inequality (`tests/unit/test_csrf_body_cap.py`). Before this an
  unauthenticated 50 MB `POST /login` grew RSS ~50 MB before its guaranteed
  403. Not attacker-authenticated; a single-machine availability hardening.

## 5. B5 — Step-up & elevated-action freshness (B4, the enrollment guard: §3 E)

- **E**: `StepUpCategory` = `EXPORTS | DESTRUCTIVE | ADMIN | CREDENTIALS`
  (`models/enums.py:15-19`). `require_step_up(category)`
  (`routes/deps.py:230-266`) 401s if unauthenticated, else requires
  `now - session.reauthenticated_at <= effective_step_up_window()` (default
  600s, `config.py:372`; per-category admin override,
  `services/security_settings.py:146-188`). **43** real call sites (37 as of
  the 2026-08-05 re-derivation; +6 from finding B2 on 2026-08-09; the first-ever sweep
  said "~40 / 16 exports" by counting a docstring example at
  `routes/deps.py:236` and a prose mention at
  `routes/scenario_export_routes.py:20` as call sites): **14** exports, 9
  destructive (7 deletes, overlay deactivate, run sample purge), **14** admin/user-mgmt (7 in
  `routes/users.py:110,160,256,376,467,510,557`, 1 in
  `routes/settings.py:176`, and the **6 finding-B2 additions** — `POST /organization`,
  `POST /fx-rates`, and the four admin-only SME-directory mutations
  new/edit/archive/unarchive), 6 credential changes
  (`routes/mfa.py:96,136,190,232,259,337`). Re-verification
  (`routes/step_up.py:90-332`) is throttled per route (§3 D/brute-force; five failed attempts there
  also lock `/login`, which the passkey route avoids on purpose) and stamps `reauthenticated_at`;
  login itself counts as a re-auth (`create_session`, `services/auth.py:308`).
- **Finding B1 — the settings write is never disarmed (2026-09).** The global
  kill-switch (window ≤ 0) and the per-category ADMIN override both live in the
  settings that `POST /settings/security` writes. `step_up_required()` honours
  them, so one fresh write could previously switch step-up off for good —
  every later write, including re-arming it, passed with any stale admin
  cookie. That route alone is wired `require_step_up(ADMIN, unconditional=True)`
  (`routes/settings.py:172-177`, `routes/deps.py:259-262`): it ignores both
  switches and checks freshness against `settings_write_step_up_window()`
  (`services/security_settings.py`) — the env default (600 s floor when the env
  opts out), which a configured window can only TIGHTEN (`min`), so neither the
  kill-switch, the ADMIN override nor a huge window disarms it. Configured
  windows are bounded to one day (`MAX_STEP_UP_WINDOW_SECONDS`, validated in
  `routes/settings.py::_parse_window`), which also keeps `is_step_up_fresh`
  clear of a `timedelta` overflow. Switched-off categories stay off everywhere
  else. Pinned by `tests/integration/test_step_up_categories.py::test_b1_*`.
- **Findings B6/A3, inherited (2026-08-15):** step-up re-verify shares
  `verify_totp_or_recovery` and `verify_user_password` with login
  (`routes/step_up.py:148,150`), so it inherits both the atomic recovery-code
  burn (§3 E) and the dedicated-pool Argon2 offload (§3 S) with no step-up-
  specific code.
- **Finding B2 (2026-08-09)**: `POST /organization` and `POST /fx-rates` mutate inputs
  that feed org-wide FAIR/financial computation (industry, risk_appetite,
  security_maturity, preferred_currency; the FX rates behind every ALE), so a
  stale admin session could previously change them freely while being bounced
  from `/settings/security`. All six are now `StepUpCategory.ADMIN`-gated. The
  count-regression tripwire (`tests/unit/test_step_up_call_sites.py`, now 43)
  is joined by a COMPLETENESS guard (`tests/unit/test_step_up_completeness.py`):
  it introspects the live route table and fails if any state-changing route gated by
  `require_role(ADMIN)` alone is neither step-up-gated nor on an explicit exemption
  allowlist (the routine reference-data / multi-step-import surface) — so a NEW
  admin-only route can no longer ship ungated unnoticed, the exact hole a count cannot
  see (a broader role list or an in-handler role check is outside its scope, by its docstring).

## 6. B6 — RBAC (role tier)

- **E**: `UserRole` = `ADMIN | ANALYST | REVIEWER | VIEWER`
  (`models/enums.py:8-12`). `require_role(*roles)` (`routes/deps.py:163-190`)
  403s outside the allowlist; the 403 path now also emits a structured
  `rbac_denied` WARNING (actor UUID, role, required roles, path —
  `deps.py:180-186`) so privilege-probing leaves a trace (C1, 2026-08-09; a
  WARNING not a DB `AuditLog` row, to keep it bounded by log rotation rather
  than an attacker-driven write amplifier). Gating is 100% per-route (no
  `APIRouter(dependencies=...)` blanket gate found) — all 80 state-changing
  handlers across `routes/*.py` (every one a POST: no PUT/PATCH/DELETE route exists;
  re-derived 2026-10-23) were diffed against an auth dependency; the 6 unguarded are the
  pre-authentication login POSTs (`routes/auth.py:129,274,360,368`) plus
  `POST /logout` (`routes/auth.py:459-463`, takes `user: User | None =
  Depends(current_user)` — no-op on an already-logged-out session, correct
  by design) and `/setup` (`routes/setup.py:58`), whose only first-run check is its own
  `_has_any_user` re-check (`routes/setup.py:77-78`): the outer `setup_guard`
  (`app.py:1190-1224`) allowlists `/setup` (`app.py:826-829`) and never blocks it.
- **Doc-drift flag — RESOLVED 2026-08-05**: `CLAUDE.md`'s scope-discipline
  section named three roles ("analyst / reviewer / admin"); the code has
  four. `VIEWER` is used in **9** read-only routes (re-derived 2026-10-01 for
  the Threat Agent Library, up from 7 — the first sweep said "2", an
  undercount): 4 inline in `routes/library.py:96,145,266,389` and 3 via the
  `_VIEWER_PLUS` allowlist (`routes/control_library.py:45`, applied at
  `routes/control_library.py:154,210,249`), plus 2 new Threat Agent Library read-only pages behind
  their own `_ALL_ROLES` allowlist — `routes/threat_communities.py:19`,
  applied at `GET /library/threat-communities` (`routes/threat_communities.py:23`, role dependency
  `routes/threat_communities.py:27`) and `GET /library/threat-communities/{slug}` (`routes/threat_communities.py:39`, role
  dependency `routes/threat_communities.py:44`). Separately, `scenario_export_routes.py:51` deliberately
  uses bare `require_user` — a strict VIEWER-inclusive allowlist would 403
  admins and analysts, per its inline comment. Never a security defect, only
  a doc-accuracy one; CLAUDE.md (local, untracked) now names all four roles — its
  "Corrected 2026-08-05" note was dropped in a later consolidation (re-checked 2026-10-23).
- **New analyst+ POST (Threat Agent Library, 2026-10-01):** `POST
  /scenarios/{scenario_id}/confirm-threat-community`
  (`routes/scenarios.py:1556`) is gated `require_role(UserRole.ANALYST,
  UserRole.ADMIN)` (`routes/scenarios.py:1562`) — REVIEWER and VIEWER get 403. It is the
  dedicated confirm path; edit-form and wizard saves also resolve it (§11).
- **Role assignment and the last-admin invariant (added by the 2026-10-23 re-audit):** only
  an ADMIN with a fresh ADMIN step-up can change a role or deactivate a user (`/users/{id}/edit`
  and `/set-active`, §5). An admin cannot demote or deactivate themselves
  (`routes/users.py:280-287`), and disarming the last active admin is refused inside the
  write itself — `guarded_admin_disarm`'s UPDATE re-checks that another active admin
  remains (`routes/users.py:321-344`, `services/users.py:159-194`, idraa#83). The role is
  re-read from the `users` row on every request (`middleware/session.py:58-60`), so a
  demotion takes effect on the next request; deactivation also revokes live sessions
  (cited in §3). Measured 2026-10-23: REVIEWER is named only in the same six
  four-role allowlists as VIEWER (the 9 GET routes above), so its reach equals VIEWER's.

## 7. B7 — Multi-tenancy / org boundary (IDOR)

- **I/E**: repository reads of org-scoped tables take `organization_id` (`repositories/scenario_repo.py:65-82`,
  `repositories/run_repo.py:27-41`), except the zero-caller, unscoped `get_override_by_version`
  (`repositories/scenario_library_repo.py:361-377`, invisible to the checker below);
  the codebase follows a "no bare-PK / no existence-oracle" convention —
  cross-org IDs 404, not 403, so lookups don't leak existence
  (`routes/overlays.py:457`, `routes/scenarios.py:732,774`,
  `routes/qualitative_bands.py:224,262`). `routes/controls.py:710`'s check
  (`assignment.control_id != control_id`) is not itself an org check — it's
  transitively safe because `control` was org-verified earlier in the same handler
  (`routes/controls.py:705-706`).
- **Correcting Misaligned Decisions link is a same-control slug, never a
  client id (issue #268):** the link is a top-level form field, not a `ControlForm` field,
  parsed to a sub-function slug (`routes/controls.py:971-986`); the server resolves it to the
  row of the SAME, already org-verified control by an explicit control + org select
  (`services/controls.py:249-267`), so it can never name another control's or org's row.
  A client `assignments[N][derived_from_assignment_id]` is dropped before validation
  (`routes/controls.py:82-89`); one that survives, a submitted `dsc_corr_misaligned` row and
  an ineligible or absent source are refused by a pure check (`services/controls.py:196-246`),
  and an unknown slug by the route parse. That check and the org-wide eligibility check in the
  next bullet are both pure and both run before any mutation in create and update, so a
  refusal leaves no row and no audit entry. The 422 messages are fixed text or enum values (the
  org-wide refusal is fixed text too); the
  slug comes back only as JSON via `| tojson` in a single-quoted `x-data` (`templates/controls/form.html:344`;
  breakout-tested, `tests/routes/test_controls_derived_link_form.py`).
- **Org-wide controls (issue #299):** the run-create freeze and every inherited-controls
  display read org-wide controls through `ControlRepo.list_org_wide_meta`
  (`repositories/control_repo.py:68`) and `ControlRepo.count_org_wide_meta` (no line number of
  its own), both filtered by `organization_id` plus the published-and-operating predicate, so
  another org's flagged control is never inherited
  (`tests/services/test_org_wide_meta_eligibility.py::test_list_and_count_org_wide_meta_filter_status_stage_and_org`,
  `tests/services/test_run_service_inherited_controls.py::test_other_orgs_org_wide_controls_never_inherited`).
  The flag is set only through the existing analyst/admin control form (§6, B6): the importer,
  library adopt and duplicate never set it. `duplicate_control` resets it to False
  (`services/controls.py:867`), and the importer's `Control(...)` constructor
  (`services/controls_importer.py:313-323`) names no `org_wide_meta`, so the column default
  False applies. A save that sets the flag without an inheritable Variance Management or
  Decision Support assignment is refused with fixed text before any mutation, and run-create
  independently refuses, again with fixed text, an org-wide control that has none (defence
  in depth). Every change to the effective org-wide set flags the org's COMPLETED runs stale
  and writes one `control.org_wide_sweep` audit row (§11).
- **Gate-enforced, not just convention (2026-08-05):** `scripts/
  lint_org_scoped_lookups.py`, run in every local gate + CI `gate` job, ASTs
  every file under `src/idraa/{routes,services,repositories}` and flags any
  bare `<expr>.get(Model, id)` on an org-scoped model with no
  `organization_id` check (or `*_for_org*` delegated-safety call) anywhere
  in the enclosing function. Its first real run found exactly one live gap
  and ten correct-but-undocumented exemptions (eleven suppressions today, listed below):
  - **Fixed**: `services/controls.py`'s `get_control()` — the concrete
    example this section used to cite as "no org check at all at that
    line, safe only by caller discipline" — is now `get_control_for_org(db,
    organization_id=..., control_id=...)`, checking internally like every
    other `*_for_org` method. All 8 former call sites in `routes/
    controls.py` dropped their now-redundant duplicate check.
  - **Suppressed with a recorded reason** (`# org-scope: ok — <reason>`,
    same line as the call — the checker enforces a non-empty reason):
    6 sites rebinding the *current session's own* `user.id` across a
    detached-instance boundary (`routes/mfa.py:148,198,270,348`,
    `routes/step_up.py:103,313`); 3 sites resolving a user id from a
    source that's already unforgeable — a server-signed pending-MFA
    cookie (`routes/auth.py:301`) and an already-verified WebAuthn
    credential's owner (`routes/auth.py:422,437` — the B5 replay-audit lookup
    and login); 2 sites in
    `services/retention.py:195,237` that are a system-wide background
    sweep by design (iterates every org's rows on a schedule, not a
    per-request handler with an attacker-suppliable id).
  - **Walked, not predicate-checked (#299):** the two org-wide `ControlRepo` methods above
    live under `repositories/`, which `--all` walks, and the lint run
    (`uv run python scripts/lint_org_scoped_lookups.py --all`) exits 0 with them in place.
    That is all it establishes. It flags only a bare `.get(Model, id)`; these are
    `select(Control).where(...)` queries, the shape the residual-risk bullet below says it
    never flags, and their own `organization_id` comparison would satisfy its function-wide
    search for any `.get` added to them later. Their org predicate is held by the two
    cross-org tests named above, and their published-and-operating filter by
    `test_repo_filter_matches_predicate` in `tests/test_control_repo_active_stage.py`.
- **Residual risk, narrowed but real (fact-checked 2026-08-05):** the
  checker's org-verification search is **function-wide, not bound to the
  flagged call** — it accepts *any* `organization_id` Compare or
  `*_for_org*`-named call anywhere in the enclosing function, without
  requiring either to reference the same variable/id as the flagged
  lookup. Concretely, this passes the checker today (a live blind spot, not
  a hypothetical):
  ```python
  async def get_two(db, control_id, run_id, org):
      control = await db.get(Control, control_id)
      if control is None or control.organization_id != org.id:
          raise NotFound()
      run = await db.get(RiskAnalysisRun, run_id)  # different id, NEVER
      return control, run                            # checked — not flagged
  ```
  A parent-fetch-then-child-fetch handler with two related ids is an
  ordinary shape, so this is the more exploitable gap versus the allowlist-
  staleness one below — tracked as issue #151 (bind the check to the
  specific assigned variable / the flagged call's id argument, not just
  "some check exists in this function"). Additionally, the checker parses only the
  `<expr>.get(Model, id)` shape on a bare model name: a `select(Model).where(Model.id == x)`
  with no org predicate (the dead method above; `services/reports.py:1709-1735`'s legacy
  fallback), an aliased or attribute-qualified model, and the two org-child tables in
  `NON_ORG_COLUMN_MODELS` are never flagged; allowlist staleness is closed by the parity
  test below (2026-10-01). A suppression's *reason* is human-asserted, never verified — the
  security-auditor persona (`CLAUDE.md` → Review ceremony) is the backstop for both: reading
  new lookups of those shapes and each suppression's reason for whether it is really true.
- **Org scoping has two sources; one is single-org-only (re-audit 2026-10-23):** 44
  `require_sole_org(db)` call sites in 11 route modules (controls 15, overlays 9, reports 4,
  runs 3, users 3, six more with 1–2 each) scope by `select(Organization).first()` — the
  first org row, no ORDER BY (`services/org.py:25-33`) — not by the caller's
  `user.organization_id`. Correct only while exactly one org exists (the sole constructor is
  `/setup`, `routes/setup.py:106`); all of them pass the checker. Any
  multi-tenancy work must replace them, and the legacy fallback above, first.
- **Threat Agent Library canonical catalog is deliberately NOT org-scoped
  (2026-10-01):** `ThreatCommunity` (`models/threat_community.py`) has no
  `organization_id` column — P1 ships a single canonical, shared catalog
  (`source` is always `"seed"`), mirroring `ScenarioLibraryEntry`. It is
  listed in `NON_ORG_COLUMN_MODELS`
  (`scripts/lint_org_scoped_lookups.py:98`), and the list's own header
  comment (`scripts/lint_org_scoped_lookups.py:85`) is a standing tripwire: "P2's org-authored threat-community
  table MUST go in `ORG_SCOPED_MODELS`" — P2 (spec §9) adds a *separate*,
  org-scoped table for org-authored communities, it does not add an
  `organization_id` column to this canonical one. `ORG_SCOPED_MODELS` and
  `NON_ORG_COLUMN_MODELS` together are a closed partition, enforced by
  `tests/contracts/test_org_scoped_models_parity.py`: every SQLAlchemy mapper
  must appear in exactly one of the two lists (`tests/contracts/test_org_scoped_models_parity.py:15-16`), so a new model
  (P2's org-authored table included) that lands in neither — or in the
  wrong one — fails CI rather than silently falling through the
  `lint_org_scoped_lookups.py` checker unclassified.

## 8. B8 — Templating (XSS and template injection)

Two distinct injection classes, checked separately (2026-08-05 sweep,
prompted by a review flag that Jinja2 is a known SSTI vector):

- **Output escaping (XSS)** — every render goes through
  `Jinja2Templates(directory=..., context_processors=[...])`
  (`app.py:97-100`), which Starlette constructs with
  `select_autoescape(["html", "htm", "xml"])`; confirmed live
  (`templates.env.autoescape` is the `select_autoescape` closure) and
  confirmed total — all 146 tracked templates are `.html`, so autoescape covers every
  one; the two `| safe` bypasses emit app-built markup only (`app.py:152`'s `csrf_field()`
  Markup, the segno TOTP SVG at `templates/account/_totp.html:12`). **Alpine `x-data` is the sharp edge**: an attribute the browser
  HTML-decodes before Alpine evaluates it as a JS expression, so autoescape is
  no defense there — the value must be JSON-encoded via `| tojson` inside a
  single-quoted attribute. The money-field / loss-readout `x-data` sites do
  this and were fuzzed with breakout payloads (quote / angle-bracket /
  attribute-injection) during the PR #148 review — neutralized. **Correction
  (B3, 2026-08-09)**: the earlier "all `x-data` neutralized" reading was WRONG —
  three distribution selectors in `scenarios/form.html` (tef/pl/sl) interpolated
  the reflected POST value RAW into a single-quoted `x-data` string with no
  `| tojson`. Arbitrary JS executed against the app's own vendored Alpine in a
  headless-Chromium PoC (self-XSS: `form_raw` reflects only the requester's own
  submission, and CSRF blocks cross-site forging, so not remotely weaponizable
  today — but any future feature reflecting ANOTHER user's data through this
  pattern makes it cross-user). Now fixed: all three adopt the single-quoted +
  `| tojson` idiom, `data_table.html`'s `page_size` x-data gets a defensive
  `| int`, and a source-grep guard (`tests/unit/test_scenario_form_xdata_escaping.py`)
  fails if any dist selector regresses to the raw shape; #268's link select reflects its slug the same way (§7).
- **URL-context injection (href)** — a third class, distinct from both:
  autoescape escapes `&<>"'` in an `href` value, preventing attribute
  breakout, but does not validate the URL scheme, so `javascript:` survives
  escaping intact — a raw `<a href="{{ url }}">` would still let a
  `javascript:`-scheme citation URL execute on click. Two gates share one allowlist rule (https scheme + non-empty
  host; everything else — `javascript:`, `data:`, `http:`, malformed —
  renders inert): `formatting.linkify_https` (`formatting.py:29`, issue
  #349) auto-links `https://` URLs inside free-text citation strings, used
  in `templates/library/entry_detail.html`'s citations block; and
  `formatting.safe_https_href` (`formatting.py:62`, issue #349/Sec-I5),
  registered as the Jinja filter `https_href` (`app.py:503`), gates a
  citation URL used directly as an `href=` attribute value — e.g. the
  Threat Agent Library's community detail page
  (`templates/library/threat_community_detail.html:74`:
  `{% set href = c.url | https_href %}`, so a non-https/malformed citation
  URL resolves to `href = None` and the template omits the anchor rather
  than emitting a live link). Both gates are total (never raise) and both
  explicitly reject `javascript:` — regression-tested in
  `tests/unit/test_formatting_linkify.py`
  (`test_linkify_https_javascript_scheme_stays_inert`,
  `test_safe_https_href_rejects` parametrized with `"javascript:alert(1)"`;
  `test_linkify_https_malformed_url_is_total_and_inert` for totality on `https://[`)
  plus a route-level end-to-end check that a stored `javascript:` citation
  URL actually renders as inert text on a real response body
  (`tests/routes/test_threat_community_pages.py:128`,
  `test_javascript_citation_url_renders_as_text`).
- **Template injection (SSTI)** — the distinct, more severe class: attacker
  text becoming the template *source* (`Environment.from_string()`,
  `Template(user_text)`, `render_template_string`), not just a substituted
  variable. Swept the full `src/` and `fair_cam/` trees: **zero application
  call sites** of `from_string(`, `Template(`, or `render_template_string`.
  The only textual hit is an explanatory comment in `app.py:102-107` about a
  CSRF context-var patch that defensively also covers `from_string` *in case
  it's ever called* — it documents an environment capability, not a used
  one. 125 of 128 `TemplateResponse` calls pass a literal path; the wizard step template
  (`routes/scenarios.py:2393-2395`, consumed at `routes/scenarios.py:2573`) indexes a **fixed
  6-element literal list** by an int bounds-checked to `1..6` (`routes/scenarios.py:2385`), and
  two more pick one of two literals by a flag (`routes/scenarios.py:3020-3022`, `routes/help.py:134`);
  one `{% include %}` joins a registry slug into a path (`templates/help/_article.html:28`;
  slugs are code constants, `help_content.py:169`). None takes attacker-controlled text.
- **CSP is not an XSS backstop (2026-10-23)** — `SecurityHeadersMiddleware` (§1) sends a
  CSP whose `script-src` still carries `'unsafe-inline'` and `'unsafe-eval'`
  (`CSP_POLICY`, `middleware/security_headers.py:68-80`; the latter is Alpine's expression
  evaluator), so it does not stop injected inline script or an injected Alpine expression:
  output escaping, `| tojson` in `x-data` and the href gates above are the only defence.
  Tracked publicly: dropping `'unsafe-eval'` via the `@alpinejs/csp` build (#281) and a
  nonce-based `script-src` that drops `'unsafe-inline'` (#282).
- **Invariant to protect**: no future code may call
  `Environment.from_string()` / `jinja2.Template()` /
  `render_template_string()` with any request-derived string (a "custom
  report template" or "custom email template" feature would be the natural
  way to reintroduce this class). The security-auditor persona
  (`CLAUDE.md` → Review ceremony) checks new templating code against this
  invariant explicitly.

## 9. B9 — File import / export

**Import** — five admin-only upload routes, each size- and row-capped; zip-bomb-guarded
only where it applies (the XLSX-capable register path — the others have no zip path).
`services/register_import_parsers.py` accepts CSV and XLSX, sniffed by
extension/content-type/zip-magic (`register_import_parsers.py:160-183`).
`services/scenario_import_parsers.py` accepts CSV and JSON only (per its own module docstring — it has no XLSX path); `services/library_bundle_import.py` parses JSON bundles into the GLOBAL library catalog every org reads; `services/controls_importer.py` and `services/overlays_importer.py` parse CSV.
Guards: 5 MB upload cap via a forgeable
`Content-Length` pre-check plus a post-read length check that holds when the header lies (the `MAX_UPLOAD_BYTES` constant in `routes/deps.py` cited in §4 (B3); enforced in
`routes/register_import.py:253-264`, `routes/scenario_import.py:124-133`,
`library_import.py:80-89`, `routes/controls.py:505-514`, `routes/overlays.py:302-311`); a zip-bomb guard on the XLSX path that reads only
central-directory metadata before `load_workbook` — max 200 members / 50 MB
per member / 500x per-member compression ratio above a 1 MB floor
(`register_import_parsers.py:80-94,112-147`). `zipfile` bounds every read to the
declared `file_size` (verified: a 210 MB member yields exactly 210 MB), so
declared sizes ARE the extraction bound. The residual, characterised 2026-08-08
and consciously accepted: openpyxl (`read_only`) STREAMS worksheet sheet-data
(a 120k-row sheet costs ~1 MB RSS) and never reads media, so a large SHEET is
cheap — the only memory amplifier is the fully-buffered parts (`sharedStrings`/
`styles`, ~7.6x RSS), and each is already bounded to 50 MB by the per-member cap
(~380 MB RSS per part, ~760 MB if both are maxed). No metadata-only cap tightens
this without either false-rejecting legit files or being bypassed: openpyxl
resolves `sharedStrings` by manifest `PartName` (attacker-controlled), not by
path, so a path-based buffered cap is trivially evaded; a value cap false-rejects
legit Excel workbooks (workbook-global `sharedStrings` compresses ~10x); the
ratio cap only denies degenerate/accidental bombs since valid XML maxes ~412x
and 5 MB of wire reaches 50 MB at any ratio. This is tolerated because the whole
surface is ADMIN-only (every `register-import` route is `require_role(ADMIN)` —
`routes/register_import.py:234,248,322,358,402,436,519,578,662,736,775,837`; the
`delete_profile` route additionally requires step-up, `routes/register_import.py:831`) and ~760 MB
transient is
survivable on the 4 GB VM. The non-bypassable tightening (parse
`[Content_Types].xml` to bound buffered parts by role) is deferred as
disproportionate for an admin-only surface; revisit if import ever becomes
non-admin. Row caps: one shared
`MAX_ROWS = 500` constant (`scenario_import_parsers.py:85`) enforced at
the scenario and register entry points — CSV (`scenario_import_parsers.py:275`)
and JSON (`scenario_import_parsers.py:325`) in that module, and re-imported and enforced again
for XLSX (`register_import_parsers.py:221-222`) and CSV
(`register_import_parsers.py:250-251`) in the other; the other three importers carry their own — `MAX_ENTRIES = 500` (`services/library_bundle_import.py:57`), `MAX_CSV_ROWS = 500` (`services/overlays_importer.py:103`) and `MAX_CSV_ROWS = 10_000` (`services/controls_importer.py:104`), the last truncating with a logged warning rather than rejecting the file; `defusedxml`
auto-substitution blocking XML entity expansion (billion-laughs class,
`register_import_parsers.py:29-37`). Parsed cells are coerced to
`str(v).strip()` only — no formula evaluation on import.

**Threat Agent Library slug resolution (2026-10-01):** both import paths
resolve a free-text `threat_community` cell against actual DB membership, not
just syntactic shape — `services/scenario_import.py:348` (`raw_tc not in
published_slugs`) for scenario CSV/JSON import and
`services/library_bundle_import.py:253` (`tc_slug not in published_slugs`)
for library-bundle JSON import; both build `published_slugs` from the same
live query, `ThreatCommunityService(db, organization_id=org_id)
.list_published()` (`services/scenario_import.py:713-715`,
`library_bundle_import.py:419-420`) — constructed with the caller's required
`organization_id`, but that parameter is **unused in P1** against the
canonical catalog (`_latest_seed()` filters only `source == "seed"`,
`services/threat_communities.py:58-73`, never `self._organization_id`); it
is the hook that keeps P2's org-authored union (§7's new bullet) from ever
running unscoped (`services/threat_communities.py:50-52`). An
unrecognized slug is a per-row `422`-shaped preview error, never a silent
pass-through or a foreign-key constraint violation surfacing as a 500. The
**either-of header rule** (`services/scenario_import.py:276-281`): a row may carry
`threat_community` OR the legacy `threat_actor_type` column but never both —
both present is an `"ambiguous"` per-row error
(`services/scenario_import.py:311-317`), matching the CSV header-level check
(`scenario_import_parsers.py:250,256`); neither present is a legitimate
blank ("unassigned"), not an error. The library-bundle importer is looser:
`resolve_entry_threat_community()` (`services/library_bundle_import.py:172-176`) lets a present `threat_community` key win silently over a legacy
`threat_actor_type` key in the same entry rather than rejecting the row as
ambiguous — the shipped seed library JSON deliberately carries both on every
entry for back-compat — whereas the scenario importer rejects that same
combination outright; both importers' resolved slug is still checked
against the published set before acceptance. The **legacy column is never
exported**:
`scenario_export.py`'s `CSV_EXPORT_HEADERS` is built directly from the
canonical `CSV_HEADERS` list (`scenario_export.py:64`), which has no
`threat_actor_type` entry; `library_bundle_export.py`'s `EXPORT_FIELDS`
explicitly excludes `LEGACY_SEED_FIELDS` (`library_bundle_export.py:41`);
and the third export path, the library catalog's own `library.csv` download,
hardcodes its header list with `threat_community` and no legacy column
(`routes/library.py:183`) — a
round-tripped export always re-imports through the canonical slug path, never
the legacy one, so legacy-format acceptance is import-only, one-directional,
and shrinking (back-compat for bundles exported before P1), not a live
dual-write surface.

**Export — download header (C9, 2026-09)**: every download in `src/idraa`
(CSV, scenario JSON, library bundle, PDF reports, the verification workbook, samples export, the four
template/sample downloads) builds `Content-Disposition` through one helper,
`attachment_disposition()` (`utils/download.py`), which quotes the name and
replaces `"`, `;`, `\` and every control character. The scenario JSON and
library-bundle exports used to interpolate `filename` raw (safe only because
every caller passed a literal or a UUID). A guard in
`tests/unit/test_csv_export_helper.py` fails on any hand-built
`"Content-Disposition"` key in the source tree.

**Export**: CSV/XLSX formula-injection guarded by single-quote-prefixing any
cell starting with `=+-@\t\r` (`utils/csv_export.py:33-40`, applied by `csv_response` to every cell and reused by
`services/sample_export.py:68,181`); the XLSX verification workbook has its own superset guard, `_neutralize` (`services/verification_workbook.py:51-69`), which also prefixes a leading `\n` and
legacy `{=...}` array-formula braces. PDF report
strings pass through `rl_escape()` before hitting a reportlab `Paragraph`
(`services/pdf_report.py:25-27,515-517,561` plus the by-threat-community
names/amounts at `services/pdf_report.py:1363,1371`). The TOC/URI fix
(`pdf_report.py:298-321`, `_RunReportDoc.afterFlowable`) re-escapes heading
text a second time before the `TOCEntry` notify call — without it, a
scenario/run name containing markup could smuggle a live `/URI` Action into
the generated table of contents, because `Paragraph.getPlainText()` returns
raw (unescaped) markup even when the input was already escaped once. Both
CSV/XLSX formula injection and the PDF TOC/URI vector have explicit, already
mitigated, code paths — not open findings, but exactly the class of thing a
new export format must re-implement, not assume is "someone else's problem."

## 10. B10 — Run execution / Monte Carlo (resource exhaustion)

- **D (RAM / OOM)**: `mc_iterations_max` (`config.py:65-81`, default
  1,000,000, env `MC_ITERATIONS_MAX`) is enforced server-side in the one service gate both dispatch
  routes share (`services/runs.py:144-153`) — `POST /analyses` re-checks it (`routes/runs.py:1240-1251`), the legacy `POST /scenarios/{id}/run` adapter (`routes/runs.py:1284`) relies on the service gate alone — and the HTML form's `max=`
  attribute (`templates/analyses/new.html:173`) is explicitly documented
  in-code as client-side sugar only. Two GLOBAL (not per-org) concurrency caps
  bound simultaneous in-flight runs, since RAM and the DB connection pool are
  shared across all orgs on one VM (`config.py:253-295`): high-fidelity
  (≥250k effective-iteration, i.e. iterations × scenarios) runs to
  `max_concurrent_high_fidelity_runs` (default 2), and standard (sub-250k)
  BACKGROUND runs to `max_concurrent_standard_runs` (default 8). A standard
  (sub-250k) sub-1000-iteration INLINE run is intentionally UNGATED — its engine
  phase is still small and self-limiting (sub-second / few-MB), and gating such
  runs would put the unindexed candidate-count query on the latency-sensitive
  inline path. Since #299 an inline run is not sub-second end to end: it WAITS for
  any attribution ahead of it (the one-at-a-time slot in the attribution-memory
  bullet below), so the request can sit for minutes behind a background run's
  attribution, holding no pooled connection while it waits.
  Memory-cleanup pattern in `services/run_executor.py` — staged deletes across
  the run pipeline (`del enhanced` at `services/run_executor.py:2549`; `del calculator, fc_controls`
  at `services/run_executor.py:2737` and again at `services/run_executor.py:2933`; `del per_scenario_inputs` at `services/run_executor.py:2934`;
  `del aggregate` at `services/run_executor.py:2752`; `del results_payload` at `services/run_executor.py:2967`), with
  explicit `gc.collect()` passes after `del aggregate` and before the samples split (`services/run_executor.py:2753`, `services/run_executor.py:2953`) — breaks numpy reference
  cycles before SQLite serialization; comments tie this directly to a prior
  OOM incident (issue #211).
- **D (connection-pool exhaustion)**: the Monte-Carlo / Shapley / ensemble
  compute runs inside `asyncio.to_thread`, and the executor's cancel-checks
  plus scenario/control loads autobegin an uncommitted transaction — so absent
  a release a run would PIN its pooled DB connection for the whole compute
  window. A burst of concurrent runs would then drain the 15-slot pool (size 5
  + overflow 10 — SQLAlchemy's pool defaults, which `db.py` leaves unset), and every other request — INCLUDING `/login` — 500s
  after the 30s pool timeout. This is the SAME DoS shape as the 2026-06-15
  reports.py production outage (`routes/reports.py:217-229,329-347`). Control
  (A1 / #508 Part 1): `_release_conn_for_compute` (`run_executor.py:2119`) runs
  `session.expunge_all()` + `await session.close()` to return the connection to
  the pool BEFORE each compute-offload `to_thread` (8 sites) plus once before the
  on-loop split/encode in the AGGREGATE terminal window (9 releases total); the
  next DB op autobegins a fresh transaction. `expire_on_commit=False` (`db.py`) keeps
  already-loaded scalar columns readable on the now-detached ORM objects, so no
  run holds a pooled connection across heavy off-loop work. The
  `max_concurrent_*` caps above additionally bound how many runs can be
  mid-compute at once. Note the standard cap is a GLOBAL reject-not-queue gate,
  so it can 503 a run from an org whose own analysts are driving all 8 in-flight
  standard runs (fail-closed, far likelier to hit in normal use than the
  high-fidelity cap of 2).
- **D (Argon2 executor isolation, A3, 2026-08-15).** Argon2 password/recovery
  hashing runs on a DEDICATED `ThreadPoolExecutor` (`_HASH_POOL`, the
  `services/auth.py` pool cited in §3 (B2), size `argon2_max_threads` default 4), NOT the
  default event-loop executor that the Monte-Carlo `to_thread` computes above
  saturate. Without this isolation, offloading Argon2 (A3) would have made a
  login queue behind up to ~10 concurrent multi-second run computes on the
  6-thread (2-vCPU) default pool — and an auth flood starve runs — trading A3's
  event-loop block for a cross-boundary starvation DoS. New RAM term: each
  concurrent Argon2 verify holds ~64 MiB (`m=65536`), so the pool adds
  `argon2_max_threads × 64 MiB` ≈ 256 MiB transient at the default, atop the MC
  budget (`config.py:275-279`) — fits the 4 GB VM; re-derive with the MC caps if
  the VM shape changes.
- **D (inputs that size the sampler and the wizard fit, #256).** Two caps bound
  user-supplied inputs before they reach the engine or the elicitation fit; both
  predate the 2026-08-05 audit and were simply never listed here. (1) **σ cap:** a
  lognormal σ above 10 (`_SIGMA_MAX`, `services/fair_cam_validation.py:44-47`, "a
  user-controllable OOM/DoS path to the engine sampler") is refused for a scalar
  lognormal (`services/fair_cam_validation.py:156-157`) and for every mixture
  component (`services/fair_cam_validation.py:206-210`); a mixture also carries 1 to
  `max_smes_per_fieldset` components (`services/fair_cam_validation.py:168-173`).
  The check sits in `validate_fair_distributions`, which every distribution write
  path calls — form create and update, wizard save, scenario and library-bundle
  import, loss pinning, library overrides. (2) **SME-row cap:**
  `max_smes_per_fieldset` (`config.py:180-184`, default 20, env
  `MAX_SMES_PER_FIELDSET`) bounds the SME-estimate rows per fieldset through one
  shared validator (`schemas/wizard_step3.py:52-58`) that both fieldset models call;
  the wizard applies it on every step-3/4 POST (`routes/scenarios.py:2707-2708`) and
  again before finalize (`routes/scenarios.py:3255`). The finalize fit is further
  bounded per fit (`quantile_fit_maxiter` 200, `config.py:188`;
  `quantile_fit_wall_clock_ms` 500 ms, `config.py:193`) and per call
  (`finalize_wall_clock_ms` 5000 ms, `config.py:196`). The row schema does not
  itself reject a non-finite `high` (#141, open).
- **D (disk).** A dispatch is refused while the DB volume has less than
  `min_free_disk_bytes` free (`config.py:241`, default 300 MB;
  `services/runs.py:155-168`), the 2026-06-29 full-disk outage class; per-iteration
  samples are reclaimed by the operator-set retention purge
  (`retention_sample_purge_days`, off by default). Check-then-dispatch with no byte
  reservation: it stops sequential accumulation, not a concurrent burst nor one run
  whose samples outgrow the space left. This is the disk guard §11's C1 note means.
- **D (export and report compute).** The raw-samples export runs one at a time
  process-wide (`_SAMPLES_EXPORT_GUARD`, `routes/runs.py:401`; a concurrent request
  gets 429), and the verification workbook builds its in-Excel Monte Carlo for at
  most K = 15 scenarios with per-run N ≤ 50,000 and ΣN ≤ 150,000
  (`config.py:86-108`). Both, like the PDF report, sit behind EXPORTS step-up and
  the per-user export rate limit at the §11 bulk-export choke point.
- **Attribution memory (issue #299):** attribution (Shapley, leave-one-out and the
  weight-robustness ensemble) is the memory-heavy phase of a run and its footprint does not
  scale with iterations, so it is bounded apart from the iteration caps above. (1) The
  attribution composition caches are entry-capped at `MAX_COMPOSITION_CACHE_ENTRIES`
  (`services/run_executor.py:777`, 75,000), per cache; past the cap a miss recomputes the
  value instead of storing it, so results are bit-identical and only speed changes (the
  in-code sizing is about 315 MB per cache at 25 players or fewer and about 555 MB at the
  64-player cap). The ensemble's per-(scenario, subset) draw-vector cache is cleared on
  entering each scenario, and a SINGLE run frees its canonical cache before the ensemble
  starts. (2) One attribution phase runs at a time per process: a process-wide asyncio lock,
  one per event loop (`services/run_executor.py:848`), that a run enters only after returning
  its pooled connection, so a queued run holds none. Like the samples-export guard above,
  this is a single-process invariant; running more than one machine would need a shared
  limiter. (3) Inline runs (under 1000 iterations, ungated by the concurrency caps above)
  therefore wait for any attribution ahead of them, as the first bullet of this section says.
- **CLAUDE.md drift flag — RESOLVED 2026-08-05.** The original sweep found
  CLAUDE.md's "Production deploy + operational envelope" section claiming
  `VM size: shared-cpu-1x / memory_mb = 2048` while `fly.toml:91-94` had
  actually read `performance / 2 cpus / 4096mb` since 2026-06-29 (PR #428/
  #429) — a ~5-week drift. **CLAUDE.md carries the corrected figures** (it is operator-local, untracked since 2026-07-23);
  the inline "Corrected 2026-08-05" note it gained then has since been removed. Kept here (rather than
  deleted) because the underlying coupling still matters: `config.py:71-78`
  ties the 1,000,000-iteration cap directly to the 4GB headroom (~700MB peak
  RSS at N=1M/M=30), so if the VM shape is ever downgraded, that cap must be
  re-benchmarked, not just inherited. This document defers to `fly.toml` as
  ground truth, per its own "Source of truth is `fly.toml`" convention.

## 11. Audit logging (repudiation coverage)

`AuditLog` (`models/audit_log.py:36-64`, indexed on `(org, timestamp)` and
`(entity_type, entity_id)`) is written via `AuditWriter.log`
(`services/audit.py:140-168`), which JSON-safe-coerces Decimal/UUID/
datetime/Enum. Confirmed call sites at login success (`routes/auth.py:262`), failed
login and lockout (`routes/auth.py:188` `user.login_failed`, `routes/auth.py:198`
`user.login_locked_out`), role change (dict built at `routes/users.py:317`,
logged at `routes/users.py:362` under the generic `"update"` action — not a role-specific
action string), and the
request-driven run lifecycle — create (`services/runs.py:414`), cancel (`services/runs.py:468`),
delete (`services/runs.py:528`), sample-purge (`services/runs.py:561`); the executor and reaper add start/complete/fail/reap — plus bulk export via its
own rate-limit-then-audit choke point
(`services/audit.py:171-252`). Emails redacted to `*****@domain` — the domain is kept; hash-based redaction is #283
(`redact_email`, `audit.py:95-116`; the two user-create sites that logged a
raw email — `routes/users.py` invite and `routes/setup.py` first admin — were
fixed in 2026-09 (advisory C4) and an AST guard,
`tests/unit/test_audit_email_never_raw.py`, now fails on any email-keyed
`changes=` literal not passed through `redact_email` — a tripwire: it does not
see a `changes` dict assembled in a variable first). **Accepted residual:**
user-create rows written BEFORE the C4 fix still hold the raw email — including
for users since hard-deleted, whose own `user.delete` row is redacted, so the
old create row is then the only place the address survives. They are not
rewritten automatically, because rewriting audit history undermines its
tamper-evidence; an erasure request for such a user needs a manual, audited
redaction of that one row. Financial values are NOT redacted: the SME-estimate row stores the raw low/high
next to an order-of-magnitude bucket (`bucket_amount`, `audit.py:119-137`; both at `services/wizard_finalize.py:759-762`). 70+ call sites spot-
checked across controls/runs/scenarios/users all logged correctly; **not**
verified as an exhaustive per-route coverage matrix — flagged as a known
gap, not a finding of an actual miss.
The operator CLI `python -m idraa auth reset-mfa` (§3) writes its `user.mfa_admin_reset` and
`user.sessions_revoked` rows with `user_id` and `ip_address` NULL and `"via": "cli"`: those rows show
that an operator acted, not which one.

**Detection hardening (C1/C2, 2026-08-09).** Two blind spots closed:
- **C2** — every failed password attempt by a known, unlocked user now writes
  a `user.login_failed` row (the `routes/auth.py` call cited above), not only the attempt that trips
  the lockout. A low-and-slow campaign staying under the threshold (or running
  with lockout disabled, `auth_max_failed_logins=0`) is no longer invisible.
  Mirrors the `/login/mfa` path's per-attempt audit. **Row-count bounds, in
  order:** the per-account lockout when enabled (`auth_max_failed_logins`,
  default 5); the per-source IP throttle when enabled
  (`auth_ip_max_failed_logins`, default 20); and — independent of BOTH, so it
  still holds on a self-hosted deploy that disables them — a hard per-account
  ceiling `_FAILED_LOGIN_AUDIT_CAP` (50, `routes/auth.py:101,183`). The ceiling matters
  because the first N misses ARE the detection signal; past N, more rows add
  only disk cost. Unknown emails still write nothing (no user to attribute to;
  no enumeration oracle).
- **C1** — RBAC denials now emit a `rbac_denied` WARNING at the `require_role`
  chokepoint (§6, the `rbac_denied` log call in `deps.py` cited there). Deliberately a log line, not an
  `AuditLog` row: an unauthenticated/cross-org prober must not be able to drive
  unbounded DB writes (that would compound §10 / the disk-guard). The
  complementary gap — a request-level access log carrying user identity on
  cross-org `*_for_org` **404s** — is NOT closed here (those 404s are raised at
  many scattered call sites with no shared user-carrying chokepoint); it is
  tracked as a follow-up (§12).

**New action (B5, 2026-09): `user.webauthn_challenge_replayed`.** Written when
a verified WebAuthn assertion arrives for a challenge that was already
consumed — the forensic signal of the replay B5 closes (`changes.surface` =
login / stepup / register). **Write-amplification bound:** it needs a valid
captured assertion (random submissions fail signature verification first), and
it is self-limiting per challenge: `audit_replay_once` flips
`replay_audited` with a guarded UPDATE (`rowcount == 1`), so each challenge
yields at most ONE such row however often it is replayed. On step-up, every
replay additionally writes the existing `user.step_up_failed` row (first replay
= two rows, later replays = one each), bounded by the per-source step-up
throttle when a client-IP trust strategy is configured (without one the
throttle no-ops, as for every other step-up failure reason — a replay still
needs the victim's session plus a captured assertion).

**New action (B6, 2026-08-15): `user.recovery_code_claim_lost`.** Written by
`verify_totp_or_recovery` (`services/second_factor.py:159-172`) when a submitted
recovery code hash-matched an unused row at SELECT time but LOST the atomic
claim (`rowcount==0`) — i.e. a concurrent request burned it first. This is the
forensic signal of the exact double-redemption the B6 fix defends against.
**Write-amplification bound:** it fires ONLY on a hash-match, so it requires
possession of a valid recovery code (random guesses never match — 2⁴⁰ space);
per burst it is bounded by `argon2_max_threads` (4) and by the route's attempt
limits, which differ. `/login/mfa` consults the per-source `LoginAttempt`
throttle before verifying (when a client-IP trust strategy is configured), bounces
a locked account, and counts every miss, a lost claim included, against the
per-account lockout and the source (`routes/auth.py:281-323`). The step-up
code/password route has no per-source throttle and is bounded by the per-account
lockout alone: it bounces a locked account before verifying, with no audit row,
and counts each miss (`routes/step_up.py:138-148`; §3). It is also self-limiting —
once the code is burned, the
`used_at IS NULL` SELECT filters it, yielding a clean `None` with NO further
`claim_lost` row (regression-pinned by
`tests/services/test_recovery_code_atomicity.py::test_already_consumed_code_rejects_cleanly`).
A lost claim therefore emits this row PLUS the caller's `user.login_mfa_failed` /
`user.step_up_failed` — two rows by design.

**New action (threat-agent library P1, 2026-10): `scenario.threat_community_confirmed`.**
`changes` carries `threat_community` `[old_slug, new_slug]`,
`threat_community_provenance` `[old, "assigned"]` and `row_version`. The
create audit (`scenario.create`) now carries `threat_community` and
`threat_community_provenance` as `[None, value]`. Provenance leaves a review
state only through an analyst-visible choice: the confirm route; an edit-form
save made with the review notice shown; or a wizard save after an active
step-2 selection (the wizard seeds a blank community for a flagged scenario
and finalize refuses a draft without one) — both saves audited as
`scenario.update` with a `threat_community_provenance` diff. The migration
backfill writes no audit rows (the `vuln_framing` precedent). Write
amplification: analyst/admin + CSRF; at most one confirm row per scenario,
because an already-`assigned` scenario returns 409 with no audit — absent a
concurrent race (SQLite; accepted, see the service docstring at
`services/scenarios.py:815-819`: two in-flight confirms can both pass the
review check, so the later one overwrites or fails `BUSY`, and both writes
are audited).

**New action (#199 slice 1, 2026-10): `run.flagged_stale`.** One deploy-gated
writer, not request-reachable: migration `c4d2e8f1a6b9` writes one `run.flagged_stale`
row per COMPLETED run it marks stale (`user_id` NULL; `changes.reason` names the engine
change), bounded by the run count and idempotent (already-stale rows are skipped).

**New action (#299, 2026-10): `control.org_wide_sweep`.** Written by `_org_wide_sweep`
(`services/controls.py:129`) when a control create, update or soft delete changes the
EFFECTIVE org-wide set (published, operating and flagged org-wide): a flag, status or stage
flip, or an assignment or Correcting Misaligned Decisions link edit on a control that is
effective now. Every COMPLETED run in the org is flagged stale, because an org-wide control
changes every scenario's composition, not only the linked ones. `changes` carries
`runs_flagged_stale` `[None, count]` and `trigger` `[None, name]`, the name one of
`create`, `flag`, `status`, `stage`, `assignments` or `delete`. A library re-sync of an
effective org-wide control sweeps the whole org inside its own `control.resync_from_library`
row (its `runs_flagged_stale` count) and writes no separate sweep row; a save that only
re-stamps confirmations changes nothing and writes nothing. Write amplification: it sits
behind the existing control-write RBAC and CSRF, is at most one row per qualifying save, and
the sweep is idempotent and org-scoped (already-stale runs are skipped; only id, status and
`is_stale` are loaded).

**Changed row (#299, 2026-10): `risk_analysis_run.create`.** `changes` now also carries
`inherited_control_ids_per_scenario` (`services/runs.py:364`, the SINGLE `audit_changes` dict;
the AGGREGATE dict carries the same key), the frozen org-wide inheritance map as plain
hyphenated UUID strings, one key per scenario in the run. The run column is the source of
truth and the audit row is its immutable echo; the list repeats per scenario by design, so it
is bounded by the run's scenario and control counts. A run created before the feature has no
such key in its audit row.

## 12. Known gaps / follow-ups

None of these are active vulnerabilities as of this sweep — they're the
places where the *control* depends on convention/review rather than an
automated gate, which is exactly what the security-auditor persona (§13, and
`CLAUDE.md` → Review ceremony → Security-auditor persona) exists to keep
watching:

1. Org-scoping discipline (§7) is **gate-enforced as of 2026-08-05**
   (`scripts/lint_org_scoped_lookups.py`) for its one specific shape (bare
   `.get(Model, id)`), with a function-wide org-check search not bound to the flagged call (#151, open); still convention-only for any other shape a future
   lookup might take. Allowlist currency is gate-enforced since 2026-10-01 by the parity test cited in §7.
2. Audit-logging coverage (§11) is spot-checked, not exhaustively matrixed.
3. ~~`CLAUDE.md`'s role list (§6) and VM-envelope figures (§10) are stale~~ —
   **CLOSED 2026-08-05**: both corrected in CLAUDE.md the same day, each with
   an inline "Corrected 2026-08-05" note (both notes since removed from the now operator-local file) recording the stale value (the VM
   note dates the drift to ~5 weeks; the role note records the duration as
   unknown). See §10's and §6's resolved drift flags. Note the first closure
   attempt carried the pre-audit VIEWER undercount ("2 routes") into
   CLAUDE.md's own correction note — fixed to 7 in the same re-audit, which
   is the kind of thing only a cross-document check catches.
4. `fly.toml:4`'s `uat-deploy.yml` comment (§2) references a workflow file
   that doesn't exist — cosmetic, low severity. Still open (re-confirmed by
   the 2026-08-05 re-audit).
5. ~~`REVIEWER` (§6) functions almost entirely as a read-only allowlist member~~
   — **CLOSED 2026-10-23**: the owner kept the reviewer role read-only by design;
   promotion stays analyst/admin (#51; help copy #285). Not a defect.
6. The `uat_basic_auth` boundary (§2a) was **entirely absent** from this
   document's first version — found only by the 2026-08-05 full re-audit, not
   by any per-PR review. Nothing automated enumerates the middleware stack
   against this doc's boundary list, so a future middleware could go
   undocumented the same way. §13's cadence is the only control.
7. **Cross-org 404 access logging (C1 partial, 2026-08-09).** RBAC 403s now log
   (§6/§11), but a cross-org `*_for_org` **404** — the IDOR-probe signal — still
   leaves no request-level trace carrying user identity. A proper fix is a
   request-access-log middleware (bounded, authenticated-only, to avoid
   amplifying anonymous asset 404s), out of scope for the C1/C2 cheap-win batch
   because it changes the middleware stack. Tracked in GHSA-46jj-823j-mjj9's open checklist (C1).
8. ~~**Non-atomic per-account lockout counter**~~ — **CLOSED 2026-08-15.**
   `register_failed_login` (`services/auth.py:393-434`) was a plain
   read-modify-write (`user.failed_login_count += 1`) that lost increments under
   concurrency (~1 of 5 retained). It now does a guarded
   `UPDATE users SET failed_login_count = failed_login_count + 1` with a `CASE`
   lock and `RETURNING` (reflected via `set_committed_value` under
   `synchronize_session=False`), so N concurrent misses each count and the lock
   trips at the true threshold (verified 5-of-5 vs the old 1-of-5). See §3
   D/brute-force. `reset_login_throttle` stays a blind set (not a
   read-modify-write, mutually exclusive with the failure path). The deferred
   follow-up that this document previously named is done.

9. **CSRF: pre-login forms share one anon binding (GHSA-46jj-823j-mjj9 B4 residual, 2026-09).**
   `/login`, `/login/mfa`, `/login/passkey/*` and `/setup` bind to the fixed
   anon value, so an attacker-minted anon token is valid there: login CSRF is
   not prevented, and those forms rest on SameSite=Strict + host-only cookies
   alone. Follow-up: rename the cookie `__Host-csrf_token` in prod (blocks
   subdomain cookie tossing outright; every client/test reads the name, so it
   is its own change), and optionally bind `/login/mfa` to the MFA-pending
   cookie.
10. **CSRF: a leaked token lives as long as its session (GHSA-46jj-823j-mjj9 B4 residual).** It is
    bound to one session cookie but not rotated, so a leak is usable for that
    session's 14-day absolute TTL. A forced reload on the first POST after a
    session change discards typed-but-unsaved values on that page (drafts are
    server-side and intact); a one-shot "session changed" notice is a UX
    follow-up.
11. **Out of scope: developer tooling.** Two developer-machine tools send excerpts of this
    repository and review text (claims, findings) to the TypeSafe API: the curation checker
    (`scripts/curation_check/`) and the review clerk (configured by `clerk.toml`; see
    `.clerk/README.md`). Unpublished head-commit content leaves the machine through the clerk's
    judge only on a run given `--external` (`clerk.toml` sets `allow_external_judge = "per-run"`);
    embargoed work and every plan-gate run `--no-external`, which refuses `--external`; the
    curation checker has no `--no-external` switch: a live run (`--judge jev`, the default) sends
    the working tree's library entries, unpushed edits included, and `--judge replay` sends
    nothing. The clerk reads the
    API key only from the OS keyring (macOS Keychain; on Linux the secret service); the checker
    from the Keychain or, off macOS, from a per-command environment variable. CI never
    holds the key (pinned by `tests/arch/test_workflows_no_secrets.py` since 2026-10-23), and neither tool is a boundary of the running application. Listed so the
    quarterly re-audit knows the flows exist.
12. **Merge-gate integrity (#277, open).** Every test this document names as a pin
    blocks a merge only through `ci-success`, which fails only when a needed job's
    result is `failure` or `cancelled` (`.github/workflows/ci.yml:214-216`). A needed
    job that gains `if:` or `continue-on-error`, or a step ending in `|| true`,
    reports `skipped`/`success` and silently stops gating — including §13's
    citation-freshness test (in the `gate` job) and, since 2026-10-23 (#158), `dast`.
13. **WebAuthn login: an inactive user's passkey use advances the counter with no
    audit row (C3 in GHSA-46jj-823j-mjj9, open).** `login_passkey_verify` writes
    `sign_count`/`last_used_at` before its active-user check, and the inactive
    branch returns without an `AuditWriter` call (`routes/auth.py:434-440`); the
    request-end commit persists the counter. Low: the account is already inactive.

## 13. Keeping this document current

This is a living document, kept current three ways.

**Reactive (per-PR).** `CLAUDE.md` → Review ceremony → Security-auditor
persona (operator-local, as is the `security-auditor` skill it loads) requires the security-auditor role to check, at every milestone
PR-gate and whenever a PR touches a boundary listed in §2–§11, whether this
document needs updating — and to include that update in the same PR rather
than deferring it; since #262 every design spec's Risk-assessment table names the boundaries it changes, checked at the plan-gate. A boundary section whose file:line citations no longer
match the code is itself a finding.

**Periodic (full re-audit).** The reactive pass alone is provably
insufficient — the **first** full re-audit (2026-08-05, same day the doc
was written) found: an entire boundary missing (§2a, `uat_basic_auth`, the
outermost layer in the request path); the §1 diagram ordering B2 before B3
when the wire order is the reverse; a citation pointing 18 lines past a
file's EOF; two independently miscounted claims (step-up call sites, VIEWER
routes); and several citations silently invalidated by a *later, unrelated* PR
that inserted comment lines earlier in a cited file. None of those were the
kind of thing a per-PR review would surface, because none of them were "a
PR touched this boundary."

Cadence: **a full re-audit at least quarterly, and after any PR that adds or
removes middleware, an auth mechanism, or a route-gating dependency.** The
method that works (used for the 2026-08-05 pass): three parallel reviewers
split §1–§5 / §6–§8 / §9–§13, each instructed to open every cited file:line
and independently re-derive every counting claim, rather than confirming the
doc's own prose. Findings are then applied as one consolidated pass.

Practical note for re-auditors: the highest-yield check is not "is this
mechanism still correct" (they usually are) but "did an unrelated edit to a
cited file shift the line numbers." Insertions early in a long file
invalidate every later citation into it silently and en masse.

**Second full re-audit (2026-10-23).** Run by the method above, in the same split: three parallel
reviewers over §1–§5 / §6–§8 / §9–§13, each opening every cited file:line and re-deriving every
counting claim from the tree. Cited ranges checked: 81 + 60 + 87 (HOLDS 75 / 58 / 85, DRIFTED 1 / 0 / 0,
WRONG 5 / 2 / 2, DEAD 0 / 0 / 0). The findings were applied as one consolidated pass (#263). The two
highest-yield classes this time were counting claims gone stale after later PRs, and mechanisms with
no entry at all (the `reset-mfa` factor reset in B2, the σ and SME-row caps in B10; both now documented).
Last full re-audit: 2026-10-23; the next is due 2027-01-23.

**Automated (citation freshness, 2026-10).** The backticked `file:line` citations in this
document and in `docs/reference/fair-departures-register.md` are compiled by
`scripts/clerk_manifests.py` into `.clerk/manifests/citations.json`, one entry per cited
range, each pinned to the text of its first substantive line, on which the citation starts.
`tests/scripts/test_clerk_manifests.py::test_committed_manifest_is_fresh` fails the merge gate
when a cited anchor line moves or a citation is edited without regenerating. The generator
refuses to re-anchor code that moved under an unchanged citation when that anchor text is no
longer anchored as often in the same document and file, unless the author names the site with
`--accept-drift`, recorded as a line in the commit and PR bodies; it accepts a re-cite collision (a
correct re-cite whose new anchor line held another citation's anchor); and it refuses a second
citation of the same site within one document, so each site is cited once per document and later
mentions refer back to it in prose. What this pins: the first anchor line of every cited range.
What it does not: lines inserted, deleted or edited after the anchor inside a range, and where a
range ends; which occurrence an anchor means when its line recurs in the same file (45 do today;
`--verbose` lists them), so a sibling block inserted, deleted or reordered above such an anchor
can leave a stale citation green; cited lines swapped or rotated in the code under unchanged
citations; a stale citation offset by a newly added citation on the moved text (one new citation
can cover a whole chain of stale ones, or an in-place edit when it lands on an identical line, or
copy an old line number while the original is re-cited); a citation re-cited to the wrong line,
which can let a forgotten neighbour pass (the pre-flight accepts these three; the orchestrator's
base-manifest `clerk gate` run reports them, not CI); the prose counting claims; the
meaning of a citation (a correct line can still support a wrong sentence);
citations of `fly.toml` (deployment config, deliberately untracked); citations without a line
number or not written in backticks; anything inside the fenced section 1 diagram. For this
document those remain this section's re-audit scope; for the departures register they are owned
by the per-PR methodology review (the `methodology-reviewer` skill's register checklist), not by
this section's periodic re-audit, which covers this threat model only. A bare file name that
exists in two places (`auth.py`) and a bare continuation (`:NNN`) are refused by the generator,
so every citation here names its file and, where needed, its directory. The drift guard is the
author's pre-flight: a hand-edited manifest, or one regenerated with `--accept-drift`, passes
the CI freshness test; the orchestrator's base-manifest `clerk gate` run and the security
review of the manifest diff are the checks that catch it.
