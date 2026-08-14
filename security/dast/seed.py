"""Seed a throwaway DAST target DB with one org + one MFA-enrolled admin.

``seed(db_url)`` ASSUMES the schema already exists — the Task-5 orchestrator
runs ``alembic upgrade head`` against the ephemeral DB before calling this,
so this module issues no DDL, only two INSERTs. The admin's password is
generated at runtime (``secrets.token_urlsafe``) and returned to the caller;
it is never a constant, never committed, and — via the CLI entry point below
— never printed to stdout.

Run standalone (reads ``DATABASE_URL``, writes the generated password to
``--out``, never stdout):

    DATABASE_URL=sqlite+aiosqlite:///./dast.db \\
        uv run python -m security.dast.seed --out /tmp/dast-admin-pw.txt
"""

from __future__ import annotations

import argparse
import asyncio
import os
import secrets
import sys
from pathlib import Path

from security.dast.config import SEED_EMAIL, SEED_ORG
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from idraa.db import _install_sqlite_pragmas, strict_json_dumps
from idraa.models._types import now_utc
from idraa.models.enums import IndustryType, OrganizationSize, UserRole
from idraa.models.organization import Organization
from idraa.models.user import User
from idraa.services.auth import hash_password


async def seed(db_url: str) -> str:
    """Create one org + one MFA-enrolled admin user; return the generated password.

    Builds its own engine from the explicit ``db_url`` rather than going
    through ``idraa.db.get_engine()`` (a process-wide singleton keyed off
    ``Settings.database_url``) — this function is invoked from a short-lived
    script/subprocess, not the app process, so it owns its own engine
    lifecycle and disposes it before returning.
    """
    engine = create_async_engine(db_url, future=True, json_serializer=strict_json_dumps)
    if engine.dialect.name == "sqlite":
        _install_sqlite_pragmas(engine)

    password = secrets.token_urlsafe(16)
    try:
        sessionmaker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with sessionmaker() as session:
            org = Organization(
                name=SEED_ORG,
                industry_type=IndustryType.MANUFACTURING,
                organization_size=OrganizationSize.MEDIUM,
            )
            session.add(org)
            await session.flush()

            admin = User(
                organization_id=org.id,
                email=SEED_EMAIL,
                password_hash=hash_password(password),
                full_name="DAST Admin",
                role=UserRole.ADMIN,
                is_active=True,
                # MFA-enrolled so EnrollmentGuardMiddleware never redirects
                # the fuzzing session to /account/security mid-run.
                mfa_enrolled_at=now_utc(),
            )
            session.add(admin)
            await session.commit()
    finally:
        await engine.dispose()

    return password


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        required=True,
        type=Path,
        help="File path to write the generated admin password to. Never printed to stdout.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("DATABASE_URL must be set to seed the DAST target DB.", file=sys.stderr)
        raise SystemExit(1)

    # Fail fast + owner-only perms: prove --out is creatable/writable BEFORE
    # seeding. A seeded admin whose password write then fails (missing
    # parent dir, unwritable path) is irrecoverable, so the path must be
    # validated first. touch()'s mode= only applies on first creation
    # (POSIX: opening an existing file never changes its mode), so chmod
    # explicitly afterward to guarantee 0600 even if --out points at a
    # pre-existing, more-permissive file.
    try:
        args.out.touch(mode=0o600, exist_ok=True)
        args.out.chmod(0o600)
    except OSError as exc:
        print(f"Cannot create --out path {args.out}: {exc}", file=sys.stderr)
        raise SystemExit(1) from None

    password = asyncio.run(seed(db_url))
    args.out.write_text(password, encoding="utf-8")


if __name__ == "__main__":
    main()
