"""FastAPI application factory."""

from __future__ import annotations

import contextlib
import contextvars
import datetime
import logging
import math
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from jinja2 import pass_context
from markupsafe import Markup
from sqlalchemy import func, select
from starlette.requests import Request as StarletteRequest
from starlette.responses import PlainTextResponse, Response
from starlette.types import Scope

from idraa.config import get_settings
from idraa.db import get_session
from idraa.errors import StepUpRequired
from idraa.help_content import help_url as _help_url
from idraa.middleware.csrf import CSRFMiddleware
from idraa.middleware.enrollment_guard import EnrollmentGuardMiddleware
from idraa.middleware.maintenance_count import MaintenanceBadgeCountMiddleware
from idraa.middleware.security_headers import SecurityHeadersMiddleware
from idraa.middleware.session import SessionMiddleware
from idraa.middleware.uat_basic_auth import uat_basic_auth_factory
from idraa.models.enums import SUB_FUNCTION_UNITS
from idraa.models.user import User
from idraa.services.audit import ExportRateLimitedError
from idraa.utils.text import humanize_slug as _humanize_slug_impl

# Per-coroutine current request. Set by the _patched_template_render wrapper
# (applied below after ``templates`` is created) so that csrf_field() can
# resolve the CSRF token even inside macros imported without "with context".
# Jinja2 isolates imported-macro contexts from the calling template's
# variables (``request`` is NOT inherited), so @pass_context alone is
# insufficient for macros defined in separate template files.
_current_request_var: contextvars.ContextVar[StarletteRequest | None] = contextvars.ContextVar(
    "_current_request", default=None
)

PACKAGE_ROOT = Path(__file__).resolve().parent
TEMPLATES_DIR = PACKAGE_ROOT / "templates"
STATIC_DIR = PACKAGE_ROOT / "static"

# Cache-busting version stamp for static assets. Computed once at module
# import (stable for the lifetime of a single deployed instance, changes
# on every fresh container start ≡ every `fly deploy`). Templates append
# `?v={{ static_version }}` to /static/* URLs so aggressive mobile-browser
# caches don't serve stale JS/CSS across deploys (live UAT 2026-05-20:
# stale combobox JS caused pre-filled assignment selects to render
# empty on a real phone). Integer seconds keeps the URL short.
STATIC_VERSION = str(int(time.time()))


def _csrf_token_from_request(request: StarletteRequest | None) -> str:
    """Read the CSRF token off a request's ``state`` (set by CSRFMiddleware).

    Returns empty string if no middleware ran (e.g. a bare-env template
    render outside an HTTP request — mainly a test path). Empty-string is
    safer than raising: a form rendered without a token will fail CSRF
    verification on submit, which is the correct fail-closed outcome.
    """
    token = getattr(getattr(request, "state", None), "csrf_token", None)
    return token if isinstance(token, str) else ""


def _csrf_token_context_processor(request: StarletteRequest) -> dict[str, object]:
    """Inject ``csrf_token`` into every TemplateResponse's context.

    ``Jinja2Templates(context_processors=[...])`` runs this on each render,
    so templates can use ``{{ csrf_token }}`` as a bare value reference
    (e.g. inside ``hx-headers``). Also sets ``_current_request_var`` so
    that ``csrf_field()`` works inside macros imported without "with context".
    """
    _current_request_var.set(request)
    return {"csrf_token": _csrf_token_from_request(request)}


templates = Jinja2Templates(
    directory=str(TEMPLATES_DIR),
    context_processors=[_csrf_token_context_processor],
)

# Patch the Jinja2 environment's template_class so that every render call
# (including bare ``env.from_string(src).render(...)``) sets the
# ``_current_request_var`` ContextVar.  This lets ``csrf_field()`` resolve
# the token even inside macros imported without "with context" — those macros
# run in an isolated Jinja2 context that does NOT inherit the calling
# template's ``request`` variable.
_orig_jinja2_template_render = templates.env.template_class.render


def _patched_template_render(self, *args, **kwargs):  # type: ignore[no-untyped-def]
    """Wrap Jinja2 Template.render to publish ``request`` in the ContextVar."""
    vars_ = dict(*args, **kwargs)
    req = vars_.get("request")
    if req is not None:
        token = _current_request_var.set(req)
        try:
            return _orig_jinja2_template_render(self, vars_)
        finally:
            _current_request_var.reset(token)
    return _orig_jinja2_template_render(self, vars_)


templates.env.template_class.render = _patched_template_render  # type: ignore[method-assign]


@pass_context
def _csrf_field(context: dict[str, object]) -> Markup:
    """Jinja global: emit ``<input type="hidden" name="_csrf" value="...">``.

    Used by every ``<form method="post">`` the app renders (starting with
    the /setup wizard in plan Task 1.1.5). Pulls the token from the Jinja
    context — set either by the context processor above (production path)
    or by the test's ``render(request=...)`` call (unit-test path).

    Fallback chain (first non-empty token wins):
    1. ``context["request"]`` — present when called from a top-level template
       or a same-file macro that inherits the calling template's context.
    2. ``_current_request_var`` ContextVar — present when the request passed
       through the FastAPI middleware stack, which calls
       ``_set_current_request_middleware``.  This covers macros imported via
       ``{% from ... import %}`` (without "with context") because Jinja2
       isolates their context from the calling template's local variables.
    """
    request = context.get("request") or _current_request_var.get()
    token = _csrf_token_from_request(request)  # type: ignore[arg-type]
    # Build via Markup.format() rather than f-string-into-Markup so the
    # token value is auto-escaped by MarkupSafe (defense in depth — today
    # the token is hex+"." with no metacharacters, but future formats must
    # not silently become an injection sink). ruff S704 rejects the
    # f-string pattern for exactly this reason.
    template: Markup = Markup('<input type="hidden" name="_csrf" value="{token}">')
    return template.format(token=token)


