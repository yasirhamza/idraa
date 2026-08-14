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
from an earlier doc — none existed). **Fully re-audited 2026-08-05** — every
file:line citation re-opened and every counting claim re-derived against the
tree by three parallel reviewers; findings applied in one consolidated pass
(see §13 for what that caught and why the periodic pass exists). Every claim
below cites a file:line as of that re-audit; re-verify before trusting an old
citation, per the project's own "point-in-time observation" convention.

## 1. System overview

```
Internet
   │  TLS terminated at Fly edge
   ▼
Fly.io edge/proxy  ──(B1)──  trusted client-IP header (Fly secret) or XFF hop-count
   │  HTTP, port 8000 only — no other port published
   ▼
Idraa FastAPI process (single VM: performance / 2 cpu / 4096mb, fly.toml:91-94)
   │  Middleware is LIFO-registered; wire order below is outermost-first
   │  (app.py:1110-1112) — B0 and B3 run BEFORE B2 (session lookup hits the
   │  DB, so CSRF/basic-auth reject cheaply before paying that cost).
   ├─ uat_basic_auth (outermost; UAT-hosted only) ──(B0)── no credential ↔ shared HTTP Basic credential
   ├─ CSRFMiddleware ──(B3)── state-changing request ↔ verified-origin request
   ├─ SessionMiddleware (ASGI-wide, before routing) ──(B2)── unauth ↔ authenticated
   ├─ EnrollmentGuardMiddleware ──(B4)── authenticated ↔ MFA-enrolled
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

- **S**: `request.client.host` is spoofable behind Fly's edge and is **never**
  trusted directly (`routes/deps.py:59-66,95-96`). Two opt-in trust
  strategies: a dedicated Fly-secret header (`trusted_client_ip_header`,
  `fly.toml:27-31`) or an N-hop XFF walk (`trusted_proxy_count`,
  `config.py:331-342`). Both unset → the per-IP throttle no-ops rather than
  trusting a spoofable value (`deps.py:117-127`); audit logging falls back to
  best-effort `request.client` instead (`deps.py:130-151`) — a deliberate
  forensic-vs-security-critical asymmetry.
- **D**: boot-time warning fires in prod if the per-IP login throttle is
  enabled with no trust strategy configured (`app.py:977-988`) — misconfig is
  loud, not silent.
- Gap: `fly.toml:4` comments that it's "read on every `fly deploy` (run by
  `.github/workflows/uat-deploy.yml`)" — that workflow does not exist in
  `.github/workflows/` (only `ci.yml`, `dependency-review.yml`). Deploys
  actually run through `./scripts/fly` per project convention; the comment is
  stale. Low severity (comment, not behavior) — worth a follow-up cleanup.

## 2a. B0 — UAT basic-auth pre-gate (hosted UAT only)

**Omitted from the original 2026-08-05 sweep** — caught by the 2026-08-05
full-doc re-audit. This is the app's actual **outermost** request-path layer
(`middleware/uat_basic_auth.py`, wired at `app.py:1174`; confirmed order
`app.py:1110-1112`) — outside even `setup_guard` (which carries no boundary
letter of its own; see §6), B3's CSRF, and B2's session auth. A single
shared HTTP Basic credential gating the hosted UAT
deployment, layered ON TOP of the app's normal `/login` session auth (compromising
the edge credential does not equal compromising the app, per the module
docstring).

- **S/E**: when `UAT_BASIC_AUTH_PASSWORD` is unset (dev/test/local docker),
  the middleware is a pure no-op (`uat_basic_auth.py:74-75`) — this boundary
  doesn't exist outside the hosted UAT runtime. When set, every request
  without a valid `Authorization: Basic` header 401s
  (`_check_auth`, `uat_basic_auth.py:100-134`), except the exact paths in
  `EXEMPT_PATHS` (`:32-47` — `/healthz` plus enumerated PWA install assets),
  and only for `GET`/`HEAD` on those paths (`:71`) — a guard against a future
  POST handler on one of those paths silently inheriting an unauthenticated
  write.
- **T (misconfiguration)**: an empty-string `user` with a real password set
  fails closed rather than matching any caller (`uat_basic_auth.py:79-80`,
  explicit trap comment).
- **S (timing)**: `secrets.compare_digest` on both user and password,
  assigned to locals and AND-ed at the end rather than short-circuited
  (`uat_basic_auth.py:132-134`) — the docstring explains the short-circuit
  form would leak "wrong user" vs. "wrong password" via response timing.
- **Residual risk**: single shared credential (not per-user), by design for
  a UAT gate — compromise of that one credential exposes the whole hosted
  instance to the inner (still-intact) session-auth boundary, not to a
  specific account. The middleware's *presence and position* ARE pinned by
  `tests/unit/test_app_middleware_order.py::test_middleware_wire_order`,
  which asserts the exact 7-element `app.user_middleware` list with
  `uat_basic_auth` outermost — removing or reordering it fails that test.
  What nothing checks is whether THIS DOCUMENT still enumerates the real
  middleware stack (see §12 item 6).

## 3. B2 — Unauthenticated ↔ authenticated (session)

- **S/T**: session cookie `idraa_session`, `itsdangerous.URLSafeSerializer`
  signed (`services/auth.py:23,60-65`); cookie attributes — `httponly`,
  `samesite=lax`, `secure` in prod — set in `set_session_cookie`
  (`auth.py:244-263`). `SessionMiddleware.dispatch` (`middleware/session.py:33-64`)
  unsigns and loads `AuthSession`+`User` before any route runs, ASGI-wide —
  cannot be bypassed per-route (verified while checking B8/HTMX below: every
  fragment handler still resolves through the same dependency graph).
  Absolute 14-day TTL, does not slide (`auth.py:24,225-241,285-301`).
- **S** (credential stuffing): Argon2 password hashing with a precomputed
  dummy-hash timing-safe check for nonexistent/inactive users
  (`auth.py:38-53`) — prevents a login-existence oracle via response timing.
- **D/brute-force**: two independent DB-backed throttles, both fail-open on
  store errors — per-account lockout (5 attempts/900s, `config.py:323-324`;
  `auth.py:307-328`) and per-source `LoginAttempt` throttle (20/900s/900s,
  `config.py:346-348`; `services/login_throttle.py`), applied to both
  `/login` and step-up re-verification (`routes/step_up.py:211,239`).
- **E**: MFA — TOTP (pyotp, single-use-per-step replay guard,
  `services/totp.py:21-55`) and WebAuthn/passkeys
  (`services/webauthn_service.py:66-142`) both live; `user_has_strong_factor`
  (`services/mfa_enrollment.py:18-29`) accepts either.
  `EnrollmentGuardMiddleware` (`middleware/enrollment_guard.py:36-54`)
  force-redirects unenrolled users to `/account/security` when the effective
  MFA policy is `required`.

## 4. B3 — CSRF (state-changing request ↔ verified-origin request)

- **T**: stateless double-submit HMAC pattern (`middleware/csrf.py`). Cookie
  `csrf_token` = `<nonce>.<HMAC-SHA256(session_secret, nonce)>`
  (`csrf.py:64,105-123`), `HttpOnly=False` (must be JS/Jinja-readable),
  `SameSite=Strict`. Validation requires cookie present+valid **and**
  header-or-form token to `hmac.compare_digest`-match it (`csrf.py:271-291`);
  mismatch is a generic opaque 403 (`_forbid`, `csrf.py:306-317`) — no oracle. **No
  exemption list** — module docstring and inline comments
  (`routes/register_import.py:45`, `routes/library.py:344`) state nothing is
  exempted; fail-closed by design. Non-form JS (WebAuthn) reads a
  `<meta name="csrf-token">` (`templates/base.html:7`) into an
  `X-CSRF-Token` header instead of a hidden form field.
- **Non-ASCII-token hardening (D1, 2026-08-14)**: `hmac.compare_digest` raises
  `TypeError` (not a clean non-match) when either operand carries a
  non-ASCII character, so an attacker-crafted non-ASCII token used to escape
  as an unhandled 500 rather than the intended 403. Both `compare_digest`
  call sites now guard with `.isascii()` first, rejecting as a normal
  verification failure instead: `verify_csrf_token`'s `sig_hex` check
  (`csrf.py:145-151`) covers the cookie-signature compare — this one closes
  an **unauthenticated 500-on-every-request** class, since `verify_csrf_token`
  runs unconditionally near the top of `dispatch` (even on safe GETs) and
  `sig_hex` is never hex-validated before reaching it (unlike `nonce_hex`);
  `dispatch`'s `submitted` check (`csrf.py:280-286`) covers the double-submit
  compare against the form-field-or-header token. Regression:
  `tests/integration/test_csrf_nonascii_token.py`.
- **DoS ceiling (A4, 2026-08-09)**: this middleware buffers the FULL body of
  every unsafe-method request before the route runs (to replay it for
  downstream form parsing — `_CachedRequest.wrapped_receive`). That buffer is
  now size-capped: `_read_body_capped` (`csrf.py:76-102`) reads via
  `request.stream()` under `settings.max_request_body_bytes` (default 8 MB,
  `config.py:368`) and returns a 413 before the whole body is buffered
  (`csrf.py:235-252`). The cap sits ABOVE `MAX_UPLOAD_BYTES` (5 MB,
  `routes/deps.py:23`) + multipart framing so legitimate imports pass; a test
  pins that inequality (`tests/unit/test_csrf_body_cap.py`). Before this an
  unauthenticated 50 MB `POST /login` grew RSS ~50 MB before its guaranteed
  403. Not attacker-authenticated; a single-machine availability hardening.

## 5. B4/B5 — Step-up & elevated-action freshness

- **E**: `StepUpCategory` = `EXPORTS | DESTRUCTIVE | ADMIN | CREDENTIALS`
  (`models/enums.py:15-19`). `require_step_up(category)`
  (`routes/deps.py:230-251`) 401s if unauthenticated, else requires
  `now - session.reauthenticated_at <= effective_step_up_window()` (default
  600s, `config.py:356`; per-category admin override,
  `services/security_settings.py:141-160`). **43** real call sites (37 as of
  the 2026-08-05 re-derivation; +6 from B2 on 2026-08-09; the first-ever sweep
  said "~40 / 16 exports" by counting a docstring example at
  `routes/deps.py:234` and a prose mention at
  `routes/scenario_export_routes.py:20` as call sites): **14** exports, 9
  destructive deletes, **14** admin/user-mgmt (7 in
  `routes/users.py:110,160,253,373,464,507,554`, 1 in
  `routes/settings.py:162`, and the **6 B2 additions** — `POST /organization`,
  `POST /fx-rates`, and the four admin-only SME-directory mutations
  new/edit/archive/unarchive), 6 credential changes
  (`routes/mfa.py:89,129,183,221,248,301`). Re-verification
  (`routes/step_up.py:87-239`) has its own throttle and stamps
  `reauthenticated_at`; login itself counts as a re-auth (`create_session`,
  `auth.py:234`).
- **B2 (2026-08-09)**: `POST /organization` and `POST /fx-rates` mutate inputs
  that feed org-wide FAIR/financial computation (industry, risk_appetite,
  security_maturity, preferred_currency; the FX rates behind every ALE), so a
  stale admin session could previously change them freely while being bounced
  from `/settings/security`. All six are now `StepUpCategory.ADMIN`-gated. The
  count-regression tripwire (`tests/unit/test_step_up_call_sites.py`, now 43)
  is joined by a COMPLETENESS guard (`tests/unit/test_step_up_completeness.py`):
  it introspects the live route table and fails if any admin-only
  state-changing route is neither step-up-gated nor on an explicit exemption
  allowlist (the routine reference-data / multi-step-import surface) — so a NEW
  admin route can no longer ship ungated unnoticed, the exact hole a count
  cannot see.

## 6. B6 — RBAC (role tier)

- **E**: `UserRole` = `ADMIN | ANALYST | REVIEWER | VIEWER`
  (`models/enums.py:8-12`). `require_role(*roles)` (`routes/deps.py:163-190`)
  403s outside the allowlist; the 403 path now also emits a structured
  `rbac_denied` WARNING (actor UUID, role, required roles, path —
  `deps.py:180-186`) so privilege-probing leaves a trace (C1, 2026-08-09; a
  WARNING not a DB `AuditLog` row, to keep it bounded by log rotation rather
  than an attacker-driven write amplifier). Gating is 100% per-route (no
  `APIRouter(dependencies=...)` blanket gate found) — every
  `POST/PUT/PATCH/DELETE` handler across `routes/*.py` was diffed against
  presence of an auth dependency; the only unguarded hits are the
  pre-authentication login routes (`routes/auth.py:116,240,326,334`) plus
  `POST /logout` (`routes/auth.py:404-405`, takes `user: User | None =
  Depends(current_user)` — no-op on an already-logged-out session, correct
  by design) and `/setup` (`routes/setup.py:58`, gated instead by the
  outer `setup_guard` DB-count middleware plus its own `_has_any_user` check,
  `app.py:1144-1167`).
- **Doc-drift flag — RESOLVED 2026-08-05**: `CLAUDE.md`'s scope-discipline
  section named three roles ("analyst / reviewer / admin"); the code has
  four. `VIEWER` is used in **7** read-only routes (re-derived 2026-08-05 —
  the first sweep said "2", an undercount): 4 inline in
  `routes/library.py:96,145,265,387` and 3 via the `_VIEWER_PLUS` allowlist
  (`routes/control_library.py:45`, applied at `:154,210,247`). Separately,
  `scenario_export_routes.py:51` deliberately uses bare `require_user` — a
  strict VIEWER-inclusive allowlist would 403 admins and analysts, per its
  inline comment. Never a security defect, only a doc-accuracy one; CLAUDE.md
  now names all four roles with an inline "Corrected 2026-08-05" note.

## 7. B7 — Multi-tenancy / org boundary (IDOR)

- **I/E**: enforcement is consistent, not incidental. Repos expose only
  `*_for_org(organization_id, ...)` methods
  (`repositories/scenario_repo.py:57-74`, `repositories/run_repo.py:27-41`);
  the codebase follows a "no bare-PK / no existence-oracle" convention —
  cross-org IDs 404, not 403, so lookups don't leak existence
  (`routes/overlays.py:456`, `routes/scenarios.py:713,748`,
  `routes/qualitative_bands.py:224,262`). `routes/controls.py:697`'s check
  (`assignment.control_id != control_id`) is not itself an org check — it's
  transitively safe because `control` was org-verified two lines earlier
  (`:693`).
- **Gate-enforced, not just convention (2026-08-05):** `scripts/
  lint_org_scoped_lookups.py`, run in every local gate + CI `gate` job, ASTs
  every file under `src/idraa/{routes,services,repositories}` and flags any
  bare `<expr>.get(Model, id)` on an org-scoped model with no
  `organization_id` check (or `*_for_org*` delegated-safety call) anywhere
  in the enclosing function. Its first real run found exactly one live gap
  and ten correct-but-undocumented exemptions:
  - **Fixed**: `services/controls.py`'s `get_control()` — the concrete
    example this section used to cite as "no org check at all at that
    line, safe only by caller discipline" — is now `get_control_for_org(db,
    organization_id=..., control_id=...)`, checking internally like every
    other `*_for_org` method. All 8 former call sites in `routes/
    controls.py` dropped their now-redundant duplicate check.
  - **Suppressed with a recorded reason** (`# org-scope: ok — <reason>`,
    same line as the call — the checker enforces a non-empty reason):
    6 sites rebinding the *current session's own* `user.id` across a
    detached-instance boundary (`routes/mfa.py:141,191,259,312`,
    `routes/step_up.py:100,282`); 2 sites resolving a user id from a
    source that's already unforgeable — a server-signed pending-MFA
    cookie (`routes/auth.py:267`) and an already-verified WebAuthn
    credential's owner (`routes/auth.py:382`); 2 sites in
    `services/retention.py:195,237` that are a system-wide background
    sweep by design (iterates every org's rows on a schedule, not a
    per-request handler with an attacker-suppliable id).
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
  "some check exists in this function"). Additionally: a new PK lookup
  that skips both the `get_for_org` pattern AND ends up naming its model
  outside the checker's maintained `ORG_SCOPED_MODELS` allowlist (new
  model, allowlist not updated) would slip through silently, and a
  suppression comment's *reason* is human-asserted, never verified — the
  security-auditor persona (`CLAUDE.md` → Review ceremony) is the actual
  backstop for both: keeping the allowlist current and reading each
  suppression's reason for whether it's really true, not just present.

## 8. B8 — Templating (XSS and template injection)

Two distinct injection classes, checked separately (2026-08-05 sweep,
prompted by a review flag that Jinja2 is a known SSTI vector):

- **Output escaping (XSS)** — every render goes through
  `Jinja2Templates(directory=..., context_processors=[...])`
  (`app.py:94-97`), which Starlette constructs with
  `select_autoescape(["html", "htm", "xml"])`; confirmed live
  (`templates.env.autoescape` is the `select_autoescape` closure) and
  confirmed total — every file under `src/idraa/templates/` is `.html` (zero
  non-html templates exist), so autoescape covers 100% of rendered output,
  not a subset. **Alpine `x-data` is the sharp edge**: an attribute the browser
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
  fails if any dist selector regresses to the raw shape.
- **Template injection (SSTI)** — the distinct, more severe class: attacker
  text becoming the template *source* (`Environment.from_string()`,
  `Template(user_text)`, `render_template_string`), not just a substituted
  variable. Swept the full `src/` and `fair_cam/` trees: **zero application
  call sites** of `from_string(`, `Template(`, or `render_template_string`.
  The only textual hit is an explanatory comment in `app.py:99-104` about a
  CSRF context-var patch that defensively also covers `from_string` *in case
  it's ever called* — it documents an environment capability, not a used
  one. Every `TemplateResponse` call site uses a literal path string, with
  one exception (`routes/scenarios.py:2254`, the wizard step template) that
  indexes a **fixed 6-element literal list** by an integer bounds-checked to
  `1..6` (`scenarios.py:2244`) — not attacker-controlled text, so it's path
  *selection* among a fixed set, not path or template *construction*.
- **Invariant to protect**: no future code may call
  `Environment.from_string()` / `jinja2.Template()` /
  `render_template_string()` with any request-derived string (a "custom
  report template" or "custom email template" feature would be the natural
  way to reintroduce this class). The security-auditor persona
  (`CLAUDE.md` → Review ceremony) checks new templating code against this
  invariant explicitly.

## 9. B9 — File import / export

**Import** — two parser modules, both size- and row-capped; zip-bomb-guarded
only where it applies (the XLSX-capable module — the other has no zip path).
`services/register_import_parsers.py` accepts CSV and XLSX, sniffed by
extension/content-type/zip-magic (`register_import_parsers.py:160-183`).
`services/scenario_import_parsers.py` accepts CSV and JSON only (per its own
module docstring — it has no XLSX path). Guards: 5 MB upload cap via
`Content-Length` (`routes/deps.py:20`; enforced in
`register_import.py:253-257`, `scenario_import.py:123-127`,
`library_import.py:79-83`); a zip-bomb guard on the XLSX path that reads only
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
`register_import.py:234,248,322,358,402,436,519,578,662,736,775,837`; the
`delete_profile` route additionally requires step-up, `:831`) and ~760 MB
transient is
survivable on the 4 GB VM. The non-bypassable tightening (parse
`[Content_Types].xml` to bound buffered parts by role) is deferred as
disproportionate for an admin-only surface; revisit if import ever becomes
non-admin. There is also a single
`MAX_ROWS = 500` constant (`scenario_import_parsers.py:78`) enforced at
every entry point that accepts row data — CSV (`scenario_import_parsers.py:
243`) and JSON (`:293`) in that module, and re-imported and enforced again
for XLSX (`register_import_parsers.py:221-222`) and CSV
(`register_import_parsers.py:250-251`) in the other; `defusedxml`
auto-substitution blocking XML entity expansion (billion-laughs class,
`register_import_parsers.py:29-37`). Parsed cells are coerced to
`str(v).strip()` only — no formula evaluation on import.

**Export**: CSV/XLSX formula-injection guarded by single-quote-prefixing any
cell starting with `=+-@\t\r` (`utils/csv_export.py:26-33`, used by
`services/sample_export.py:68,181` and `services/verification_workbook.py:
51-66`, which also guards legacy `{=...}` array-formula braces). PDF report
strings pass through `rl_escape()` before hitting a reportlab `Paragraph`
(`services/pdf_report.py:24-27,509-511,555`). The TOC/URI fix
(`pdf_report.py:296-319`, `_RunReportDoc.afterFlowable`) re-escapes heading
text a second time before the `TOCEntry` notify call — without it, a
scenario/run name containing markup could smuggle a live `/URI` Action into
the generated table of contents, because `Paragraph.getPlainText()` returns
raw (unescaped) markup even when the input was already escaped once. Both
CSV/XLSX formula injection and the PDF TOC/URI vector have explicit, already
mitigated, code paths — not open findings, but exactly the class of thing a
new export format must re-implement, not assume is "someone else's problem."

## 10. B10 — Run execution / Monte Carlo (resource exhaustion)

- **D (RAM / OOM)**: `mc_iterations_max` (`config.py:63-74`, default
  1,000,000, env `MC_ITERATIONS_MAX`) is enforced server-side at
  `POST /analyses` (`routes/runs.py:1185-1196`) — the HTML form's `max=`
  attribute (`templates/analyses/new.html:149`) is explicitly documented
  in-code as client-side sugar only. Two GLOBAL (not per-org) concurrency caps
  bound simultaneous in-flight runs, since RAM and the DB connection pool are
  shared across all orgs on one VM (`config.py:252-294`): high-fidelity
  (≥250k effective-iteration, i.e. iterations × scenarios) runs to
  `max_concurrent_high_fidelity_runs` (default 2), and standard (sub-250k)
  BACKGROUND runs to `max_concurrent_standard_runs` (default 8). The
  sub-1000-iteration INLINE dispatch path is intentionally UNGATED — such runs
  are sub-second / few-MB and self-limiting, and gating them would put the
  unindexed candidate-count query on the latency-sensitive inline path.
  Memory-cleanup pattern in `services/run_executor.py` — staged deletes across
  the run pipeline (`del enhanced` at `:2224`; `del calculator, fc_controls`
  at `:2392` and again at `:2578`; `del per_scenario_inputs` at `:2579`;
  `del aggregate` at `:2406`; `del results_payload` at `:2611`) each paired
  with an explicit `gc.collect()` (`:2407`, `:2597`) — breaks numpy reference
  cycles before SQLite serialization; comments tie this directly to a prior
  OOM incident (issue #211).
- **D (connection-pool exhaustion)**: the Monte-Carlo / Shapley / ensemble
  compute runs inside `asyncio.to_thread`, and the executor's cancel-checks
  plus scenario/control loads autobegin an uncommitted transaction — so absent
  a release a run would PIN its pooled DB connection for the whole compute
  window. A burst of concurrent runs would then drain the 15-slot pool (size 5
  + overflow 10, `db.py`), and every other request — INCLUDING `/login` — 500s
  after the 30s pool timeout. This is the SAME DoS shape as the 2026-06-15
  reports.py production outage (`routes/reports.py:219-228,332-346`). Control
  (A1 / #508 Part 1): `_release_conn_for_compute` (`run_executor.py:1844`) runs
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
- **CLAUDE.md drift flag — RESOLVED 2026-08-05.** The original sweep found
  CLAUDE.md's "Production deploy + operational envelope" section claiming
  `VM size: shared-cpu-1x / memory_mb = 2048` while `fly.toml:91-94` had
  actually read `performance / 2 cpus / 4096mb` since 2026-06-29 (PR #428/
  #429) — a ~5-week drift. **CLAUDE.md now carries the corrected figures**
  plus its own inline "Corrected 2026-08-05" note. Kept here (rather than
  deleted) because the underlying coupling still matters: `config.py:66-75`
  ties the 1,000,000-iteration cap directly to the 4GB headroom (~700MB peak
  RSS at N=1M/M=30), so if the VM shape is ever downgraded, that cap must be
  re-benchmarked, not just inherited. This document defers to `fly.toml` as
  ground truth, per its own "Source of truth is `fly.toml`" convention.

## 11. Audit logging (repudiation coverage)

`AuditLog` (`models/audit_log.py:36-64`, indexed on `(org, timestamp)` and
`(entity_type, entity_id)`) is written via `AuditWriter.log`
(`services/audit.py:140-168`), which JSON-safe-coerces Decimal/UUID/
datetime/Enum. Confirmed call sites at login success (`auth.py:259`), failed
login and lockout (`auth.py:185` `user.login_failed`, `auth.py:195`
`user.login_locked_out`), role change (dict built at `routes/users.py:314`,
logged at `:359` under the generic `"update"` action — not a role-specific
action string), and the
full run lifecycle — create (`services/runs.py:328`), cancel (`:382`),
delete (`:442`), sample-purge (`:475`) — plus bulk export via its
own rate-limit-then-audit choke point
(`services/audit.py:171-252`). Emails redacted
(`redact_email`, `audit.py:95-116`), financial values bucketed
(`bucket_amount`, `audit.py:119-137`) before storage. 70+ call sites spot-
checked across controls/runs/scenarios/users all logged correctly; **not**
verified as an exhaustive per-route coverage matrix — flagged as a known
gap, not a finding of an actual miss.

**Detection hardening (C1/C2, 2026-08-09).** Two blind spots closed:
- **C2** — every failed password attempt by a known, unlocked user now writes
  a `user.login_failed` row (`auth.py:185`), not only the attempt that trips
  the lockout. A low-and-slow campaign staying under the threshold (or running
  with lockout disabled, `auth_max_failed_logins=0`) is no longer invisible.
  Mirrors the `/login/mfa` path's per-attempt audit. **Row-count bounds, in
  order:** the per-account lockout when enabled (`auth_max_failed_logins`,
  default 5); the per-source IP throttle when enabled
  (`auth_ip_max_failed_logins`, default 20); and — independent of BOTH, so it
  still holds on a self-hosted deploy that disables them — a hard per-account
  ceiling `_FAILED_LOGIN_AUDIT_CAP` (50, `auth.py:98,180`). The ceiling matters
  because the first N misses ARE the detection signal; past N, more rows add
  only disk cost. Unknown emails still write nothing (no user to attribute to;
  no enumeration oracle).
- **C1** — RBAC denials now emit a `rbac_denied` WARNING at the `require_role`
  chokepoint (§6, `deps.py:180-186`). Deliberately a log line, not an
  `AuditLog` row: an unauthenticated/cross-org prober must not be able to drive
  unbounded DB writes (that would compound §10 / the disk-guard). The
  complementary gap — a request-level access log carrying user identity on
  cross-org `*_for_org` **404s** — is NOT closed here (those 404s are raised at
  many scattered call sites with no shared user-carrying chokepoint); it is
  tracked as a follow-up (§12).

## 12. Known gaps / follow-ups

None of these are active vulnerabilities as of this sweep — they're the
places where the *control* depends on convention/review rather than an
automated gate, which is exactly what the security-auditor persona (§13, and
`CLAUDE.md` → Review ceremony → Security-auditor persona) exists to keep
watching:

1. Org-scoping discipline (§7) is **gate-enforced as of 2026-08-05**
   (`scripts/lint_org_scoped_lookups.py`) for its one specific shape (bare
   `.get(Model, id)`); still convention-only for any other shape a future
   lookup might take, and for keeping the checker's model allowlist current.
2. Audit-logging coverage (§11) is spot-checked, not exhaustively matrixed.
3. ~~`CLAUDE.md`'s role list (§6) and VM-envelope figures (§10) are stale~~ —
   **CLOSED 2026-08-05**: both corrected in CLAUDE.md the same day, each with
   an inline "Corrected 2026-08-05" note recording the stale value (the VM
   note dates the drift to ~5 weeks; the role note records the duration as
   unknown). See §10's and §6's resolved drift flags. Note the first closure
   attempt carried the pre-audit VIEWER undercount ("2 routes") into
   CLAUDE.md's own correction note — fixed to 7 in the same re-audit, which
   is the kind of thing only a cross-document check catches.
4. `fly.toml:4`'s `uat-deploy.yml` comment (§2) references a workflow file
   that doesn't exist — cosmetic, low severity. Still open (re-confirmed by
   the 2026-08-05 re-audit).
5. `REVIEWER` (§6) functions almost entirely as a read-only allowlist member
   rather than a fully distinct write-permission tier — confirm this matches
   intended RBAC design; not treated as a defect here.
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
   because it changes the middleware stack. Tracked as a follow-up.

## 13. Keeping this document current

This is a living document, kept current two ways.

**Reactive (per-PR).** `CLAUDE.md` → Review ceremony → Security-auditor
persona requires the security-auditor role to check, at every milestone
PR-gate and whenever a PR touches a boundary listed in §2–§11, whether this
document needs updating — and to include that update in the same PR rather
than deferring it. A boundary section whose file:line citations no longer
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
