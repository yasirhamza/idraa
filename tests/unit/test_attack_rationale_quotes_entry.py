"""Guard (idraa#234): a crosswalk rationale that quotes its entry must quote text that is
still there.

#209 re-worded seven library entries; ten rationales in four entries kept quoting the
removed text (found by hand, fixed in #234). This test makes the next re-wording of an
entry fail here instead of leaving a stale quote in the curated crosswalk.

Definition (spec docs/superpowers/specs/2026-10-10-issue-234-crosswalk-rationales-design.md
section 4.3):

* Covered rows: a rationale opening ``Entry description ``, ``Entry's example_incidents ``,
  ``Entry's canonical_fair_gap `` or ``Description states `` (any verb after the field name).
  Rows opening ``Entry describes`` / ``Entry states`` / ``Entry names`` / ``Entry's
  attack_vector`` and rows quoting with double quotes are NOT covered (widening is a tracked
  follow-up). The guard catches quoted text that has LEFT the entry; it does not catch a quote
  whose meaning is reversed (an exclusion quoted as an inclusion).
* Spans: single-quoted text (apostrophe-aware), 12+ characters, split on ``...``, ``…`` and
  bracketed insertions into parts; parts under 10 characters are ignored.
* A part passes when, after normalisation, it is a substring of the entry's text (name,
  description, example_incidents, canonical_fair_gap, tags, calibration_anchor,
  source_citations), of the technique's catalog description, or of the row's citations.
* Two pinned allowlists excuse parts whose source is not the entry: ``EXTERNAL_QUOTES`` (the
  row's external sources; may grow under review) and ``KNOWN_QUOTE_DEFECTS`` (pre-existing
  defects; may only shrink).
"""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any

_DATA = Path(__file__).resolve().parents[2] / "data"

_MAPPING_FILES = (
    "seed_attack_full_mappings.json",
    "seed_attack_d_iii_b_full.json",
    "seed_attack_exemplar_mappings.json",
    "seed_attack_avgapfill_full.json",
)
_ENTRY_FILES = ("seed_library_entries.json", "seed_library_entries_extension.json")
_CATALOG_FILE = "seed_attack_catalog.json"

_COVERED_OPENERS = (
    "Entry description ",
    "Entry's example_incidents ",
    "Entry's canonical_fair_gap ",
    "Description states ",
)
_ENTRY_FIELDS = (
    "name",
    "description",
    "example_incidents",
    "canonical_fair_gap",
    "tags",
    "calibration_anchor",
    "source_citations",
)
_SPAN_RE = re.compile(r"(?<![A-Za-z])'(.+?)'(?![A-Za-z])")
_PART_SPLIT_RE = re.compile(r"\.\.\.|…|\[[^\]]*\]")
_MIN_SPAN_CHARS = 12
_MIN_PART_CHARS = 10
_PREFIX_CHARS = 40

# Covered rows on the fixed tree (90 at the #234 merge-base; the six rows whose openers #234
# rewrote -- ot T0888, insider-ip T1005 / T1027 / T1567, higher-ed T1498, ot T1078 -- joined).
_COVERED_ROW_FLOOR = 96

RowKey = tuple[str, str, str]  # (entry_slug, domain, technique_id)

# The ten rows whose rationales #234 re-worded (spec section 3, R1..R10); each must be covered.
_ISSUE_234_ROWS: tuple[RowKey, ...] = (
    ("hospitality-guest-data-insider", "enterprise", "T1078"),
    ("ot-network-scanning-reconnaissance", "enterprise", "T1595"),
    ("ot-network-scanning-reconnaissance", "enterprise", "T1596"),
    ("ot-network-scanning-reconnaissance", "ics", "T0888"),
    ("insider-ip-theft-manufacturing", "enterprise", "T1005"),
    ("insider-ip-theft-manufacturing", "enterprise", "T1027"),
    ("insider-ip-theft-manufacturing", "enterprise", "T1078"),
    ("insider-ip-theft-manufacturing", "enterprise", "T1567"),
    ("higher-ed-insider-ddos", "enterprise", "T1498"),
    ("ot-network-scanning-reconnaissance", "enterprise", "T1078"),
)

