"""Scenario loss-pin / stale-wide-banner helpers for :mod:`idraa.routes.scenarios`.

Extracted out of ``routes/scenarios.py`` under issue #119 (standing architect
advisory — the file crossed 4,600 lines after PR3 (#126) grew the sigma-
recalibration pin/banner seam by +1,152 lines). Mechanical extraction, NO
semantic change: every function here is byte-identical to its prior home in
``routes/scenarios.py``, just re-homed. ``routes/scenarios.py`` re-imports
these names for its own call sites (the view/edit context builders, the
pin/unpin/refresh routes, and the wizard finalize redirect).

These are pure (input-bound) helpers with no dependency on the router
itself — they read/derive from stored ``Scenario.primary_loss`` /
``secondary_loss`` dicts and build template-context dicts. Moving them out
is a no-op for behaviour, mirroring the ``scenario_form_helpers.py``
precedent (E7's extraction of the form-parse/render helpers).

Public surface consumed by ``routes/scenarios.py``:

- :func:`_expert_loss_readout_cfgs` / :func:`_pin_panel_context` — live-
  preview / pin-panel context builders for the create/edit form and its
  pin panel.
- :func:`_loss_was_recalibrated` / :func:`_loss_stale_wide` — the
  recalibration-migration banner and the stale-wide tripwire banner shown
  on the scenario view page.
- :func:`_max_tripwire_sigma` / :data:`_SIGMA_TOL` — the firing-basis sigma
  read + tolerance shared by the view page's ``?loss_wide=1`` flash and the
  wizard finalize redirect.
- :func:`_loss_sigma_display` / :func:`_cap_remint_disclosure` — the
  basis-labeled sigma reading and cap-remint disclosure rendered on the
  library-refresh confirmation page.
- :func:`_parse_pin_quantile` — form-string-to-float parsing for the pin
  route's ``pin_p50``/``pin_p95`` fields.

Also defines (module-internal + re-imported by ``scenario_wizard_seeding``):

- :data:`_Z_0_95` — the canonical 95th-percentile z-score used throughout
  the sigma-recalibration machinery.
- :func:`_stored_loss_sigma` / :func:`_field_has_provenance` /
  :func:`_tripwire_component_sigma` — the lower-level sigma/provenance
  reads the banner functions above compose.
"""

from __future__ import annotations

import math
from typing import Any

from fair_cam.quantile_pooling import (
    LogNormalTruncFit,
    LognormMixture,
    QuantilePoolingError,
    lognormal_quantiles,
    mixture_quantile_lognorm,
)

from idraa.app import _format_money_input
from idraa.models.scenario import Scenario
from idraa.services.calibration import SIGMA_WARN_THRESHOLD, WITHIN_SCENARIO_SIGMA_DEFAULT
from idraa.services.wizard_questions import IMPACT_FIELDSETS
from idraa.utils.money import sanitize_money_str