templates.env.globals["csrf_field"] = _csrf_field
templates.env.globals["help_url"] = _help_url


# Chart series palette — single Python source of truth, mirrored into
# app.css (--chart-inherent/--chart-residual). Dark mode is pure CSS now
# (the old chart_theme.js client-restyle was retired in #547 P3). See
# services/chart_palette.py for the validation record;
# tests/unit/test_chart_tokens.py pins both copies.
from idraa.services.chart_palette import (  # noqa: E402
    CHART_SERIES,
    TRACE_META_INHERENT,
    TRACE_META_RESIDUAL,
)

templates.env.globals["chart_series"] = CHART_SERIES
templates.env.globals["trace_meta_inherent"] = TRACE_META_INHERENT
templates.env.globals["trace_meta_residual"] = TRACE_META_RESIDUAL

# First-party SVG chart geometry (epic #547 P1 + P2) — pure functions exposed
# as a SimpleNamespace so macros call chart_svg.dual_curve(...) / .epc_curve(...) /
# .slider_pos(...) / etc. without leaking module internals into the Jinja
# global namespace.
from idraa.services.chart_svg import (  # noqa: E402
    ci_band,
    comparison_bars,
    dual_curve,
    effectiveness_bars,
    epc_curve,
    single_epc_curve,
    single_lec_curve,
    slider_pos,
)

templates.env.globals["chart_svg"] = SimpleNamespace(
    dual_curve=dual_curve,
    epc_curve=epc_curve,
    slider_pos=slider_pos,
    single_lec_curve=single_lec_curve,
    single_epc_curve=single_epc_curve,
    ci_band=ci_band,
    effectiveness_bars=effectiveness_bars,
    comparison_bars=comparison_bars,
)
# Per-figure id source for figure-internal controls (avoids duplicate DOM ids
# when a page renders more than one chart). uuid4().hex[:8] is collision-safe
# at page scale.
templates.env.globals["chart_uid"] = lambda: uuid.uuid4().hex[:8]

# Per-deploy static-asset cache-bust version. Templates append
# `?v={{ static_version }}` to /static/* URLs so a fresh `fly deploy`
# automatically invalidates aggressive mobile-browser JS/CSS caches.
templates.env.globals["static_version"] = STATIC_VERSION

# Issue #413: control-weight provenance disclaimer — exposed as a Jinja global so
# the run-detail control-value surfaces (cost-vs-risk-reduction section + Shapley
# attribution matrix) render the same byte-identical string used by the PDF + Excel
# reports. Anchored to fair_cam's GROUP_NODE_MAPPING weights_provenance label.
from idraa.services._view_model_helpers import (  # noqa: E402
    CONTROL_WEIGHT_PROVENANCE_DISCLAIMER,
    CONTROL_WEIGHT_PROVENANCE_DISCLAIMER_BASE,
    CONTROL_WEIGHT_PROVENANCE_DISCLAIMER_WITH_IF_REMOVED,
)

templates.env.globals["control_weight_provenance_disclaimer"] = CONTROL_WEIGHT_PROVENANCE_DISCLAIMER
# M4: base variant (no indistinguishable sentence) for surfaces where
# weight_robustness is absent.
templates.env.globals["control_weight_provenance_disclaimer_base"] = (
    CONTROL_WEIGHT_PROVENANCE_DISCLAIMER_BASE
)
# Leave-one-out "if removed" legend (2026-07-03): appended variant used ONLY on
# the per-control value-range table surfaces (SINGLE + AGGREGATE) that carry the
# "If removed" column — other CONTROL_WEIGHT_PROVENANCE_DISCLAIMER call sites
# (headline explainer boxes, attribution-matrix intro) do not gain this sentence.
templates.env.globals["control_weight_provenance_disclaimer_with_if_removed"] = (
    CONTROL_WEIGHT_PROVENANCE_DISCLAIMER_WITH_IF_REMOVED
)

# Redesign P1 T3: consolidated caveat registry — single source of caveat prose
# for the run-detail Summary panel (and future Workbench rail), replacing the
# scattered disclaimer paragraphs above with numbered chips + one panel.
from idraa.services.run_caveats import active_run_caveats  # noqa: E402

templates.env.globals["active_run_caveats"] = active_run_caveats

# Redesign P1 T4: web-only display-label map over the canonical tail ladder
# (TAIL_LADDER_LABELS) — see _view_model_helpers.py for the methodology note.
from idraa.services._view_model_helpers import TAIL_LADDER_DISPLAY_LABELS  # noqa: E402

templates.env.globals["tail_ladder_display_labels"] = TAIL_LADDER_DISPLAY_LABELS

# Mean+typical side-by-side (2026-07-04): trailing sentence appended after the
# per-control value-range legend ONLY when the run's weight_robustness blob is
# basis=="mean" — see _view_model_helpers.py for the full rationale. Exposed as
# a Jinja global (same pattern as the disclaimer constants above) so templates
# render the byte-identical string without a per-template import.
from idraa.services._view_model_helpers import MEAN_BASIS_PAIRING_NOTE  # noqa: E402

templates.env.globals["mean_basis_pairing_note"] = MEAN_BASIS_PAIRING_NOTE


# Issue #129 T3 — unit-aware capability widgets dispatch on this map.
# Pre-flattened to unit-string values (UnitType.value) so macro template
# literals can match without invoking enum lookups inside Jinja.
templates.env.globals["sub_function_units_map"] = {
    sf: unit.value for sf, unit in SUB_FUNCTION_UNITS.items()
}

