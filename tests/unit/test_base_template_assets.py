"""base.html must serve CSS same-origin — no CDN, no runtime JIT config."""

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BASE = (REPO / "src/idraa/templates/base.html").read_text(encoding="utf-8")


def test_no_tailwind_play_cdn():
    assert "cdn.tailwindcss.com" not in BASE


def test_no_inline_tailwind_config():
    assert "window.tailwind" not in BASE


def test_no_daisyui_cdn():
    assert "jsdelivr" not in BASE and "cdn.jsdelivr" not in BASE


def test_local_css_links_present_and_ordered():
    daisy = BASE.index("/static/vendor/daisyui-4.12.10.min.css")
    tw = BASE.index("/static/css/tailwind.css")
    app = BASE.index("/static/css/app.css")
    assert daisy < tw < app, "cascade order must be DaisyUI -> tailwind -> app.css"
