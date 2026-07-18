"""Service-layer API over QualitativeMappingRepo (epic #34 P1b, Task 4).

Two responsibilities:
  1. Read-side: ``effective_bands`` / ``mapping_versions`` compute the
     canonical-⊕-org merged view the converter (Task 5) reads bands from.
  2. Write-side: ``QualitativeBandService`` org-band CRUD, mirroring
     ``ScenarioLibraryService``'s override-CRUD discipline (validation,
     audit, IDOR, optimistic lock, soft-delete).

Spec: docs/superpowers/specs/2026-07-18-qualitative-register-converter-design.md §2.
"""

from __future__ import annotations

import math
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy.ext.asyncio import AsyncSession

from idraa.errors import (
    NotFoundError,
    QualitativeBandVersionConflictError,
    ValidationError,
)
from idraa.models.qualitative_mapping import QualitativeMappingOrgBand
from idraa.repositories.qualitative_mapping_repo import QualitativeMappingRepo
from idraa.services.audit import AuditWriter

if TYPE_CHECKING:
    from idraa.models.user import User

# ---------------------------------------------------------------------------
# Validation constants
# ---------------------------------------------------------------------------

VALID_KINDS: frozenset[str] = frozenset({"frequency", "magnitude"})
LABEL_PATTERN = re.compile(r"^[a-z_]{1,40}$")


@dataclass(frozen=True)
class EffectiveBand:
    """One row of the canonical-⊕-org merged band view.

    ``source`` records which layer this value came from (``"canonical"`` |
    ``"org"``) and ``source_version`` the winning row's own ``version`` —
    both feed ``conversion_metadata.mapping_versions`` provenance in Task 5
    so a converted scenario's description can be traced back to exactly
    which band values produced it, even after a later re-derivation bumps
    the canonical or org version.
    """

    kind: str
    label: str
    low: float
    mode: float
    high: float
    source: str  # "canonical" | "org"
    source_version: int


