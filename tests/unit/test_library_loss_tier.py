"""Loss-tier enum + seed-schema field tests (Epic C-i #335, Task 3)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from idraa.models.enums import LossTier
from idraa.services.seed_library_loader import LibraryEntrySeed


@pytest.fixture
def _minimal_seed_dict() -> dict[str, Any]:
    """First entry of the seed JSON, with any ``loss_tier`` key dropped.

    NEW fixture (Task 3, plan-gate spec-#3) — no existing seed fixture is
    reusable here. Built from the real seed data so it exercises the actual
    LibraryEntrySeed required-field surface, minus loss_tier (to assert the
    back-compat default fires).
    """
    raw = json.loads(Path("data/seed_library_entries.json").read_text(encoding="utf-8"))
    entry = dict(raw[0])
    entry.pop("loss_tier", None)
    return entry


def test_loss_tier_enum_values() -> None:
    assert {t.value for t in LossTier} == {"paginated", "vendor", "anecdotal", "none"}


def test_seed_defaults_loss_tier_anecdotal_when_absent(
    _minimal_seed_dict: dict[str, Any],
) -> None:
    # back-compat: an entry JSON without loss_tier parses with the exact default
    s = LibraryEntrySeed(**_minimal_seed_dict)  # _minimal_seed_dict omits loss_tier
    assert s.loss_tier == "anecdotal"  # pin the exact default (plan-gate arch-NTH)


def test_seed_accepts_explicit_loss_tier(_minimal_seed_dict: dict[str, Any]) -> None:
    s = LibraryEntrySeed(**{**_minimal_seed_dict, "loss_tier": "paginated"})
    assert s.loss_tier == "paginated"


def test_seed_rejects_unknown_loss_tier(_minimal_seed_dict: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        LibraryEntrySeed(**{**_minimal_seed_dict, "loss_tier": "bogus"})
