"""Guard: no seed entry may carry a forbidden/mis-cited citation (issues #510, #480).

The tokens below are the mis-cited / unverifiable sources from the #475 gap
report. D-iii-a cleaned source_citations but the tokens survived in narrative
fields (description / example_incidents / canonical_fair_gap). This guard scans
EVERY string value of EVERY field so they can never reappear. 'AMI' (Advanced
Metering Infrastructure) is legitimate vocabulary and is deliberately NOT a token.

#480: ``ICSA-17-181-01`` is a dead/mis-identified CISA advisory id (404s on
cisa.gov; NOT the Industroyer/CRASHOVERRIDE advisory — the verified primaries are
ICS-ALERT-17-206-01 + TA17-163A). It was embedded in denial-of-control's composite
citation, already swept by the Epic D citation rewrites; this token keeps it out.
(The dead-id WARNING in data/seed_attack_exemplar_mappings.json's _note is
intentional documentation and is not scanned here — this guard only covers the
library entry seed files.)
"""

from __future__ import annotations

import json
from pathlib import Path

_FORBIDDEN = ("I-091019-PSA", "15-1433", "AA22-186A", "PREPA", "ICSA-17-181-01")


def _strings(obj) -> list[str]:
    out: list[str] = []
    if isinstance(obj, str):
        out.append(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            out.extend(_strings(v))
    elif isinstance(obj, list):
        for v in obj:
            out.extend(_strings(v))
    return out


def test_no_seed_entry_carries_a_forbidden_citation() -> None:
    entries: list[dict] = []
    for name in ("seed_library_entries.json", "seed_library_entries_extension.json"):
        entries.extend(json.loads(Path("data", name).read_text(encoding="utf-8")))
    offenders: dict[str, list[str]] = {}
    for e in entries:
        hits = [t for t in _FORBIDDEN if any(t in s for s in _strings(e))]
        if hits:
            offenders[e["slug"]] = hits
    assert not offenders, f"forbidden citations present: {offenders}"
