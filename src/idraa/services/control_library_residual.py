"""Residual entries: those whose assignment SET does not score (#437 T7 -> #439)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from idraa.services.control_library_scoring import classify_entry, entry_scores


def residual_meta_entries(entries: list[dict[str, Any]]) -> list[str]:
    """Return slugs of ALL non-scoring entries (union of under-authored + genuinely-meta).

    An entry is included iff entry_scores() returns False AND it has at least one
    assignment (zero-assignment entries are also under-authored, but not yet in scope
    for #439 channel work).

    This is the UNION set (under-authored + genuinely-meta).  The #439 coupling-math
    scope is the GENUINELY-META partition only — use ``residual_partition()`` to split
    the two categories before routing to #439 vs. curation.

    See ``residual_partition()`` for the authoritative split.
    """
    return [e["slug"] for e in entries if e.get("assignments") and not entry_scores(e)]


_NEW_N1_CAVEAT = (
    "NOTE (NEW-N1): This report is PROVISIONAL. "
    "Under full audit-rollout, entries migrate between partitions in both directions "
    "as curation adds or removes direct channels. "
    "Treat counts as best-current-state only."
)


def residual_partition(entries: list[dict[str, Any]]) -> dict[str, list[str]]:
    """Split non-scoring entries into genuinely-meta vs under-authored partitions.

    Uses ``classify_entry`` (Task 4) to produce the authoritative 3-way triage and
    then groups the two non-scoring outcomes:

    - ``"genuinely_meta"``  — classify == "non-scoring-residual":
      ≥2 assignments, no scoring channel.  Meta — credits via the κ reliability
      coupling on runs (E_meta uplifts a co-present Loss-Event control's reliability,
      #439 D1); the standalone catalog score is $0 by design, NOT a gap to graft a
      fake direct channel onto.

    - ``"under_authored"``  — classify == "under-authored":
      ≤1 assignment (including zero-assignment entries whose slug is present).
      They likely need a MISSING direct channel added via curation.
      Re-evaluate AFTER curation; do NOT route to #439 coupling math.

    Returns a dict with keys ``"genuinely_meta"`` and ``"under_authored"``, each
    mapping to a list of slugs in stable iteration order.

    Boundary definition: under-authored means ≤1 assignment (matches classify_entry
    in control_library_scoring.py).  This is NOT a 0-vs-≥1 split.
    """
    genuinely_meta: list[str] = []
    under_authored: list[str] = []

    for e in entries:
        classification = classify_entry(e)
        if classification == "non-scoring-residual":
            genuinely_meta.append(e["slug"])
        elif classification == "under-authored":
            under_authored.append(e["slug"])
        # "scoring" entries are not included in either bucket

    return {"genuinely_meta": genuinely_meta, "under_authored": under_authored}


def _main() -> None:
    """CLI: load the seed library and print the two residual partitions."""
    seed_path = Path(__file__).parents[3] / "data" / "seed_control_library_entries.json"
    if not seed_path.exists():
        print(f"ERROR: seed file not found at {seed_path}", file=sys.stderr)
        sys.exit(1)

    data = json.loads(seed_path.read_text(encoding="utf-8"))
    entries: list[dict[str, Any]] = data.get("entries", [])

    partition = residual_partition(entries)
    genuinely_meta = partition["genuinely_meta"]
    under_authored = partition["under_authored"]
    total = len(entries)

    print(
        f"# Residual entries — {len(genuinely_meta) + len(under_authored)} of {total} (two partitions)"
    )
    print()

    print(
        f"## Genuinely-meta residual ({len(genuinely_meta)} entries) "
        "— primary #439 coupling-math candidates\n"
        "   (≥2 assignments, no scoring channel)"
    )
    for slug in genuinely_meta:
        print(f"  - {slug}")
    print()

    print(
        f"## Under-authored ({len(under_authored)} entries) "
        "— likely needs a missing direct channel; re-evaluate AFTER curation, NOT #439 coupling math\n"
        "   (≤1 assignment)"
    )
    for slug in under_authored:
        print(f"  - {slug}")
    print()

    print(_NEW_N1_CAVEAT)


if __name__ == "__main__":
    _main()
