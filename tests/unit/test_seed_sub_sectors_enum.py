"""Issue #241: every sub-sector string in the seed files is an ``IndustrySubSector`` member.

The library browse filter keeps only query values that are ``IndustrySubSector`` members
(``routes/library.py`` ``_multi``) and the repository matches ``applicable_sub_sectors``
against those values, while the facet builder labels unknown strings with the raw string.
A non-member value in the seeds therefore produces a facet option that looks selectable but
applies no filter. This guard reads both seed files directly, independent of any loader.

It also pins the human label for every member: ``_SUB_SECTOR_LABELS`` feeds the library
sub-sector facet, which falls back to the raw slug (``k12``, ``mvno``) when a label is absent.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import idraa
from idraa.models.enums import IndustrySubSector
from idraa.services.scenario_library import _SUB_SECTOR_LABELS

_ROOT = Path(idraa.__file__).resolve().parent.parent.parent
_SEED_FILES = ("seed_library_entries.json", "seed_library_entries_extension.json")
_EXPECTED_ENTRY_COUNT = 102
_MEMBERS = frozenset(m.value for m in IndustrySubSector)
_ORIGINAL_EIGHT = frozenset(
    {
        "chemical_manufacturing",
        "electric_utility",
        "nuclear",
        "oil_and_gas",
        "other",
        "pipeline",
        "process_manufacturing",
        "water_utility",
    }
)


def _load_all() -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for name in _SEED_FILES:
        entries.extend(json.loads((_ROOT / "data" / name).read_text(encoding="utf-8")))
    return entries


def _walk_pins(node: Any) -> Iterator[Any]:
    """Yield every non-null ``sub_sector_pin`` value found anywhere in a seed entry."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "sub_sector_pin" and value is not None:
                yield value
            yield from _walk_pins(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk_pins(item)


def test_scanned_the_full_seed_set() -> None:
    # An empty or truncated read must not let the assertions below pass vacuously.
    assert len(_load_all()) == _EXPECTED_ENTRY_COUNT


def test_every_applicable_sub_sectors_item_is_an_industry_sub_sector_member() -> None:
    stray = sorted(
        {
            (e["slug"], item)
            for e in _load_all()
            for item in (e.get("applicable_sub_sectors") or [])
            if item not in _MEMBERS
        }
    )
    assert stray == [], f"non-IndustrySubSector applicable_sub_sectors items: {stray}"


def test_the_seeds_exercise_the_extended_enum() -> None:
    # Guards the guard: the seeds must actually use values beyond the original OT/energy
    # slice, or the membership assertion above says nothing about the extension.
    used = {item for e in _load_all() for item in (e.get("applicable_sub_sectors") or [])}
    assert len(used - _ORIGINAL_EIGHT) >= 31, sorted(used)
    assert {"hospital", "k12", "mvno", "intelligence_community"} <= used


def test_any_sub_sector_pin_in_a_seed_is_an_industry_sub_sector_member() -> None:
    # No seed carries a pin today (the field left Scenario in PR pi); if one ever does,
    # it must name a member.
    stray = sorted(
        {(e["slug"], str(pin)) for e in _load_all() for pin in _walk_pins(e) if pin not in _MEMBERS}
    )
    assert stray == [], f"non-IndustrySubSector sub_sector_pin values: {stray}"


def test_every_industry_sub_sector_member_has_a_label() -> None:
    missing = sorted(m.value for m in IndustrySubSector if m not in _SUB_SECTOR_LABELS)
    assert missing == [], f"IndustrySubSector members without a facet label: {missing}"


def test_no_label_is_keyed_by_a_non_member() -> None:
    stray = sorted(str(k) for k in _SUB_SECTOR_LABELS if k not in _MEMBERS)
    assert stray == [], f"_SUB_SECTOR_LABELS keys that are not IndustrySubSector members: {stray}"


def test_every_label_is_a_distinct_human_string() -> None:
    labels = [_SUB_SECTOR_LABELS[m] for m in IndustrySubSector]
    assert len(set(labels)) == len(labels), "duplicate sub-sector facet labels"
    # A label equal to the raw slug means a member was labelled with its own key.
    raw = sorted(m.value for m in IndustrySubSector if _SUB_SECTOR_LABELS[m] == m.value)
    assert raw == [], f"labels identical to their slug: {raw}"


def test_browse_filter_keeps_extended_sub_sector_values() -> None:
    # The route's allowlist is derived from the enum: ``?sub_sector=hospital`` used to be
    # dropped silently (no narrowing), which is the bug #241 closes.
    from starlette.requests import Request

    from idraa.routes.library import _parse_browse_filters

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/library",
            "query_string": b"sub_sector=hospital&sub_sector=k12&sub_sector=not_a_sector",
            "headers": [],
        }
    )
    parsed = _parse_browse_filters(request)
    assert parsed.applicable_sub_sectors == [IndustrySubSector.HOSPITAL, IndustrySubSector.K12]
