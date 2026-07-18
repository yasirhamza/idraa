"""Scenario ORM — the FAIR-parameterized analytical unit.

Scenarios are organization-owned analyst work product. They carry:

- Descriptive identity: name, threat actor, attack vector, asset_class,
  FAIR distribution params for TEF/Vuln/PrimaryLoss/SecondaryLoss.
- Forward-compat metadata: ``source`` enum, allowing future ingest
  paths (FAIR-taxonomy library, CSV import, register import) to land
  without schema migration.
- Control linkage: ``mitigating_controls`` relationship — the set of
  Control rows associated with this scenario via the scenario_controls
  join table.

Issue #88: the ``industry`` and ``revenue_tier`` columns were removed.
Those were denormalized snapshots of org-level properties that went
stale when the org was updated. Consumers needing industry / revenue
tier access ``scenario.organization.industry_slug`` and
``scenario.organization.revenue_tier`` instead.

PR π note: the calibration runtime framework was excised. Scenarios
no longer carry overlay_pins / sub_sector_pin / calibration_override_pin
/ iris_calibration_year / mc_iterations / last_simulated_at /
last_simulation_inputs_hash. The Monte Carlo runner reads
threat_event_frequency / vulnerability / primary_loss / secondary_loss
distributions directly from this row. mc_iterations is now a
RiskAnalysisRun-side concept.

``version`` (str) is the analyst-chosen descriptive label.
``row_version`` (int) is the optimistic-lock primitive, server-bumped
on every mutation. Spec §5.10 + Q9 paranoid-review fix.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, Enum, ForeignKey, Integer, Numeric, String, Text, text
from sqlalchemy import Uuid as UuidType
from sqlalchemy.orm import Mapped, mapped_column, relationship

from idraa.db import Base
from idraa.models.enums import (
    AssetClass,
    EntityStatus,
    ScenarioEffect,
    ScenarioSource,
    ScenarioType,
    ThreatActorType,
    ThreatCategory,
)
from idraa.models.mixins import IdMixin, OrgMixin, TimestampMixin

if TYPE_CHECKING:
    from idraa.models.attack import ScenarioAttackMapping
    from idraa.models.control import Control
    from idraa.models.organization import Organization


class Scenario(IdMixin, TimestampMixin, OrgMixin, Base):
    __tablename__ = "scenarios"

    # Descriptive
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    scenario_type: Mapped[ScenarioType] = mapped_column(
        Enum(ScenarioType, native_enum=False, values_callable=lambda x: [e.value for e in x]),
        default=ScenarioType.CUSTOM,
        nullable=False,
    )
    threat_category: Mapped[ThreatCategory] = mapped_column(
        Enum(ThreatCategory, native_enum=False, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
    )
    threat_actor_type: Mapped[ThreatActorType | None] = mapped_column(
        Enum(ThreatActorType, native_enum=False, values_callable=lambda x: [e.value for e in x]),
        nullable=True,
    )
    attack_vector: Mapped[str | None] = mapped_column(String(128), nullable=True)
    asset_class: Mapped[AssetClass | None] = mapped_column(
        Enum(AssetClass, native_enum=False, values_callable=lambda x: [e.value for e in x]),
        nullable=True,
    )
    # FAIR taxonomy 4th axis (Effect). NULL = unspecified → detection-gated
    # (no behaviour change for legacy rows). AVAILABILITY unlocks the
    # effect-type-aware recovery gate (self-detecting event, FAIR-CAM §3.3.2 p.19).
    effect: Mapped[ScenarioEffect | None] = mapped_column(
        Enum(ScenarioEffect, native_enum=False, values_callable=lambda x: [e.value for e in x]),
        nullable=True,
    )

    # FAIR distributions (JSON: {"distribution": "PERT", "low": ..., "mode": ..., "high": ...})
    threat_event_frequency: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    vulnerability: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    primary_loss: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    secondary_loss: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    # Multi-currency P2: the currency the analyst ENTERED loss magnitudes in, and
    # the rate pinned at entry time (code-per-USD). Stored loss distributions
    # remain USD (engine contract); these are immutable provenance metadata
    # (read-only after create — see scenarios.py update handler).
    entry_currency: Mapped[str] = mapped_column(
        String(3), default="USD", server_default="USD", nullable=False
    )
    entry_rate: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)

    # Audit-F2 provenance: which elicitation framing the stored vulnerability
    # was captured under. 'legacy_residual' = pre-#339 wording ("through your
    # current controls" — value embeds the analyst's control discount, so the
    # FAIR-CAM control layer double-counts on top); 'inherent' = post-#339
    # control-naive framing. System-managed (NOT a form field); app-enforced
    # value set, no CHECK (SC-I2 / #303 CHECK-widening foot-gun). Flipped to
    # 'inherent' by the confirm endpoint or when an update changes the
    # vulnerability numeric triple. default + server_default symmetric per
    # the row_version pattern.
    vuln_framing: Mapped[str] = mapped_column(
        String(32), default="inherent", server_default="inherent", nullable=False
    )

    # Phase 1.5a — library reference; NULL for expert-mode scenarios
    library_pin: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    # Epic #34 P1b — qualitative register converter provenance. NULL for every
    # scenario not created via the converter. Validated by the Pydantic
    # ConversionMetadata model (services/qualitative_converter.py, Task 5)
    # before assignment; the ORM column itself is unconstrained JSON. Internal
    # (ORM-only) — never on ScenarioForm; the converter writes it directly.
    conversion_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    # Forward-compat
    source: Mapped[ScenarioSource] = mapped_column(
        Enum(ScenarioSource, native_enum=False, values_callable=lambda x: [e.value for e in x]),
        default=ScenarioSource.EXPERT_JUDGMENT,
        nullable=False,
    )

    # Lifecycle
    status: Mapped[EntityStatus] = mapped_column(
        Enum(EntityStatus, native_enum=False, values_callable=lambda x: [e.value for e in x]),
        default=EntityStatus.ACTIVE,
        nullable=False,
    )
    version: Mapped[str] = mapped_column(String(32), default="1.0", nullable=False)
    # Optimistic-lock primitive — server-bumped on every mutation.
    # Distinct from descriptive ``version`` per Q9 paranoid-review fix.
    # server_default mirrors the migration so ``create_all`` (tests) and
    # Alembic (prod) both apply DEFAULT 1 at the SQL layer.
    row_version: Mapped[int] = mapped_column(
        Integer, default=1, server_default=text("1"), nullable=False
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UuidType(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    mitigating_controls: Mapped[list[Control]] = relationship(
        "Control",
        secondary="scenario_controls",
        lazy="selectin",
    )

    attack_mappings: Mapped[list[ScenarioAttackMapping]] = relationship(
        "ScenarioAttackMapping",
        lazy="selectin",
        cascade="all, delete-orphan",
        # Arch-N4: created_at can tie within one request (rows built in one
        # loop) — id tiebreak keeps edit-form row order deterministic.
        order_by="[ScenarioAttackMapping.created_at, ScenarioAttackMapping.id]",
    )

    # Issue #88: enable scenario.organization.revenue_tier etc. for
    # templates + services without an extra session-level fetch.
    organization: Mapped[Organization] = relationship(
        "Organization",
        back_populates="scenarios",
        lazy="select",
    )
