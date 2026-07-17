"""build_flash helper: constructs a flash dict with a whitelisted level.

Closes GH #3 — whitelist at Python construction time; drop template-level fallback.
"""

from __future__ import annotations

import pytest

from idraa.services.flash import FlashLevel, InvalidFlashLevelError, build_flash


def test_build_flash_accepts_valid_string_levels() -> None:
    """build_flash accepts the 4 valid string levels and returns a dict."""
    for level in ("info", "success", "warning", "error"):
        flash = build_flash("test message", level)
        assert flash["message"] == "test message"
        assert flash["level"] == level
        assert flash.get("href") is None
        assert flash.get("href_text") is None


def test_build_flash_accepts_strenum_levels() -> None:
    """build_flash accepts FlashLevel enum members directly."""
    flash = build_flash("ok", FlashLevel.SUCCESS)
    assert flash["message"] == "ok"
    assert flash["level"] == "success"
    assert flash.get("href") is None
    assert flash.get("href_text") is None


def test_build_flash_rejects_invalid_level() -> None:
    """build_flash raises on non-whitelisted level — caller bug, not user input."""
    with pytest.raises(InvalidFlashLevelError):
        build_flash("msg", "danger")  # not in enum
    with pytest.raises(InvalidFlashLevelError):
        build_flash("msg", "")
    with pytest.raises(InvalidFlashLevelError):
        build_flash("msg", "INFO")  # case-sensitive — enum values are lowercase
    with pytest.raises(InvalidFlashLevelError):
        build_flash("msg", 42)  # type: ignore[arg-type]  # non-string, non-enum


def test_flash_level_is_strenum_enum() -> None:
    """FlashLevel is a StrEnum mirroring the 4 valid levels.
    Templates can compare flash.level against FlashLevel.SUCCESS etc."""
    assert FlashLevel.INFO.value == "info"
    assert FlashLevel.SUCCESS.value == "success"
    assert FlashLevel.WARNING.value == "warning"
    assert FlashLevel.ERROR.value == "error"
    assert {lv.value for lv in FlashLevel} == {"info", "success", "warning", "error"}