def _expert_loss_readout_cfgs(
    form: dict[str, Any], capacity_max: float | None
) -> dict[str, dict[str, Any]]:
    """PR3 T3: live-preview cfg for the expert form's PL/SL readout mounts
    (form.html, mounted under each field's dist-selector block per plan
    Step 5(a)). Shares the cfg dict SHAPE ``lossDispersionReadout`` expects
    -- Task 2's ``_build_readout_cfg`` N6 comment: "keep this dict's shape
    stable for that reuse."

    ``mode`` is ALWAYS ``"lognormal"`` here -- deliberately NOT threading
    the live dist-selector's PERT state into a ``"capped_pert"`` mount.
    Under PERT the operator types the distribution's own low/mode/high
    triple directly (``dist_from_raw``'s PERT fallthrough) -- those are NOT
    p5/p95 quantiles, and feeding them through ``fitP5P95`` ->
    ``capPertFromFit`` (the ONLY thing loss_preview.js's ``"capped_pert"``
    mode does) would silently mis-describe the exact PERT already being
    typed. The mount is x-show-gated to the lognormal selector state in
    form.html and simply hidden under PERT, where the raw low/mode/high
    triple already IS the full, correct picture with nothing left to
    preview (a deliberate, disclosed scope narrowing vs the wizard mount).

    ``tefMean``/``vulnMean`` are left ``None`` (the ALE composition line
    renders ONLY when both are non-null, per Task 2's Interfaces): the
    expert form's TEF/vuln are typed PERT triples with no scipy fit
    involved, and wiring their own live mean into ``$store.lossPreview``
    is out of this task's scope.

    T3.a gate fix (METH B-1): ``cap`` is the FIELD's OWN ``{field_key}_max``
    form value when present, falling back to the org-wide ``capacity_max``
    only when it is blank/unparseable -- mirroring ``loss_pinning.pin_loss``'s
    own ``existing_max if existing_max is not None else minted`` ternary.
    Passing the bare org cap unconditionally (the pre-fix behavior) is
    WRONG whenever a field carries a bespoke ``max`` narrower than
    ``k * revenue``: the live preview would advertise a looser ceiling than
    the chokepoint (``validate_fair_distributions``) actually enforces
    against that field's stored ``max``, producing a false "will be
    accepted" read that then 422s on save.
    """
    out: dict[str, dict[str, Any]] = {}
    for field_key, label in IMPACT_FIELDSETS:
        initial_low: float | None = None
        initial_high: float | None = None
        if form.get(f"{field_key}_dist") == "lognormal":
            try:
                initial_low = float(form.get(f"{field_key}_low") or "")
                initial_high = float(form.get(f"{field_key}_high") or "")
            except (TypeError, ValueError):
                initial_low = None
                initial_high = None
        raw_field_max = form.get(f"{field_key}_max")
        field_cap: float | None = None
        if raw_field_max is not None and raw_field_max != "":
            try:
                field_cap = float(raw_field_max)
            except (TypeError, ValueError):
                field_cap = None
        out[field_key] = {
            "mode": "lognormal",
            "quantileBasis": "p5p95",
            "sigmaDefault": WITHIN_SCENARIO_SIGMA_DEFAULT,
            "warnThreshold": SIGMA_WARN_THRESHOLD,
            "cap": field_cap if field_cap is not None else capacity_max,
            "currency": "USD",
            "tefMean": None,
            "vulnMean": None,
            "fieldKey": field_key,
            "label": label,
            "initialLow": initial_low,
            "initialHigh": initial_high,
        }
    return out


