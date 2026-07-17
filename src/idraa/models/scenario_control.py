"""Scenario↔Control many-to-many join.

CASCADE on scenario_id: deleting a scenario removes its control refs.
RESTRICT on control_id: deleting a Control referenced by any scenario
is blocked at DB level (audit-grade reference posture).
"""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from idraa.db import Base


class ScenarioControl(Base):
    __tablename__ = "scenario_controls"

    scenario_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("scenarios.id", ondelete="CASCADE"),
        primary_key=True,
    )
    control_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("controls.id", ondelete="RESTRICT"),
        primary_key=True,
    )
