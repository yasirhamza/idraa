"""Read-only query API over the framework->FAIR-CAM crosswalk (P2a). Cited by the
control library (P2b) to ground FAIR-CAM claims. Canonical data — no org scoping.

Version handling (gate M4): the model permits multiple framework_versions. A query
without an explicit framework_version requires exactly ONE seeded version for that
framework and raises MultipleVersionsError otherwise — never silently unions across
versions. (Defer-with-guard; spec §7 amended. A future 'latest' resolver needs a
semantic version comparator, not lexicographic.)"""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.enums import FairCamSubFunction
from idraa.models.framework_crosswalk import FrameworkControl, FrameworkControlFairCam


class MultipleVersionsError(RuntimeError):
    """Raised when a framework has >1 seeded version and no explicit version was given."""


class CrosswalkService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def _resolve_version(self, framework: str, framework_version: str | None) -> str | None:
        if framework_version is not None:
            return framework_version
        versions = (
            (
                await self._db.execute(
                    select(FrameworkControl.framework_version)
                    .where(FrameworkControl.framework == framework)
                    .distinct()
                )
            )
            .scalars()
            .all()
        )
        if len(versions) > 1:
            raise MultipleVersionsError(
                f"{framework} has versions {sorted(versions)}; pass framework_version explicitly"
            )
        return versions[0] if versions else None

    async def faircam_functions_for(
        self, framework: str, codes: Iterable[str], *, framework_version: str | None = None
    ) -> set[FairCamSubFunction]:
        codes = list(codes)
        if not codes:
            return set()
        version = await self._resolve_version(framework, framework_version)
        if version is None:
            return set()
        stmt = (
            select(FrameworkControlFairCam.fair_cam_function)
            .join(
                FrameworkControl,
                FrameworkControl.id == FrameworkControlFairCam.framework_control_id,
            )
            .where(
                FrameworkControl.framework == framework,
                FrameworkControl.framework_version == version,
                FrameworkControl.code.in_(codes),
            )
        )
        return set((await self._db.execute(stmt)).scalars().all())

    async def validate_claims(
        self, framework_tags: dict[str, list[str]], claimed: Iterable[FairCamSubFunction]
    ) -> set[FairCamSubFunction]:
        # P2b note: calls faircam_functions_for without an explicit version, so a
        # framework with >1 seeded version raises MultipleVersionsError here. The
        # seed currently has one version per framework; if P2b needs multi-version
        # grounding, thread an optional per-framework version map through.
        supported: set[FairCamSubFunction] = set()
        for framework, codes in framework_tags.items():
            supported |= await self.faircam_functions_for(framework, codes)
        return set(claimed) - supported

    async def codes_for(self, framework: str, *, framework_version: str | None = None) -> list[str]:
        """All ``FrameworkControl`` codes for a framework — a coverage reference set.

        Used by the dashboard control-coverage aggregate (Task 3, #478): the
        denominator for "how much of framework X do our controls cover" is
        every seeded code for that framework, not just the ones tied to a
        specific FAIR-CAM function (``subcategories_for`` filters by function;
        this does not).

        Raises ``MultipleVersionsError`` under the same rule as
        ``faircam_functions_for``: a framework with >1 seeded version and no
        explicit ``framework_version`` is ambiguous, never silently unioned.
        Returns ``[]`` if the framework has no seeded rows at all.
        """
        version = await self._resolve_version(framework, framework_version)
        if version is None:
            return []
        stmt = (
            select(FrameworkControl.code)
            .where(FrameworkControl.framework == framework)
            .where(FrameworkControl.framework_version == version)
        )
        return list((await self._db.execute(stmt)).scalars().all())

    async def subcategories_for(
        self, function: FairCamSubFunction, framework: str | None = None
    ) -> list[FrameworkControl]:
        stmt = (
            select(FrameworkControl)
            .join(
                FrameworkControlFairCam,
                FrameworkControlFairCam.framework_control_id == FrameworkControl.id,
            )
            .where(FrameworkControlFairCam.fair_cam_function == function)
        )
        if framework is not None:
            stmt = stmt.where(FrameworkControl.framework == framework)
        return list((await self._db.execute(stmt)).scalars().all())
