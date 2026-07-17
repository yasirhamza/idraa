"""Offline builder: MITRE ATT&CK STIX bundles → data/seed_attack_catalog.json.

Dev-only (like scripts/build_crosswalk_seed.py). NOT imported by migrations.

Usage:
    uv run python scripts/build_attack_catalog_seed.py \
        --enterprise-bundle /path/to/enterprise-attack.json \
        --ics-bundle /path/to/ics-attack.json \
        --accessed 2026-07-04 \
        --source-commit <attack-stix-data commit sha> \
        --out data/seed_attack_catalog.json

Bundles come from the official MITRE repo (do NOT commit them — ~40MB):
    https://github.com/mitre-attack/attack-stix-data
    enterprise-attack/enterprise-attack.json , ics-attack/ics-attack.json

Fail-loud discipline (crosswalk_reconciliation precedent): structure drift in
the source bundles must break the build, never silently drop rows. Every
emitted row is validated via the Task-2 seed schemas before writing.

Extraction rules:
- tactics: ``x-mitre-tactic`` objects; kill-chain order from the domain's
  ``x-mitre-matrix.tactic_refs``; skip deprecated/revoked tactics.
- techniques: ``attack-pattern`` objects with ``x_mitre_is_subtechnique`` false
  and neither ``revoked`` nor ``x_mitre_deprecated`` true (PR 1 seeds a clean
  current catalog; the ``deprecated`` column exists for FUTURE version-refresh
  migrations to flag removals without breaking mapping FKs).
- descriptions: first paragraph only, ``(Citation: ...)`` markers stripped.
- attack_version: from the bundle's ``x-mitre-collection.x_mitre_version``.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from idraa.schemas.attack_catalog import AttackTacticSeed, AttackTechniqueSeed

_KILL_CHAIN_BY_DOMAIN = {"enterprise": "mitre-attack", "ics": "mitre-ics-attack"}
_CITATION_MARKER = re.compile(r"\s*\(Citation:[^)]*\)")


def _die(msg: str) -> None:
    raise SystemExit(f"build_attack_catalog_seed: {msg}")


def _mitre_ref(obj: dict) -> dict:
    for ref in obj.get("external_references", []):
        if ref.get("source_name") == "mitre-attack":
            if not ref.get("external_id") or not ref.get("url"):
                _die(f"mitre-attack reference missing id/url on {obj.get('name')!r}")
            return ref
    _die(f"no mitre-attack external reference on {obj.get('name')!r}")
    raise AssertionError  # unreachable; _die raises


def _first_paragraph(text: str | None) -> str | None:
    if not text:
        return None
    para = text.strip().split("\n\n", 1)[0]
    return _CITATION_MARKER.sub("", para).strip() or None


def _is_dead(obj: dict) -> bool:
    return bool(obj.get("revoked")) or bool(obj.get("x_mitre_deprecated"))


def collection_version(bundle: dict) -> str:
    for obj in bundle["objects"]:
        if obj.get("type") == "x-mitre-collection":
            version = obj.get("x_mitre_version")
            if not version:
                _die("x-mitre-collection has no x_mitre_version")
            return str(version)
    _die("bundle has no x-mitre-collection object")
    raise AssertionError


def extract_tactics(bundle: dict, domain: str) -> list[dict]:
    matrices = [o for o in bundle["objects"] if o.get("type") == "x-mitre-matrix"]
    if len(matrices) != 1:
        _die(f"{domain}: expected exactly 1 x-mitre-matrix, found {len(matrices)}")
    order = {stix_id: i for i, stix_id in enumerate(matrices[0]["tactic_refs"])}

    rows: list[dict] = []
    for obj in bundle["objects"]:
        if obj.get("type") != "x-mitre-tactic" or _is_dead(obj):
            continue
        if obj["id"] not in order:
            _die(f"{domain}: tactic {obj.get('name')!r} not in matrix tactic_refs")
        ref = _mitre_ref(obj)
        rows.append(
            {
                "domain": domain,
                "tactic_id": ref["external_id"],
                "shortname": obj["x_mitre_shortname"],
                "name": obj["name"],
                "description": _first_paragraph(obj.get("description")),
                "display_order": order[obj["id"]],
                "url": ref["url"],
            }
        )
    if not rows:
        _die(f"{domain}: no tactics extracted")
    rows.sort(key=lambda r: r["display_order"])
    return rows


def extract_techniques(bundle: dict, domain: str, tactic_shortnames: set[str]) -> list[dict]:
    kill_chain = _KILL_CHAIN_BY_DOMAIN[domain]
    rows: list[dict] = []
    for obj in bundle["objects"]:
        if obj.get("type") != "attack-pattern" or _is_dead(obj):
            continue
        if obj.get("x_mitre_is_subtechnique"):
            continue
        ref = _mitre_ref(obj)
        phases = [
            p["phase_name"]
            for p in obj.get("kill_chain_phases", [])
            if p.get("kill_chain_name") == kill_chain
        ]
        if not phases:
            _die(f"{domain}: technique {ref['external_id']} has no {kill_chain} phases")
        unresolved = sorted(set(phases) - tactic_shortnames)
        if unresolved:
            _die(
                f"{domain}: technique {ref['external_id']} references unknown "
                f"tactic shortnames {unresolved} — source drift, refusing to drop"
            )
        rows.append(
            {
                "domain": domain,
                "technique_id": ref["external_id"],
                "name": obj["name"],
                "description": _first_paragraph(obj.get("description")),
                "tactics": phases,
                "url": ref["url"],
            }
        )
    if not rows:
        _die(f"{domain}: no techniques extracted")
    rows.sort(key=lambda r: r["technique_id"])
    return rows


# Meth-N4: this constant is the SINGLE source of the required attribution
# sentence — the NOTICE quotes the same wording. Task-3 Step 6 verifies it
# against MITRE's current Terms of Use and updates it here before generating.
_MITRE_COPYRIGHT = (
    "© 2026 The MITRE Corporation. This work is reproduced and "
    "distributed with the permission of The MITRE Corporation."
)


def build_catalog(
    enterprise_bundle: dict, ics_bundle: dict, *, accessed: str, source_commit: str
) -> dict:
    attribution: dict = {}
    tactics: list[dict] = []
    techniques: list[dict] = []

    for domain, bundle in (("enterprise", enterprise_bundle), ("ics", ics_bundle)):
        version = collection_version(bundle)
        attribution[domain] = {
            "source": "MITRE ATT&CK",
            "copyright": _MITRE_COPYRIGHT,
            "license": "MITRE ATT&CK Terms of Use",
            "document": f"ATT&CK {'Enterprise' if domain == 'enterprise' else 'ICS'} Matrix",
            "attack_version": version,
            # Sec-N2: attack-stix-data commit the bundles were fetched at —
            # commit-hash + accessed-date per the primary-cited-gate convention
            # for non-paginated sources.
            "source_commit": source_commit,
            "accessed": accessed,
            "note": "See data/seed_attack_catalog.NOTICE.md.",
        }
        domain_tactics = extract_tactics(bundle, domain)
        shortnames = {t["shortname"] for t in domain_tactics}
        domain_techniques = extract_techniques(bundle, domain, shortnames)
        citation = {
            k: attribution[domain][k]
            for k in (
                "source",
                "copyright",
                "license",
                "document",
                "attack_version",
                "source_commit",
                "accessed",
            )
        }
        for t in domain_techniques:
            t["citation"] = citation

        # Validate every row through the seed schemas — bad extraction fails here.
        for t in domain_tactics:
            AttackTacticSeed.model_validate(t)
        for t in domain_techniques:
            AttackTechniqueSeed.model_validate(t)

        tactics.extend(domain_tactics)
        techniques.extend(domain_techniques)

    return {"_attribution": attribution, "tactics": tactics, "techniques": techniques}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--enterprise-bundle", required=True, type=Path)
    parser.add_argument("--ics-bundle", required=True, type=Path)
    parser.add_argument("--accessed", required=True, help="YYYY-MM-DD access date")
    parser.add_argument(
        "--source-commit",
        required=True,
        help="attack-stix-data commit hash the bundles were fetched at",
    )
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    enterprise = json.loads(args.enterprise_bundle.read_text(encoding="utf-8"))
    ics = json.loads(args.ics_bundle.read_text(encoding="utf-8"))
    catalog = build_catalog(
        enterprise, ics, accessed=args.accessed, source_commit=args.source_commit
    )
    args.out.write_text(json.dumps(catalog, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(
        f"wrote {args.out}: {len(catalog['tactics'])} tactics, "
        f"{len(catalog['techniques'])} techniques "
        f"(enterprise {catalog['_attribution']['enterprise']['attack_version']}, "
        f"ics {catalog['_attribution']['ics']['attack_version']})"
    )


if __name__ == "__main__":
    main()
