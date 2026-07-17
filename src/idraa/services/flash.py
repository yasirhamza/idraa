"""Flash message constructor + level whitelisting (GH #3).

The codebase uses a per-render-dict flash pattern (route handlers build a
{"message": ..., "level": ...} dict and pass it via TemplateResponse
context — see routes/calibration_overrides.py for the precedent). This
module ships a constructor helper that validates the level against a
closed StrEnum set so templates can drop `default('info')` and trust
that flash.level is always one of {info, success, warning, error}.

There is NO middleware. There is NO request.session involvement. Flash
state is per-render, ephemeral, never stored.
"""

from __future__ import annotations

from enum import StrEnum


class FlashLevel(StrEnum):
    INFO = "info"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"


_VALID_LEVELS: frozenset[str] = frozenset(level.value for level in FlashLevel)


class InvalidFlashLevelError(ValueError):
    """Raised when build_flash receives a level outside FlashLevel.

    Carries the offending value as ``.level`` so consumers (logging,
    HTMX error boundary) can inspect without parsing ``str(exc)``.

    This is a CALLER bug (typo in level kwarg), not user input — the
    invalid call should fail loudly during development, not silently
    mask itself with a `default('info')` template fallback as before.
    """

    def __init__(self, level: object) -> None:
        self.level = level
        super().__init__(f"flash level {level!r} not in {sorted(_VALID_LEVELS)}")


def build_flash(
    message: str,
    level: str | FlashLevel,
    *,
    href: str | None = None,
    href_text: str | None = None,
) -> dict[str, str | None]:
    """Construct a validated flash dict for use in TemplateResponse context.

    Optional ``href`` + ``href_text`` render a button/link inside the alert
    (used post-import to deep-link to /controls/maintenance — issue #87).
    If ``href`` is set without ``href_text``, falls back to ``"Open →"`` so
    the template never renders a bare URL as the link text.

    Usage::

        return templates.TemplateResponse(
            request, "library/browse.html",
            {"current_user": user, "flash": build_flash("Saved", "success"), ...},
        )

    Raises :class:`InvalidFlashLevelError` if level is not one of
    FlashLevel values (case-sensitive, exact match against lowercase enum
    values).
    """
    if isinstance(level, FlashLevel):
        level_value = level.value
    elif level in _VALID_LEVELS:
        level_value = level
    else:
        raise InvalidFlashLevelError(level)
    return {
        "message": message,
        "level": level_value,
        "href": href,
        "href_text": (href_text or "Open →") if href else None,
    }