def _pin_panel_context(scenario: Scenario, capacity_max: float | None) -> dict[str, dict[str, Any]]:
    """PR3 T3 (D20/D21): per-field pin-panel context for the EDIT form
    (form.html, rendered AFTER ``</form>`` per plan Step 5(b) -- the panel
    POSTs to its own ``/loss/pin`` route, never nested inside the main
    scenario-edit ``<form>``).

    One entry per ``("pl", "sl")`` ALWAYS (even when ``secondary_loss`` is
    unset), so the template's loop is unconditional; an absent/non-lognormal
    field renders its one-line explanation instead of the pin controls
    (``kind`` drives that branch — ``None`` for absent, ``"pert"`` /
    ``"lognormal_mixture"`` / ``"lognormal"`` otherwise).

    ``prefill_p50``/``prefill_p95`` seed the pin panel's own p50/p95 inputs
    from the field's CURRENTLY stored lognormal fit (re-expressed from its
    native p5/p95 basis) so the analyst edits forward from the live
    dispersion rather than a blank pair. T3.a gate fix (METH I-4): routed
    through ``_format_money_input`` (2dp, no sci notation) -- the same PR
    #247 precision-class bug the expert form's own ``pert_input`` macro
    already guards against; a raw ``str(float)`` here rendered
    ``353667.92334052623`` into the input's ``value`` attribute.
    """
    from datetime import datetime as _datetime

    out: dict[str, dict[str, Any]] = {}
    for field_key, field_col, label in (
        ("pl", "primary_loss", "Primary loss"),
        ("sl", "secondary_loss", "Secondary loss"),
    ):
        dist = getattr(scenario, field_col)
        if not isinstance(dist, dict):
            out[field_key] = {"kind": None, "label": label, "pinned": False}
            continue
        kind = str(dist.get("distribution", "")).lower()
        meta = dist.get("distribution_fit_metadata")
        stamp = (meta or {}).get("sigma_recalibration") if isinstance(meta, dict) else None
        pinned = isinstance(stamp, dict) and stamp.get("source") == "analyst_pin"
        entry: dict[str, Any] = {
            "kind": kind,
            "label": label,
            "pinned": pinned,
            "pinned_at": None,
            "pinned_sigma": None,
            # T3.a NTH N-1/N-2: threaded so the pinned-chip copy can read
            # "(platform default X.XX)" beside the pinned sigma -- set
            # unconditionally (cheap, and used only when ``pinned``).
            "sigma_default": WITHIN_SCENARIO_SIGMA_DEFAULT,
            "readout_cfg": None,
            "prefill_p50": "",
            "prefill_p95": "",
        }
        if kind == "lognormal":
            mu, sigma = dist.get("mean"), dist.get("sigma")
            if (
                isinstance(mu, int | float)
                and isinstance(sigma, int | float)
                and sigma > 0
                and math.isfinite(float(mu))
                and math.isfinite(float(sigma))
            ):
                p50, p95 = lognormal_quantiles(float(mu), float(sigma), (0.5, 0.95))
                entry["prefill_p50"] = _format_money_input(p50)
                entry["prefill_p95"] = _format_money_input(p95)
                # T3.a gate fix (METH B-1): the FIELD's own stored ``max``
                # wins over the org-wide capacity_max fallback -- mirrors
                # loss_pinning.pin_loss's own
                # ``existing_max if existing_max is not None else minted``
                # ternary exactly. Passing the bare org cap here (pre-fix)
                # advertised a looser ceiling than the D19 chokepoint
                # actually enforces against THIS field's stored max.
                field_max = dist.get("max")
                field_cap = float(field_max) if isinstance(field_max, int | float) else capacity_max
                entry["readout_cfg"] = {
                    "mode": "lognormal",
                    "quantileBasis": "p50p95",
                    "sigmaDefault": WITHIN_SCENARIO_SIGMA_DEFAULT,
                    "warnThreshold": SIGMA_WARN_THRESHOLD,
                    "cap": field_cap,
                    "currency": "USD",
                    "tefMean": None,
                    "vulnMean": None,
                    "fieldKey": f"{field_key}_pin",
                    "label": label,
                    "initialLow": p50,
                    "initialHigh": p95,
                }
            if pinned:
                pinned_at_raw = stamp.get("pinned_at") if isinstance(stamp, dict) else None
                if isinstance(pinned_at_raw, str):
                    try:
                        entry["pinned_at"] = _datetime.fromisoformat(pinned_at_raw)
                    except ValueError:
                        entry["pinned_at"] = None
                entry["pinned_sigma"] = float(sigma) if isinstance(sigma, int | float) else None
        out[field_key] = entry
    return out


def _loss_was_recalibrated(scenario: Scenario) -> bool:
    """True when either loss node carries the sigma-recalibration migration's
    stamp (Task 3, plan ``2026-07-25-sigma-recal-pr1.md``, revision
    ``c4e4d441087c``).

    Defensive on every layer: ``primary_loss`` / ``secondary_loss`` may be
    ``None`` (no secondary loss configured; some prod rows also stored the
    literal JSON text ``"null"``, which parses to Python ``None`` same as
    SQL NULL); ``distribution_fit_metadata`` may be absent entirely (a PERT
    node, or a legacy row with no sidecar) or present but not a dict;
    ``sigma_recalibration`` may likewise be absent or non-dict. Only the
    migration's own stamp (``source == "migration_recalibration"``) trips
    the banner -- ``analyst_pin`` and any other source must NOT.
    """
    for dist in (scenario.primary_loss, scenario.secondary_loss):
        if not isinstance(dist, dict):
            continue
        meta = dist.get("distribution_fit_metadata")
        if not isinstance(meta, dict):
            continue
        stamp = meta.get("sigma_recalibration")
        if not isinstance(stamp, dict):
            continue
        if stamp.get("source") == "migration_recalibration":
            return True
    return False


