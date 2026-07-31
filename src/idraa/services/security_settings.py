"""Cache-backed effective security policy (idraa#85 admin knobs).

Single-process in-memory cache (matches #211 invariant): warmed at boot,
refreshed after each COMMITTED write. The middleware reads the sync effective_*
helpers with no per-request DB. Cache holds a PRIMITIVE SNAPSHOT (never the ORM
instance) so a detached-instance lazy-load can't fault the middleware hot path.
NULL / missing -> env default.

Multi-machine scale-out (not yet the deployment model, but noted per #211):
each process's cache is independently warmed/refreshed, so a write committed
on machine A is invisible on machine B until B's own next boot or write. For
MFA specifically this divergence is asymmetric in the SECURITY-DOWNGRADE
direction: if an admin tightens `mfa_policy` to `required` on machine A,
machine B keeps enforcing the env default (typically `optional`) until it
independently reloads -- i.e. the unpatched machine fails open on the exact
knob meant to raise the bar, not just stale-serves a value. A durable fix
needs the heartbeat-column / shared-invalidation design sketched in #211
Option 1; single-process deployments are unaffected.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Literal

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
# True once a warm/load has COMPLETED successfully in this process, even if it
# found no SecuritySettings row. Distinguishes "empty" (normal: no row saved
# yet) from "cold" (never warmed / warm failed) in cache_state() — idraa#107.
_warmed: bool = False

_CAT_ATTR = {
    StepUpCategory.EXPORTS: "exports",
    StepUpCategory.DESTRUCTIVE: "destructive",
    StepUpCategory.ADMIN: "admin",
    StepUpCategory.CREDENTIALS: "credentials",
}


def invalidate() -> None:
    """Drop the snapshot AND the process warm flag (full reset to "cold").

    Today's callers are tests (autouse conftest fixture) and the settings
    write path, which always reloads immediately after — so the transient
    "cold" is unobservable in prod. If a future prod path ever invalidates
    WITHOUT reloading (e.g. a settings-row delete), split the flag handling
    first: leaving _warmed True there is correct ("empty", not "cold" =
    boot-warm-failed false alarm on /healthz). See cache_state().
    """
    global _cache, _warmed
    _cache = None
    _warmed = False


def cache_state() -> Literal["warm", "empty", "cold"]:
    """Tri-state warm signal for /healthz (idraa#107(2)).

    - ``"warm"``  — snapshot loaded; persisted overrides are in effect.
    - ``"empty"`` — a warm/load completed successfully but no
      ``SecuritySettings`` row exists. This is the NORMAL state until an
      admin first saves security settings; env defaults apply by design.
    - ``"cold"``  — never warmed (or the boot warm FAILED — see the
      ``warm_cache`` exception log). Env fallback is in effect; a persisted
      ``mfa_policy=required`` override would be silently relaxed until the
      next settings write or restart. Post-boot ``cold`` is the actionable
      operator signal this field exists for.

    A two-state warm/cold signal would report "cold" forever on a healthy
    install that never wrote a settings row, training operators to ignore
    the field. Read-only on module state — safe for /healthz, which must
    stay DB-free during the boot-write window.
    """
    if _cache is not None:
        return "warm"
    return "empty" if _warmed else "cold"


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

    global _warmed
    try:
        async with get_session() as db:
            org = await get_sole_org(db)
            if org is not None:
                await load_security_settings(db, org.id)
        # Mark the warm as completed even when no org/row exists yet — that is
        # the normal "empty" state, not a failure (cache_state(), idraa#107).
        _warmed = True
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
