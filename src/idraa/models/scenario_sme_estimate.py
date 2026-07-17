"""Per scenario x fieldset x SME estimate. See spec §6.2.

2026-05-25 update: row identity is either ``sme_id`` (FK to
subject_matter_experts) OR ``sme_name`` (free-text). Exactly one is set
per row, enforced by ck_sse_sme_id_xor_name. Free-text rows are emitted
by the wizard combobox when the analyst types a name without picking
from the directory.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import CheckConstraint, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column

from idraa.db import Base
from idraa.models.enums import ScenarioFieldset
from idraa.models.mixins import IdMixin, OrgMixin


class ScenarioSMEEstimate(Base, IdMixin, OrgMixin):
    __tablename__ = "scenario_sme_estimates"

    scenario_id: Mapped[UUID] = mapped_column(
        ForeignKey("scenarios.id", ondelete="CASCADE"),
        index=True,
    )
    fieldset: Mapped[ScenarioFieldset] = mapped_column(
        sa.Enum(
            ScenarioFieldset,
            native_enum=False,
            name="scenario_fieldset",
            # Required by project contract test_every_enum_column_has_values_callable:
            # without this SA serializes StrEnum by NAME ("TEF") but the Alembic-emitted
            # CHECK enforces VALUE ("tef") → IntegrityError at insert time.
            values_callable=lambda x: [e.value for e in x],
        ),
    )
    sme_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("subject_matter_experts.id", ondelete="RESTRICT"),
        index=True,
    )
    sme_name: Mapped[str | None] = mapped_column(sa.String(200))
    low: Mapped[float]
    high: Mapped[float]
    recorded_at: Mapped[datetime]
    recorded_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"))

    __table_args__ = (
        Index("ix_sse_scenario_fieldset", "scenario_id", "fieldset"),
        CheckConstraint("low > 0", name="ck_sse_low_positive"),
        CheckConstraint("high >= low", name="ck_sse_high_ge_low"),
        CheckConstraint(
            "fieldset != 'vuln' OR (low <= 1.0 AND high <= 1.0)",
            name="ck_sse_vuln_upper_bound",
        ),
        CheckConstraint(
            "(sme_id IS NULL) != (sme_name IS NULL)",
            name="ck_sse_sme_id_xor_name",
        ),
    )
