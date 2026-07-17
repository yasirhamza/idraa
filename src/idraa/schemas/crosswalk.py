"""Seed-validation schema for the Framework→FAIR-CAM crosswalk (P2a).

``CrosswalkSeed`` validates each ``data/seed_framework_crosswalk.json`` entry at
seed-load / migration time. It is a SEED-VALIDATION schema, not a contract DTO —
do NOT register it in any DTO/contract snapshot registry.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, model_validator

from idraa.models.enums import FairCamSubFunction


class CrosswalkSeed(BaseModel):
    model_config = ConfigDict(extra="forbid")

    framework: str
    framework_version: str
    code: str
    title: str
    asset_type: str | None = None
    security_function: str | None = None
    citation: dict[str, Any]
    # Faithful transcription of the FAIR-Institute source X-marks — never edited
    # by Idraa methodology decisions.
    fair_cam_functions: list[FairCamSubFunction]
    # Idraa-added functions (methodology decisions; #437 rollout T1/T2). Kept
    # structurally separate from the canonical layer per the layered-override
    # convention (#449); per-entry rationale lives in citation.riskflow_extension.
    # (the stored riskflow_extension* keys and their citation text retain the
    # historical project naming; frozen data contracts — see the rename design
    # doc: docs/superpowers/specs/2026-07-17-idraa-rename-design.md.)
    riskflow_extension_functions: list[FairCamSubFunction] = []

    @model_validator(mode="after")
    def _extension_layer_disjoint_and_documented(self) -> CrosswalkSeed:
        overlap = set(self.fair_cam_functions) & set(self.riskflow_extension_functions)
        if overlap:
            raise ValueError(
                "riskflow_extension_functions overlaps the FAIR-Institute base "
                f"layer: {sorted(fn.value for fn in overlap)}"
            )
        if self.riskflow_extension_functions and not self.citation.get("riskflow_extension"):
            raise ValueError(
                "riskflow_extension_functions present without a "
                "citation.riskflow_extension rationale — every extension must "
                "document its methodology decision"
            )
        return self