# Short human-readable description per sub-function. Rendered under the
# sub-function select in the controls form so analysts can read what
# each slug means instead of memorising the FAIR-CAM identifiers.
from idraa.models.enums import SUB_FUNCTION_DESCRIPTIONS  # noqa: E402

templates.env.globals["sub_function_descriptions"] = SUB_FUNCTION_DESCRIPTIONS

# Grouped option data consumed by the Tier-2 combobox in
# controls/_assignment_row.html. JSON-safe shape so it can be injected
# directly into a <script>window.SUB_FUNCTION_GROUPS = {{...|tojson}}</script>.
from idraa.services.sub_function_options import groups_to_json_safe  # noqa: E402

templates.env.globals["sub_function_groups_json"] = groups_to_json_safe()

from idraa.utils.breadcrumbs import breadcrumb_for  # noqa: E402

templates.env.globals["breadcrumb_for"] = breadcrumb_for


def _format_pct(value: float | None, *, precision: int = 1) -> str:
    if value is None:
        return "—"
    return f"{value * 100:.{precision}f}%"


def _format_count(value: int | float | None) -> str:
    """Human-readable count. >=10k abbreviated; >=1M shows M."""
    if value is None:
        return "—"
    v = float(value)
    if abs(v) >= 1_000_000:
        return f"{v / 1_000_000:.1f}M"
    if abs(v) >= 10_000:
        return f"{v / 1_000:.1f}k"
    return f"{int(v):,}"


def _format_delta(
    value: float | None,
    *,
    format: str = "pct",
    precision: int = 1,
    code: str = "USD",
) -> tuple[str, str]:
    """Return (formatted_str, css_class_suffix) for a signed delta.

    ``code`` is the ISO currency code for the "money" format variant — the value
    is already converted; this is a label-only parameter passed from the template
    context (e.g. ``display_results.currency.code``).  Defaults to USD so
    existing callers that don't thread ``code`` remain correct (backward-compat).
    """
    if value is None:
        return ("—", "ink-3")
    sign = "+" if value > 0 else ("-" if value < 0 else "±")
    abs_v = abs(value)
    if format == "pct":
        body = f"{abs_v * 100:.{precision}f}%"
    elif format == "money":
        # Use safe_money_format for the label (never multiplies — value already converted).
        from idraa.formatting import safe_money_format

        body = safe_money_format(abs_v, code, compact=True)
    else:
        body = f"{abs_v:,.{precision}f}"
    css = "numeric-pos" if value > 0 else ("numeric-neg" if value < 0 else "ink-2")
    return (f"{sign}{body}", css)


templates.env.filters["format_pct"] = _format_pct
templates.env.filters["format_count"] = _format_count
templates.env.filters["format_delta"] = _format_delta


def _format_mult(value: float | int | str | None) -> str:
    """Render a multiplier as ``"%.2f"`` (e.g. ``1.40``).

    Centralizes the duplicated ``"%.2f"|format(...)`` filter chain that
    was scattered across overlay + override + (future) scenario
    templates. Returns the bare number — templates that want a trailing
    multiplication-sign glyph keep that styling decision local.

    Tolerates ``None`` (returns ``"—"``) so partial templates can use
    the filter on optional fields without a guard.
    """
    if value is None:
        return "—"
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return str(value)


templates.env.filters["format_mult"] = _format_mult


# Humanizer moved to the leaf module ``idraa.utils.text`` so the service layer
# (e.g. the verification workbook) can reuse it without importing the FastAPI app.
# Kept as ``_humanize_slug`` here for the filter registration + the existing
# ``from idraa.app import _humanize_slug`` test import.
_humanize_slug = _humanize_slug_impl


templates.env.filters["humanize_slug"] = _humanize_slug


def _format_money(value: float | None) -> str:
    """Format a numeric value as money: 1234567.89 -> '1,234,568'."""
    if value is None:
        return "n/a"
    return f"{round(value):,}"


templates.env.filters["format_money"] = _format_money


# ---------------------------------------------------------------------------
# Numeric-input formatters (UAT bug from PR #247).
#
# The T1 quantile-pooling pipeline produces honest tiny-float low
# quantiles (e.g. 1.5e-06 dollars when an SME enters a $1k low estimate
# on a long-tailed distribution). Rendering those raw into
# <input type="number"> fields produced unreadable scientific notation
# truncated mid-exponent ("1.5146025633444114e-0...") and absurd 6+
# decimal precision on currency ("69999999.98617376").
#
# Three filters cover the three numeric classes used in FAIR scenarios:
#   - money    (PL / SL): 2 decimals (cents)
#   - rate     (TEF):     4 decimals (events/year are often <1/yr)
#   - probability (Vuln): 4 decimals (0..1)
#
# All three suppress scientific notation via fixed-point ".Nf" and
# accept ``None`` → "" so optional fields (e.g. omitted SL) render an
# empty input. These are display-only — pooling math is unchanged.
#
# DO NOT include "$" or thousands separators: ``<input type="number">``
# rejects non-numeric characters and silently drops the prefill. Display
# contexts that want $/, formatting should keep using
# ``format_money`` / ``abbreviate_money``.
# ---------------------------------------------------------------------------


def _format_money_input(value: float | None) -> str:
    """Render a $ value for ``<input type="number">``: 2 decimals, no sci notation.

    Examples: 1.5e-06 -> "0.00", 69999999.98617376 -> "69999999.98".
    None -> "" (empty input for optional fields).
    """
    if value is None:
        return ""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return ""
    if v != v or v in (float("inf"), float("-inf")):  # NaN / inf guard
        return ""
    return f"{v:.2f}"


