# tests/contracts/_registries.py
"""Plain-module registry helpers for PR ρ schema-consistency tests.

Populated incrementally:
- F6 adds ``field_sync_pairs()`` (loads field-sync config from pyproject.toml).
- F7 adds ``all_orm_entities()`` (auto via ``Base.registry.mappers``) +
  ``all_pydantic_schemas()`` (manual list).

This module is intentionally NOT ``conftest.py`` — pytest treats conftest.py
as a fixture-and-hook discovery surface; importing helper functions from it
risks double-collection at session start. Test modules import directly from
``tests.contracts._registries``.
"""

from __future__ import annotations

import importlib
import tomllib
from pathlib import Path
from typing import Any


def _project_root() -> Path:
    """Locate pyproject.toml by walking up from this file."""
    here = Path(__file__).resolve()
    for parent in [here, *list(here.parents)]:
        if (parent / "pyproject.toml").is_file():
            return parent
    raise RuntimeError("pyproject.toml not found above tests/contracts/_registries.py")


def _load_pyproject() -> dict[str, Any]:
    pyproject_path = _project_root() / "pyproject.toml"
    with pyproject_path.open("rb") as f:
        return tomllib.load(f)


def _import_class(dotted_path: str) -> type:
    module_path, class_name = dotted_path.rsplit(".", 1)
    module = importlib.import_module(module_path)
    cls = getattr(module, class_name)
    assert isinstance(cls, type), f"{dotted_path!r} is not a class"
    return cls


def field_sync_pairs() -> list[tuple[str, type, type, set[str], frozenset[str]]]:
    """Return list of (case_id, orm_class, dto_class, allowlist, dto_only_allowlist) tuples.

    One tuple per (entity, dto) combination from
    [tool.idraa.contracts.field_sync.*] blocks.

    case_id is the entity name + DTO short-name, used as pytest parametrize id
    so test failures surface the specific case (e.g., 'scenario:ScenarioForm').
    """
    config = _load_pyproject()
    blocks = config.get("tool", {}).get("idraa", {}).get("contracts", {}).get("field_sync", {})

    pairs: list[tuple[str, type, type, set[str], frozenset[str]]] = []
    for entity_name, block in blocks.items():
        orm_class = _import_class(block["orm"])
        allowlist = set(block.get("allowlist", []))
        dto_only_allowlist = frozenset(block.get("dto_only_allowlist", []))
        for dto_dotted in block["dto_classes"]:
            dto_class = _import_class(dto_dotted)
            case_id = f"{entity_name}:{dto_class.__name__}"
            pairs.append((case_id, orm_class, dto_class, allowlist, dto_only_allowlist))
    return pairs


# ---- ORM entity registry (for snapshot test) ----


def all_orm_entities() -> list[tuple[str, type]]:
    """Return list of (entity_short_name, orm_class) pairs.

    Auto-discovered via SQLAlchemy's declarative mapper registry. The import
    of ``idraa.models`` is what registers all 17 model classes onto Base
    (verified: idraa/models/__init__.py imports every concrete class).
    Adding a new model class to idraa.models = it shows up here
    automatically.

    The entity_short_name is ``cls.__name__`` (PascalCase) — used as the
    pytest parametrize id AND as the snapshot file basename.
    """
    # Importing idraa.models registers all mappers on the Base.
    from idraa import models as _models  # noqa: F401  (registration import)
    from idraa.db import Base

    pairs: list[tuple[str, type]] = []
    for mapper in Base.registry.mappers:
        cls = mapper.class_
        entity_id = cls.__name__  # e.g., "Scenario", "ControlFunctionAssignment"
        pairs.append((entity_id, cls))
    # Sort for deterministic parametrize id ordering.
    return sorted(pairs, key=lambda p: p[0])


# ---- Pydantic schema registry (for snapshot test) ----

# Manual list. Adding a new schema = adding an import here. Explicit list
# is shorter than a sentinel-base-class auto-discovery and easier to debug.

_PYDANTIC_SCHEMAS_DOTTED: tuple[str, ...] = (
    "idraa.schemas.control.ControlFunctionAssignmentDTO",
    "idraa.schemas.control.ControlForm",
    "idraa.schemas.organization.OrganizationForm",
    "idraa.schemas.overlay.OverlayForm",
    "idraa.schemas.overlay.OverlayDeactivateForm",
    "idraa.schemas.run.RunTriggerForm",
    "idraa.schemas.run.RunStatusDTO",
    "idraa.schemas.run.RunDetailDTO",
    "idraa.schemas.run_snapshot.ControlSnapshotV1",
    "idraa.schemas.run_snapshot.ControlSnapshotV2",
    "idraa.schemas.run_snapshot.ControlSnapshotV3",
    "idraa.schemas.run_snapshot.ControlFunctionAssignmentSnapshotDTO",
    "idraa.schemas.scenario.ScenarioForm",
)


def all_pydantic_schemas() -> list[tuple[str, type]]:
    """Return list of (schema_short_name, schema_class) pairs.

    schema_short_name is ``cls.__name__`` — used as parametrize id AND as
    snapshot file basename.
    """
    pairs: list[tuple[str, type]] = []
    for dotted in _PYDANTIC_SCHEMAS_DOTTED:
        cls = _import_class(dotted)
        pairs.append((cls.__name__, cls))
    return sorted(pairs, key=lambda p: p[0])
