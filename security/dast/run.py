"""CI DAST Phase 1 — migrate -> seed -> serve -> login -> fuzz -> teardown.

The single orchestration entrypoint (``main``), invoked via
``python -m idraa.tasks dast`` (see ``src/idraa/tasks/runner.py::_dast``) both
locally and from the advisory ``dast`` CI job.

**Safety invariant (design doc §0): this suite NEVER targets a deployed
environment.** Before doing anything else, ``main`` asserts — via a real
``if``/``raise SystemExit``, not a bare ``assert`` (``python -O`` strips
asserts, which would silently remove this guard) — that the DB it is about
to migrate/seed is an ephemeral local sqlite file and that the app it is
about to serve is bound to ``127.0.0.1``. There is no argument or env var
that can point this orchestrator at a remote host.

Flow, all subprocesses launched as argv lists (never ``shell=True``) with an
explicit ``env`` (inherits the ambient environment for PATH/locale/SSL certs,
but always force-sets ``ENVIRONMENT=test`` / ``DATABASE_URL=<ephemeral>`` /
``PYTHONHASHSEED=0`` so no ambient value leaks in):

1. ``alembic upgrade head`` against a fresh temp-dir sqlite file.
2. Seed one org + one MFA-enrolled admin (``security.dast.seed``, subprocess
   — writes the generated password to a 0600 temp file, never stdout).
3. Extract the filtered OpenAPI schema in-process (``build_schema``) and dump
   it to a temp file.
4. Start ``uvicorn`` as a subprocess, stdout+stderr redirected to a temp log
   file (never ``PIPE`` — an unread pipe deadlocks once uvicorn fills the OS
   buffer).
5. Poll ``GET /healthz`` (~30 x 1s). On timeout OR an early process exit,
   print the captured log and fail — ``/healthz`` is deliberately DB-free
   (see ``app.py``), so it can't itself diagnose a bad migration; the log
   and the login smoke below are what can.
6. Log in via httpx as the seeded admin, capturing the ``idraa_session`` +
   ``csrf_token`` cookies (CSRF here is a stateless double-submit cookie —
   see ``middleware/csrf.py`` — so the same token value drives the login
   POST's ``_csrf`` field, ``DAST_CSRF`` for the hook, and the schemathesis
   ``-H "Cookie: ..."`` header).
7. Non-vacuous pre-fuzz smoke: the authenticated session actually reaches a
   protected page, the extracted schema isn't suspiciously small, and one
   known non-step-up admin POST (``/controls/new``) reaches its handler
   with a manually-injected ``_csrf`` (400/422, not 403).
8. Run ``schemathesis`` as a subprocess against the live app, with
   ``SCHEMATHESIS_HOOKS=security.dast.hooks`` injecting ``_csrf`` into every
   mutating request body.
9. Post-run canary, sourced from the HAR (not JUnit — a 403 passes the sole
   ``not_a_server_error`` check, so an all-403 run from a silently-inert CSRF
   hook would otherwise be a fully green JUnit report with zero signal):
   at least one state-changing (POST/PUT/PATCH) request must have gotten a
   non-403 response.
10. Post-run session-liveness: the same admin session must still reach
    ``/organization`` — a dead session means the fuzz run nuked its own
    coverage partway through.
11. ``finally``: terminate -> wait(timeout) -> kill uvicorn, structured so no
    exception from any step above can skip teardown.

Exit code is schemathesis's own exit code, or ``1`` if any smoke, canary, or
liveness check failed (schemathesis never even ran, or its green run wasn't
trustworthy).

**Never logs the assembled schemathesis argv or ``-H`` header value** — it
carries the live session cookie + CSRF token, and this runs in public CI
logs.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
from security.dast.config import EXCLUDED_PATH_REGEX, MAX_EXAMPLES, SEED_EMAIL

# security/dast/run.py -> security/dast -> security -> repo root.
REPO_ROOT = Path(__file__).resolve().parents[2]

HOST = "127.0.0.1"
PORT = 8000

_HEALTHZ_RETRIES = 30
_STATE_CHANGING_METHODS = frozenset({"POST", "PUT", "PATCH"})


def _wait_healthy(base_url: str, proc: subprocess.Popen[bytes], log_path: Path) -> bool:
    """Poll ``GET {base_url}/healthz`` until it returns 200, or fail loudly.

    Failure paths (timeout, or uvicorn exiting before ever answering) both
    print the captured uvicorn log — ``/healthz`` is DB-free, so a bad
    migration/seed shows up here as a silent non-200, not an exception; the
    log is the only diagnostic.
    """
    healthz_url = f"{base_url}/healthz"
    for _ in range(_HEALTHZ_RETRIES):
        if proc.poll() is not None:
            print(
                f"[dast] uvicorn exited early (rc={proc.returncode}) before /healthz responded",
                file=sys.stderr,
            )
            _print_log(log_path)
            return False
        try:
            resp = httpx.get(healthz_url, timeout=1.0)
        except httpx.HTTPError:
            time.sleep(1.0)
            continue
        if resp.status_code == 200:
            return True
        time.sleep(1.0)
    print(f"[dast] /healthz did not return 200 within {_HEALTHZ_RETRIES}s", file=sys.stderr)
    _print_log(log_path)
    return False


def _print_log(log_path: Path) -> None:
    print(f"[dast] --- uvicorn log ({log_path}) ---", file=sys.stderr)
    try:
        print(log_path.read_text(encoding="utf-8", errors="replace"), file=sys.stderr)
    except OSError as exc:
        print(f"[dast] (could not read uvicorn log: {exc})", file=sys.stderr)
    print("[dast] --- end uvicorn log ---", file=sys.stderr)


def _login(client: httpx.Client, password: str) -> tuple[str, str] | None:
    """GET /login for a CSRF cookie, POST credentials, return (session, csrf) or None.

    The CSRF token minted on the GET is the SAME value ``csrf_field()``
    renders into the login form (both read ``request.state.csrf_token``,
    set by ``CSRFMiddleware`` from the same request) — no HTML scraping
    needed, the cookie value alone is the valid form-field value too.
    """
    get_resp = client.get("/login")
    if get_resp.status_code != 200:
        print(f"[dast] GET /login returned {get_resp.status_code}, expected 200", file=sys.stderr)
        return None
    csrf = client.cookies.get("csrf_token")
    if not csrf:
        print("[dast] GET /login did not set a csrf_token cookie", file=sys.stderr)
        return None

    post_resp = client.post(
        "/login",
        data={"email": SEED_EMAIL, "password": password, "_csrf": csrf},
    )
    if post_resp.status_code != 303:
        print(
            f"[dast] POST /login returned {post_resp.status_code}, expected 303 "
            "(bad seed credentials, or CSRF double-submit mismatch)",
            file=sys.stderr,
        )
        return None

    session_cookie = client.cookies.get("idraa_session")
    if not session_cookie:
        print("[dast] POST /login did not mint an idraa_session cookie", file=sys.stderr)
        return None
    # Re-read: CSRFMiddleware only re-issues the cookie when it minted a
    # fresh one this request (see middleware/csrf.py) — on the POST it
    # reused the inbound (still-valid) cookie, so this is normally the
    # same value as above, but reading it again keeps this correct even if
    # that internal behavior changes.
    csrf = client.cookies.get("csrf_token") or csrf
    return session_cookie, csrf


def _check_get_ok(client: httpx.Client, path: str) -> bool:
    resp = client.get(path)
    if resp.status_code != 200:
        print(f"[dast] GET {path} returned {resp.status_code}, expected 200", file=sys.stderr)
        return False
    return True


def _smoke_admin_post_reaches_handler(client: httpx.Client, csrf: str) -> bool:
    """POST /controls/new with an invalid (empty) body reaches the HANDLER, not a gate.

    ``/controls/new`` is ``require_role(ADMIN, ANALYST)`` with no step-up
    (``routes/controls.py``), and survives ``EXCLUDED_PATH_REGEX`` — so it's
    also in the fuzzed schema, which keeps the post-run HAR canary
    satisfiable. A manually-injected ``_csrf`` with no other fields should
    pass CSRF + the role gate and fail Pydantic validation inside the
    handler: 400/422. A 403 here would mean CSRF or the role gate rejected
    the request before handler logic ever ran.
    """
    resp = client.post("/controls/new", data={"_csrf": csrf})
    if resp.status_code not in (400, 422):
        print(
            f"[dast] POST /controls/new (invalid body) returned {resp.status_code}, "
            "expected 400/422",
            file=sys.stderr,
        )
        return False
    return True


def _parse_har_canary(har_path: Path) -> int:
    """Count state-changing (POST/PUT/PATCH) HAR entries that got a non-403 response.

    Sourced from the HAR, not the JUnit report: a 403 satisfies the sole
    ``not_a_server_error`` check, so a run where the CSRF hook silently never
    fired would be a fully green JUnit report with no signal that nothing
    ever reached a handler. The HAR carries the real per-request status.
    """
    data = json.loads(har_path.read_text(encoding="utf-8"))
    entries = data.get("log", {}).get("entries", [])
    count = 0
    for entry in entries:
        method = entry.get("request", {}).get("method", "")
        status = entry.get("response", {}).get("status")
        if method in _STATE_CHANGING_METHODS and status != 403:
            count += 1
    return count


def main(argv: list[str] | None = None) -> int:
    _ = [] if argv is None else argv  # no CLI args yet; reserved for future use

    with tempfile.TemporaryDirectory(prefix="idraa-dast-") as tmpdir_str:
        tmpdir = Path(tmpdir_str)
        db_path = tmpdir / "dast.db"
        db_url = f"sqlite+aiosqlite:///{db_path.as_posix()}"

        # Safety guard — real if/raise, not `assert` (python -O strips
        # asserts, silently removing the guard). Must run before ANY
        # subprocess, DB, or network action below. Both halves are
        # structurally guaranteed by the literals above; the check exists so
        # this invariant is a code-enforced fact even if that construction
        # changes later, not just a convention.
        if not (db_url.startswith("sqlite+aiosqlite:///") and HOST == "127.0.0.1"):
            raise SystemExit("refusing: non-local DAST target")

        env: dict[str, str] = {
            **os.environ,
            "ENVIRONMENT": "test",
            "DATABASE_URL": db_url,
            "PYTHONHASHSEED": "0",
        }
        # This process ALSO imports idraa.app in-process below (build_schema)
        # — Settings._check_secret_hardening refuses to boot with the
        # default SESSION_SECRET outside environment="test". Mirror the
        # subprocess env into our own so that import succeeds the same way.
        os.environ["ENVIRONMENT"] = "test"
        os.environ["DATABASE_URL"] = db_url

        base_url = f"http://{HOST}:{PORT}"

        print("[dast] alembic upgrade head", flush=True)
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            env=env,
            cwd=REPO_ROOT,
            check=True,
        )

        print("[dast] seeding admin user", flush=True)
        pw_path = tmpdir / "dast-admin-pw.txt"
        # S603: argv is a hardcoded internal-tool invocation; `str(pw_path)` is
        # a path this function built itself (a tempdir it owns), not external
        # input.
        subprocess.run(  # noqa: S603
            [sys.executable, "-m", "security.dast.seed", "--out", str(pw_path)],
            env=env,
            cwd=REPO_ROOT,
            check=True,
        )
        password = pw_path.read_text(encoding="utf-8")
        pw_path.unlink()

        print("[dast] extracting OpenAPI schema", flush=True)
        # Lazy: importing this transitively imports idraa.app, which must
        # not happen until ENVIRONMENT=test is set above.
        from security.dast.extract_openapi import build_schema

        schema = build_schema(base_url)
        n_ops = len(schema.get("paths", {}))
        schema_path = tmpdir / "schema.json"
        schema_path.write_text(json.dumps(schema), encoding="utf-8")

        uvicorn_log = tmpdir / "uvicorn.log"
        junit_path = tmpdir / "schemathesis-junit.xml"
        har_path = tmpdir / "schemathesis.har"

        with uvicorn_log.open("wb") as log_f:
            # S603: argv is a hardcoded internal-tool invocation (uvicorn on a
            # fixed local host/port); no external input reaches this call.
            proc = subprocess.Popen(  # noqa: S603
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "idraa.app:app",
                    "--host",
                    HOST,
                    "--port",
                    str(PORT),
                ],
                env=env,
                cwd=REPO_ROOT,
                stdout=log_f,
                stderr=subprocess.STDOUT,
            )
        # log_f is now closed in the parent; uvicorn holds its own dup'd fd
        # and keeps writing to it regardless.

        try:
            print("[dast] waiting for /healthz", flush=True)
            if not _wait_healthy(base_url, proc, uvicorn_log):
                return 1

            with httpx.Client(base_url=base_url, timeout=10.0) as client:
                print("[dast] logging in as seeded admin", flush=True)
                creds = _login(client, password)
                if creds is None:
                    return 1
                session_cookie, csrf = creds

                print("[dast] pre-fuzz smoke checks", flush=True)
                if not _check_get_ok(client, "/organization"):
                    return 1
                if n_ops <= 50:
                    print(
                        f"[dast] extracted schema has only {n_ops} operations (need >50)",
                        file=sys.stderr,
                    )
                    return 1
                if not _smoke_admin_post_reaches_handler(client, csrf):
                    return 1

            print(
                f"[dast] running schemathesis: {n_ops} operations, max-examples={MAX_EXAMPLES}",
                flush=True,
            )
            fuzz_env: dict[str, str] = {
                **env,
                "SCHEMATHESIS_HOOKS": "security.dast.hooks",
                "DAST_CSRF": csrf,
            }
            schemathesis_bin = str(Path(sys.executable).parent / "schemathesis")
            fuzz_argv = [
                schemathesis_bin,
                "run",
                str(schema_path),
                "--url",
                base_url,
                "-c",
                "not_a_server_error",
                "--exclude-path-regex",
                EXCLUDED_PATH_REGEX,
                "--exclude-method",
                "DELETE",
                "--max-examples",
                str(MAX_EXAMPLES),
                "--seed",
                "0",
                "--generation-deterministic",
                "-H",
                f"Cookie: idraa_session={session_cookie}; csrf_token={csrf}",
                "--report",
                "junit",
                "--report-junit-path",
                str(junit_path),
                "--report-har-path",
                str(har_path),
            ]
            # Deliberately not logging fuzz_argv: the -H value carries the
            # live session cookie + CSRF token, and this runs in public CI
            # logs.
            # S603: argv is built entirely from hardcoded flags, this
            # process's own local config, and the session it just minted —
            # no external/attacker-controlled input reaches this call.
            fuzz_result = subprocess.run(  # noqa: S603
                fuzz_argv, env=fuzz_env, cwd=REPO_ROOT, check=False
            )
            schemathesis_rc = fuzz_result.returncode
            print(f"[dast] schemathesis exited {schemathesis_rc}", flush=True)

            print("[dast] post-run canary check (HAR)", flush=True)
            try:
                canary_count = _parse_har_canary(har_path)
            except (OSError, json.JSONDecodeError) as exc:
                # If schemathesis died before writing (or mid-write) the HAR,
                # a raw traceback here would mask the real signal (the
                # schemathesis exit code above) behind a confusing crash.
                # Fail closed the same way a canary_count < 1 does, but with
                # a clean one-line diagnostic instead of a traceback.
                print(f"[dast] CANARY FAILED: could not read the HAR ({exc})", file=sys.stderr)
                return 1
            if canary_count < 1:
                print(
                    "[dast] CANARY FAILED: no non-403 state-changing (POST/PUT/PATCH) "
                    "request recorded in the HAR — the CSRF hook may not have fired",
                    file=sys.stderr,
                )
                return 1
            print(
                f"[dast] canary OK: {canary_count} non-403 state-changing request(s) "
                "reached a handler",
                flush=True,
            )

            print("[dast] post-run session-liveness check", flush=True)
            with httpx.Client(
                base_url=base_url,
                timeout=10.0,
                cookies={"idraa_session": session_cookie, "csrf_token": csrf},
            ) as client:
                if not _check_get_ok(client, "/organization"):
                    print(
                        "[dast] LIVENESS FAILED: the admin session died during the fuzz run",
                        file=sys.stderr,
                    )
                    return 1
            print("[dast] session-liveness OK", flush=True)

            return schemathesis_rc
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=10)


if __name__ == "__main__":
    sys.exit(main())