def _format_rate_input(value: float | None) -> str:
    """Render a rate (events/year) for ``<input type="number">``: 4 decimals, no sci notation.

    Examples: 0.00200000144 -> "0.0020", 0.060000000777 -> "0.0600".
    None -> "".
    """
    if value is None:
        return ""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return ""
    if v != v or v in (float("inf"), float("-inf")):
        return ""
    return f"{v:.4f}"


def _format_probability_input(value: float | None) -> str:
    """Render a 0..1 probability for ``<input type="number">``: 4 decimals, no sci notation.

    Examples: 0.2999998117 -> "0.3000", 0.551088591 -> "0.5511".
    None -> "".
    """
    if value is None:
        return ""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return ""
    if v != v or v in (float("inf"), float("-inf")):
        return ""
    return f"{v:.4f}"


templates.env.filters["format_money_input"] = _format_money_input
templates.env.filters["format_rate_input"] = _format_rate_input
templates.env.filters["format_probability_input"] = _format_probability_input


# Re-export from the shared formatting module.
# Task 8: ``abbreviate_money`` filter retired — all templates use ``money`` (safe_money_format).
# ``_abbreviate_money`` alias removed; test_filters.py tests were triaged accordingly.
from idraa.formatting import linkify_https as _linkify_https  # noqa: E402
from idraa.formatting import safe_money_format as _safe_money_format  # noqa: E402

templates.env.filters["linkify_https"] = _linkify_https


def _money_filter(value: object, code: str = "USD", compact: bool = True) -> str:
    """Jinja filter: format a converted money value in the given currency.

    Usage: {{ value | money(currency.code) }}
    The value is ALREADY in reporting currency (converted by the view-model);
    this filter ONLY formats — it never multiplies. Task 8 retires abbreviate_money.
    """
    import math

    if value is None:
        return "—"
    try:
        v = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "—"
    if not math.isfinite(v):
        return "—"
    return _safe_money_format(v, code, compact=compact)


templates.env.filters["money"] = _money_filter


def _format_datetime(dt: datetime.datetime | None) -> str:
    """Render a datetime as a client-localizable <time> element.

    UAT 2026-05-21: operators reported timestamps were showing UTC with
    no obvious indication, making it impossible to mentally convert to
    their local zone. Emit a <time datetime="<ISO UTC>" data-localize=
    "datetime"> element; the client-side localizer in `base.html` walks
    these on DOMContentLoaded and after each htmx swap, replacing the
    inner text with the browser's local-format string. Server-side
    fallback text is "YYYY-MM-DD HH:MM UTC" so the value remains
    legible if JS is disabled.

    Returns em-dash for None.
    """
    if dt is None:
        return "—"
    if dt.tzinfo is None:
        # DB rows are stored in UTC but some SQLAlchemy paths return naive
        # datetimes — assume UTC at the boundary.
        dt = dt.replace(tzinfo=datetime.UTC)
    iso = dt.astimezone(datetime.UTC).isoformat()
    fallback = dt.astimezone(datetime.UTC).strftime("%Y-%m-%d %H:%M UTC")
    # Build via Markup.format() rather than f-string-into-Markup so the
    # interpolated values are auto-escaped (defense in depth — today they
    # come from datetime.strftime and can't contain markup, but the
    # codebase convention rejects f-string Markup per ruff S704; same
    # pattern as _csrf_field above).
    template: Markup = Markup('<time datetime="{iso}" data-localize="datetime">{fallback}</time>')
    return template.format(iso=iso, fallback=fallback)


def _format_date(dt: datetime.datetime | None) -> str:
    """Date-only variant of _format_datetime. Client-localized to the
    browser's locale via the same <time> element pattern."""
    if dt is None:
        return "—"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.UTC)
    iso = dt.astimezone(datetime.UTC).isoformat()
    fallback = dt.astimezone(datetime.UTC).strftime("%Y-%m-%d")
    template: Markup = Markup('<time datetime="{iso}" data-localize="date">{fallback}</time>')
    return template.format(iso=iso, fallback=fallback)


templates.env.filters["format_datetime"] = _format_datetime
templates.env.filters["format_date"] = _format_date


# ---------------------------------------------------------------------------
# Epic B (#326): lognormal display helper + format dispatcher
# ---------------------------------------------------------------------------


def lognormal_display_rows(dist: dict[str, object] | None) -> dict[str, float] | None:
    """Compute real-space percentile display rows for a lognormal distribution.

    Returns None for non-lognormal dicts (callers use this to branch the
    Jinja template: if truthy → lognormal table, else → PERT low/mode/high).

    For lognormal dicts returns a dict with keys:
      p5     — real-space 5th percentile  (= exp(mean - z*sigma))
      median — real-space 50th percentile (= exp(mean))
      mean   — real-space expected value  (= exp(mean + sigma²/2)) — MANDATORY
      p95    — real-space 95th percentile (= exp(mean + z*sigma))

    Derives all values directly from the stored {mean, sigma} log-space params
    — no reflattening through a PERT triple (plan CRITICAL requirement).

    The mean exceeds the median for sigma > 0 (skewed right tail); including
    both is mandatory — a median-only view would understate expected loss.
    """
    if not dist or str(dist.get("distribution", "")).lower() != "lognormal":
        return None
    from fair_cam.quantile_pooling import lognormal_mean, lognormal_quantiles

    # Stored log-space params are floats; dist is typed dict[str, object].
    m = cast(float, dist["mean"])
    s = cast(float, dist["sigma"])
    p5, p50, p95 = lognormal_quantiles(m, s, (0.05, 0.5, 0.95))
    return {
        "p5": p5,
        "median": p50,
        "mean": lognormal_mean(m, s),
        "p95": p95,
    }


