"""Cache-backed effective security policy (idraa#85 admin knobs).

Single-process in-memory cache (matches #211 invariant): warmed at boot,
refreshed after each COMMITTED write. The middleware reads the sync effective_*
helpers with no per-request DB. Cache holds a PRIMITIVE SNAPSHOT (never the ORM
instance) so a detached-instance lazy-load can't fault the middleware hot path.
NULL / missing -> env default.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.config import Settings, get_settings
from idraa.models.enums import StepUpCategory
from idraa.models.security_settings import SecuritySettings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _Snapshot:
    mfa_policy: str | None
    step_up_window_seconds: int | None
    exports: bool | None
    destructive: bool | None
    admin: bool | None
    credentials: bool | None


_cache: _Snapshot | None = None  # None = not loaded -> env fallback

_CAT_ATTR = {
    StepUpCategory.EXPORTS: "exports",
    StepUpCategory.DESTRUCTIVE: "destructive",
    StepUpCategory.ADMIN: "admin",
    StepUpCategory.CREDENTIALS: "credentials",
}


def invalidate() -> None:
    global _cache
    _cache = None


async def load_security_settings(db: AsyncSession, org_id: uuid.UUID) -> None:
    """Load the committed row into the snapshot cache (single atomic reassignment)."""
    global _cache
    row = (
        await db.execute(select(SecuritySettings).where(SecuritySettings.organization_id == org_id))
    ).scalar_one_or_none()
    _cache = (
        None
        if row is None
        else _Snapshot(
            mfa_policy=row.mfa_policy,
            step_up_window_seconds=row.step_up_window_seconds,
            exports=row.step_up_exports,
            destructive=row.step_up_destructive,
            admin=row.step_up_admin,
            credentials=row.step_up_credentials,
        )
    )


async def warm_cache(settings: Settings) -> None:
    """Boot-time load. A warm failure leaves the cache empty (env fallback) + logs."""
    from idraa.db import get_session
    from idraa.services.org import get_sole_org  # existing helper (app.py lifespan uses it)

    try:
        async with get_session() as db:
            org = await get_sole_org(db)
            if org is not None:
                await load_security_settings(db, org.id)
    except Exception:
        logger.exception(
            "security_settings cache warm FAILED; env defaults in effect (MFA policy "
            "may fall back to env until first settings write) — investigate"
        )


def effective_mfa_policy() -> str:
    if _cache is not None and _cache.mfa_policy is not None:
        return _cache.mfa_policy
    return get_settings().auth_mfa_policy


def effective_step_up_window() -> int:
    if _cache is not None and _cache.step_up_window_seconds is not None:
        return _cache.step_up_window_seconds
    return get_settings().auth_step_up_max_age_seconds


def step_up_required(category: StepUpCategory) -> bool:
    if effective_step_up_window() <= 0:  # global kill-switch
        return False
    if _cache is not None:
        override: bool | None = getattr(_cache, _CAT_ATTR[category])
        if override is not None:
            return override
    return True  # default-on
