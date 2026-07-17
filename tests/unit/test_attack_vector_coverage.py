"""§7 vector-marginal coverage guard (attack-coverage gap-fill epic, #529).

The epic's root cause was an un-audited marginal: initial-access techniques
for edge-appliance exploitation (T1190/T1133), client-side exploitation
(T1203), transient-device compromise (T0864), removable media (T0847), and
the destructive/wiper impact pattern (T1485) all had ZERO mapped library
entries -- invisibly, across four prior epics, until a manual reverse-audit
surfaced them (design doc §1). This module is the structural fix: it makes
that class of gap fail-loud going forward instead of requiring another
manual audit.

Three pieces (design doc §7):
  1. MUST_COVER assertion -- both scenario-defining axes (initial-access +
     impact) each require >=1 mapped library entry in the crosswalk.
  2. Informational marginal report -- prints the attack_vector distribution,
     MUST_COVER technique coverage counts, and the §6.2 consciously-deferred
     niche techniques with their rationale. Not a hard gate.
  3. Canonical-vector-class soft check -- every attack_vector value used in
     the library must be catalogued in docs/reference/attack-vector-classes.md.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import idraa

_ROOT = Path(idraa.__file__).resolve().parent.parent.parent
_DATA = _ROOT / "data"
_CLASS_DOC = _ROOT / "docs" / "reference" / "attack-vector-classes.md"

_MAPPING_FILES = (
    "seed_attack_full_mappings.json",
    "seed_attack_d_iii_b_full.json",
    "seed_attack_avgapfill_full.json",
    "seed_attack_exemplar_mappings.json",
)
_LIBRARY_FILES = ("seed_library_entries.json", "seed_library_entries_extension.json")


def _mappings(name: str) -> list[dict]:
    return json.loads((_DATA / name).read_text(encoding="utf-8"))["mappings"]


def _all_mappings() -> list[dict]:
    """Union of the full crosswalk across all four mapping seed files.

    Mirrors tests/migrations/test_attack_full_mappings_seed.py's
    _d_iii_b_full()/_avgapfill_full() union pattern -- this is a read-only
    coverage guard, not a migration test, so unioning all four sources here
    is safe (it doesn't feed any pinned migration-count assertion)."""
    out: list[dict] = []
    for f in _MAPPING_FILES:
        out.extend(_mappings(f))
    return out


def _library_entries() -> list[dict]:
    out: list[dict] = []
    for f in _LIBRARY_FILES:
        out.extend(json.loads((_DATA / f).read_text(encoding="utf-8")))
    return out


# ---------------------------------------------------------------------------
# §7.1: MUST_COVER -- both scenario-defining axes
# ---------------------------------------------------------------------------

MUST_COVER_INITIAL_ACCESS = frozenset(
    {
        "T1566",  # Phishing
        "T1078",  # Valid Accounts
        "T1190",  # Exploit Public-Facing Application
        "T1133",  # External Remote Services
        "T1203",  # Exploitation for Client Execution
        "T1195",  # Supply Chain Compromise
        "T0864",  # Transient Cyber Asset (ICS)
        "T0847",  # Replication Through Removable Media (ICS)
        "T1498",  # Network Denial of Service
    }
)
MUST_COVER_IMPACT = frozenset(
    {
        "T1486",  # Data Encrypted for Impact
        "T1485",  # Data Destruction
    }
)
MUST_COVER = MUST_COVER_INITIAL_ACCESS | MUST_COVER_IMPACT


def test_must_cover_axes_partition_is_exactly_11() -> None:
    """Structural sanity on the frozensets themselves before trusting the
    coverage assertion below."""
    assert len(MUST_COVER_INITIAL_ACCESS) == 9
    assert len(MUST_COVER_IMPACT) == 2
    assert MUST_COVER_INITIAL_ACCESS.isdisjoint(MUST_COVER_IMPACT)
    assert len(MUST_COVER) == 11


def test_must_cover_techniques_have_at_least_one_mapped_entry() -> None:
    """§7.1: every high-prevalence technique across both scenario-defining
    axes must have >=1 mapped library entry in the crosswalk (union of
    seed_attack_full_mappings.json + seed_attack_d_iii_b_full.json +
    seed_attack_avgapfill_full.json + seed_attack_exemplar_mappings.json).

    This is the guard that turns "edge/client/transient/removable-media/
    wiper = 0" from invisible into a red test. Pre-epic, T1203 (client
    exploitation), T0864 (transient asset), and T0847 (removable media) had
    ZERO mapped entries anywhere in the crosswalk -- this assertion is what
    would have caught that gap; post-epic all three are covered exclusively
    by data/seed_attack_avgapfill_full.json (the #529 mapping file).
    """
    mapped_techniques = {m["technique_id"] for m in _all_mappings()}
    uncovered = MUST_COVER - mapped_techniques
    assert not uncovered, (
        f"MUST_COVER technique(s) with zero mapped library entries: {sorted(uncovered)} -- "
        f"this is the exact failure mode (edge/client/transient/removable-media/wiper "
        f"reading as coverage-zero) the attack-coverage gap-fill epic (#529) exists to catch."
    )


# ---------------------------------------------------------------------------
# §7.2: informational marginal report (no hard assert on content)
# ---------------------------------------------------------------------------

# §6.2 (design doc): niche initial-access techniques consciously deferred,
# not silently dropped. Logged here so future curation sees the marginal +
# the deferrals instead of rediscovering them by accident.
_CONSCIOUSLY_DEFERRED = {
    "T0848": (
        "Rogue Master (ICS) -- partly covered by grid-protective-relay-manipulation / "
        "pipeline-scada-integrity (protocol-manipulation OT); a dedicated master-spoofing "
        "entry is deferred as low marginal value over those."
    ),
    "T0819": (
        "Exploit Public-Facing Application (ICS) -- enterprise twin T1190 is covered "
        "(edge-ransomware-perimeter-gateway / edge-espionage-nationstate / "
        "web-app-exploitation); no existing entry faithfully models exploitation of an "
        "exposed OT app, so the ICS twin is deferred rather than force-fit onto a "
        "credential entry (hmi-credential-compromise)."
    ),
    "T1659": (
        "Content Injection -- MitM/traffic content-injection; low prevalence in the "
        "FAIR loss corpus, deferred."
    ),
    "T1669": (
        "Wi-Fi Networks (enterprise) -- distinct from the OT field-wireless T0860 this "
        "epic covers (ot-wireless-field-network-compromise); deferred as low-frequency "
        "relative to the 9 authored entries."
    ),
}


def test_vector_marginal_report() -> None:
    """Informational: prints the attack_vector distribution, per-MUST_COVER-
    technique mapped-entry counts, and the consciously-deferred niche
    technique log. Exists so future curation SEES the marginal instead of
    rediscovering it by accident (design doc §7.2). Not a hard gate --
    the assertion at the end is trivially true; the point is the printed
    report (run with `-s` to see it).
    """
    entries = _library_entries()
    vec_counts = Counter(e.get("attack_vector") for e in entries)
    mappings = _all_mappings()
    tech_counts = Counter(m["technique_id"] for m in mappings)

    lines: list[str] = []
    lines.append(
        f"\n=== attack_vector marginal ({len(entries)} entries, {len(vec_counts)} distinct values) ==="
    )
    for vec, n in sorted(vec_counts.items(), key=lambda kv: (-kv[1], kv[0] or "")):
        lines.append(f"  {n:3d}  {vec}")

    lines.append("\n=== MUST_COVER technique -> mapped-entry count ===")
    for tech in sorted(MUST_COVER):
        lines.append(f"  {tech}: {tech_counts.get(tech, 0)} mapped entries")

    lines.append("\n=== §6.2 consciously-deferred niche IA techniques (not silent) ===")
    for tech, rationale in sorted(_CONSCIOUSLY_DEFERRED.items()):
        lines.append(f"  {tech}: {rationale}")

    print("\n".join(lines))
    assert True


# ---------------------------------------------------------------------------
# §7.3: canonical-vector-class soft check
# ---------------------------------------------------------------------------


def test_every_attack_vector_maps_to_a_known_canonical_class() -> None:
    """§7.3 soft check: every distinct attack_vector free-text value used in
    the library must be documented in docs/reference/attack-vector-classes.md
    under one of the canonical initial-access classes. Catches new ad-hoc
    values drifting into the library uncatalogued -- the exact failure mode
    that let the epic's root-cause gap go unaudited for four prior epics.

    The full attack_vector ENUM migration of the column is a noted
    follow-up, not this epic (design doc §7.3, scope-drift-log item 8) --
    this doc-presence check is the proportionate interim.
    """
    doc = _CLASS_DOC.read_text(encoding="utf-8")
    values = {e.get("attack_vector") for e in _library_entries()}
    undocumented = sorted(v for v in values if v and f"`{v}`" not in doc)
    assert not undocumented, (
        f"attack_vector value(s) not catalogued in {_CLASS_DOC}: {undocumented} -- "
        f"add each to its canonical class (or a new class) in the doc."
    )
