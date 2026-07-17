"""Tests for build_flash href extension (issue #87)."""

from __future__ import annotations

from idraa.services.flash import build_flash


class TestBuildFlashWithoutHref:
    def test_default_args_unchanged(self) -> None:
        result = build_flash("hello", "info")
        assert result["message"] == "hello"
        assert result["level"] == "info"
        assert result.get("href") is None
        assert result.get("href_text") is None


class TestBuildFlashWithHref:
    def test_href_and_text_present(self) -> None:
        result = build_flash(
            "Imported 10",
            "success",
            href="/controls/maintenance",
            href_text="Open Maintenance",
        )
        assert result["message"] == "Imported 10"
        assert result["level"] == "success"
        assert result["href"] == "/controls/maintenance"
        assert result["href_text"] == "Open Maintenance"

    def test_href_without_text_defaults_to_open_label(self) -> None:
        # If caller forgets href_text, fall back to "Open →" so the
        # template never renders a bare URL as the link text.
        result = build_flash("msg", "info", href="/x")
        assert result["href"] == "/x"
        assert result["href_text"] == "Open →"
