"""Qualitative register → DRAFT scenario converter (epic #34 P1b, Task 5).

Consumes ``QualitativeBandService.effective_bands`` / ``mapping_versions``
(Task 4) and turns already-bound register rows (``BoundRow`` — binding
itself is P1c's job, out of scope here) into ``EntityStatus.DRAFT``
scenarios. Persists ONLY via ``ScenarioService.create()`` — never a raw ORM
write — so every P1a create-path gate (FAIRCAM validation, the create-time
status domain, ``scenario.create`` audit emission) applies for free (plan
Architecture note). ``vuln_framing`` / ``conversion_metadata`` are
ORM-only, service-managed fields (``ScenarioForm`` never carries them);
the converter sets both AFTER ``create()`` returns, in the same per-row
savepoint.

Spec: docs/superpowers/specs/2026-07-18-qualitative-register-converter-design.md §3.
Plan: docs/superpowers/plans/2026-07-18-mapping-tables-converter-p1b.md Task 5
(+ the BINDING Task 5 plan-gate amendments).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, field_validator
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.errors import IdraaError
from idraa.models.enums import EntityStatus, ScenarioSource, ScenarioType, ThreatCategory
from idraa.models.scenario import Scenario
from idraa.schemas.scenario import ScenarioForm
from idraa.services.audit import AuditWriter
from idraa.services.qualitative_bands import QualitativeBandService
from idraa.services.scenarios import ScenarioService

if TYPE_CHECKING:
    from idraa.models.user import User

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# spec §3 D3: register likelihood IS the LEF; vulnerability is a fixed,
# non-derived pass-through. Task 3 verified this exact triple through both
# the run-executor engine mapper AND ScenarioService.create() — no fallback
# fired (the degenerate point-mass short-circuits fair_cam's PERT sampler
# cleanly; see tests/unit/test_degenerate_vuln_pert.py).
NEUTRAL_VULN_PERT: dict[str, Any] = {
    "distribution": "PERT",
    "low": 1.0,
    "mode": 1.0,
    "high": 1.0,
}

# spec §3: fixed sentence, identical on every conversion report (secondary
# loss cannot be derived from a single register impact score).
SL_NOTE = (
    "SL not derivable from a single impact score — add during review or "
    "anchor to a library entry (P2)."
)

# Sec-I3 (plan-gate Task 5 amendment): fail-closed input bounds, enforced
# BEFORE any write. ``raw`` carries EXACTLY these 3 bound cells; the wider
# ``carry_along`` bag caps at 20 keys. Both cap individual key/value length.
_RAW_KEYS: frozenset[str] = frozenset({"likelihood", "impact", "category"})
_MAX_CARRY_ALONG_KEYS = 20
_MAX_KEY_LEN = 100
_MAX_VALUE_LEN = 2000


def _check_bounded_dict(
    d: dict[str, str],
    *,
    label: str,
    exact_keys: frozenset[str] | None = None,
    max_keys: int | None = None,
) -> None:
    """Fail-closed bound check (Sec-I3). Raises ``ValueError`` — caught by
    the per-row try in :meth:`QualitativeConverterService.convert` and
    reported as a ``RowError`` — NEVER silently truncates.

    Shared by :class:`ConversionMetadata`'s ``raw`` field validator (exact
    3-key set) and the converter's direct ``carry_along`` gate (``raw`` and
    ``carry_along`` are the two dict-shaped, user-influenced inputs the
    amendment names; ``carry_along`` itself is not part of the persisted
    ``conversion_metadata`` shape — spec §3 — so it cannot be validated as
    a ConversionMetadata field and is checked here directly instead).
    """
    if exact_keys is not None and set(d.keys()) != exact_keys:
        raise ValueError(
            f"{label} must have exactly keys {sorted(exact_keys)}, got {sorted(d.keys())}"
        )
    if max_keys is not None and len(d) > max_keys:
        raise ValueError(f"{label} has {len(d)} keys, exceeding the maximum of {max_keys}")
    for k, v in d.items():
        if len(k) > _MAX_KEY_LEN:
            raise ValueError(f"{label} key {k!r} exceeds {_MAX_KEY_LEN} characters")
        if len(v) > _MAX_VALUE_LEN:
            raise ValueError(f"{label} value for key {k!r} exceeds {_MAX_VALUE_LEN} characters")


# ---------------------------------------------------------------------------
# Input: a single already-bound register row (P1c produces these)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BoundRow:
    """One qualitative register row after column-map + value-bind (P1c).

    ``raw`` is EXACTLY the 3 bound cells (Sec-I3) — the fixed subset the
    conversion_metadata provenance pins to, NOT a full-row capture.
    ``carry_along`` is whatever additional columns the user chose to keep
    for the description's provenance block; it is bounded independently
    (``_check_bounded_dict``) and never persisted as a JSON field.
    """

    source_row: int
    title: str
    description: str | None
    owner: str | None
    likelihood_label: str
    magnitude_label: str
    category: ThreatCategory | None  # None == PARKED (D5)
    raw: dict[str, str]  # {"likelihood": ..., "impact": ..., "category": ...}
    carry_along: dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# conversion_metadata — validated before it ever touches the ORM column
# ---------------------------------------------------------------------------


class ConversionMetadata(BaseModel):
    """Validated shape of ``Scenario.conversion_metadata`` (spec §3).

    ``extra="forbid"`` — same anti-blob-smuggling discipline as
    ``ScenarioForm`` (this is an internal, service-constructed model, but
    the discipline is cheap and forecloses a future careless caller from
    smuggling extra keys into stored provenance JSON).
    """

    model_config = ConfigDict(extra="forbid")

    source_file: str
    source_row: int
    raw: dict[str, str]
    bindings: dict[str, str]
    mapping_versions: dict[str, Any]
    # Spec-I2: forward-compat for P1c's binding-profile feature; unset in P1b.
    binding_profile_id: str | None = None
    converted_at: str

    @field_validator("raw")
    @classmethod
    def _raw_bounds(cls, v: dict[str, str]) -> dict[str, str]:
        _check_bounded_dict(v, label="raw", exact_keys=_RAW_KEYS)
        return v


# ---------------------------------------------------------------------------
# Output: the conversion report
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ConvertedRow:
    scenario_id: uuid.UUID
    source_row: int
    title: str


@dataclass(frozen=True)
class SkippedRow:
    source_row: int
    title: str
    reason: str  # "name" | "same_source"


@dataclass(frozen=True)
class RowError:
    source_row: int
    message: str


@dataclass
class ConversionReport:
    created: list[ConvertedRow]
    parked: list[int]
    skipped_duplicates: list[SkippedRow]
    errors: list[RowError]
    sl_note: str
    mapping_versions: dict[str, Any]
    source_file: str


# ---------------------------------------------------------------------------
# Dedup lookups (org-scoped, ALL statuses — spec §3.1 / Sec-I1)
# ---------------------------------------------------------------------------


async def _all_scenario_names(db: AsyncSession, organization_id: uuid.UUID) -> set[str]:
    """casefold()-ed names of ALL scenarios in the org, any status.

    Deliberately NOT ``services/scenario_import.py``'s ACTIVE-only
    ``_existing_active_names`` — spec §3.1 requires dedup against DRAFT
    rows too, or re-importing a register would double-create converted
    scenarios on every re-upload (plan-gate finding Arch-N3).
    """
    stmt = select(Scenario.name).where(Scenario.organization_id == organization_id)
    rows = (await db.execute(stmt)).scalars().all()
    return {name.casefold() for name in rows}


async def _existing_conversion_sources(
    db: AsyncSession, organization_id: uuid.UUID
) -> set[tuple[str, int]]:
    """``(source_file_stem, source_row)`` pairs already converted in this org.

    Org-scoped (Sec-I1): an identical ``(source_file_stem, source_row)`` in
    a DIFFERENT org must never dedup against this one.
    """
    stmt = select(Scenario.conversion_metadata).where(
        Scenario.organization_id == organization_id,
        Scenario.conversion_metadata.isnot(None),
    )
    rows = (await db.execute(stmt)).scalars().all()
    sources: set[tuple[str, int]] = set()
    for cm in rows:
        if not isinstance(cm, dict):
            continue
        sf = cm.get("source_file")
        sr = cm.get("source_row")
        if isinstance(sf, str) and isinstance(sr, int):
            sources.add((Path(sf).stem, sr))
    return sources


def _compose_description(row: BoundRow, *, source_file: str) -> str:
    """Original description + a plain-text "Register provenance" block
    (spec §3): owner, raw likelihood/impact/category, carried columns,
    source file + row. No markup — this text renders as-is."""
    lines = [
        f"Owner: {row.owner or 'unspecified'}",
        f"Likelihood (raw): {row.raw.get('likelihood', '')}",
        f"Impact (raw): {row.raw.get('impact', '')}",
        f"Category (raw): {row.raw.get('category', '')}",
    ]
    if row.carry_along:
        carried = "; ".join(f"{k}={v}" for k, v in row.carry_along.items())
        lines.append(f"Carried columns: {carried}")
    lines.append(f"Source: {source_file}, row {row.source_row}")
    block = "\n".join(lines)
    base = row.description or ""
    return f"{base}\n\n--- Register provenance ---\n{block}"


class QualitativeConverterService:
    """Converts bound qualitative register rows into DRAFT scenarios."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def convert(
        self,
        *,
        organization_id: uuid.UUID,
        user: User,
        source_file: str,
        rows: list[BoundRow],
        ip_address: str | None = None,
    ) -> ConversionReport:
        """Convert every bound row; row failures never abort the batch.

        Order per row: park (category is None) → dedup (name, then
        same-source) → band lookup → input bounds → build + persist. A
        RowError rolls back only that row's SAVEPOINT
        (``async with self._db.begin_nested()``); rows before and after it
        are unaffected (Arch-I4).
        """
        band_service = QualitativeBandService(self._db)
        effective = await band_service.effective_bands(organization_id)
        mapping_versions = await band_service.mapping_versions(organization_id)

        source_stem = Path(source_file).stem
        existing_names = await _all_scenario_names(self._db, organization_id)
        existing_sources = await _existing_conversion_sources(self._db, organization_id)
        seen_names: set[str] = set()
        seen_sources: set[tuple[str, int]] = set()

        created: list[ConvertedRow] = []
        parked: list[int] = []
        skipped: list[SkippedRow] = []
        errors: list[RowError] = []

        for row in rows:
            try:
                if row.category is None:
                    parked.append(row.source_row)
                    continue

                name_key = row.title.strip().casefold()
                if name_key in existing_names or name_key in seen_names:
                    skipped.append(
                        SkippedRow(source_row=row.source_row, title=row.title, reason="name")
                    )
                    continue

                source_key = (source_stem, row.source_row)
                if source_key in existing_sources or source_key in seen_sources:
                    skipped.append(
                        SkippedRow(source_row=row.source_row, title=row.title, reason="same_source")
                    )
                    continue

                freq_band = effective.get(("frequency", row.likelihood_label))
                if freq_band is None:
                    raise ValueError(
                        f"row {row.source_row}: unknown frequency band label "
                        f"{row.likelihood_label!r} — binding is stale against the "
                        "current mapping table"
                    )
                mag_band = effective.get(("magnitude", row.magnitude_label))
                if mag_band is None:
                    raise ValueError(
                        f"row {row.source_row}: unknown magnitude band label "
                        f"{row.magnitude_label!r} — binding is stale against the "
                        "current mapping table"
                    )

                _check_bounded_dict(row.raw, label="raw", exact_keys=_RAW_KEYS)
                _check_bounded_dict(
                    row.carry_along, label="carry_along", max_keys=_MAX_CARRY_ALONG_KEYS
                )

                tef = {
                    "distribution": "PERT",
                    "low": freq_band.low,
                    "mode": freq_band.mode,
                    "high": freq_band.high,
                }
                pl = {
                    "distribution": "PERT",
                    "low": mag_band.low,
                    "mode": mag_band.mode,
                    "high": mag_band.high,
                }

                form = ScenarioForm(
                    name=row.title,
                    description=_compose_description(row, source_file=source_file),
                    scenario_type=ScenarioType.CUSTOM,
                    threat_category=row.category.value,
                    threat_event_frequency=tef,
                    vulnerability=dict(NEUTRAL_VULN_PERT),
                    primary_loss=pl,
                    secondary_loss=None,
                    source=ScenarioSource.QUALITATIVE_REGISTER_IMPORT,
                    status=EntityStatus.DRAFT,
                )

                metadata = ConversionMetadata(
                    source_file=source_file,
                    source_row=row.source_row,
                    raw=row.raw,
                    bindings={
                        "likelihood_label": row.likelihood_label,
                        "magnitude_label": row.magnitude_label,
                        "category": row.category.value,
                    },
                    mapping_versions=mapping_versions,
                    converted_at=datetime.now(UTC).isoformat(),
                )

                async with self._db.begin_nested():
                    scenario = await ScenarioService(self._db).create(
                        organization_id=organization_id,
                        form=form,
                        current_user=user,
                        ip_address=ip_address,
                    )
                    scenario.vuln_framing = "legacy_residual"
                    scenario.conversion_metadata = metadata.model_dump()
                    await self._db.flush()

                created.append(
                    ConvertedRow(
                        scenario_id=scenario.id, source_row=row.source_row, title=row.title
                    )
                )
                seen_names.add(name_key)
                seen_sources.add(source_key)
            except (IdraaError, PydanticValidationError, SQLAlchemyError, ValueError) as exc:
                errors.append(RowError(source_row=row.source_row, message=str(exc)))
                continue

        await AuditWriter(self._db).log(
            organization_id=organization_id,
            entity_type="scenario",
            entity_id=organization_id,
            action="scenario.convert_qualitative",
            changes={
                "created": [str(c.scenario_id) for c in created],
                "parked": len(parked),
                "skipped": len(skipped),
                "errors": len(errors),
                "source_file": source_file,
                "vuln_framing": "legacy_residual",
                "conversion_metadata": "set",
            },
            user_id=user.id,
            ip_address=ip_address,
        )

        return ConversionReport(
            created=created,
            parked=parked,
            skipped_duplicates=skipped,
            errors=errors,
            sl_note=SL_NOTE,
            mapping_versions=mapping_versions,
            source_file=source_file,
        )