class QualitativeBandService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = QualitativeMappingRepo(session)

    # ------------------------------------------------------------------
    # Read side
    # ------------------------------------------------------------------

    async def effective_bands(
        self,
        organization_id: uuid.UUID,
    ) -> dict[tuple[str, str], EffectiveBand]:
        """Canonical ⊕ org merged band table, keyed by (kind, label).

        Org rows win per (kind, label) — an org row either overrides a
        canonical label's values or adds a wholly new label. Soft-deleted
        org rows are excluded (``list_org_bands`` already filters
        ``deleted_at IS NULL``), so a tombstoned override reverts the
        effective value back to canonical without any special-casing here.
        """
        canonical = await self.repo.list_canonical()
        merged: dict[tuple[str, str], EffectiveBand] = {
            (band.kind, band.label): EffectiveBand(
                kind=band.kind,
                label=band.label,
                low=band.low,
                mode=band.mode,
                high=band.high,
                source="canonical",
                source_version=band.version,
            )
            for band in canonical
        }

        org_bands = await self.repo.list_org_bands(organization_id)
        for org_band in org_bands:
            merged[(org_band.kind, org_band.label)] = EffectiveBand(
                kind=org_band.kind,
                label=org_band.label,
                low=org_band.low,
                mode=org_band.mode,
                high=org_band.high,
                source="org",
                source_version=org_band.version,
            )
        return merged

    async def mapping_versions(self, organization_id: uuid.UUID) -> dict[str, Any]:
        """Version-pinning snapshot for ``conversion_metadata`` (Task 5).

        Shape: ``{"canonical": {"<kind>:<label>": <version>, ...}, "org":
        {"<kind>:<label>": <org row version>, ...}}``. Both keys use the
        ``"<kind>:<label>"`` string form (not a tuple) because this dict is
        destined for JSON storage on ``Scenario.conversion_metadata``.

        P1c Task 3 amendment (Meth-N3, BINDING — supersedes the original
        "max canonical version across all bands" shape): a single collapsed
        max would silently drop the per-(kind, label) reproducibility
        invariant a re-derivation of ONE band needs — e.g. bumping only the
        "high" magnitude band's version must be visible in a converted
        scenario's stored provenance even though every OTHER canonical
        band's version is unchanged. Building this from ``list_canonical()``
        (ALL canonical rows) rather than the merged ``effective_bands()``
        view is deliberate too: an org override SHADOWS a canonical
        (kind, label) in the merged view, but the canonical row's own
        version must still appear here — a shadowed canonical must never
        drop out of the provenance snapshot. Safe as a stored-JSON shape
        change (spec §8) solely because NO converted rows exist yet in any
        deployed environment before P1c ships.
        """
        canonical = await self.repo.list_canonical()
        canonical_versions = {f"{band.kind}:{band.label}": band.version for band in canonical}

        org_bands = await self.repo.list_org_bands(organization_id)
        org_versions = {f"{band.kind}:{band.label}": band.version for band in org_bands}

        return {"canonical": canonical_versions, "org": org_versions}

    # ------------------------------------------------------------------
    # Write side — org band CRUD
    # ------------------------------------------------------------------

    async def create_org_band(
        self,
        *,
        organization_id: uuid.UUID,
        kind: str,
        label: str,
        low: float,
        mode: float,
        high: float,
        reason: str,
        user: User,
        ip_address: str | None = None,
    ) -> QualitativeMappingOrgBand:
        """Create a new per-org band override (or a wholly new label).

        Raises:
            ValidationError: bad ``kind``, malformed ``label``, non-monotonic
                or non-positive ``low``/``mode``/``high``, blank ``reason``,
                or an active (org, kind, label) override already exists —
                use ``update_org_band`` to modify it instead.
        """
        self._validate_band_values(kind=kind, label=label, low=low, mode=mode, high=high)
        self._validate_reason(reason)

        active_bands = await self.repo.list_org_bands(organization_id)
        for existing in active_bands:
            if existing.kind == kind and existing.label == label:
                raise ValidationError(
                    f"an active org band override already exists for "
                    f"(kind={kind!r}, label={label!r}); use update_org_band to modify it"
                )

        band = QualitativeMappingOrgBand(
            organization_id=organization_id,
            kind=kind,
            label=label,
            low=low,
            mode=mode,
            high=high,
            reason=reason,
            version=1,
            row_version=1,
            created_by=user.id,
        )
        self.session.add(band)
        await self.session.flush()

        await self._write_audit(
            user=user,
            organization_id=organization_id,
            entity_id=band.id,
            action="qualitative_band.create",
            changes={
                "kind": [None, kind],
                "label": [None, label],
                "low": [None, low],
                "mode": [None, mode],
                "high": [None, high],
            },
            ip_address=ip_address,
        )
        return band

    async def update_org_band(
        self,
        *,
        organization_id: uuid.UUID,
        band_id: uuid.UUID,
        low: float,
        mode: float,
        high: float,
        reason: str,
        expected_row_version: int,
        user: User,
        ip_address: str | None = None,
    ) -> QualitativeMappingOrgBand:
        """Apply new low/mode/high + reason to an existing org band.

        ``kind``/``label`` are immutable after creation (identity fields) —
        only the numeric legs and ``reason`` change here.

        Raises:
            NotFoundError: the band doesn't exist, is tombstoned, or belongs
                to a different organization — repo-level IDOR closure
                (``QualitativeMappingRepo.get_org_band`` scopes the WHERE by
                ``organization_id``) means all three cases are
                indistinguishable from here, by design (existence-hiding).
            QualitativeBandVersionConflictError: ``expected_row_version``
                doesn't match the row's current ``row_version`` (optimistic
                lock) — another user updated this band; reload and retry.
            ValidationError: non-monotonic/non-positive values or blank
                ``reason``.
        """
        band = await self.repo.get_org_band(organization_id, band_id, for_update=True)
        if band is None:
            raise NotFoundError(f"qualitative org band {band_id} not found")

        if band.row_version != expected_row_version:
            raise QualitativeBandVersionConflictError(
                f"qualitative org band version conflict: "
                f"expected_row_version={expected_row_version} but actual "
                f"row_version={band.row_version}; another user updated "
                "this band — reload and retry"
            )

        self._validate_band_values(kind=band.kind, label=band.label, low=low, mode=mode, high=high)
        self._validate_reason(reason)

        prev_row_version = band.row_version
        band.low = low
        band.mode = mode
        band.high = high
        band.reason = reason
        band.version = band.version + 1
        band.row_version = band.row_version + 1

        await self.session.flush()

        await self._write_audit(
            user=user,
            organization_id=organization_id,
            entity_id=band.id,
            action="qualitative_band.update",
            changes={
                "row_version": [prev_row_version, band.row_version],
                "low": [None, low],
                "mode": [None, mode],
                "high": [None, high],
            },
            ip_address=ip_address,
        )
        return band

    async def delete_org_band(
        self,
        *,
        organization_id: uuid.UUID,
        band_id: uuid.UUID,
        user: User,
        ip_address: str | None = None,
    ) -> QualitativeMappingOrgBand:
        """Soft-delete (tombstone) an org band override.

        The partial unique index on (organization_id, kind, label) is
        ``deleted_at IS NULL``-scoped, so a subsequent ``create_org_band``
        call for the same (kind, label) succeeds — delete-then-recreate is a
        supported flow, not blocked by the tombstone.

        Raises:
            NotFoundError: same repo-level IDOR closure as
                ``update_org_band`` — missing, already-tombstoned, and
                cross-org bands are all indistinguishable "not found" cases.
        """
        band = await self.repo.get_org_band(organization_id, band_id, for_update=True)
        if band is None:
            raise NotFoundError(f"qualitative org band {band_id} not found")

        band.deleted_at = datetime.now(UTC)
        await self.session.flush()

        await self._write_audit(
            user=user,
            organization_id=organization_id,
            entity_id=band.id,
            action="qualitative_band.delete",
            changes={"deleted_at": [None, band.deleted_at.isoformat()]},
            ip_address=ip_address,
        )
        return band

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_band_values(
        *,
        kind: str,
        label: str,
        low: float,
        mode: float,
        high: float,
    ) -> None:
        """Shared validation for create/update: kind, label, ordering, positivity."""
        if kind not in VALID_KINDS:
            raise ValidationError(f"kind must be one of {sorted(VALID_KINDS)}; got {kind!r}")
        if not LABEL_PATTERN.match(label):
            raise ValidationError(
                f"label {label!r} must match ^[a-z_]{{1,40}}$ (lowercase snake_case, 1-40 chars)"
            )
        # Sec-I1 (P1c Task 3 amendment): reject non-finite values BEFORE the
        # ordering/positivity checks below — inf/-inf/nan can otherwise slip
        # through `low <= mode <= high` (e.g. high=inf) or a NaN comparison
        # (always False) would surface as a confusing "ordering" error
        # instead of the actual problem.
        if not (math.isfinite(low) and math.isfinite(mode) and math.isfinite(high)):
            raise ValidationError(
                f"band values must be finite numbers; got low={low}, mode={mode}, high={high}"
            )
        if not (low <= mode <= high):
            raise ValidationError(
                f"band values must satisfy low <= mode <= high; "
                f"got low={low}, mode={mode}, high={high}"
            )
        if not (low < high):
            raise ValidationError(
                f"band low ({low}) must be strictly less than high ({high}) — "
                "a degenerate zero-width band is not a valid frequency/magnitude range"
            )
        if low <= 0:
            raise ValidationError(
                f"{kind} band low ({low}) must be strictly positive — frequency "
                "(events/year) and magnitude (USD) are both positive quantities"
            )

    @staticmethod
    def _validate_reason(reason: str) -> None:
        if not reason or not reason.strip():
            raise ValidationError("reason is required")

    async def _write_audit(
        self,
        *,
        user: User,
        organization_id: uuid.UUID,
        entity_id: uuid.UUID,
        action: str,
        changes: dict[str, Any],
        ip_address: str | None = None,
    ) -> None:
        await AuditWriter(self.session).log(
            organization_id=organization_id,
            entity_type="qualitative_mapping_org_band",
            entity_id=entity_id,
            action=action,
            changes=changes,
            user_id=user.id,
            ip_address=ip_address,
        )
