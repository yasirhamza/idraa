"""Pydantic validator for data/seed_qualitative_bands.json (epic #34 P1b).

Used at migration time to validate every canonical band before DB insert, so
seed-data corruption surfaces at ``alembic upgrade`` rather than silently at
first query. Mirrors ``idraa.services.seed_library_loader.LibraryEntrySeed``.

The migration (``alembic/versions/<rev>_qualitative_mapping_bands.py``) imports
ONLY ``BandSeed`` from this module — ``json.loads``, the seed-file path anchor,
and the INSERT loop live inline in the migration (precedent
``c1d2e3f4a5b6_seed_library_entries.py``) so a later refactor of this module
cannot silently break ``alembic upgrade`` on a fresh database.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class BandSeed(BaseModel):
    """One row of ``data/seed_qualitative_bands.json`` — a canonical band.

    Ordering is validated non-strict on the ``low <= mode`` / ``mode <= high``
    edges (a degenerate band where mode coincides with an edge is legal) but
    strict on ``low < high`` (a zero-width band is never a valid PERT source).
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["frequency", "magnitude"]
    label: str = Field(pattern=r"^[a-z_]+$")
    low: float
    mode: float
    high: float
    sort_order: int
    derivation: str = Field(min_length=40)

    @model_validator(mode="after")
    def _check_ordering(self) -> BandSeed:
        if not (0 <= self.low <= self.mode <= self.high):
            raise ValueError(
                f"{self.kind}/{self.label}: expected 0 <= low <= mode <= high, "
                f"got low={self.low} mode={self.mode} high={self.high}"
            )
        if not (self.low < self.high):
            raise ValueError(f"{self.kind}/{self.label}: low must be strictly < high")
        return self
