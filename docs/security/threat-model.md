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

**Status.** First version, drafted 2026-08-05 from a live code sweep (not
carried forward from an earlier doc — none existed). Every claim below cites
a file:line as of that sweep; re-verify before trusting an old citation, per
the project's own "point-in-time observation" convention.

## 1. System overview

```
Internet
   │  TLS terminated at Fly edge
   ▼
Fly.io edge/proxy  ──(B1)──  trusted client-IP header (Fly secret) or XFF hop-count
   │  HTTP, port 8000 only — no other port published
   ▼
Idraa FastAPI process (single VM: performance / 2 cpu / 4096mb, fly.toml:91-94)
   │
   ├─ SessionMiddleware (ASGI-wide, before routing) ──(B2)── unauth ↔ authenticated
   ├─ CSRFMiddleware ──(B3)── state-changing request ↔ verified-origin request
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

Ten labeled boundaries (B1–B10), each taken in turn below under a STRIDE pass.
**S**poofing / **T**ampering / **R**epudiation / **I**nformation disclosure /
**D**enial of service / **E**levation of privilege — only the rows with a real
control or a real residual risk are listed per boundary; blank categories
means "not a meaningfully distinct risk at this boundary; covered by another
boundary's row."

## 2. B1 — Public internet ↔ Fly edge

- **S**: `request.client.host` is spoofable behind Fly's edge and is **never**
  trusted directly (`routes/deps.py:59-66,95-96`). Two opt-in trust
  strategies: a dedicated Fly-secret header (`trusted_client_ip_header`,
  `fly.toml:27-31`) or an N-hop XFF walk (`trusted_proxy_count`,
  `config.py:329-342`). Both unset → the per-IP throttle no-ops rather than
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
  (`services/webauthn_service.py:66-160`) both live; `user_has_strong_factor`
  (`services/mfa_enrollment.py:18-28`) accepts either.
  `EnrollmentGuardMiddleware` (`middleware/enrollment_guard.py:36-54`)
  force-redirects unenrolled users to `/account/security` when the effective
  MFA policy is `required`.

## 4. B3 — CSRF (state-changing request ↔ verified-origin request)

- **T**: stateless double-submit HMAC pattern (`middleware/csrf.py`). Cookie
  `csrf_token` = `<nonce>.<HMAC-SHA256(session_secret, nonce)>`
  (`csrf.py:57,63-81`), `HttpOnly=False` (must be JS/Jinja-readable),
  `SameSite=Strict`. Validation requires cookie present+valid **and**
  header-or-form token to `hmac.compare_digest`-match it (`csrf.py:205-218`);
  mismatch is a generic opaque 403 (`csrf.py:233-244`) — no oracle. **No
  exemption list** — module docstring and inline comments
  (`routes/register_import.py:46`, `routes/library.py:340`) state nothing is
  exempted; fail-closed by design. Non-form JS (WebAuthn) reads a
  `<meta name="csrf-token">` (`templates/base.html:7`) into an
  `X-CSRF-Token` header instead of a hidden form field.

## 5. B4/B5 — Step-up & elevated-action freshness

- **E**: `StepUpCategory` = `EXPORTS | DESTRUCTIVE | ADMIN | CREDENTIALS`
  (`models/enums.py:15-19`). `require_step_up(category)`
  (`routes/deps.py:214-235`) 401s if unauthenticated, else requires
  `now - session.reauthenticated_at <= effective_step_up_window()` (default
  600s, `config.py:329`; per-category admin override,
  `services/security_settings.py:141-160`). ~40 call sites: 16 exports, 9
  destructive deletes, 8 admin/user-mgmt (7 in
  `routes/users.py:110,160,253,373,464,507,554`, 1 in
  `routes/settings.py:162`), 6 credential changes (`routes/mfa.py`). Re-verification
  (`routes/step_up.py:87-239`) has its own throttle and stamps
  `reauthenticated_at`; login itself counts as a re-auth (`auth.py:238`).

## 6. B6 — RBAC (role tier)

- **E**: `UserRole` = `ADMIN | ANALYST | REVIEWER | VIEWER`
  (`models/enums.py:8-12`). `require_role(*roles)` (`routes/deps.py:160-174`)
  403s outside the allowlist; gating is 100% per-route (no
  `APIRouter(dependencies=...)` blanket gate found) — every
  `POST/PUT/PATCH/DELETE` handler across `routes/*.py` was diffed against
  presence of an auth dependency; the only unguarded hits are the
  pre-authentication login routes (`routes/auth.py:116,240,325,333`) plus
  `POST /logout` (`routes/auth.py:402-403`, takes `user: User | None =
  Depends(current_user)` — no-op on an already-logged-out session, correct
  by design) and `/setup` (`routes/setup.py:58`, gated instead by the
  outer `setup_guard` DB-count middleware plus its own `_has_any_user` check,
  `app.py:1144-1167`).
- **Doc-drift flag**: `CLAUDE.md`'s scope-discipline section names three
  roles ("analyst / reviewer / admin"); the code has four
  (`VIEWER` also exists, used in 2 admin-adjacent read-only routes plus a
  4-role allowlist in `scenario_export_routes.py:51`, which deliberately uses
  bare `require_user` — a strict VIEWER-inclusive allowlist would 403 admins
  and analysts, per its inline comment). Not a security defect; CLAUDE.md's
  role list is stale and should be corrected in a separate pass.

## 7. B7 — Multi-tenancy / org boundary (IDOR)

- **I/E**: enforcement is consistent, not incidental. Repos expose only
  `*_for_org(organization_id, ...)` methods
  (`repositories/scenario_repo.py:57-74`, `repositories/run_repo.py:27-41`);
  the codebase follows a "no bare-PK / no existence-oracle" convention —
  cross-org IDs 404, not 403, so lookups don't leak existence
  (`routes/overlays.py:456`, `routes/scenarios.py:713,748`,
  `routes/qualitative_bands.py:224,262`). Spot-checked across
  `qualitative_bands.py`, `library_overrides.py`, `scenarios.py`, `runs.py`,
  `organization.py` — all org-scoped. `routes/scenario_export_routes.py:110`'s
  `db.get(Scenario, scenario_id)` is immediately followed on the next line by
  an explicit `organization_id` equality check, and
  `services/runs.py:473,517` documents one such exemption explicitly
  ("run_id was just org-verified above"). Two spots are weaker than a first
  read suggests, and matter more precisely because of that:
  `services/controls.py:92`'s `get_control()` performs **no org check at
  all** at that line — the guarantee instead lives entirely at its 8 call
  sites in `routes/controls.py` (lines 591, 692, 870, 891, 914, 935, 969,
  1039), each of which checks `control.organization_id != org.id`
  immediately after calling it; `routes/controls.py:696`'s check
  (`assignment.control_id != control_id`) is not an org check either — it's
  transitively safe only because `control` itself was org-verified two lines
  earlier (`:693`). Both are correct **today**, entirely by caller
  discipline.
- **Residual risk**: this discipline is convention-enforced (comments,
  in-repo precedent — see the "Raw-text seed UUID foot-gun" and IDOR-guard
  patterns already recurring in commit history), not type- or ORM-enforced —
  `get_control()` above is the concrete example: a 9th caller that forgot the
  `organization_id` check would be a live IDOR with nothing automated to
  catch it. A new PK lookup that skips the `get_for_org` pattern (or adds a
  bare `db.get` without an inline check) would not be caught by any
  automated check — only by review. The security-auditor persona
  (`CLAUDE.md` → Review ceremony) exists specifically to catch this class at
  the point a new PK lookup is introduced.

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
  not a subset. `tojson`-in-single-quoted-attribute and
  verbatim-echo-then-autoescape paths were independently fuzzed with
  breakout payloads during the PR #148 review (quote, angle-bracket, and
  attribute-injection strings) — all neutralized.
- **Template injection (SSTI)** — the distinct, more severe class: attacker
  text becoming the template *source* (`Environment.from_string()`,
  `Template(user_text)`, `render_template_string`), not just a substituted
  variable. Swept the full `src/` and `fair_cam/` trees: **zero application
  call sites** of `from_string(`, `Template(`, or `render_template_string`.
  The only textual hit is an explanatory comment in `app.py:99-104` about a
  CSRF context-var patch that defensively also covers `from_string` *in case
  it's ever called* — it documents an environment capability, not a used
  one. Every `TemplateResponse` call site uses a literal path string, with
  one exception (`routes/scenarios.py:2246`, the wizard step template) that
  indexes a **fixed 6-element literal list** by an integer bounds-checked to
  `1..6` (`scenarios.py:2236`) — not attacker-controlled text, so it's path
  *selection* among a fixed set, not path or template *construction*.
- **Invariant to protect**: no future code may call
  `Environment.from_string()` / `jinja2.Template()` /
  `render_template_string()` with any request-derived string (a "custom
  report template" or "custom email template" feature would be the natural
  way to reintroduce this class). The security-auditor persona
  (`CLAUDE.md` → Review ceremony) checks new templating code against this
  invariant explicitly.

## 9. B9 — File import / export

**Import** — two parser modules, both size/row-capped and zip-bomb-guarded.
`services/register_import_parsers.py` accepts CSV and XLSX, sniffed by
extension/content-type/zip-magic (`register_import_parsers.py:103-135`).
`services/scenario_import_parsers.py` accepts CSV and JSON only (per its own
module docstring — it has no XLSX path). Guards: 5 MB upload cap via
`Content-Length` (`routes/deps.py:20`; enforced in
`register_import.py:253-257`, `scenario_import.py:123-127`,
`library_import.py:79-83`); a zip-bomb guard on the XLSX path that reads only
central-directory metadata before `load_workbook` — max 200 members / 50 MB
per member (`register_import_parsers.py:57-58,76-100`); a single
`MAX_ROWS = 500` constant (`scenario_import_parsers.py:78`) enforced at
every entry point that accepts row data — CSV (`scenario_import_parsers.py:
243`) and JSON (`:293`) in that module, and re-imported and enforced again
for XLSX (`register_import_parsers.py:174-175`) and CSV
(`register_import_parsers.py:203-204`) in the other; `defusedxml`
auto-substitution blocking XML entity expansion (billion-laughs class,
`register_import_parsers.py:22-30`). Parsed cells are coerced to
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

- **D**: `mc_iterations_max` (`config.py:63-74`, default 1,000,000, env
  `MC_ITERATIONS_MAX`) is enforced server-side at `POST /analyses`
  (`routes/runs.py:1185-1196`) — the HTML form's `max=` attribute
  (`templates/analyses/new.html:149`) is explicitly documented in-code as
  client-side sugar only. A global (not per-org) concurrency cap limits
  simultaneous high-fidelity (≥250k-iteration) runs to 2
  (`config.py:252-265`), since RAM is shared across all orgs on one VM.
  Memory-cleanup pattern in `services/run_executor.py` — staged deletes
  across the run pipeline (`del enhanced` at `:2166`; `del calculator,
  fc_controls` at `:2329` and again at `:2502`; `del per_scenario_inputs` at
  `:2503`; `del aggregate` at `:2343`; `del results_payload` at `:2526`)
  each paired with an explicit `gc.collect()` (`:2344`, `:2512`) — breaks
  numpy reference cycles before SQLite serialization; comments tie this
  directly to a prior OOM incident (issue #211).
- **CLAUDE.md drift flag**: the "Production deploy + operational envelope"
  section states `VM size: shared-cpu-1x / memory_mb = 2048`. Actual current
  `fly.toml:91-94` (verified this sweep): `performance / 2 cpus / 4096mb`.
  `config.py:66-75` ties the 1,000,000-iteration cap directly to the 4GB
  headroom (~700MB peak RSS at N=1M/M=30) — if the VM shape were ever
  downgraded toward CLAUDE.md's stated 2048mb without re-benchmarking, the
  iteration cap would no longer be safely calibrated to the running VM.
  CLAUDE.md's operational-envelope section should be corrected in a separate
  pass (this document defers to `fly.toml` as ground truth going forward, per
  its own "Source of truth is `fly.toml`" convention).

## 11. Audit logging (repudiation coverage)

`AuditLog` (`models/audit_log.py:36-64`, indexed on `(org, timestamp)` and
`(entity_type, entity_id)`) is written via `AuditWriter.log`
(`services/audit.py:140-168`), which JSON-safe-coerces Decimal/UUID/
datetime/Enum. Confirmed call sites at login/lockout
(`routes/auth.py:160-168`), role change (`routes/users.py:314`), and the
full run lifecycle — create (`services/runs.py:328`), cancel (`:382`),
delete (`:442`), sample-purge (`:475`) — plus bulk export via its
own rate-limit-then-audit choke point
(`services/audit.py:171-252`). Emails redacted
(`redact_email`, `audit.py:95-116`), financial values bucketed
(`bucket_amount`, `audit.py:119-137`) before storage. 70+ call sites spot-
checked across controls/runs/scenarios/users all logged correctly; **not**
verified as an exhaustive per-route coverage matrix — flagged as a known
gap, not a finding of an actual miss.

## 12. Known gaps / follow-ups

None of these are active vulnerabilities as of this sweep — they're the
places where the *control* depends on convention/review rather than an
automated gate, which is exactly what the security-auditor persona (§13, and
`CLAUDE.md` → Review ceremony → Security-auditor persona) exists to keep
watching:

1. Org-scoping discipline (§7) is convention-enforced, not type/ORM-enforced.
2. Audit-logging coverage (§11) is spot-checked, not exhaustively matrixed.
3. `CLAUDE.md`'s role list (§6) and VM-envelope figures (§10) are stale
   against the live code/config — correct in a separate, non-security PR.
4. `fly.toml:4`'s `uat-deploy.yml` comment (§2) references a workflow file
   that doesn't exist — cosmetic, low severity.
5. `REVIEWER` (§6) functions almost entirely as a read-only allowlist member
   rather than a fully distinct write-permission tier — confirm this matches
   intended RBAC design; not treated as a defect here.

## 13. Keeping this document current

This is a living document. `CLAUDE.md` → Review ceremony → Security-auditor
persona requires the security-auditor role to check, at every milestone
PR-gate and whenever a PR touches a boundary listed in §2–§11, whether this
document needs updating — and to include that update in the same PR rather
than deferring it. A boundary section whose file:line citations no longer
match the code is itself a finding.