templates.env.globals["lognormal_display_rows"] = lognormal_display_rows


def lognormal_mixture_display_rows(dist: dict[str, object] | None) -> dict[str, Any] | None:
    """Compute real-space percentile display rows for a lognormal_mixture
    distribution (issue #27 Task 6, true mixture pooling).

    Returns None for non-lognormal_mixture dicts (callers use this to branch
    the Jinja template), mirroring ``lognormal_display_rows`` above.

    For lognormal_mixture dicts returns a dict with keys:
      p5, median, p95 — MIXTURE percentiles (NOT a per-component average) via
        fair_cam's deterministic ``mixture_quantile_lognorm`` (bisection on
        the pooled CDF Sigma w_i F_i(x) — no sampling).
      mean — analytic mixture mean Sigma w_i * exp(mean_i + sigma_i**2/2).
      n_components — component count.
      components — per-component [{"weight", "mean", "sigma"}, ...] for the
        "n expert opinions, weights ..." sub-list.
      weights_display — pre-formatted "50.0%, 50.0%" string (Python-built,
        not Jinja loop concatenation) for the template's numeric-only
        weights sub-list — every value is a rounded float, no raw stored
        text passes through.

    Storage note: catastrophic pl/sl mixture components are stored as native,
    UNTRUNCATED {mean, sigma} pairs (wizard_finalize.build_scenario_payload:
    "non-binding [0, inf] truncation ... each component's (meanlog, sdlog) IS
    its native untruncated {mean, sigma}"). This rebuilds a
    ``LognormMixture`` of ``LogNormalTruncFit``s with
    min_support=0.0/max_support=inf to match that untruncated support before
    calling the fair_cam quantile math — the SAME support convention as the
    native single-lognormal path above (``lognormal_quantiles``, which is
    also unbounded).
    """
    if not dist or str(dist.get("distribution", "")).lower() != "lognormal_mixture":
        return None
    from fair_cam.quantile_pooling import (
        LogNormalTruncFit,
        LognormMixture,
        mixture_quantile_lognorm,
    )

    components_raw = cast(list[dict[str, object]], dist["components"])
    fits = tuple(
        LogNormalTruncFit(
            meanlog=cast(float, c["mean"]),
            sdlog=cast(float, c["sigma"]),
            min_support=0.0,
            max_support=math.inf,
        )
        for c in components_raw
    )
    weights = tuple(cast(float, c["weight"]) for c in components_raw)
    mix = LognormMixture(components=fits, weights=weights)

    analytic_mean = sum(
        w * math.exp(f.meanlog + f.sdlog**2 / 2.0) for f, w in zip(fits, weights, strict=True)
    )
    return {
        "p5": mixture_quantile_lognorm(mix, 0.05),
        "median": mixture_quantile_lognorm(mix, 0.5),
        "mean": analytic_mean,
        "p95": mixture_quantile_lognorm(mix, 0.95),
        "n_components": len(fits),
        "components": [
            {"weight": w, "mean": f.meanlog, "sigma": f.sdlog}
            for f, w in zip(fits, weights, strict=True)
        ],
        "weights_display": ", ".join(f"{w * 100:.1f}%" for w in weights),
    }


templates.env.globals["lognormal_mixture_display_rows"] = lognormal_mixture_display_rows


def _format_dist_value(value: float | None, fmt: str) -> str:
    """Dispatch a distribution display value to the appropriate format filter.

    Reuses the existing format_money_input / format_rate_input /
    format_probability_input filters so lognormal percentile table cells
    render identically to PERT row cells.
    'money'       → format_money_input       (PL/SL: 2dp, no sci notation)
    'rate'        → format_rate_input        (TEF:   4dp, no sci notation)
    'probability' → format_probability_input (Vuln:  4dp 0..1 probability)
    """
    if fmt == "rate":
        return _format_rate_input(value)
    if fmt == "probability":
        # I-2: vuln is a 0..1 probability; rendering it via money (2dp) loses
        # precision (e.g. 0.3500 -> "0.35"). Use the 4dp probability filter.
        return _format_probability_input(value)
    return _format_money_input(value)


templates.env.filters["format_dist_value"] = _format_dist_value


# Setup-guard allowlist. Two shapes so the guard can use segment-aware
# matching instead of a naive ``startswith`` (which would allow
# ``/setupXYZ`` / ``/loginAttack`` through). ``_ALLOW_EXACT`` is for routes
# where only the literal path is safe; ``_ALLOW_DIR_PREFIXES`` is for
# Starlette-mount-style subtrees whose descendants are intentionally in
# the allowlist. ``/api`` is NOT listed — the JSON API surface does not
# exist yet; re-add it explicitly when 1.2+ ships it.
_ALLOW_EXACT = frozenset(
    {"/setup", "/healthz", "/login", "/sw.js"}
)  # /sw.js: PWA shim, static asset served at root scope (M0.1)
_ALLOW_DIR_PREFIXES = ("/setup/", "/static/", "/login/")


def _path_allowed(path: str) -> bool:
    """True if `path` bypasses setup_guard's redirect to /setup.

    Matching is segment-aware to prevent prefix-abuse URLs like `/setupXYZ`
    or `/loginAttack` from slipping past the exact-match paths. Dir-prefix
    paths require a trailing-slash separator in the allowlist constants.
    """
    return path in _ALLOW_EXACT or path.startswith(_ALLOW_DIR_PREFIXES)


