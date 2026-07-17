"""Policy tests for scripts/sca_gate.py (supply-chain epic #555)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))
from sca_gate import evaluate, parse_suppressions  # ruff's E402 exempts sys.path.insert idioms


def _vuln(pkg, vid, fixes):
    return {"name": pkg, "vulns": [{"id": vid, "fix_versions": fixes}]}


def test_fixable_unsuppressed_fails():
    failures, warnings = evaluate([_vuln("foo", "GHSA-xxxx", ["1.2.3"])], set())
    assert len(failures) == 1 and not warnings


def test_unfixable_warns():
    failures, warnings = evaluate([_vuln("foo", "GHSA-yyyy", [])], set())
    assert not failures and len(warnings) == 1


def test_suppressed_fixable_warns_not_fails():
    failures, warnings = evaluate([_vuln("foo", "GHSA-xxxx", ["1.2.3"])], {"GHSA-xxxx"})
    assert not failures and len(warnings) == 1


def test_parse_suppressions_requires_reason_comment(tmp_path):
    f = tmp_path / "s.txt"
    f.write_text("# reason: unfixable transitive; review-by 2026-10-01\nGHSA-zzzz\n\n")
    assert parse_suppressions(f) == {"GHSA-zzzz"}


def test_bare_suppression_id_raises(tmp_path):
    import pytest

    f = tmp_path / "s.txt"
    f.write_text("GHSA-bare\n")
    with pytest.raises(ValueError, match="lacks a reason comment"):
        parse_suppressions(f)
