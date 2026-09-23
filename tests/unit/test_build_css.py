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


def test_built_sheet_carries_daisyui_controls_restore():
    """UAT 2026-07-21 (wizard catastrophic toggle): @tailwindcss/forms' global
    [type=checkbox]/[type=radio] reset ties DaisyUI's .toggle/.checkbox/.radio
    on specificity and wins by sheet order, flattening every DaisyUI form
    control to an unstyled 1rem square. build() must append the extracted
    DaisyUI control rules AFTER the reset (end of the built sheet)."""
    css = build_css.OUTPUT.read_text(encoding="utf-8")
    marker = css.find(build_css._RESTORE_MARKER)
    assert marker != -1, "daisyui-controls-restore block missing from tailwind.css"
    reset = css.find("[type=checkbox]")
    if reset == -1:
        reset = css.find('[type="checkbox"]')
    assert reset != -1 and marker > reset, "restore block must come AFTER the forms reset"
    restore = css[marker:]
    assert ".toggle{" in restore and "width:3rem" in restore
    assert ".checkbox{" in restore
    assert ".radio{" in restore


def test_extract_control_rules_nonempty_and_scoped():
    """The extraction pulls only control-class rules from the vendored sheet."""
    restore = build_css._extract_control_rules()
    assert len(restore) > 5_000  # toggle+checkbox+radio families are substantial
    # spot-check scoping: no unrelated component rules leak in
    assert ".btn{" not in restore
    assert ".card{" not in restore


def test_every_text_class_in_templates_is_defined():
    """#176: 29 ``text-h4`` usages (and ``text-caption``) rendered at the
    inherited size because no stylesheet defined them — Tailwind silently
    drops unknown classes. Every static ``text-*`` class in a template's
    ``class="..."`` must exist in one of the served sheets: the built
    Tailwind sheet, the hand-written app.css utilities, or vendored DaisyUI."""
    import re

    static = Path(build_css.__file__).resolve().parents[1] / "static"
    templates = static.parent / "templates"
    sheets = build_css.OUTPUT.read_text(encoding="utf-8") + (static / "css" / "app.css").read_text(
        encoding="utf-8"
    )
    sheets += "".join(p.read_text(encoding="utf-8") for p in static.rglob("daisyui*.css"))
    used = {
        cls
        for path in templates.rglob("*.html")
        for attr in re.finditer(r'class="([^"]*)"', path.read_text(encoding="utf-8"))
        for cls in attr.group(1).split()
        if cls.startswith("text-") and not re.search(r"[{}'%]", cls)
    }
    assert {"text-h4", "text-display", "text-ink-1"} <= used  # non-vacuous

    def defined(cls: str) -> bool:
        # End-anchored: ``text-ink`` must not pass on the strength of ``.text-ink-1``.
        selector = "." + re.sub(r"([:/.\[\]%#])", r"\\\1", cls)
        return re.search(re.escape(selector) + r"(?=[\s{,:>+~.\[)])", sheets) is not None

    missing = sorted(cls for cls in used if not defined(cls))
    assert not missing, f"text-* classes used in templates but defined by no stylesheet: {missing}"
