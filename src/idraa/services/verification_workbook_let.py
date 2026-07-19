"""LET formula emitter — array-form FAIR Monte Carlo for the verification workbook.

PURE string-building module (NO openpyxl). Builds the self-contained per-scenario
``LET`` dynamic-array formula that generates its own uniform draws internally
(``RANDARRAY(N,1)`` per FAIR input), runs the base + residual loss chain over the
*shared* draws (common random numbers), and returns the summary stats as a small
spilled array.

It OWNS the inverse-CDF + residual param-scaling math:

  - ``_invcdf`` — inverse-CDF Excel expression for ONE FAIR node over a uniform
    array variable. Ports the SAME already-reviewed math from
    ``verification_workbook.py`` (Vose moment-matched PERT -> Beta; lognormal
    log-space; uniform; triangular; beta), re-expressed over an array var.
  - ``scaled_params`` — MOVED VERBATIM from ``verification_workbook.py`` (it is
    pure, has no openpyxl). Co-locating it keeps all the math the methodology
    reviewer must verify in one pure module. Task 7 removes the old copy.

Excel function encoding — the VALIDATED SPILL RECIPE (gate-passed
``vwb_gate_v8.xlsx``):

  - **BARE function names** (``BETA.INV``/``NORM.INV``/``PERCENTILE.INC``/
    ``RANDARRAY``/``LET``/``CHOOSE``/``SUMPRODUCT``/``EXP``/``SQRT``/``IF``/
    ``AVERAGE``/``MAX``). NO hand-written ``_xlfn.``/``_xlfn._xlws.`` — xlsxwriter
    (configured with ``use_future_functions=True``) adds those itself, and ANY
    ``_xlfn.`` in the string short-circuits its auto-prefixing.
  - **``_xlpm.``-prefixed LET parameter names** at definition AND every use.
    Excel's own LET encoding is ``LET(_xlpm.x, 1, _xlpm.x*2)``; without ``_xlpm.``
    Mac Excel refuses to open the file (THE root-cause bug of the superseded form).
  - **Element-wise boolean clips, NEVER ``MAX(0,arr)``/``MIN(1,arr)``** — those
    AGGREGATE an array to a scalar (collapsing the Monte Carlo). non-negative clip
    ``max(0,x)`` -> ``(x>0)*x``; clip-to-[0,1] -> ``((x>0)*(x<1)*x+(x>=1))``.
  - **Tail mean (ES) via SUMPRODUCT, NOT AVERAGEIF** (AVERAGEIF needs a real cell
    range; it rejects an in-memory LET array).
  - **9-stat array via ``CHOOSE({1;2;3;4;5;6;7;8;9}, ...)``** (semicolons ->
    vertical column spill), NOT VSTACK.

Design: docs/plans/2026-06-15-verification-workbook-spill-redesign.md
Plan:   docs/plans/2026-06-15-verification-workbook-spill-redesign-plan.md
"""

from __future__ import annotations

import math
from typing import Any

_FMT = 10  # round emitted constants: kills float-noise (3.999999999999999 -> 4.0)

# LET parameter-name prefix. Excel's own LET encoding prefixes parameter names
# with "_xlpm." at both definition and every use; without it Mac Excel refuses to
# open the file (THE root-cause bug of the superseded encoding).
_XLPM = "_xlpm."


def _is_mixture(dist_dict: dict[str, Any]) -> bool:
    """True when a node's distribution kind is the native ``lognormal_mixture``
    shape (issue #27 Task 8) — catastrophic multi-SME pl/sl only; see
    ``wizard_finalize.build_scenario_payload``. A missing "distribution" key
    defaults to "pert" (mirrors ``_invcdf``/``scaled_params``), so a key-less
    PERT node is never mistaken for a mixture."""
    return str(dist_dict.get("distribution", "pert")).lower() == "lognormal_mixture"