# Canonical 95th-percentile z-score (``scipy.stats.norm.ppf(0.95)``), NOT
# ``_quantile_pair``'s truncated ``1.645`` (wizard_helpers.py:116-118) --
# the truncated constant is fine for that module's own render-only prefill
# math, but reusing it here would drift the finalize-advisory / view-time
# sigma reads a few ulp away from the migration's own ``_recalibrate_dist``
# and Task 1's pinned ``WITHIN_SCENARIO_SIGMA_DEFAULT`` bound argument.
_Z_0_95 = 1.6448536269514722


def _stored_loss_sigma(dist: Any) -> float | None:
    """Sigma implied by one stored PL/SL distribution dict, or ``None`` when
    the shape carries no dispersion reading.

    Mirrors ``run_executor._dict_to_fair_distribution``'s key-read contract
    (``distribution`` optional, defaults ``"pert"``, case-insensitive) and
    the migration's ``_recalibrate_dist`` shape handling:

      - ``lognormal`` -> the distribution's own ``sigma``.
      - ``lognormal_mixture`` -> the TRUE mixture implied sigma (PR3 T4,
        B-M3b/D21 amendment) -- ``ln(Q(0.95) / Q(0.5)) / Z_0_95`` where
        ``Q`` is fair_cam's deterministic ``mixture_quantile_lognorm`` on
        the pooled CDF. Components are reconstructed as
        ``min_support=0.0, max_support=inf`` (the SAME untruncated-support
        convention ``app.lognormal_mixture_display_rows`` already uses --
        catastrophic pl/sl mixture components are stored as native,
        untruncated ``{mean, sigma}`` pairs; the shared top-level ``max``
        capacity cap, when present, is a separate storage-only field, not
        applied per-component here). This REPLACES the pre-T4
        max-component read: that was a within-component LOWER bound -- a
        divergent 2-component mixture with both components at sigma=1.7 but
        medians 200x apart implied sigma~=2.94 in reality yet read exactly
        1.70 under max-component, silently defeating the stale-copy
        tripwire. A single-component mixture is numerically unchanged
        (``mixture_quantile_lognorm`` delegates a 1-component mix straight
        to the same closed-form quantile the old branch effectively used;
        float association leaves ~1e-16 relative drift, not a behavior
        change). DISCLOSED: this also changes the PR1-shipped view-time
        sigma advisory displays that already called this function.

        Silent-None note (T4.a gate fix, METH N-3): malformed or
        non-positive-weight components are SKIPPED during reconstruction
        (the ``isinstance``/``> 0`` guards below); if that leaves ZERO
        usable components, this branch returns ``None`` -- the SAME
        silent-degrade convention the rest of this function already uses
        for absent/malformed shapes. A mixture whose real components are
        merely malformed (not narrow) therefore reads as "no sigma
        reading" here rather than as wide -- callers (``_loss_stale_wide``
        included) cannot distinguish "narrow" from "unreadable" from this
        return value alone.
      - ``PERT`` -> implied sigma ``ln(high/low) / (2 * Z_0_95)`` (D4'
        provenance: capped ranges are mechanically
        ``exp(mu -+ Z_0_95 * sigma)`` of the underlying lognormal fit).

    Every access is isinstance-guarded: prod dicts can be ``None`` from
    literal JSON text ``"null"`` (same F1/migration precedent), or carry
    missing/malformed keys on a legacy row.
    """
    if not isinstance(dist, dict):
        return None
    kind = str(dist.get("distribution", "pert")).lower()
    if kind == "lognormal":
        sigma = dist.get("sigma")
        return float(sigma) if isinstance(sigma, int | float) else None
    if kind == "lognormal_mixture":
        comps = dist.get("components")
        if not isinstance(comps, list) or not comps:
            return None
        fits: list[LogNormalTruncFit] = []
        weights: list[float] = []
        for c in comps:
            if not isinstance(c, dict):
                continue
            mean_, sigma_, weight_ = c.get("mean"), c.get("sigma"), c.get("weight")
            if not (
                isinstance(mean_, int | float)
                and isinstance(sigma_, int | float)
                and sigma_ > 0
                and isinstance(weight_, int | float)
                and weight_ > 0
            ):
                continue
            fits.append(
                LogNormalTruncFit(
                    meanlog=float(mean_),
                    sdlog=float(sigma_),
                    min_support=0.0,
                    max_support=math.inf,
                )
            )
            weights.append(float(weight_))
        if not fits:
            return None
        try:
            mix = LognormMixture(components=tuple(fits), weights=tuple(weights))
            q50 = mixture_quantile_lognorm(mix, 0.5)
            q95 = mixture_quantile_lognorm(mix, 0.95)
        except (ValueError, ArithmeticError, QuantilePoolingError):
            return None
        if not (q50 > 0 and q95 > 0):
            return None
        return math.log(q95 / q50) / _Z_0_95
    if kind == "pert":
        low, high = dist.get("low"), dist.get("high")
        if not (isinstance(low, int | float) and isinstance(high, int | float)):
            return None
        if not (low > 0 < high):
            return None
        return math.log(high / low) / (2 * _Z_0_95)
    return None


