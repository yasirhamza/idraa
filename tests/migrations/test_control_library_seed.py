"""Seed-shape + §7 crosswalk-validation pinning over the REAL control-library seed (P2b).

Two guards:
1. test_every_seed_entry_validates — every entry round-trips ControlLibraryEntrySeed
   (unit bounds, virtual reject, unique sub-functions), ≥61 seeded-or-escalated, and
   every _meta.escalated / _meta.claim_drops item carries a documented reason.
2. test_every_seed_entry_claims_are_crosswalk_supported — loads BOTH the P2a crosswalk
   seed and the P2b control-library seed into the ORM (the harness uses create_all, not
   migrations — gate Arch-I2) and asserts no entry over-claims a FAIR-CAM function its
   NIST/CIS tags do not ground.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import idraa
from idraa.models.enums import FairCamSubFunction
from idraa.models.framework_crosswalk import FrameworkControl, FrameworkControlFairCam
from idraa.schemas.control_library import ControlLibraryEntrySeed
from idraa.services.control_library_validation import unsupported_claims_for_entry


def _payload() -> dict:
    p = (
        Path(idraa.__file__).resolve().parent.parent.parent
        / "data"
        / "seed_control_library_entries.json"
    )
    return json.loads(p.read_text(encoding="utf-8"))


def test_every_seed_entry_validates() -> None:
    payload = _payload()
    entries = payload["entries"]
    # Target: all 61 CSV controls (D2). Floor allows for documented _meta.escalated
    # exclusions; any control not seeded MUST appear in _meta.escalated with a reason.
    escalated = payload.get("_meta", {}).get("escalated", [])
    assert len(entries) + len(escalated) >= 63, (
        "every CSV control is seeded or escalated (61 CSV-derived + 2 #459 gap entries)"
    )
    # Methodology guardrail: any escalated/claim-dropped item must carry a reason (Spec-4).
    for item in escalated:
        assert item.get("reason"), "escalated control must document a reason"
    claim_drops = payload.get("_meta", {}).get("claim_drops", [])
    for item in claim_drops:
        assert item.get("reason") and item.get("dropped"), (
            "claim_drop must list dropped claims + reason"
        )
    # No (slug, dropped-item) pair may appear twice — guards against a later re-drop
    # accidentally overwriting an earlier rationale (#437 rollout T2 regression).
    claim_drop_pairs = [(item["slug"], d) for item in claim_drops for d in item.get("dropped", [])]
    dupes = [p for p in set(claim_drop_pairs) if claim_drop_pairs.count(p) > 1]
    assert not dupes, f"duplicate (slug, dropped-item) in claim_drops: {dupes}"
    for raw in entries:
        seed = ControlLibraryEntrySeed.model_validate(raw)  # raises on bad shape/unit/virtual
        assert seed.assignments
    slugs = [e["slug"] for e in entries]
    assert len(slugs) == len(set(slugs)), "duplicate slug in seed"


async def _load_crosswalk(db) -> None:
    p = (
        Path(idraa.__file__).resolve().parent.parent.parent
        / "data"
        / "seed_framework_crosswalk.json"
    )
    payload = json.loads(p.read_text(encoding="utf-8"))
    for e in payload["entries"]:
        fc = FrameworkControl(
            framework=e["framework"],
            framework_version=e["framework_version"],
            code=e["code"],
            title=e["title"],
            description=None,
            asset_type=e.get("asset_type"),
            security_function=e.get("security_function"),
            citation=e["citation"],
        )
        db.add(fc)
        await db.flush()
        # #449: compose base + Idraa-extension layers (mirrors the seed
        # migration's load-time composition), carrying link-level provenance.
        for slug, is_ext in [
            *((f, False) for f in e["fair_cam_functions"]),
            *((f, True) for f in e.get("riskflow_extension_functions", [])),
        ]:
            db.add(
                FrameworkControlFairCam(
                    framework_control_id=fc.id,
                    fair_cam_function=FairCamSubFunction(slug),
                    is_extension=is_ext,
                )
            )
    await db.flush()


@pytest.mark.asyncio
async def test_every_seed_entry_claims_are_crosswalk_supported(db_session) -> None:
    await _load_crosswalk(db_session)
    for raw in _payload()["entries"]:
        claimed = [FairCamSubFunction(a["sub_function"]) for a in raw["assignments"]]
        unsupported = await unsupported_claims_for_entry(
            db_session,
            nist_csf_subcategories=raw.get("nist_csf_subcategories", []),
            cis_safeguards=raw.get("cis_safeguards", []),
            claimed=claimed,
        )
        assert unsupported == set(), (
            f"{raw['slug']} over-claims (no crosswalk support): {unsupported}"
        )