def _invcdf(dist_dict: dict[str, Any], uniform_var: str, u_sel: str | None = None) -> str:
    """Inverse-CDF Excel expression for one FAIR node over a uniform array variable.

    ``uniform_var`` is the name of a uniform-on-[0,1] array variable in the LET
    (e.g. ``u_tef``). The returned expression maps that uniform array elementwise
    to a sample from the node's distribution — the array-form analogue of the
    per-cell ``sample_formula_for`` emitters in verification_workbook.py.

    Dispatch on ``dist_dict.get("distribution", "pert")`` (mirrors
    ``_dict_to_fair_distribution`` / ``sample_formula_for``): the wizard stores
    PERT nodes WITHOUT a "distribution" key, so a missing key DEFAULTS to "pert".

    Math ported verbatim (algebra unchanged; only RAND() cell -> array var).
    Function names are BARE — xlsxwriter adds the ``_xlfn.`` prefix:

      - PERT (verification_workbook.py:167-184): Vose BetaPERT (gamma=4)
        moment-match. mean=(low+4*mode+high)/6; stdev=(high-low)/6;
        alpha=g1*(g2-1); beta=alpha*(high-mean)/(mean-low). Degenerate
        ``high <= low`` -> the constant ``low``.
      - lognormal (verification_workbook.py:187-193): EXP(NORM.INV(u, mean, sigma))
        in LOG-space params.
      - uniform (verification_workbook.py:196-198): low + u*(high-low).
      - triangular (verification_workbook.py:201-214): split at Fc=(mode-low)/(high-low).
      - beta (verification_workbook.py:217-219): BETA.INV(u, alpha, beta), vuln-only.
      - lognormal_mixture (issue #27 Task 8, catastrophic multi-SME pl/sl only):
        nested cumulative-weight IFs on an INDEPENDENT ``u_sel`` array selecting
        which component's ``EXP(NORM.INV(u, mean_i, sigma_i))`` applies. ``u_sel``
        MUST be independent of ``uniform_var`` — a mixture must never reuse one
        uniform for both component selection and inversion (that is a comonotonic
        coupling, not a linear-opinion-pool draw; Task 8 binding decision rule).
        Component i owns the half-open interval ``[cum_{i-1}, cum_i)`` of
        ``u_sel``; the LAST component is the unconditional "else" so weight-sum
        drift (weights sum to 1 +/- 1e-9, Task 4) never leaves a sliver of
        ``u_sel``-space unmapped. Raises ValueError if ``u_sel`` is omitted.

    Raises ValueError on an unsupported distribution kind so the assembly later
    emits a labeled "unsupported in v1 — app value" cell rather than a wrong
    formula (I-SC-1).
    """
    kind = str(dist_dict.get("distribution", "pert")).lower()
    if kind in ("pert", "triangular"):
        low = dist_dict["low"]
        mode = dist_dict["mode"]
        high = dist_dict["high"]
        if high <= low:
            return f"{round(low, _FMT)}"  # degenerate -> constant (no BETA.INV / div-by-zero)
        if kind == "pert":
            mean = (low + 4 * mode + high) / 6
            stdev = (high - low) / 6
            g1 = (mean - low) / (high - low)
            g2 = (mean - low) * (high - mean) / (stdev**2)
            alpha = round(g1 * (g2 - 1), _FMT)  # symmetric -> 4.0, NOT 3.999999999999999
            beta = round(alpha * (high - mean) / (mean - low), _FMT)
            lo = round(low, _FMT)
            hi = round(high, _FMT)
            return f"{lo} + BETA.INV({uniform_var}, {alpha}, {beta}) * ({hi} - {lo})"
        # triangular
        lo = round(low, _FMT)
        mo = round(mode, _FMT)
        hi = round(high, _FMT)
        below = f"{lo} + SQRT({uniform_var}*({hi} - {lo})*({mo} - {lo}))"
        above = f"{hi} - SQRT((1 - {uniform_var})*({hi} - {lo})*({hi} - {mo}))"
        return f"IF({uniform_var} < ({mo} - {lo})/({hi} - {lo}), {below}, {above})"
    if kind == "uniform":
        lo = round(dist_dict["low"], _FMT)
        hi = round(dist_dict["high"], _FMT)
        return f"{lo} + {uniform_var}*({hi} - {lo})"
    if kind == "lognormal":
        # mult==0 is intercepted upstream in scaled_params (collapses to a constant-0
        # UNIFORM node, never reaching this branch); do NOT re-add an unguarded
        # LN(mult) here — log-space scaling lives in scaled_params, not here.
        mean = round(dist_dict["mean"], _FMT)
        sigma = round(dist_dict["sigma"], _FMT)
        return f"EXP(NORM.INV({uniform_var}, {mean}, {sigma}))"
    if kind == "beta":
        alpha = round(dist_dict["alpha"], _FMT)
        beta = round(dist_dict["beta"], _FMT)
        return f"BETA.INV({uniform_var}, {alpha}, {beta})"
    if kind == "lognormal_mixture":
        if u_sel is None:
            raise ValueError(
                "_invcdf: lognormal_mixture requires an independent component-"
                "selection uniform (u_sel) — a mixture must not reuse the "
                "inversion uniform to also pick the component (comonotonic "
                "coupling is not a linear-opinion-pool draw); see Task 8 "
                "binding decision rule"
            )
        components = dist_dict.get("components") or []
        if not components:
            raise ValueError("_invcdf: lognormal_mixture requires >=1 component")

        def _component_expr(c: dict[str, Any]) -> str:
            mean = round(c["mean"], _FMT)
            sigma = round(c["sigma"], _FMT)
            return f"EXP(NORM.INV({uniform_var}, {mean}, {sigma}))"

        # Nested cumulative-weight IFs: component i owns u_sel in
        # [cum_{i-1}, cum_i). The LAST component is the unconditional "else"
        # branch (built first, then wrapped from the last conditional outward
        # to the first) so a cumsum that lands at 1 - epsilon (Task 4's
        # |sum(weights)-1| <= 1e-9 tolerance) never leaves a sliver of
        # u_sel-space unmapped to a #NUM!/blank cell.
        expr = _component_expr(components[-1])
        running = 0.0
        cum_thresholds: list[float] = []
        for c in components[:-1]:
            running += c["weight"]
            cum_thresholds.append(running)
        for c, cum in zip(reversed(components[:-1]), reversed(cum_thresholds), strict=True):
            expr = f"IF({u_sel} < {round(cum, _FMT)}, {_component_expr(c)}, {expr})"
        return expr
    raise ValueError(
        f"_invcdf: unsupported distribution {kind!r} "
        f"(no native-Excel inverse-CDF; caller should emit app-value cell)"
    )


