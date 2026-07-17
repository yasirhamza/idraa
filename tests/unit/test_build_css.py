from pathlib import Path

import pytest

from idraa.tasks import build_css


def test_every_pinned_asset_has_a_sha():
    for (system, machine), (name, sha) in build_css._ASSETS.items():
        assert name
        assert len(sha) == 64 and sha != "REPLACE_IN_STEP_2", f"{system}/{machine} sha unpinned"


def test_unknown_platform_raises(monkeypatch):
    monkeypatch.setattr(build_css.platform, "system", lambda: "Plan9")
    monkeypatch.setattr(build_css.platform, "machine", lambda: "pdp11")
    with pytest.raises(SystemExit):
        build_css._asset()


def test_check_reports_stale(monkeypatch, tmp_path):
    # build() writes canned bytes; committed OUTPUT differs -> stale (rc 1)
    monkeypatch.setattr(build_css, "OUTPUT", tmp_path / "committed.css")
    (tmp_path / "committed.css").write_bytes(b"OLD")

    def fake_build(output: Path) -> int:
        output.write_bytes(b"NEW")
        return 0

    monkeypatch.setattr(build_css, "build", fake_build)
    assert build_css.check() == 1


def test_check_reports_fresh(monkeypatch, tmp_path):
    monkeypatch.setattr(build_css, "OUTPUT", tmp_path / "committed.css")
    (tmp_path / "committed.css").write_bytes(b"SAME")
    monkeypatch.setattr(build_css, "build", lambda output: (output.write_bytes(b"SAME"), 0)[1])
    assert build_css.check() == 0


def test_normalize_collapses_crlf():
    assert build_css._normalize(b"a\r\nb\r\n") == b"a\nb\n"


def test_check_fresh_across_crlf(monkeypatch, tmp_path):
    # committed is LF; a CRLF-emitting platform binary must still compare EQUAL (plan-gate I2)
    monkeypatch.setattr(build_css, "OUTPUT", tmp_path / "committed.css")
    (tmp_path / "committed.css").write_bytes(b"x\ny\n")
    monkeypatch.setattr(
        build_css, "build", lambda output: (output.write_bytes(b"x\r\ny\r\n"), 0)[1]
    )
    assert build_css.check() == 0
