"""Tests for docs/reference/calibration-sources/source-catalogue.md.

The catalogue must:
  1. Index every per-source .md file in the calibration-sources dir
     (excluding README, _template, and source-catalogue itself).
  2. Use the classification vocabulary tokens that the C-i tiering framework
     defines: loss-magnitude, frequency, paginated, vendor, anecdotal.
"""

from pathlib import Path

_DIR = Path("docs/reference/calibration-sources")


def test_catalogue_indexes_every_source_file():
    cat = (_DIR / "source-catalogue.md").read_text(encoding="utf-8")
    # every per-source note (excluding README/_template/the catalogue itself) must be indexed
    notes = {p.stem for p in _DIR.glob("*.md")} - {"README", "_template", "source-catalogue"}
    for stem in notes:
        assert stem in cat, f"source-catalogue.md does not index {stem}"


def test_catalogue_classifies_carries_and_tier():
    cat = (_DIR / "source-catalogue.md").read_text(encoding="utf-8")
    for token in ("loss-magnitude", "frequency", "paginated", "vendor", "anecdotal"):
        assert token in cat, f"source-catalogue.md missing classification token {token!r}"