def scaled_params(dist_dict: dict[str, Any], mult: float) -> dict[str, Any]:
    """PARAMETER-level scaling of a residual tef/pl/sl distribution dict, mirroring
    ``FAIRParameters.scaled`` -> ``_node`` -> ``_scale_distribution``
    (fair_core.py:271-345).

    - ``mult == 0`` collapses the node to the degenerate constant-0 UNIFORM
      ``{low:0, high:0}`` (fair_core.py:274-275, ``_node`` perfect-control path).
    - ``mult < 0`` or non-finite -> ValueError (fair_core.py:272-273).
    - PERT/triangular: scale ``low``/``mode``/``high`` (fair_core.py:303-308).
    - uniform: scale ``low``/``high`` (fair_core.py:309-313).
    - lognormal: LOG-space ``mean += ln(mult)`` (mult<1 LOWERS mean), ``sigma``
      unchanged (fair_core.py:319-325).
    - lognormal_mixture (issue #27 Task 8, catastrophic multi-SME pl/sl only):
      the SAME log-space shift applied to EVERY component's ``mean``; each
      component's ``sigma``/``weight`` unchanged (mirrors the plain-lognormal
      shift, extended elementwise over the mixture).

    BETA is rejected (vuln-only, sample-level; never param-scaled — fair_core.py:326).
    Returns a NEW dict (does not mutate input); scaled constants rounded to _FMT dp.
    """
    if not math.isfinite(mult) or mult < 0:
        raise ValueError(f"node multiplier must be finite and >= 0; got {mult!r}")
    if mult == 0:
        # _node fair_core.py:274-275 — degenerate collapse to constant 0.
        return {"distribution": "uniform", "low": 0.0, "high": 0.0}

    # Same engine-mirroring default as sample_formula_for: the residual path
    # routes key-less PERT nodes through here, so a missing "distribution" key
    # must default to "pert" — not hard-subscript (which the widened except would
    # silently swallow into a spurious reconstructible=False / blank residual).
    kind = str(dist_dict.get("distribution", "pert")).lower()
    if kind in ("pert", "triangular"):
        return {
            "distribution": kind,
            "low": round(dist_dict["low"] * mult, _FMT),
            "mode": round(dist_dict["mode"] * mult, _FMT),
            "high": round(dist_dict["high"] * mult, _FMT),
        }
    if kind == "uniform":
        return {
            "distribution": "uniform",
            "low": round(dist_dict["low"] * mult, _FMT),
            "high": round(dist_dict["high"] * mult, _FMT),
        }
    if kind == "lognormal":
        # fair_core.py:319-325 — log-space additive shift; sigma unchanged.
        return {
            "distribution": "lognormal",
            "mean": round(dist_dict["mean"] + math.log(mult), _FMT),
            "sigma": dist_dict["sigma"],
        }
    if kind == "lognormal_mixture":
        # fair_core.py:319-325 mirror, applied per component (Task 8): the SAME
        # log-space additive shift on EVERY component's mean; sigma/weight
        # unchanged. mult==0 never reaches here — the generic collapse above
        # already returned the degenerate constant-0 UNIFORM.
        components = dist_dict["components"]
        return {
            "distribution": "lognormal_mixture",
            "components": [
                {
                    "mean": round(c["mean"] + math.log(mult), _FMT),
                    "sigma": c["sigma"],
                    "weight": c["weight"],
                }
                for c in components
            ],
        }
    if kind == "beta":
        raise ValueError(
            "scaled_params: BETA is unscaled [0,1] (vulnerability-only, sample-level "
            "clip); never parameter-scaled (mirrors _scale_distribution fair_core.py:326)"
        )
    raise ValueError(f"scaled_params: unsupported distribution {kind!r}")