async def _export_rate_limited_handler(request: StarletteRequest, exc: Exception) -> Response:
    """#357: ExportRateLimitedError -> 429 with Retry-After.

    Export endpoints are plain GET downloads, so a text body is what the
    browser shows the user; API callers get the same via the status code +
    Retry-After header. Registered app-level so none of the ~10 export
    routes carries limiter code."""
    # Starlette types the handler as (Request, Exception); registration keys
    # this handler to ExportRateLimitedError, so the isinstance is a narrowing
    # for mypy, not a runtime guard (S101 forbids assert in src).
    retry_after = exc.retry_after_seconds if isinstance(exc, ExportRateLimitedError) else 300
    return PlainTextResponse(
        "Export rate limit exceeded — try again shortly.",
        status_code=429,
        headers={"Retry-After": str(retry_after)},
    )


async def _auth_redirect_handler(request: StarletteRequest, exc: HTTPException) -> Response:
    """401 -> 303 /login?next=... for HTML callers; JSON for API callers.

    1.1.8 is the first route that uses ``require_user``; without this
    handler a browser user whose session has expired would see a bare
    401 JSON body instead of being bounced to /login. The dispatch
    decision is the Accept header:

    - JSON API consumer (``Accept: application/json``) -> pass through
      to FastAPI's default JSON-shaped error.
    - Browser (``text/html``, ``*/*``, or no Accept header at all) ->
      303 to /login?next=<original path> so the login form posts back
      to the right place after sign-in.

    Treating a missing Accept header as HTML is deliberate: today every
    route in the app is HTML-rendering, and mis-routing a hypothetical
    API client to /login is a clean 303 they can choose to follow or
    ignore. We only fall BACK to JSON when the caller explicitly asks
    for it.

    Scope: 401-only. 403 (``require_role``) falls through to the default
    JSON handler — the right behaviour for "signed in but wrong role",
    where a redirect would loop the user back and forth.
    """
    if exc.status_code == 401:
        accept = request.headers.get("accept", "")
        if "application/json" not in accept:
            return RedirectResponse(f"/login?next={request.url.path}", status_code=303)
    return JSONResponse(
        {"detail": exc.detail},
        status_code=exc.status_code,
        headers=getattr(exc, "headers", None),
    )


async def _step_up_handler(request: StarletteRequest, exc: StepUpRequired) -> Response:
    """StepUpRequired -> the /auth/step-up challenge.

    Browsers get a 303; HTMX callers get 204 + HX-Redirect (mirrors
    EnrollmentGuardMiddleware); fetch/JSON callers (Accept:
    application/json — webauthn.js sets it) get a structured 401 whose
    `redirect` the client follows. The next target inside `dest` was
    sanitized by routes/deps.py::_step_up_next before it reached the
    exception.
    """
    dest = f"/auth/step-up?next={quote(exc.next_url, safe='/')}"
    if request.headers.get("HX-Request") == "true":
        return Response(status_code=204, headers={"HX-Redirect": dest})
    if "application/json" in request.headers.get("accept", ""):
        return JSONResponse({"error": "step_up_required", "redirect": dest}, status_code=401)
    return RedirectResponse(dest, status_code=303)


