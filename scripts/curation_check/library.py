"""Read-only loaders for the scenario and control seed libraries (spec §3)."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from scripts.curation_check.config import REPO_ROOT

SCENARIO_FILES = (
    Path("data/seed_library_entries.json"),
    Path("data/seed_library_entries_extension.json"),
)
CONTROL_FILE = Path("data/seed_control_library_entries.json")


@dataclass(frozen=True)
class ScenarioItem:
    slug: str
    name: str
    description: str
    status: str
    threat_event_type: str
    asset_class: str
    threat_actor_type: str

    def state(self) -> dict[str, str]:
        return {"scenario_name": self.name, "scenario_description": self.description}


@dataclass(frozen=True)
class ControlItem:
    slug: str
    name: str
    description: str
    status: str
    functions: frozenset[str]
    dropped: dict[str, str] = field(default_factory=dict, compare=False, hash=False)

    def state(self) -> dict[str, str]:
        return {"control_name": self.name, "control_description": self.description}


def load_scenarios(root: Path = REPO_ROOT) -> list[ScenarioItem]:
    items: list[ScenarioItem] = []
    for rel in SCENARIO_FILES:
        for e in json.loads((root / rel).read_text(encoding="utf-8")):
            items.append(
                ScenarioItem(
                    slug=e["slug"],
                    name=e["name"],
                    description=" ".join(str(e["description"]).split()),
                    status=e["status"],
                    threat_event_type=str(e["threat_event_type"]),
                    asset_class=str(e["asset_class"]),
                    threat_actor_type=str(e["threat_actor_type"]),
                )
            )
    return items


def load_controls(root: Path = REPO_ROOT) -> list[ControlItem]:
    data = json.loads((root / CONTROL_FILE).read_text(encoding="utf-8"))
    dropped: dict[str, dict[str, str]] = {}
    for drop in data.get("_meta", {}).get("claim_drops", []):
        for fn in drop["dropped"]:
            per_slug = dropped.setdefault(drop["slug"], {})
            if fn in per_slug:
                raise ValueError(f"claim_drops lists {drop['slug']!r} / {fn!r} twice")
            per_slug[fn] = drop["reason"]
    return [
        ControlItem(
            slug=e["slug"],
            name=e["name"],
            description=" ".join(str(e["description"]).split()),
            status=e["status"],
            functions=frozenset(a["sub_function"] for a in e["assignments"]),
            dropped=dropped.get(e["slug"], {}),
        )
        for e in data["entries"]
    ]


def seed_hashes(root: Path = REPO_ROOT) -> dict[str, str]:
    return {
        str(rel): hashlib.sha256((root / rel).read_bytes()).hexdigest()
        for rel in (*SCENARIO_FILES, CONTROL_FILE)
    }


def match_options(
    scenarios: Iterable[ScenarioItem], exclude_slug: str | None = None
) -> tuple[dict[str, str], dict[str, str]]:
    """Option name → description, and option name → slug. Names must be unique."""
    options: dict[str, str] = {}
    name_to_slug: dict[str, str] = {}
    for s in scenarios:
        if s.slug == exclude_slug:
            continue
        if s.name in name_to_slug:
            raise ValueError(
                f"library entries {name_to_slug[s.name]!r} and {s.slug!r} share the name {s.name!r}; "
                "matching questions need unique names"
            )
        options[s.name] = s.description
        name_to_slug[s.name] = s.slug
    return options, name_to_slug