# Quotes from the row's own external sources (a press release, an advisory, the ATT&CK
# definition): they are not entry text and the row's citations carry only the source label.
# Keyed by row -> normalised 40-character prefixes of the excused parts. May grow under review.
EXTERNAL_QUOTES: dict[RowKey, tuple[str, ...]] = {
    ("gov-employee-insider-leak", "enterprise", "T1078"): (
        # DOJ OPA, 'Maryland Man Charged With Removal of Classified Materials and Theft of
        # Government Property' (2016-08-29) -- Martin.
        "was a contractor with the federal govern",
        # DOJ OPA, 'Federal Government Contractor in Georgia Charged With Removing and Mailing
        # Classified Materials to a News Outlet' (2017-06-05) -- Winner.
        "admitted intentionally identifying and p",
    ),
    ("gov-records-tampering", "enterprise", "T1565"): (
        # MITRE ATT&CK T1565 Data Manipulation definition wording; the catalog description in
        # seed_attack_catalog.json does not reproduce this phrase.
        "to influence external outcomes or decisi",
    ),
    ("insider-ip-theft-manufacturing", "enterprise", "T1005"): (
        # DOJ OPA, 'Former GE Engineer and Chinese Businessman Charged with Economic Espionage
        # and Theft of GE's Trade Secrets' (2019-04-23).
        "stealing multiple electronic files, incl",
    ),
    ("insider-ip-theft-manufacturing", "enterprise", "T1027"): (
        # DOJ OPA, 'New York Man Charged With Theft of Trade Secrets' (2018-08-01), the
        # criminal-complaint release (the steganographic sunset photo).
        "data files belonging to ge into an innoc",
    ),
    ("insider-ip-theft-manufacturing", "enterprise", "T1078"): (
        # DOJ OPA 2019-04-23 release (see T1005 above).
        "while employed at ge power & water in sc",
        # DOJ OPA 2019-04-23 release (see T1005 above).
        "exploited his access to ge's files",
    ),
    ("insider-ip-theft-manufacturing", "enterprise", "T1567"): (
        # DOJ OPA 2018-08-01 release (see T1027 above): the photo e-mailed to Zheng's account.
        "the digital picture, which contained the",
    ),
    ("ot-network-scanning-reconnaissance", "enterprise", "T1078"): (
        # CISA/NSA/FBI Joint Cybersecurity Advisory AA24-038A (2024-02-07), Volt Typhoon.
        "relies on valid accounts",
        # CISA/NSA/FBI Joint Cybersecurity Advisory AA24-038A (2024-02-07), Volt Typhoon.
        "repeatedly exfiltrate domain credentials",
    ),
}

# Pre-existing quote defects outside #209's footprint, filed as a follow-up (spec section 7).
# Exact keys; may only SHRINK -- the test below asserts the key set is a subset of the pinned
# tuple, so a new entry here fails unless the pin is edited in the same reviewed change.
KNOWN_QUOTE_DEFECTS: dict[RowKey, tuple[str, ...]] = {
    ("manufacturing-billing-fraud", "enterprise", "T1565"): (
        # Misattributed: 'without specific loss figures per case' is
        # logistics-tms-data-tampering's phrase, never this entry's, and contradicts this
        # entry's '$1M-$10M per incident'.
        "without specific loss figures per case",
    ),
    ("pipeline-nomination-scada-curtailment-shipper-penalty", "enterprise", "T1486"): (
        # A loose quote that closes the entry's parenthetical early:
        # 'Colonial Pipeline May 2021 (ransomware-driven proactive shutdown)'.
        "colonial pipeline may 2021 (ransomware-d",
    ),
}
_PINNED_KNOWN_QUOTE_DEFECT_KEYS: tuple[RowKey, ...] = (
    ("manufacturing-billing-fraud", "enterprise", "T1565"),
    ("pipeline-nomination-scada-curtailment-shipper-penalty", "enterprise", "T1486"),
)


def _normalise(text: str) -> str:
    text = unicodedata.normalize("NFC", text)
    text = text.replace("‘", "'").replace("’", "'")
    text = text.replace("—", "-").replace("–", "-").replace("--", "-")
    text = " ".join(text.split()).casefold()
    return text.strip(" .,;:'\"")


def _parts(rationale: str) -> list[str]:
    """Normalised parts (10+ characters) of every single-quoted span (12+ characters)."""
    parts: list[str] = []
    for match in _SPAN_RE.finditer(rationale):
        span = match.group(1)
        if len(span) < _MIN_SPAN_CHARS:
            continue
        for raw in _PART_SPLIT_RE.split(span):
            part = _normalise(raw)
            if len(part) >= _MIN_PART_CHARS:
                parts.append(part)
    return parts