# Engine canonical multiplier keys (from compose_groups via composed_node_multipliers).
_K_TEF = "threat_event_frequency"
_K_VULN = "vulnerability"
_K_PL = "primary_loss"
_K_SL = "secondary_loss"
_K_SUBTRACTOR = "currency_subtractor_total"


# Non-parameter sidecar keys the wizard attaches to a distribution dict that the
# sampling layer (_invcdf / _dict_to_fair_distribution / sample_formula_for) reads
# right past — they carry no formula-bound value, so the numeric-param guard must
# skip them (otherwise the dominant real shape {low,mode,high,
# distribution_fit_metadata} would fail-loud on a perfectly valid scenario).
_NON_PARAM_DIST_KEYS = frozenset({"distribution", "distribution_fit_metadata"})

# The lognormal_mixture container key (issue #27 Task 8): its VALUE is a list of
# per-component param dicts, not itself a scalar param — RECURSED into below, never
# just exempted (exempting the container outright would blind the Sec-N1
# formula-injection guard to a string/bool smuggled INSIDE a component).
_MIXTURE_COMPONENTS_KEY = "components"


def _assert_numeric_dist(dist_dict: dict[str, Any], node: str) -> None:
    """Defense-in-depth: every distribution PARAM must be numeric (int|float).

    A string param must NEVER reach a formula (Sec-N1) — it would silently emit a
    cell reference or a text literal rather than a number. Non-parameter keys are
    exempt: the "distribution" discriminator (a str) and the wizard's
    "distribution_fit_metadata" sidecar (a dict the sampling layer ignores — see
    _invcdf, which dispatches on low/mode/high and never reads the sidecar; mirrors
    sample_formula_for / _dict_to_fair_distribution).

    ``lognormal_mixture``'s "components" key (issue #27 Task 8) is a LIST of
    per-SME param dicts (mean/sigma/weight) — this RECURSES into each component
    and numeric-checks every value there too, rather than merely exempting the
    "components" key wholesale (which would silently admit a non-numeric value
    smuggled inside a component, defeating the guard for exactly the nested
    params a mixture adds).
    """
    for key, val in dist_dict.items():
        if key in _NON_PARAM_DIST_KEYS:
            continue
        if key == _MIXTURE_COMPONENTS_KEY:
            if not isinstance(val, list):
                raise TypeError(
                    f"scenario_let_formula: {node} {key!r} must be a list of "
                    f"components, got {type(val).__name__} {val!r}"
                )
            for i, comp in enumerate(val):
                if not isinstance(comp, dict):
                    raise TypeError(
                        f"scenario_let_formula: {node} components[{i}] must be a "
                        f"dict, got {type(comp).__name__} {comp!r}"
                    )
                for ckey, cval in comp.items():
                    if not isinstance(cval, (int, float)) or isinstance(cval, bool):
                        raise TypeError(
                            f"scenario_let_formula: {node} components[{i}] param "
                            f"{ckey!r} must be numeric (int|float), got "
                            f"{type(cval).__name__} {cval!r}"
                        )
            continue
        if not isinstance(val, (int, float)) or isinstance(val, bool):
            raise TypeError(
                f"scenario_let_formula: {node} param {key!r} must be numeric (int|float), "
                f"got {type(val).__name__} {val!r}"
            )


