"""Issue #203: every industry string in the seed files is an ``IndustryType`` member.

The library browse filter keeps only query values that are ``IndustryType`` members
(``routes/library.py`` ``_multi``) and the repository matches ``applicable_industries``
against those values, while the facet builder labels unknown strings with the raw string.
A non-enum value in the seeds therefore produces a facet option that looks selectable but
applies no filter. This guard is independent of the loader's own validator: it reads both
seed files directly.
"""

from __future__ import annotations

import json
from pathlib import Path

import idraa
from idraa.models.enums import IndustryType
from tests.integration.test_library_loss_differentiation import _IND2SEC

_ROOT = Path(idraa.__file__).resolve().parent.parent.parent
_SEED_FILES = ("seed_library_entries.json", "seed_library_entries_extension.json")
_EXPECTED_ENTRY_COUNT = 102
_MEMBERS = frozenset(m.value for m in IndustryType)


def _load_all() -> list[dict]:
    entries: list[dict] = []
    for name in _SEED_FILES:
        entries.extend(json.loads((_ROOT / "data" / name).read_text(encoding="utf-8")))
    return entries


def test_scanned_the_full_seed_set() -> None:
    # An empty or truncated read must not let the assertions below pass vacuously.
    assert len(_load_all()) == _EXPECTED_ENTRY_COUNT


def test_every_applicable_industries_item_is_an_industry_type_member() -> None:
    stray = sorted(
        {
            (e["slug"], item)
            for e in _load_all()
            for item in (e.get("applicable_industries") or [])
            if item not in _MEMBERS
        }
    )
    assert stray == [], f"non-IndustryType applicable_industries items: {stray}"


def test_every_calibration_anchor_industry_is_an_industry_type_member() -> None:
    stray = sorted(
        {
            (e["slug"], e["calibration_anchor"]["industry"])
            for e in _load_all()
            if e["calibration_anchor"]["industry"] not in _MEMBERS
        }
    )
    assert stray == [], f"non-IndustryType calibration_anchor.industry values: {stray}"


def test_no_duplicate_within_an_applicable_industries_list() -> None:
    dupes = sorted(
        (e["slug"], e["applicable_industries"])
        for e in _load_all()
        if e.get("applicable_industries") is not None
        and len(set(e["applicable_industries"])) != len(e["applicable_industries"])
    )
    assert dupes == [], f"duplicate items in applicable_industries: {dupes}"


def test_every_calibration_anchor_industry_resolves_to_an_envelope_sector() -> None:
    # Closes the generator fallback path: an anchor that is a member but absent from the
    # envelope map would silently fall through to ``applicable_industries[0]`` (see
    # ``_sector`` in tests/integration/test_library_loss_differentiation.py).
    unresolved = sorted(
        {
            (e["slug"], e["calibration_anchor"]["industry"])
            for e in _load_all()
            if e["calibration_anchor"]["industry"] not in _IND2SEC
        }
    )
    assert unresolved == [], f"calibration_anchor.industry not in _IND2SEC: {unresolved}"