_SIGMA_TOL = 1e-5  # prod stores 1.7 +/- ~1.5e-7 via dollar round-trips


def _max_tripwire_sigma(scenario: Scenario) -> float | None:
    """Max FIRING-BASIS implied sigma across PL/SL (T4.b, confirmation-gate
    I-2): every threshold consumer comparing against
    ``WITHIN_SCENARIO_SIGMA_DEFAULT`` -- the tripwire, the ``?loss_wide=1``
    flash, the finalize redirect -- must use the same basis: max COMPONENT
    sigma for a mixture (re-scoped D21; the pooled read includes
    between-expert divergence and is display-only), the plain read
    otherwise. There is deliberately NO display-max sibling (the dead
    ``_max_stored_loss_sigma`` was removed at the PR-gate, M-3): display
    surfaces read per-field via ``_loss_sigma_display``, and any new
    THRESHOLD consumer must use this function, never a display read.
    """
    sigmas = [
        s
        for dist in (scenario.primary_loss, scenario.secondary_loss)
        if isinstance(dist, dict)
        for s in (_tripwire_component_sigma(dist),)
        if s is not None
    ]
    return max(sigmas) if sigmas else None


def _field_has_provenance(dist: Any) -> bool:
    """True when THIS field carries ``analyst_pin`` or
    ``migration_recalibration`` provenance -- the single-field counterpart
    of ``_loss_was_recalibrated`` (which ORs across both fields for the
    separate recalibration banner above). Same defensive walk: ``dist`` may
    be non-dict (``None``, or the literal JSON text ``"null"`` parsed to
    Python ``None``), ``distribution_fit_metadata`` may be absent/non-dict,
    ``sigma_recalibration`` may likewise be absent/non-dict.
    """
    if not isinstance(dist, dict):
        return False
    meta = dist.get("distribution_fit_metadata")
    if not isinstance(meta, dict):
        return False
    stamp = meta.get("sigma_recalibration")
    if not isinstance(stamp, dict):
        return False
    return stamp.get("source") in ("analyst_pin", "migration_recalibration")


