"""Export → import round-trip contract (Task 5, METHODOLOGY-reviewed).

THE load-bearing fidelity test. A downloaded bundle (export serializer output)
must re-import cleanly through the EXACT import validator that the runtime
upload path uses. Specifically:

  entry_to_seed_obj(each)  →  json.dumps(array)  →  parse_bundle
                           →  _validate_entries(existing_slugs=set())

must yield every entry as ``action == "add"`` with ZERO errors, and each
validated seed's authored fields must equal the source — distributions EXACT
(JSON preserves int vs float; there is no ``collapse_num`` on the JSON bundle
path, unlike the CSV scenario-import path).

Building N ≥ 3 entries pins that the serializer is not a single-entry special
case and that mixed int/float distribution values survive the JSON boundary.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from idraa.models.enums import AssetClass, ThreatActorType, ThreatCategory
from idraa.models.scenario_library import ScenarioLibraryEntry
from idraa.services.library_bundle_export import entry_to_seed_obj
from idraa.services.library_bundle_import import _validate_entries, parse_bundle

# Three entries; mixed int/float in distributions to pin JSON numeric fidelity.
_SOURCES: list[dict[str, Any]] = [
    {
        "slug": "rt-a",
        "name": "Round Trip A",
        "status": "published",
        "threat_event_type": ThreatCategory.RANSOMWARE,
        "threat_actor_type": ThreatActorType.CYBERCRIMINALS,
        "asset_class": AssetClass.SYSTEMS,
        "attack_vector": "phishing",
        "tags": ["tag-a"],
        "description": "Round-trip entry A with a 20+ character description.",
        "example_incidents": None,
        "source_citations": ["cite-a"],
        "canonical_fair_gap": "Round-trip entry A canonical FAIR gap note.",
        "applicable_industries": ["manufacturing"],
        "applicable_sub_sectors": None,
        "applicable_org_sizes": None,
        # int low, float mode/high — must survive exactly.
        "threat_event_frequency": {"distribution": "PERT", "low": 1, "mode": 2.5, "high": 4},
        "vulnerability": {"distribution": "PERT", "low": 0.1, "mode": 0.25, "high": 0.6},
        "primary_loss": {
            "distribution": "PERT",
            "low": 100000,
            "mode": 1000000,
            "high": 15000000,
        },
        "secondary_loss": {"distribution": "PERT", "low": 50000, "mode": 500000, "high": 5000000},
        "suggested_control_ids": [],
        "standards_references": None,
        "calibration_anchor": {"industry": "other", "revenue_tier": "100m_to_1b"},
        "loss_tier": "anecdotal",
        "loss_shape": "capped",
    },
    {
        "slug": "rt-b",
        "name": "Round Trip B",
        "status": "published",
        "threat_event_type": ThreatCategory.MALWARE,
        "threat_actor_type": ThreatActorType.NATION_STATE,
        "asset_class": AssetClass.OT_SYSTEMS,
        "attack_vector": None,
        "tags": ["tag-b1", "tag-b2"],
        "description": "Round-trip entry B with a 20+ character description.",
        "example_incidents": "An example incident for B.",
        "source_citations": [],
        "canonical_fair_gap": "Round-trip entry B canonical FAIR gap note.",
        "applicable_industries": None,
        "applicable_sub_sectors": None,
        "applicable_org_sizes": None,
        "threat_event_frequency": {"distribution": "PERT", "low": 0.5, "mode": 2.0, "high": 6.0},
        "vulnerability": {"distribution": "PERT", "low": 0.05, "mode": 0.2, "high": 0.5},
        "primary_loss": {"distribution": "PERT", "low": 200000, "mode": 800000, "high": 4000000},
        "secondary_loss": None,
        "suggested_control_ids": ["ctrl-x"],
        "standards_references": {"nist_csf": ["PR.AC-1"]},
        "calibration_anchor": {"industry": "energy", "revenue_tier": "1b_to_10b"},
        "loss_tier": "vendor",
        "loss_shape": "capped",
    },
    {
        "slug": "rt-c",
        "name": "Round Trip C",
        "status": "published",
        "threat_event_type": ThreatCategory.OT_INTEGRITY,
        "threat_actor_type": ThreatActorType.INSIDER_MALICIOUS,
        "asset_class": AssetClass.SAFETY_SYSTEMS,
        "attack_vector": "supply_chain",
        "tags": [],
        "description": "Round-trip entry C with a 20+ character description.",
        "example_incidents": None,
        "source_citations": ["cite-c1", "cite-c2"],
        "canonical_fair_gap": "Round-trip entry C canonical FAIR gap note.",
        "applicable_industries": None,
        "applicable_sub_sectors": None,
        "applicable_org_sizes": None,
        "threat_event_frequency": {"distribution": "PERT", "low": 1, "mode": 3, "high": 9},
        "vulnerability": {"distribution": "PERT", "low": 0.1, "mode": 0.3, "high": 0.7},
        "primary_loss": {"distribution": "PERT", "low": 500000, "mode": 2000000, "high": 9000000},
        "secondary_loss": {"distribution": "PERT", "low": 10000, "mode": 40000, "high": 90000},
        "suggested_control_ids": [],
        "standards_references": None,
        "calibration_anchor": {"industry": "other", "revenue_tier": "100m_to_1b"},
        "loss_tier": "none",
        "loss_shape": "capped",
    },
    # rt-d: paginated + lognormal — exercises C-iii-a converted entry shape (SC-N1)
    {
        "slug": "rt-d",
        "name": "Round Trip D — lognormal paginated",
        "status": "published",
        "threat_event_type": ThreatCategory.RANSOMWARE,
        "threat_actor_type": ThreatActorType.CYBERCRIMINALS,
        "asset_class": AssetClass.SYSTEMS,
        "attack_vector": "phishing",
        "tags": ["lognormal"],
        "description": "Round-trip entry D with lognormal primary loss (paginated tier).",
        "example_incidents": None,
        "source_citations": [
            "Cyentia IRIS 2025, Figure A3, p. 35 (accessed 2026-06-10)",
        ],
        "canonical_fair_gap": "Round-trip entry D canonical FAIR gap note.",
        "applicable_industries": ["healthcare"],
        "applicable_sub_sectors": None,
        "applicable_org_sizes": None,
        "threat_event_frequency": {"distribution": "PERT", "low": 1.0, "mode": 3.0, "high": 8.0},
        "vulnerability": {"distribution": "PERT", "low": 0.1, "mode": 0.3, "high": 0.7},
        # lognormal: mean=ln(557000), sigma=ln(14000000/557000)/Z_0_95
        "primary_loss": {
            "distribution": "lognormal",
            "mean": 13.230320518909421,
            "sigma": 1.9602032155565388,
        },
        "secondary_loss": {
            "distribution": "lognormal",
            "mean": 12.131708230241312,
            "sigma": 1.9602032155565388,
        },
        "suggested_control_ids": [],
        "standards_references": None,
        "calibration_anchor": {
            "industry": "healthcare",
            "revenue_tier": "100m_to_1b",
            "loss_anchor": "IRIS 2025 Figure A3 p.35 healthcare pair; supersedes old PERT anchor",
            "vuln_posture": "inherent (control-naive) per fair-cam-methodology 'Vulnerability anchor'",
        },
        "loss_tier": "paginated",
        # Milestone B: catastrophic (lognormal loss) must survive export -> import.
        "loss_shape": "catastrophic",
        # D-i (#497): a populated loss_form_profile must survive export -> import.
        "loss_form_profile": [
            {
                "form": "response",
                "kind": "primary",
                "magnitude_basis": "IRIS 2025 Figure A3 p.35 healthcare per-record response",
                "citations": ["Cyentia IRIS 2025, Figure A3, p. 35 (accessed 2026-06-10)"],
                "verified": True,
                "composition_role": "dominant",
                "share": 0.7,
            },
            {
                "form": "reputation",
                "kind": "secondary",
                "magnitude_basis": "IRIS 2025 healthcare secondary-loss share",
                "citations": ["Cyentia IRIS 2025, Figure A3, p. 35 (accessed 2026-06-10)"],
                "verified": True,
                "composition_role": "contributing",
                "share": 0.2,
            },
        ],
    },
]

# The seed-schema field set, minus the runtime-redundant 'status' guard: every
# authored field below must survive export → import identically.
_DIST_FIELDS = ("threat_event_frequency", "vulnerability", "primary_loss", "secondary_loss")


def _entries() -> list[ScenarioLibraryEntry]:
    return [ScenarioLibraryEntry(id=uuid.uuid4(), version=1, **src) for src in _SOURCES]


def test_export_import_round_trip_all_add_zero_errors() -> None:
    entries = _entries()
    payload = json.dumps([entry_to_seed_obj(e) for e in entries])

    pairs, hard_stop = parse_bundle(payload.encode("utf-8"))
    assert hard_stop == []
    assert pairs is not None
    assert len(pairs) == len(_SOURCES)

    preview, errors, seeds = _validate_entries(pairs, existing_slugs=set())
    assert errors == []
    assert [p["action"] for p in preview] == ["add"] * len(_SOURCES)
    assert all(s is not None for s in seeds)


def test_round_trip_preserves_authored_fields_exactly() -> None:
    entries = _entries()
    payload = json.dumps([entry_to_seed_obj(e) for e in entries])
    pairs, _ = parse_bundle(payload.encode("utf-8"))
    assert pairs is not None
    _preview, _errors, seeds = _validate_entries(pairs, existing_slugs=set())

    for src, seed in zip(_SOURCES, seeds, strict=True):
        assert seed is not None
        # Scalar authored fields equal the source (enums compared by .value).
        assert seed["slug"] == src["slug"]
        assert seed["name"] == src["name"]
        assert seed["status"] == src["status"]
        assert seed["threat_event_type"] == src["threat_event_type"].value
        assert seed["threat_actor_type"] == src["threat_actor_type"].value
        assert seed["asset_class"] == src["asset_class"].value
        assert seed["attack_vector"] == src["attack_vector"]
        assert seed["tags"] == src["tags"]
        assert seed["description"] == src["description"]
        assert seed["canonical_fair_gap"] == src["canonical_fair_gap"]
        assert seed["source_citations"] == src["source_citations"]
        assert seed["applicable_industries"] == src["applicable_industries"]
        assert seed["calibration_anchor"] == src["calibration_anchor"]
        assert seed["standards_references"] == src["standards_references"]
        assert seed["suggested_control_ids"] == src["suggested_control_ids"]
        assert seed["loss_tier"] == src["loss_tier"]
        assert seed["loss_shape"] == src["loss_shape"]
        assert seed["loss_form_profile"] == src.get("loss_form_profile", [])


def test_round_trip_distributions_exact_int_float_preserved() -> None:
    entries = _entries()
    payload = json.dumps([entry_to_seed_obj(e) for e in entries])
    pairs, _ = parse_bundle(payload.encode("utf-8"))
    assert pairs is not None
    _preview, _errors, seeds = _validate_entries(pairs, existing_slugs=set())

    for src, seed in zip(_SOURCES, seeds, strict=True):
        assert seed is not None
        for field in _DIST_FIELDS:
            assert seed[field] == src[field], field
            if src[field] is None:
                continue
            dist_kind = str(src[field].get("distribution", "")).lower()
            if dist_kind == "lognormal":
                # lognormal: check mean+sigma type fidelity (float vs float).
                for key in ("mean", "sigma"):
                    if key in src[field]:
                        assert type(seed[field][key]) is type(src[field][key]), f"{field}.{key}"
            else:
                # PERT/other: int vs float type must survive the JSON boundary (no collapse_num).
                for key in ("low", "mode", "high"):
                    if key in src[field]:
                        assert type(seed[field][key]) is type(src[field][key]), f"{field}.{key}"
