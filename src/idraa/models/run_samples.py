"""RunSamples — the heavy per-iteration Monte Carlo arrays, split off the run.

1:1 with risk_analysis_runs (run_id is both PK and FK). Loaded only for full-
distribution plotting / CSV export — never on list/dashboard paths (#294).
Deleting this row = purge-samples; ON DELETE CASCADE = delete-run (#297).
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any

from sqlalchemy import JSON, ForeignKey, LargeBinary
from sqlalchemy import Uuid as UuidType
from sqlalchemy.orm import Mapped, mapped_column

from idraa.db import Base
from idraa.models._types import UtcDateTime, now_utc
from idraa.models.mixins import OrgMixin


class RunSamples(OrgMixin, Base):
    __tablename__ = "run_samples"

    run_id: Mapped[uuid.UUID] = mapped_column(
        UuidType(as_uuid=True),
        ForeignKey("risk_analysis_runs.id", ondelete="CASCADE"),
        primary_key=True,
    )
    # Legacy JSON store. Nullable as of the arrays_codec migration: new writers
    # prefer arrays_codec (services/sample_codec.py); existing rows are
    # untouched. arrays and arrays_codec are never both empty for a given row.
    arrays: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    # Compressed binary MC arrays (services/sample_codec.py). Preferred store.
    arrays_codec: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    # Phase 1 MC-seed reproducibility: map of {scenario_id: spawn_index} used
    # to derive per-scenario child seeds from random_seed on risk_analysis_runs.
    # Nullable so existing run_samples rows remain valid without backfill.
    derived_seed_keys: Mapped[dict[str, int] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        UtcDateTime, default=now_utc, nullable=False
    )