def _tripwire_component_sigma(dist: dict[str, Any]) -> float | None:
    """Sigma used for the TRIPWIRE FIRING DECISION (T4.a gate fix, METH B-1
    -- re-scoped D21, owner 2026-07-30). For ``lognormal``/``PERT`` this is
    numerically identical to ``_stored_loss_sigma``'s own read. For
    ``lognormal_mixture`` it is the MAX COMPONENT sigma, deliberately NOT
    the pooled implied sigma ``_stored_loss_sigma`` returns for display:
    calibration staleness lives in COMPONENTS (the PR1 sweep narrowed each
    component to the within-scenario default; it never touched pooled
    spread), and comparing the POOLED read against the per-component
    constant fires on correctly-calibrated divergent mixtures once their
    medians differ by as little as ~1.2%, is NON-MONOTONIC in component
    sigma (a T4-gate executed minimum of 1.92 -- never clearable by
    narrowing components further), and D21 gives mixtures no pin
    acknowledgment path at all. A wide-COMPONENT mixture (a real stale
    copy -- e.g. one component left at a pre-sweep sigma=2.9) still fires:
    the original blind spot (max-component read silently defeating the
    tripwire on a truly divergent-but-narrow-component mixture) stays
    closed where it was real.

    Returns ``None`` under the same malformed/empty-components conditions
    ``_stored_loss_sigma``'s own docstring documents (silent-None note,
    METH N-3).
    """
    kind = str(dist.get("distribution", "pert")).lower()
    if kind != "lognormal_mixture":
        return _stored_loss_sigma(dist)
    comps = dist.get("components")
    if not isinstance(comps, list) or not comps:
        return None
    sigmas = [
        float(c["sigma"])
        for c in comps
        if isinstance(c, dict) and isinstance(c.get("sigma"), int | float) and c["sigma"] > 0
    ]
    return max(sigmas) if sigmas else None


def _loss_sigma_display(dist: Any) -> dict[str, Any] | None:
    """Basis-labeled sigma reading for ONE stored PL/SL dict -- the shared
    payload both the stale-wide banner (per firing field) and the refresh
    confirm page (current-vs-entry comparison) render through the SAME
    honest-basis template branch (T4.a gate fix, METH I-4).

    ``sigma`` is always the POOLED/display read (``_stored_loss_sigma`` --
    for a mixture this INCLUDES between-expert divergence, unlike the
    tripwire's own component-threshold decision above).
    ``max_component_sigma`` is populated only for ``lognormal_mixture``
    (the per-component ceiling the tripwire itself fires on, surfaced so
    the honest mixture label can say "components <= Y.YY", per B-1);
    ``None`` for every other kind. T4.b (confirmation-gate I-3): PERT gets
    its OWN basis label in the macro ("PERT range basis: ln(high/low)/2z")
    -- a HAND-AUTHORED PERT has no parent lognormal, and labeling it
    "parent-lognormal basis" reversed the T3 adjudication that banned
    exactly that framing for typed PERT triples.

    Returns ``None`` when the field carries no sigma reading at all
    (``_stored_loss_sigma`` returns ``None``).
    """
    sigma = _stored_loss_sigma(dist)
    if sigma is None:
        return None
    kind = str(dist.get("distribution", "pert")).lower() if isinstance(dist, dict) else "pert"
    max_component_sigma = _tripwire_component_sigma(dist) if kind == "lognormal_mixture" else None
    return {"kind": kind, "sigma": sigma, "max_component_sigma": max_component_sigma}


