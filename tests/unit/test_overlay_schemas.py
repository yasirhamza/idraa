"""OverlayForm Pydantic validation unit tests.

Covers the security-critical preamble fold-ins:
- ``extra="forbid"`` blocks form-field smuggling.
- Multiplier validators reject inf/nan/sanity-cap-busting values.
- Methodology validator enforces post-strip length >= 20 (matches the
  DB CHECK constraint on ``overlay_definitions.methodology``).
- Tag validator enforces snake_case starting with a lowercase letter.

Negative tests assert on ``(loc, type, msg)`` from
``ValidationError.errors()`` rather than just exception type — a
``with pytest.raises(ValidationError):`` block alone would pass even
if the *wrong* validator fired (e.g., extra-field smuggling test
silently passing because some other field tripped instead of
``extra="forbid"``). The assertions use ``any(...)`` over the error
list rather than ``errors[0]`` indexing so Pydantic adding composite
errors in a future version doesn't break the suite.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

# Annotated as ``dict[str, Any]`` (rather than the inferred ``dict[str, object]``)
# so that ``OverlayForm(**_VALID_PAYLOAD)`` typechecks under mypy strict — the
# inferred ``object`` value type is not assignable to the ``str`` / ``float`` /
# ``list[str]`` parameter types that ``**``-spread would need to satisfy.
_VALID_PAYLOAD: dict[str, Any] = {
    "tag": "critical_infrastructure",
    "display_name": "Critical Infrastructure",
    "frequency_multiplier": 1.4,
    "magnitude_multiplier": 2.0,
    "sources": ["docs/reference/calibration-sources/ic3_2025.md"],
    "methodology": ("TEF +40%: nation-state and criminal targeting both elevated for CI."),
    "methodology_change_reason": "initial test fixture",
}


def test_overlay_form_accepts_valid_payload():
    from idraa.schemas.overlay import OverlayForm

    form = OverlayForm(**_VALID_PAYLOAD)
    assert form.tag == "critical_infrastructure"
    assert form.frequency_multiplier == pytest.approx(1.4)
    assert form.magnitude_multiplier == pytest.approx(2.0)


def test_overlay_form_rejects_extra_fields():
    """extra='forbid' blocks form-field smuggling (e.g. organization_id)."""
    from idraa.schemas.overlay import OverlayForm

    payload = {**_VALID_PAYLOAD, "organization_id": "00000000-0000-0000-0000-000000000000"}
    with pytest.raises(ValidationError) as exc_info:
        OverlayForm(**payload)
    errors = exc_info.value.errors()
    # Pin the *intended* validator: extra="forbid" on the smuggled field.
    # Without this assertion the test would silently pass if a totally
    # unrelated validator rejected the payload.
    assert any(e["type"] == "extra_forbidden" and "organization_id" in e["loc"] for e in errors), (
        f"expected extra_forbidden on organization_id, got: {errors}"
    )


@pytest.mark.parametrize("bad", [float("inf"), float("-inf"), float("nan")])
def test_overlay_form_rejects_non_finite_frequency_multiplier(bad):
    from idraa.schemas.overlay import OverlayForm

    payload = {**_VALID_PAYLOAD, "frequency_multiplier": bad}
    with pytest.raises(ValidationError) as exc_info:
        OverlayForm(**payload)
    errors = exc_info.value.errors()
    # Two layers of defense both protect against non-finite values:
    # ``Field(gt=0)`` rejects ``-inf`` and ``nan`` (neither is > 0) and
    # the explicit ``math.isfinite`` validator rejects ``inf`` (which
    # would otherwise pass ``gt=0``). The security guarantee is "no
    # non-finite multiplier reaches the FAIR composer"; both validators
    # uphold it. Pin: the error must be on the *right field* and must
    # be either the finite-check or the gt=0 constraint, not some
    # unrelated validator.
    assert any(
        e["loc"] == ("frequency_multiplier",)
        and ("finite" in e["msg"] or e["type"] == "greater_than")
        for e in errors
    ), f"expected finite-check or gt=0 error on frequency_multiplier, got: {errors}"


@pytest.mark.parametrize("bad", [float("inf"), float("-inf"), float("nan")])
def test_overlay_form_rejects_non_finite_magnitude_multiplier(bad):
    from idraa.schemas.overlay import OverlayForm

    payload = {**_VALID_PAYLOAD, "magnitude_multiplier": bad}
    with pytest.raises(ValidationError) as exc_info:
        OverlayForm(**payload)
    errors = exc_info.value.errors()
    # See test_overlay_form_rejects_non_finite_frequency_multiplier for
    # why both ``finite`` and ``greater_than`` are accepted here.
    assert any(
        e["loc"] == ("magnitude_multiplier",)
        and ("finite" in e["msg"] or e["type"] == "greater_than")
        for e in errors
    ), f"expected finite-check or gt=0 error on magnitude_multiplier, got: {errors}"


def test_overlay_form_rejects_magnitude_multiplier_above_sanity_cap():
    """magnitude_multiplier > 1e6 is treated as a sanity-cap violation."""
    from idraa.schemas.overlay import OverlayForm

    payload = {**_VALID_PAYLOAD, "magnitude_multiplier": 1e7}
    with pytest.raises(ValidationError) as exc_info:
        OverlayForm(**payload)
    errors = exc_info.value.errors()
    # Validator emits "multiplier exceeds sanity cap of 1e+06".
    assert any(
        e["loc"] == ("magnitude_multiplier",) and ("1e+06" in e["msg"] or "sanity cap" in e["msg"])
        for e in errors
    ), f"expected sanity-cap error on magnitude_multiplier, got: {errors}"


def test_overlay_form_rejects_frequency_multiplier_above_sanity_cap():
    from idraa.schemas.overlay import OverlayForm

    payload = {**_VALID_PAYLOAD, "frequency_multiplier": 1e7}
    with pytest.raises(ValidationError) as exc_info:
        OverlayForm(**payload)
    errors = exc_info.value.errors()
    assert any(
        e["loc"] == ("frequency_multiplier",) and ("1e+06" in e["msg"] or "sanity cap" in e["msg"])
        for e in errors
    ), f"expected sanity-cap error on frequency_multiplier, got: {errors}"


def test_overlay_form_rejects_zero_or_negative_multiplier():
    from idraa.schemas.overlay import OverlayForm

    for field in ("frequency_multiplier", "magnitude_multiplier"):
        for bad in (0, -0.5):
            payload = {**_VALID_PAYLOAD, field: bad}
            with pytest.raises(ValidationError):
                OverlayForm(**payload)


def test_overlay_form_rejects_short_methodology():
    """Methodology shorter than 20 chars after stripping is rejected.
    Matches the DB CHECK constraint length(trim(methodology)) >= 20."""
    from idraa.schemas.overlay import OverlayForm

    payload = {**_VALID_PAYLOAD, "methodology": "   short   "}
    with pytest.raises(ValidationError) as exc_info:
        OverlayForm(**payload)
    errors = exc_info.value.errors()
    # Validator emits "methodology must be at least 20 non-whitespace characters ..."
    assert any(e["loc"] == ("methodology",) and "20" in e["msg"] for e in errors), (
        f"expected methodology length error, got: {errors}"
    )


def test_overlay_form_rejects_methodology_exactly_19_chars_post_strip():
    from idraa.schemas.overlay import OverlayForm

    # 19 non-space chars after trim
    payload = {**_VALID_PAYLOAD, "methodology": "  " + ("x" * 19) + "  "}
    with pytest.raises(ValidationError) as exc_info:
        OverlayForm(**payload)
    errors = exc_info.value.errors()
    assert any(e["loc"] == ("methodology",) and "20" in e["msg"] for e in errors), (
        f"expected methodology length error, got: {errors}"
    )


def test_overlay_form_accepts_methodology_exactly_20_chars_post_strip():
    from idraa.schemas.overlay import OverlayForm

    payload = {**_VALID_PAYLOAD, "methodology": "  " + ("x" * 20) + "  "}
    form = OverlayForm(**payload)
    assert len(form.methodology.strip()) == 20


@pytest.mark.parametrize(
    "bad_tag",
    [
        "BadTag",  # uppercase
        "123_starts_with_digit",  # leading digit
        "has spaces",  # spaces
        "has-dash",  # dash not underscore
    ],
)
def test_overlay_form_rejects_bad_tag_formats(bad_tag):
    from idraa.schemas.overlay import OverlayForm

    payload = {**_VALID_PAYLOAD, "tag": bad_tag}
    with pytest.raises(ValidationError) as exc_info:
        OverlayForm(**payload)
    errors = exc_info.value.errors()
    # Validator emits "tag must be snake_case: lowercase letters, digits,
    # underscores; must start with a letter".
    assert any(
        e["loc"] == ("tag",) and ("snake_case" in e["msg"] or "lowercase" in e["msg"])
        for e in errors
    ), f"expected snake_case/lowercase error on tag, got: {errors}"


def test_overlay_form_rejects_empty_tag():
    """Empty tag is rejected by Field(min_length=1) before the snake_case
    regex runs — assert on the string-too-short error, not the regex one."""
    from idraa.schemas.overlay import OverlayForm

    payload = {**_VALID_PAYLOAD, "tag": ""}
    with pytest.raises(ValidationError) as exc_info:
        OverlayForm(**payload)
    errors = exc_info.value.errors()
    assert any(e["loc"] == ("tag",) and e["type"] == "string_too_short" for e in errors), (
        f"expected string_too_short on empty tag, got: {errors}"
    )