def scenario_let_formula(scen: dict[str, Any], mults: dict[str, Any], n: int) -> str:
    """Build the self-contained per-scenario LET dynamic-array formula.

    ``scen`` carries the four node distribution dicts (keyed by the engine canonical
    node names ``threat_event_frequency``/``vulnerability``/``primary_loss``/
    ``secondary_loss``). ``mults`` carries the composed multipliers (same canonical
    keys + ``currency_subtractor_total``). ``n`` is the in-Excel sample count.

    Emits four independent ``RANDARRAY(n,1)`` uniform columns and runs the SAME
    base + residual loss chain already reviewed in verification_workbook.py, over
    the SHARED draws (common random numbers): the residual reuses the SAME u_*
    vars as the base, with tef/pl/sl param-scaled via ``scaled_params`` then
    ``_invcdf``, vuln scaled SAMPLE-level (``[0,1]``-clip of ``_xlpm.vuln*mult``,
    NOT param-scaled — matching the engine asymmetry), and SL double-floored with
    the currency subtractor. Returns a ``CHOOSE({1;2;3;...;9}, ...)`` vertical
    spill of base ALE, residual ALE, control value, VaR95/99/999, ES95/99/999.

    lognormal_mixture nodes (issue #27 Task 8, catastrophic multi-SME pl/sl
    only) get one EXTRA independent ``RANDARRAY(n,1)`` uniform column per
    mixture node (``u_pl_sel`` and/or ``u_sl_sel``, emitted ONLY when that
    node's dist is a mixture) — the component-selection uniform ``_invcdf``
    needs to pick which component's inverse-CDF applies without comonotonically
    coupling to the inversion draw. Base and residual reuse the SAME selection
    column (CRN parity), exactly like they reuse ``u_pl``/``u_sl``.

    VALIDATED SPILL RECIPE (gate-passed ``vwb_gate_v8.xlsx`` — see module
    docstring): BARE function names (xlsxwriter prefixes; ZERO hand-written
    ``_xlfn.``/``_xlws.``); EVERY LET-bound name ``_xlpm.``-prefixed at definition
    AND every use (THE root-cause encoding — Mac Excel refuses un-prefixed LET
    params); element-wise BOOLEAN clips (``(x>0)*x`` / ``((x>0)*(x<1)*x+(x>=1))``,
    NEVER ``MAX(0,arr)``/``MIN(1,arr)`` which aggregate the array to a scalar and
    collapse the Monte Carlo); SUMPRODUCT tail-mean (NOT AVERAGEIF — range-only
    over a LET array); CHOOSE with SEMICOLONS for the 9-stat vertical array
    (NOT VSTACK).

    Fail-loud: ``_invcdf`` raises ValueError on an unsupported distribution kind
    and this function propagates it so the ASSEMBLY writes the labeled "unsupported
    in v1 — app value" cell (I-SC-1). All distribution params and multipliers are
    asserted numeric at the top (Sec-N1).
    """
    # --- Null secondary loss: engine parity (workbook-500 fix) -----------------
    # A scenario with NO secondary loss stores secondary_loss = None (25 of the
    # 93 library entries are null-SL; the wizard omits the node when no SL rows
    # exist). The ENGINE samples it as the degenerate constant-0 UNIFORM
    # (run_executor._ZERO_SECONDARY_LOSS -> fair_core UNIFORM {0,0}); mirror
    # that exactly — the SAME shape scaled_params emits for a perfect-control
    # collapse, so _invcdf/scaled_params handle it natively and the LET's SL
    # column is constant 0 (base and residual: nn(0-subtractor) == 0).
    if scen.get(_K_SL) is None:
        scen = {**scen, _K_SL: {"distribution": "uniform", "low": 0.0, "high": 0.0}}

    # --- Defense-in-depth: numeric params + numeric mults (Sec-N1) -------------
    for node in (_K_TEF, _K_VULN, _K_PL, _K_SL):
        _assert_numeric_dist(scen[node], node)
    for mkey in (_K_TEF, _K_VULN, _K_PL, _K_SL, _K_SUBTRACTOR):
        mval = mults[mkey]
        if not isinstance(mval, (int, float)) or isinstance(mval, bool):
            raise TypeError(
                f"scenario_let_formula: multiplier {mkey!r} must be numeric (int|float), "
                f"got {type(mval).__name__} {mval!r}"
            )

    # The sample-level mults (vuln, currency subtractor) are applied directly at
    # the sample level (clip01(vuln*mult); nn(nn(sl_raw)-subtractor)) and so never
    # pass through scaled_params, which is where the tef/pl/sl PARAM-level mults get
    # their finite-and-non-negative check (fair_core.py:272-273). Without this guard
    # a nan/inf/negative vuln or subtractor would emit silently into a #NUM!/NaN
    # residual column. Mirror the engine, which raises on exactly these two at
    # fair_core.py:412-424 (non-finite or negative secondary_loss_subtractor /
    # vulnerability_multiplier) — keeps the module's engine-parity fail-loud claim.
    # Runs AFTER the isinstance guard above so a non-numeric value raises TypeError
    # before math.isfinite is ever called on it.
    for mkey in (_K_VULN, _K_SUBTRACTOR):
        mval = mults[mkey]
        if not math.isfinite(mval) or mval < 0:
            raise ValueError(
                f"scenario_let_formula: {mkey!r} must be finite and >= 0; got {mval!r}"
            )

    tef_dist = scen[_K_TEF]
    vuln_dist = scen[_K_VULN]
    pl_dist = scen[_K_PL]
    sl_dist = scen[_K_SL]

    # Component-selection uniform is bound ONLY for a node whose dist is the
    # native lognormal_mixture shape (Task 8) — tef/vuln never carry this shape
    # in production (wizard_finalize always PERT-collapses them), so no column
    # budget is spent on them; a hand-authored/imported mixture there would
    # still fail loud via _invcdf's u_sel-required ValueError instead of
    # silently mis-sampling.
    pl_is_mixture = _is_mixture(pl_dist)
    sl_is_mixture = _is_mixture(sl_dist)

    vuln_mult = round(mults[_K_VULN], _FMT)
    subtractor = round(mults[_K_SUBTRACTOR], _FMT)

    # --- LET parameter names — EVERY name _xlpm.-prefixed (root-cause encoding;
    # see _XLPM) ---------------------------------------------------------------
    u_tef = _XLPM + "u_tef"
    u_vuln = _XLPM + "u_vuln"
    u_pl = _XLPM + "u_pl"
    u_pl_sel = _XLPM + "u_pl_sel" if pl_is_mixture else None
    u_sl = _XLPM + "u_sl"
    u_sl_sel = _XLPM + "u_sl_sel" if sl_is_mixture else None
    v_tef = _XLPM + "tef"
    v_vuln = _XLPM + "vuln"
    v_pl = _XLPM + "pl"
    v_sl = _XLPM + "sl"
    v_base_loss = _XLPM + "base_loss"
    v_tef_r = _XLPM + "tef_r"
    v_sl_raw = _XLPM + "sl_raw"
    v_pl_r = _XLPM + "pl_r"
    v_vuln_r = _XLPM + "vuln_r"
    v_sl_r = _XLPM + "sl_r"
    v_res_loss = _XLPM + "res_loss"

    # --- Element-wise boolean clips (NEVER MAX(0,arr)/MIN(1,arr) — those -------
    # aggregate the array to a scalar and collapse the Monte Carlo):
    #   non-negative clip  max(0,x)   -> (x>0)*x
    #   clip-to-[0,1]      clip(x,0,1) -> ((x>0)*(x<1)*x+(x>=1))
    def nn(x: str) -> str:
        return f"({x}>0)*{x}"

    def clip01(x: str) -> str:
        return f"(({x}>0)*({x}<1)*{x}+({x}>=1))"

    # --- Base samples (unscaled params) over the shared uniform columns --------
    base_tef = _invcdf(tef_dist, u_tef)
    base_vuln = _invcdf(vuln_dist, u_vuln)
    base_pl = _invcdf(pl_dist, u_pl, u_sel=u_pl_sel)
    base_sl = _invcdf(sl_dist, u_sl, u_sel=u_sl_sel)

    # --- Residual samples: CRN (same u_* AND same u_*_sel), tef/pl/sl param- ---
    # scaled. scaled_params preserves the "lognormal_mixture" shape (Task 8), so
    # the residual _invcdf call dispatches the SAME mixture branch, reusing the
    # SAME selection column as the base draw above (CRN parity for mixtures).
    res_tef = _invcdf(scaled_params(tef_dist, mults[_K_TEF]), u_tef)
    res_pl = _invcdf(scaled_params(pl_dist, mults[_K_PL]), u_pl, u_sel=u_pl_sel)
    res_sl_raw = _invcdf(scaled_params(sl_dist, mults[_K_SL]), u_sl, u_sel=u_sl_sel)
    # vuln residual is SAMPLE-level ([0,1]-clip of vuln*mult), NOT param-scaled
    # (N-SC-3 / B-METH-1) — scales the SAME base vuln draw, then clips.
    res_vuln = clip01(f"({v_vuln}*{vuln_mult})")
    # SL residual double-floored with the currency subtractor (pre-sum): the
    # raw scaled SL is non-neg-clipped, the subtractor removed, then re-clipped.
    res_sl = nn(f"({nn(v_sl_raw)}-{subtractor})")

    # --- Loss chains -----------------------------------------------------------
    base_loss = f"{nn(v_tef)}*{clip01(v_vuln)}*({nn(v_pl)}+{nn(v_sl)})"
    res_loss = f"{nn(v_tef_r)}*{v_vuln_r}*({nn(v_pl_r)}+{v_sl_r})"

    # --- ES helper: SUMPRODUCT tail-mean (NOT AVERAGEIF — range-only over a ----
    # LET array), empty-tail -> MAX fallback (mirror es_formula IFERROR->MAX).
    def _es(q: float) -> str:
        var_q = f"PERCENTILE.INC({v_res_loss}, {round(q, _FMT)})"
        return (
            f"IFERROR(SUMPRODUCT(({v_res_loss}>={var_q})*{v_res_loss})"
            f"/SUMPRODUCT(--({v_res_loss}>={var_q})), MAX({v_res_loss}))"
        )

    var95 = f"PERCENTILE.INC({v_res_loss}, 0.95)"
    var99 = f"PERCENTILE.INC({v_res_loss}, 0.99)"
    var999 = f"PERCENTILE.INC({v_res_loss}, 0.999)"

    # --- 9-stat vertical array: CHOOSE with SEMICOLONS (column spill), NOT -----
    # VSTACK. Order: base ALE, residual ALE, control value (base-res), VaRs, ES.
    stats = (
        "CHOOSE({1;2;3;4;5;6;7;8;9}, "
        f"AVERAGE({v_base_loss}), AVERAGE({v_res_loss}), "
        f"AVERAGE({v_base_loss})-AVERAGE({v_res_loss}), "
        f"{var95}, {var99}, {var999}, "
        f"{_es(0.95)}, {_es(0.99)}, {_es(0.999)})"
    )

    # One EXTRA independent RANDARRAY(n,1) column per mixture node (up to two:
    # u_pl_sel, u_sl_sel), bound immediately after that node's own uniform
    # column — empty string (no column, no budget spent) when the node is not
    # a mixture, so the emitted string is BYTE-IDENTICAL to pre-Task-8 output
    # for every non-mixture scenario (the dominant case).
    u_pl_sel_binding = f"{u_pl_sel}, RANDARRAY({n},1), " if u_pl_sel is not None else ""
    u_sl_sel_binding = f"{u_sl_sel}, RANDARRAY({n},1), " if u_sl_sel is not None else ""

    return (
        "=LET("
        f"{u_tef}, RANDARRAY({n},1), "
        f"{u_vuln}, RANDARRAY({n},1), "
        f"{u_pl}, RANDARRAY({n},1), "
        f"{u_pl_sel_binding}"
        f"{u_sl}, RANDARRAY({n},1), "
        f"{u_sl_sel_binding}"
        f"{v_tef}, {base_tef}, "
        f"{v_vuln}, {base_vuln}, "
        f"{v_pl}, {base_pl}, "
        f"{v_sl}, {base_sl}, "
        f"{v_base_loss}, {base_loss}, "
        f"{v_tef_r}, {res_tef}, "
        f"{v_sl_raw}, {res_sl_raw}, "
        f"{v_pl_r}, {res_pl}, "
        f"{v_vuln_r}, {res_vuln}, "
        f"{v_sl_r}, {res_sl}, "
        f"{v_res_loss}, {res_loss}, "
        f"{stats})"
    )