def _loss_stale_wide(scenario: Scenario) -> dict[str, Any] | None:
    """Basis-labeled reading for the WIDEST stored loss field that
    INDIVIDUALLY lacks provenance and trips the tripwire, or ``None`` when
    nothing fires.

    PER-FIELD suppression (plan-gate SC-1/B-M3a): a pin or migration stamp
    on one field must never mute a wide, unstamped sibling -- scenario-level
    suppression would silence exactly the mixed case prod already has
    (PL-only migration stamps beside literal-``"null"`` SLs that may later
    be hand-widened). Library linkage is NOT checked here (D23): the banner
    fires on wild imports and hand-authored wide sigma too -- linkage only
    gates whether the Refresh affordance renders (view.html), a template-
    level check against ``scenario.library_pin``.

    FIRING decision (T4.a gate fix, METH B-1) uses
    ``_tripwire_component_sigma`` -- max-component for a mixture, the plain
    read otherwise -- while the returned ``sigma`` stays the POOLED/display
    read (``_loss_sigma_display``), so the banner shows the honest,
    between-expert-inclusive number even when a narrower per-component
    reading is what triggered it.

    Return shape: ``{"field_label": "Primary loss"|"Secondary loss",
    "kind": ..., "sigma": ..., "max_component_sigma": ... | None}``, or
    ``None`` when nothing fires.
    """
    widest: dict[str, Any] | None = None
    for dist, label in (
        (scenario.primary_loss, "Primary loss"),
        (scenario.secondary_loss, "Secondary loss"),
    ):
        if not isinstance(dist, dict) or _field_has_provenance(dist):
            continue
        trip_sigma = _tripwire_component_sigma(dist)
        if trip_sigma is None or trip_sigma <= WITHIN_SCENARIO_SIGMA_DEFAULT + _SIGMA_TOL:
            continue
        display = _loss_sigma_display(dist)
        if display is None:
            continue
        # T4.b (confirmation-gate N-2): the WINNER is picked by the FIRING
        # basis, not the display read — a mixture's pooled 4.29 must not
        # outrank a plain lognormal's 3.5 when its firing component is 2.9.
        if widest is None or trip_sigma > widest["_firing_sigma"]:
            widest = {**display, "field_label": label, "_firing_sigma": trip_sigma}
    return widest


def _cap_remint_disclosure(old_dist: Any, new_dist: Any) -> tuple[float, float] | None:
    """``(old_max, new_max)`` when a library refresh's freshly-minted
    capacity cap DIFFERS from the field's PRIOR stored ``max`` -- ``None``
    when either side carries no usable numeric ``max`` (nothing to
    disclose) or the two agree within float noise.

    T4.a gate fix (NTH, meth N-2): ``_resolve_refresh`` unconditionally
    overwrites a lognormal/lognormal_mixture field's ``max`` with the
    org's current ``capacity_max_for_org`` mint (D14 — entries carry no
    ``max`` of their own). When the field's PRIOR cap was narrower than
    that mint (a bespoke, previously-authored cap, or simply an org whose
    revenue grew since the field was last capped), refresh silently
    LOOSENS it -- the executed example loosened an existing cap ~200x with
    no confirm-page disclosure before this fix.
    """
    if not isinstance(old_dist, dict) or not isinstance(new_dist, dict):
        return None
    old_max, new_max = old_dist.get("max"), new_dist.get("max")
    if not (isinstance(old_max, int | float) and isinstance(new_max, int | float)):
        return None
    if math.isclose(float(old_max), float(new_max), rel_tol=1e-9):
        return None
    return float(old_max), float(new_max)


def _parse_pin_quantile(raw: str, *, field_name: str) -> float:
    """Parse a ``pin_p50``/``pin_p95`` form string to float.

    Parse-to-float happens HERE (the route), not in the service (Sec-I2 /
    loss_pinning.pin_loss's own boundary-gate comment) — a non-parseable
    string must 422, and the service's own finite/positive/ordering gate
    only runs on already-parsed floats. Raises ``ValueError`` (never
    ``HTTPException``) — T3.a gate fix (SPEC B-1): the caller renders a
    proper 422 form via ``_render_loss_action_failure`` rather than letting
    a bare HTTPException JSON body reach the analyst.
    """
    try:
        return float(sanitize_money_str(raw))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name}: not a number (got {raw!r})") from exc