@contextlib.asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup hook: reap orphaned runs (issue #211).

    A SIGKILL / OOM on the single Fly worker can't be caught in-process, so an
    in-flight run row stays ``status='running'`` forever. On every boot we
    sweep RUNNING / stale QUEUED rows older than the threshold to FAILED — a
    process boot means any pre-boot worker is dead (in-process BackgroundTasks
    die with the process).

    Settings + the DB session are resolved HERE at startup (not bound at
    module import), so the test harness's monkeypatched DATABASE_URL +
    ``config.reset_for_tests()`` are honored. A reaper failure must not brick
    boot — it is logged and swallowed.
    """
    import asyncio as _asyncio

    from idraa.config import get_settings as _get_settings
    from idraa.services.org import get_sole_org
    from idraa.services.retention import maybe_sweep_opportunistic, sweep_retention
    from idraa.services.run_reaper import (
        periodic_reaper_loop,
        reap_orphaned_runs,
        sweep_expired_previews,
        sweep_expired_sessions,
        sweep_wizard_drafts,
    )

    _settings = _get_settings()

    # Fail-fast: a self-inconsistent retention config (#297) must crash boot,
    # NOT be swallowed by the reaper's broad except below. Runs BEFORE the
    # reaper try/except so the RetentionConfigError propagates and aborts
    # startup loudly rather than silently mis-retaining run data.
    _settings.validate_retention()

    try:
        async with get_session() as db:
            await reap_orphaned_runs(db, _settings)
            # Resolve the sole org for the startup retention sweep. On a fresh
            # DB (before /setup) there is no org yet — skip gracefully.
            org = await get_sole_org(db)
        # Startup retention sweep routed through the SAME guarded opportunistic
        # trigger (own session, atomic throttle), so a just-swept boot won't
        # re-sweep. Inside the try/except: a transient failure is swallowed; the
        # config-validation fail-fast above already crashed boot on misconfig.
        if org is not None:
            await maybe_sweep_opportunistic(_settings, org_id=org.id)
    except Exception:  # a reaper / sweep bug must never block startup
        logging.getLogger(__name__).exception(
            "Startup orphaned-run reaper / retention sweep failed"
        )

    # Drafts-surfaced spec §4 (DA-3): a boot one-shot TTL sweep of idle
    # wizard drafts — PRIMARY sweep path on scale-to-zero deploys where the
    # periodic loop below may not get a full interval before the machine
    # suspends again. Own session (opened inside sweep_wizard_drafts), own
    # try/except — a sweep bug must never block startup. Sibling to (NOT
    # nested inside) the reaper/retention try/except above.
    try:
        await sweep_wizard_drafts(_settings)
    except Exception:
        logging.getLogger(__name__).exception("Boot wizard-draft sweep failed; continuing startup")

    # Issue #80 (L9): boot one-shot TTL sweep of expired csv_import_preview
    # rows — same "primary sweep path on scale-to-zero deploys" rationale as
    # the wizard-draft sweep above. Sibling try/except (a sweep bug must
    # never block startup).
    try:
        await sweep_expired_previews(_settings)
    except Exception:
        logging.getLogger(__name__).exception(
            "Boot CSV import-preview sweep failed; continuing startup"
        )

    # Issue #80 (I2): boot one-shot TTL sweep of expired auth_sessions rows —
    # security-neutral housekeeping (bounded table growth), same sibling
    # try/except pattern.
    try:
        await sweep_expired_sessions(_settings)
    except Exception:
        logging.getLogger(__name__).exception(
            "Boot expired-session sweep failed; continuing startup"
        )

    # Task 5 (Arch-B1): a SEPARATE startup-only VACUUM sweep, additive to the
    # throttled opportunistic sweep above — NOT a replacement (replacing it
    # would bypass the request-path throttle). Its own fresh session, own
    # try/except (a VACUUM bug must never block boot). Running both at boot is
    # harmless: this second sweep finds nothing new to purge (the opportunistic
    # sweep above already purged the aged rows), and sweep_retention's own
    # vacuum=True gating fires VACUUM off ACTUAL reclaimable free space
    # (freelist), not this pass's purge count — so it still runs even though
    # this second sweep purges 0 (ARCH-I1). Gate: enabled + free_bytes >=
    # retention_vacuum_min_free_bytes + no-active-runs.
    try:
        async with get_session() as db:
            await sweep_retention(db, _settings, vacuum=True)
    except Exception:
        logging.getLogger(__name__).exception("Startup VACUUM sweep failed")

    # #211 Phase 2: periodic orphan sweep for runs that die WITHOUT a process
    # restart (the boot sweep above only fires on restart). The active-run
    # registry inside run_reaper makes the sweep safe for live runs; interval
    # 0 disables the loop (boot sweep still ran above).
    _reaper_task: _asyncio.Task[None] | None = None
    if _settings.run_reaper_interval_seconds > 0:
        _reaper_task = _asyncio.create_task(periodic_reaper_loop(_settings))
    try:
        yield
    finally:
        if _reaper_task is not None:
            _reaper_task.cancel()
            with contextlib.suppress(_asyncio.CancelledError):
                await _reaper_task


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version=settings.version,
        docs_url="/api/docs" if settings.environment != "prod" else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if settings.environment != "prod" else None,
        lifespan=_lifespan,
    )

    # Middleware is LIFO. Final wire order (outermost to innermost):
    #   request -> uat_basic_auth -> setup_guard -> SecurityHeaders ->
    #              CSRF -> Session -> EnrollmentGuard -> MaintenanceBadgeCount -> route
    #   response <- (reverse)
    #
    # LIFO add-order; uat_basic_auth registered last (after setup_guard
    # below) → outermost overall.
    #
    # EnrollmentGuard MUST be added after Maintenance but before Session so it
    # ends up inner to Session (Session runs first inbound and populates
    # request.state.user, which the guard reads with zero DB access).
    app.add_middleware(MaintenanceBadgeCountMiddleware)
    app.add_middleware(EnrollmentGuardMiddleware)
    app.add_middleware(SessionMiddleware)
    app.add_middleware(
        CSRFMiddleware,
        secret=settings.session_secret,
        # Only set the Secure cookie flag in prod — dev/test use http://
        # test clients that would otherwise silently drop the cookie.
        secure_cookie=(settings.environment == "prod"),
    )
    app.add_middleware(SecurityHeadersMiddleware)

    # Setup-guard: if no users exist in the DB, redirect everything that
    # isn't allowlisted to /setup. Registered as the OUTERMOST http middleware
    # so it can short-circuit a redirect before the request pays the cost of
    # SecurityHeaders + CSRF + Session + DB session load for the session
    # middleware. That short-circuit is also why the guard hits the DB with
    # its OWN session rather than taking one from the route-level
    # ``get_db`` dependency (no dep-injection at the middleware layer).
    #
    # The DB check runs on every request. Fine for phase 1 (single-admin
    # tool, SQLite) — explicit TODO to add a "seeded=true" signal or
    # process-local flag when the user base grows.
    async def setup_guard(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        path = request.url.path
        # /login is allowlisted from day one so Task 1.1.6 doesn't have to
        # amend this. Harmless while the DB is empty — no user matches, so
        # the login handler will render its own "invalid credentials" path.
        #
        # Segment-aware matching: a naive ``startswith("/setup")`` would also
        # accept ``/setupXYZ`` or ``/loginAttack`` — allowing an attacker to
        # craft a path that bypasses the guard. ``_ALLOW_EXACT`` holds routes
        # where only the literal path is safe; ``_ALLOW_DIR_PREFIXES`` holds
        # Starlette-mount-style subtrees (``/setup/foo``, ``/static/css/x``)
        # whose descendants are intentionally in the allowlist.
        if _path_allowed(path):
            return await call_next(request)
        async with get_session() as db:
            count = (await db.execute(select(func.count()).select_from(User))).scalar_one() or 0
        if count == 0:
            return RedirectResponse("/setup", status_code=307)
        return await call_next(request)

    app.middleware("http")(setup_guard)

    # UAT basic-auth pre-gate (Phase 1.5.5). Sits OUTSIDE setup_guard so
    # an unauthenticated UAT visitor sees the basic-auth prompt before
    # any DB roundtrip. No-ops when UAT_BASIC_AUTH_PASSWORD is unset
    # (dev, test, local docker). /healthz is exempt so Fly's health
    # probe passes regardless of credential state.
    app.middleware("http")(uat_basic_auth_factory())

    # Routers
    from idraa.routes import auth as auth_router
    from idraa.routes import control_library as control_library_router
    from idraa.routes import controls as controls_router
    from idraa.routes import dashboard as dashboard_router
    from idraa.routes import dev_styleguide as dev_styleguide_router
    from idraa.routes import fx_rates as fx_rates_router
    from idraa.routes import help as help_router
    from idraa.routes import library as library_router
    from idraa.routes import library_import as library_import_router
    from idraa.routes import library_overrides as library_overrides_router
    from idraa.routes import mfa as mfa_router
    from idraa.routes import organization as organization_router
    from idraa.routes import overlays as overlays_router
    from idraa.routes import qualitative_bands as qualitative_bands_router
    from idraa.routes import register_import as register_import_router
    from idraa.routes import reports as reports_router
    from idraa.routes import runs as runs_router
    from idraa.routes import scenario_import as scenario_import_router
    from idraa.routes import scenarios as scenarios_router
    from idraa.routes import setup as setup_router
    from idraa.routes import sme_directory as sme_directory_router
    from idraa.routes import step_up as step_up_router
    from idraa.routes import users as users_router
    from idraa.routes.scenario_form_helpers import (
        asset_class_choices as _asset_class_choices,
    )

    # Expose asset_class_choices() as a Jinja2 global so templates can derive
    # the dropdown list from the enum directly (single source of truth).
    # Registered here — inside create_app() after the deferred router imports —
    # to avoid a circular import: scenario_form_helpers imports `templates`
    # from idraa.app at its module level, so a top-level import of
    # scenario_form_helpers in app.py would form a cycle.
    templates.env.globals["asset_class_choices"] = _asset_class_choices

    app.include_router(setup_router.router)
    app.include_router(auth_router.router)
    app.include_router(dashboard_router.router)
    app.include_router(help_router.router)
    app.include_router(organization_router.router)
    app.include_router(users_router.router)
    app.include_router(mfa_router.router)
    app.include_router(step_up_router.router)
    # Arch-B1: control_library MUST be included BEFORE controls_router. The
    # latter owns GET /controls/{control_id:uuid}; FastAPI resolves routes in
    # registration order across routers, so if controls_router went first,
    # GET /controls/library would match {control_id} and 422 (uuid_parsing).
    app.include_router(control_library_router.router)
    app.include_router(controls_router.router)
    app.include_router(overlays_router.router)
    # scenario_import MUST be included BEFORE scenarios_router: scenarios owns
    # GET /scenarios/{scenario_id} as an UNTYPED path param, which would
    # otherwise capture /scenarios/import (and the static subpaths) and 404.
    app.include_router(scenario_import_router.router)
    app.include_router(scenarios_router.router)
    app.include_router(runs_router.router)
    # library_import MUST be included BEFORE library_router: library owns
    # GET /library/entries/{entry_id:uuid}. The /library/import* paths are
    # literal (not captured by the typed uuid param), but registering the
    # importer first keeps the ordering consistent with control_library /
    # scenario_import and removes any room for future regressions.
    app.include_router(library_import_router.router)
    app.include_router(library_router.router)
    app.include_router(library_overrides_router.router)
    app.include_router(fx_rates_router.router)
    app.include_router(register_import_router.router)
    app.include_router(qualitative_bands_router.router)
    app.include_router(reports_router.router)
    app.include_router(sme_directory_router.router)
    app.include_router(
        dev_styleguide_router.router
    )  # Arch-7: always mounted; in-handler 404 gate checks the flag

    # Exception handler: 401 from require_user -> 303 /login?next=... for
    # HTML clients; JSON callers still get a structured 401 body. Registered
    # AFTER include_router so the route tree is settled; handlers are
    # attached to the app itself and the registration order has no bearing
    # on dispatch precedence. See ``_auth_redirect_handler`` for details.
    app.add_exception_handler(HTTPException, _auth_redirect_handler)  # type: ignore[arg-type]
    app.add_exception_handler(ExportRateLimitedError, _export_rate_limited_handler)
    app.add_exception_handler(StepUpRequired, _step_up_handler)  # type: ignore[arg-type]

    # Static assets: serve with `Cache-Control: no-cache` so the browser
    # ALWAYS revalidates with the server before reusing a cached copy.
    # Combined with the existing ETag header that StaticFiles emits, a
    # revalidation costs ~1 round-trip and ~0 bytes when the file hasn't
    # changed (304 Not Modified). Without this, browsers (especially iOS
    # Safari) fall back to heuristic caching and hold stale JS/CSS for
    # hours across deploys — live UAT 2026-05-20: stale combobox JS on
    # real phone served pre-#195 behaviour even after multiple deploys.
    # The `?v={{ static_version }}` cache-bust still applies on top:
    # different URLs are different cache entries, so a fresh deploy
    # forces a hard fetch regardless of validator state.
    class _RevalidatedStaticFiles(StaticFiles):
        async def get_response(self, path: str, scope: Scope) -> Response:
            response = await super().get_response(path, scope)
            response.headers["Cache-Control"] = "no-cache"
            return response

    app.mount("/static", _RevalidatedStaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "version": settings.version}

    @app.get("/sw.js", include_in_schema=False)
    async def service_worker() -> FileResponse:
        """PWA installability shim served at the ROOT path (not /static/):
        a service worker's default scope is its script's directory, and the
        app must control scope "/" for install. No-cache like all statics."""
        return FileResponse(
            STATIC_DIR / "sw.js",
            media_type="text/javascript",
            headers={"Cache-Control": "no-cache"},
        )

    return app


app = create_app()
