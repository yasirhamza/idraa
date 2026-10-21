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
    python -m idraa library siem-resync [--apply] [--database-url URL]

#199 slice 1: gated resync of deployed SIEM controls after the Visibility-coverage
bump; see services/siem_visibility_resync.py.
"""

from __future__ import annotations

import argparse
import asyncio
import sys


async def _reset_mfa(email: str) -> int:
    # Imports deferred so ``--help`` never touches DB/config.
    from idraa.db import get_engine, get_session
    from idraa.services.audit import AuditWriter
    from idraa.services.auth import load_user_by_email, revoke_user_sessions
    from idraa.services.mfa_enrollment import reset_user_mfa

    try:
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
    finally:
        # One-shot process: dispose the engine so aiosqlite's pool tears down
        # cleanly (no lingering-connection warning on exit).
        await get_engine().dispose()


async def _siem_resync(*, apply: bool, database_url: str | None) -> int:
    # Imports deferred so ``--help`` never touches DB/config (same rule as _reset_mfa).
    from idraa.services.siem_visibility_resync import run

    return await run(apply=apply, database_url=database_url)


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
    library = sub.add_parser("library", help="control-library operations")
    library_sub = library.add_subparsers(dest="library_command", required=True)
    siem = library_sub.add_parser(
        "siem-resync",
        help="#199 slice 1: resync untouched SIEM Visibility-coverage defaults (dry by default)",
    )
    siem.add_argument("--apply", action="store_true", help="write class A; default is a dry run")
    siem.add_argument("--database-url", default=None, help="override DATABASE_URL (sqlite only)")
    args = parser.parse_args(argv)
    # Every subparser level is required=True, so parse_args returns only for a leaf
    # command; the two leaves are dispatched on `command` (no fallthrough branch —
    # a parser.error() tail would be mypy-unreachable under warn_unreachable=true).
    if args.command == "library":
        return asyncio.run(_siem_resync(apply=args.apply, database_url=args.database_url))
    return asyncio.run(_reset_mfa(args.email))


if __name__ == "__main__":
    sys.exit(main())