def _as_text(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def _load_json(name: str) -> Any:
    return json.loads((_DATA / name).read_text(encoding="utf-8"))


def _entries() -> dict[str, dict[str, Any]]:
    entries: dict[str, dict[str, Any]] = {}
    for name in _ENTRY_FILES:
        for entry in _load_json(name):
            entries[entry["slug"]] = entry
    return entries


def _catalog_descriptions() -> dict[tuple[str, str], str]:
    return {
        (t["domain"], t["technique_id"]): t["description"] or ""
        for t in _load_json(_CATALOG_FILE)["techniques"]
    }


def _mapping_rows() -> dict[RowKey, dict[str, Any]]:
    rows: dict[RowKey, dict[str, Any]] = {}
    for name in _MAPPING_FILES:
        for mapping in _load_json(name)["mappings"]:
            key = (mapping["entry_slug"], mapping["domain"], mapping["technique_id"])
            assert key not in rows, f"duplicate mapping row {key} across the four seed files"
            rows[key] = mapping
    return rows


def _covered(rows: dict[RowKey, dict[str, Any]]) -> dict[RowKey, dict[str, Any]]:
    return {k: m for k, m in rows.items() if m["rationale"].startswith(_COVERED_OPENERS)}


def _unmatched_parts(key: RowKey, mapping: dict[str, Any]) -> list[str]:
    """Parts of this row's quoted spans found in none of the three admissible sources."""
    entries = _entries()
    slug, domain, technique_id = key
    assert slug in entries, f"mapping row {key} names an entry that is not in the library seeds"
    entry = entries[slug]
    haystacks = (
        _normalise(" ".join(_as_text(entry.get(field, "")) for field in _ENTRY_FIELDS)),
        _normalise(_catalog_descriptions().get((domain, technique_id), "")),
        _normalise(" ".join(mapping["citations"])),
    )
    return [p for p in _parts(mapping["rationale"]) if not any(p in h for h in haystacks)]


def _excused(part: str, prefixes: tuple[str, ...]) -> bool:
    return any(part.startswith(prefix) for prefix in prefixes)


def _all_prefixes(key: RowKey) -> tuple[str, ...]:
    return EXTERNAL_QUOTES.get(key, ()) + KNOWN_QUOTE_DEFECTS.get(key, ())


def test_every_quoted_span_in_a_covered_rationale_is_still_in_its_source() -> None:
    covered = _covered(_mapping_rows())
    stale: list[str] = []
    for key, mapping in sorted(covered.items()):
        for part in _unmatched_parts(key, mapping):
            if not _excused(part, _all_prefixes(key)):
                stale.append(f"{key}: {part[:100]!r}")
    assert not stale, (
        "crosswalk rationale(s) quote text that is in neither the entry, the technique's "
        "catalog description nor the row's citations (an entry re-worded without its "
        "crosswalk rows, or a quote from an external source that needs an EXTERNAL_QUOTES "
        "entry):\n" + "\n".join(stale)
    )


def test_covered_row_floor() -> None:
    covered = _covered(_mapping_rows())
    assert len(covered) >= _COVERED_ROW_FLOOR, (
        f"only {len(covered)} rationales open with a covered phrase; the fixed tree has "
        f"{_COVERED_ROW_FLOOR}. A re-phrased opener silently removes a row from the guard."
    )


def test_the_ten_issue_234_rows_are_covered() -> None:
    covered = _covered(_mapping_rows())
    missing = [key for key in _ISSUE_234_ROWS if key not in covered]
    assert not missing, f"#234 rows no longer opening with a covered phrase: {missing}"


def test_allowlists_only_shrink_and_every_prefix_is_live() -> None:
    assert set(KNOWN_QUOTE_DEFECTS) <= set(_PINNED_KNOWN_QUOTE_DEFECT_KEYS), (
        "KNOWN_QUOTE_DEFECTS may only shrink: fix the quote, do not add the defect here"
    )
    assert not set(EXTERNAL_QUOTES) & set(KNOWN_QUOTE_DEFECTS)
    covered = _covered(_mapping_rows())
    dead: list[str] = []
    for table in (EXTERNAL_QUOTES, KNOWN_QUOTE_DEFECTS):
        for key, prefixes in table.items():
            unmatched = _unmatched_parts(key, covered[key]) if key in covered else []
            for prefix in prefixes:
                assert len(prefix) <= _PREFIX_CHARS and prefix == _normalise(prefix), (
                    f"allowlist prefix {prefix!r} must be a normalised part prefix of at most "
                    f"{_PREFIX_CHARS} characters"
                )
                if not any(p.startswith(prefix) for p in unmatched):
                    dead.append(f"{key}: {prefix!r}")
    assert not dead, (
        "allowlisted prefix(es) no longer match an otherwise-unmatched quoted part of their "
        "row (the quote was fixed or removed -- delete the stale allowlist entry):\n"
        + "\n".join(dead)
    )
