/* First-party chart hydration (epic #547 P1 + P2). Hydrates figures marked
 * data-chart-hydrate="lec" (full: hover crosshair + tooltip, log-scaled
 * p-slider, linear/log-y toggle — dual with/without-controls LEC card only),
 * data-chart-hydrate="epc" (hover-only, axis-appropriate tooltip — dual EPC
 * card), data-chart-hydrate="curve" (P2: hover-only tooltip, NO crosshair/
 * slider/toggle — the SINGLE-run LEC/EPC line charts; branches on the
 * embedded data.kind "lec" vs "epc" for axis math, reusing lecScales/
 * epcScales + pAtLoss/lossAtExceedance UNCHANGED by embedding the single
 * series as data.series.without with an empty data.series.with), or
 * data-chart-hydrate="bars" (P2: shared hover tooltip over static
 * server-rendered bars — control_effectiveness_bar / risk_comparison_bar;
 * no embedded data-chart-data needed since bars have no domain to
 * reconstruct — the tooltip text is read straight from each bar's own
 * <title>, the no-JS fallback). View geometry (viewBox + margins) for the
 * curve modes is read from the embedded data-chart-data "view" key — NEVER
 * hard-coded here — so the Python constants in services/chart_svg.py are the
 * single source and the Python<->JS drift class is impossible. Interpolation
 * is LINEAR IN LOSS / LINEAR IN PROBABILITY per segment, endpoint-clamped —
 * the exact convention of
 * services/dashboard_view_model._interpolate_exceedance_probability and its
 * inverse (interpolate_loss_at_probability), so client readouts always agree
 * with server-computed verdicts.
 *
 * Money formatting note: TWO deliberate conventions coexist.
 *   - fmtMoney (compact, "$2.0M"): mirrors chart_svg.py's `_fmt_money` —
 *     used for the crosshair tooltip, matching the axis-tick / tolerance-
 *     marker labels the server already renders inside the SVG.
 *   - fmtMoneyReadout (exact, "$2,000,000"): mirrors macros/kpi_card.html's
 *     `_format_value` money branch (`currency + '{:,.0f}'.format(value)`)
 *     bit-for-bit — used ONLY for the p-slider readout, because the readout's
 *     with-controls figure at init must string-equal Card 3's server-rendered
 *     `loss_at_tol_prob` (golden-value agreement, Arch-N1). Using the compact
 *     formatter there would compare two different textual representations of
 *     the same number and could never agree, silently defeating the very
 *     divergence check this hydration is supposed to provide.
 *
 * Static charts render fully without this file.
 */
"use strict";
(function () {
  const NS = "http://www.w3.org/2000/svg";

  function fmtMoney(v, sym) {
    if (v >= 1e9) return sym + (v / 1e9).toFixed(1).replace(/\.0$/, "") + "B";
    if (v >= 1e6) return sym + (v / 1e6).toFixed(1).replace(/\.0$/, "") + "M";
    if (v >= 1e3) return sym + (v / 1e3).toFixed(0) + "K";
    return sym + Math.round(v);
  }
  // Exact comma-grouped integer money — mirrors kpi_card.html's
  // `_format_value` money branch (`'{:,.0f}'.format(value)`) so the readout
  // and Card 3 render the identical string for the identical number.
  function fmtMoneyReadout(v, sym) {
    return sym + Math.round(v).toLocaleString("en-US");
  }
  function fmtP(p) {
    if (p >= 0.1) return (p * 100).toFixed(0) + "%";
    if (p >= 0.01) return (p * 100).toFixed(1) + "%";
    // Sub-1% tail intentionally deviates from server _fmt_pct (chart_svg.py,
    // which uses "{:.4g}" -> 4 significant figures): toPrecision(2) gives 2.
    // Cosmetic-only (tooltip readout, never compared against a stored value
    // or used in a verdict) — methodology N1, do not "fix" to match .4g.
    return (p * 100).toPrecision(2) + "%";
  }

  // linear-in-loss, endpoint-clamped — mirrors _interpolate_exceedance_probability
  function pAtLoss(pts, x) {
    if (!pts.length) return null;
    const s = pts.slice().sort((a, b) => a.loss - b.loss);
    if (x <= s[0].loss) return s[0].probability;
    if (x >= s[s.length - 1].loss) return s[s.length - 1].probability;
    for (let i = 0; i < s.length - 1; i++) {
      const a = s[i], b = s[i + 1];
      if (a.loss <= x && x <= b.loss) {
        if (b.loss === a.loss) return a.probability;
        const t = (x - a.loss) / (b.loss - a.loss);
        return a.probability + t * (b.probability - a.probability);
      }
    }
    return s[s.length - 1].probability;
  }
  // linear-in-probability inverse — mirrors interpolate_loss_at_probability
  function lossAtP(pts, p) {
    if (!pts.length) return null;
    const s = pts.slice().sort((a, b) => a.loss - b.loss);
    if (p >= s[0].probability) return s[0].loss;
    if (p <= s[s.length - 1].probability) return s[s.length - 1].loss;
    for (let i = 0; i < s.length - 1; i++) {
      const a = s[i], b = s[i + 1];
      if (a.probability >= p && p >= b.probability) {
        if (a.probability === b.probability) return a.loss;
        const t = (a.probability - p) / (a.probability - b.probability);
        return a.loss + t * (b.loss - a.loss);
      }
    }
    return s[s.length - 1].loss;
  }
  // Loss at an exceedance probability on a {percentile, loss} EPC curve
  // (linear in exceedance prob = 1 - percentile per segment, endpoint-clamped).
  function lossAtExceedance(pts, pExc) {
    if (!pts.length) return null;
    const s = pts.map(pt => ({ e: 1 - pt.percentile, loss: pt.loss })).sort((a, b) => a.e - b.e);
    if (pExc <= s[0].e) return s[0].loss;
    if (pExc >= s[s.length - 1].e) return s[s.length - 1].loss;
    for (let i = 0; i < s.length - 1; i++) {
      const a = s[i], b = s[i + 1];
      if (a.e <= pExc && pExc <= b.e) {
        if (b.e === a.e) return a.loss;
        const t = (pExc - a.e) / (b.e - a.e);
        return a.loss + t * (b.loss - a.loss);
      }
    }
    return s[s.length - 1].loss;
  }

  function svgEl(tag, attrs) {
    const e = document.createElementNS(NS, tag);
    for (const k in attrs) e.setAttribute(k, attrs[k]);
    return e;
  }

  // "Download data" CSV export — restores the affordance the retired chart
  // vendor's modebar gave these charts before the SVG port. Serializes the
  // embedded raw points (trace,x,y — same shape as the old chart_data_export.js), kind-aware:
  // LEC point {loss,probability} -> x=loss, y=exceedance-prob; EPC point
  // {percentile,loss} -> x=exceedance-prob (1-percentile), y=loss.
  function csvCell(v) {
    let s = String(v == null ? "" : v);
    if (/^[=+\-@\t\r]/.test(s)) s = "'" + s; // formula-injection escape (symmetric with utils/csv_export.py)
    if (/[",\r\n]/.test(s)) return '"' + s.replace(/"/g, '""') + '"';
    return s;
  }
  function wireCsv(fig, data) {
    const btn = fig.querySelector('[data-role="csv"]');
    if (!btn) return;
    const epc = fig.dataset.chartHydrate === "epc";
    btn.addEventListener("click", () => {
      const rows = ["trace,x,y"];
      for (const key of ["without", "with"]) {
        const name = key === "without" ? "Without controls" : "With controls";
        for (const pt of data.series[key] || []) {
          const x = epc ? 1 - pt.percentile : pt.loss;
          const y = epc ? pt.loss : pt.probability;
          rows.push([csvCell(name), csvCell(x), csvCell(y)].join(","));
        }
      }
      // Lead with a UTF-8 BOM for parity with chart_data_export.js so Excel
      // detects the encoding identically across every chart's CSV export.
      const blob = new Blob(["\ufeff" + rows.join("\r\n")], { type: "text/csv;charset=utf-8" });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = epc ? "exceedance-probability.csv" : "loss-exceedance.csv";
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(a.href);
    });
  }
  function makeTip(fig) {
    const tip = document.createElement("div");
    tip.className = "chart-tooltip";
    tip.style.display = "none";
    fig.style.position = "relative";
    fig.appendChild(tip);
    return tip;
  }
  // textContent lines ONLY — the currency symbol transits JSON and innerHTML is
  // the no-autoescape XSS sink (house precedent formatting.py:143).
  function setTipLines(tip, lines) {
    tip.replaceChildren(...lines.map(t => { const d = document.createElement("div"); d.textContent = t; return d; }));
  }
  function placeTip(tip, fig, ev) {
    const fr = fig.getBoundingClientRect();
    let tx = ev.clientX - fr.left + 14;
    if (tx + tip.offsetWidth > fr.width - 8) tx -= tip.offsetWidth + 28;
    tip.style.left = tx + "px";
    tip.style.top = (ev.clientY - fr.top - 10) + "px";
  }

  // LEC scales: x = log10(loss); y = exceedance probability (linear or log).
  // Domain reconstruction mirrors services/chart_svg.dual_curve EXACTLY
  // (folds tolerance.amount into [xMin, xMax], xMin>=1 clamp, xMax>=xMin*10
  // clamp) — any drift here pixel-offsets the crosshair/slider markers from
  // the server-drawn curve.
  function lecScales(svg, data, V, M) {
    const all = data.series.without.concat(data.series.with);
    let xMin = Infinity, xMax = -Infinity;
    for (const pt of all) { xMin = Math.min(xMin, pt.loss); xMax = Math.max(xMax, pt.loss); }
    if (data.tolerance && data.tolerance.amount != null) {
      xMin = Math.min(xMin, data.tolerance.amount); xMax = Math.max(xMax, data.tolerance.amount);
    }
    xMin = Math.max(xMin, 1); xMax = Math.max(xMax, xMin * 10);
    const lo = Math.log10(xMin), hi = Math.log10(xMax), pw = V.w - M.left - M.right;
    const logY = svg.dataset.yScale === "log", ph = V.h - M.top - M.bottom, YMIN = 1e-6;
    return {
      sx: v => M.left + (Math.log10(Math.max(v, 1e-9)) - lo) / (hi - lo) * pw,
      xAt: px => Math.pow(10, lo + (px - M.left) / pw * (hi - lo)),
      sy: p => {
        if (!logY) return M.top + (1 - p) * ph;
        const frac = (Math.log10(Math.max(p, YMIN)) - Math.log10(YMIN)) / (0 - Math.log10(YMIN));
        return M.top + (1 - frac) * ph;
      },
    };
  }
  // EPC scales: x = exceedance prob (linear [0,1]); y = loss (log). Mirrors
  // services/chart_svg.epc_curve's domain exactly (same tolerance-fold + the
  // yMin>=1 / yMax>=yMin*10 clamps as the LEC domain, on the loss axis).
  function epcScales(data, V, M) {
    const all = data.series.without.concat(data.series.with);
    let yMin = Infinity, yMax = -Infinity;
    for (const pt of all) { yMin = Math.min(yMin, pt.loss); yMax = Math.max(yMax, pt.loss); }
    if (data.tolerance && data.tolerance.amount != null) {
      yMin = Math.min(yMin, data.tolerance.amount); yMax = Math.max(yMax, data.tolerance.amount);
    }
    yMin = Math.max(yMin, 1); yMax = Math.max(yMax, yMin * 10);
    const lo = Math.log10(yMin), hi = Math.log10(yMax);
    const pw = V.w - M.left - M.right, ph = V.h - M.top - M.bottom;
    return {
      pAt: px => Math.min(Math.max((px - M.left) / pw, 0), 1),  // x px -> exceedance prob
      sy: loss => M.top + (1 - (Math.log10(Math.max(loss, 1e-9)) - lo) / (hi - lo)) * ph,
    };
  }

  function hydrateLec(fig, data, V, M) {
    const svgs = fig.querySelectorAll("svg[data-y-scale]");
    const controls = fig.querySelector("[data-chart-controls]");  // WITHIN the figure — no closest("section")
    const slider = controls && controls.querySelector('[data-role="p-slider"]');
    const readout = controls && controls.querySelector('[data-role="p-readout"]');
    // NOTE: `.hidden` is NOT a reflected IDL property on SVGElement in
    // current browsers (only HTMLElement gets that reflection) — reading or
    // writing `svgEl.hidden` silently no-ops on a plain, unreflected JS
    // property and never touches the actual `hidden` CONTENT ATTRIBUTE the
    // UA stylesheet's `[hidden] { display: none }` rule keys off. Every
    // visibility check/toggle below therefore goes through
    // hasAttribute/toggleAttribute("hidden", ...) directly, never `.hidden`.
    const visibleSvg = () => Array.from(svgs).find(s => !s.hasAttribute("hidden"));
    const sliderP = () => Math.pow(10, -4 + (+slider.value / 100) * 4);

    if (controls) {
      const lin = controls.querySelector('[data-role="y-linear"]');
      const log = controls.querySelector('[data-role="y-log"]');
      const setScale = scale => {
        svgs.forEach(s => { s.toggleAttribute("hidden", s.dataset.yScale !== scale); });
        lin.classList.toggle("tab-active", scale === "linear");
        log.classList.toggle("tab-active", scale === "log");
        if (slider) updateMarkers(sliderP());
      };
      lin.addEventListener("click", () => setScale("linear"));
      log.addEventListener("click", () => setScale("log"));
    }

    function updateMarkers(p) {
      const svg = visibleSvg(); if (!svg) return;
      const { sx, sy } = lecScales(svg, data, V, M);
      const g = svg.querySelector('[data-role="slider-marker"]');
      const lw = lossAtP(data.series.without, p), lc = lossAtP(data.series.with, p);
      g.replaceChildren();
      g.appendChild(svgEl("line", { x1: M.left, y1: sy(p), x2: V.w - M.right, y2: sy(p), stroke: "var(--color-ink-3)", "stroke-width": 1, "stroke-dasharray": "2 4", opacity: 0.7 }));
      if (lw) g.appendChild(svgEl("circle", { cx: sx(lw), cy: sy(p), r: 4, fill: "var(--chart-inherent)" }));
      if (lc) g.appendChild(svgEl("circle", { cx: sx(lc), cy: sy(p), r: 4, fill: "var(--chart-residual)" }));
    }
    function updateReadout(p) {
      if (!readout) return;
      const lw = lossAtP(data.series.without, p), lc = lossAtP(data.series.with, p);
      let txt = "at " + fmtP(p) + ": " + (lw ? fmtMoneyReadout(lw, data.currency) : "—") +
        " → " + (lc ? fmtMoneyReadout(lc, data.currency) : "—");
      if (lw && lc && lw > 0) txt += " (" + (100 * (1 - lc / lw)).toFixed(0) + "% lower)";
      readout.textContent = txt;
    }

    if (slider) {
      // P11: the INITIAL readout + markers use the EXACT tolerance probability
      // from the JSON (not the quantized slider step). After first input they
      // follow the slider value.
      const tolP = data.tolerance && data.tolerance.probability != null ? data.tolerance.probability : sliderP();
      updateReadout(tolP); updateMarkers(tolP);
      slider.addEventListener("input", () => { updateReadout(sliderP()); updateMarkers(sliderP()); });
    }

    const tip = makeTip(fig);
    const hide = () => { tip.style.display = "none"; svgs.forEach(s => { const g = s.querySelector('[data-role="hover-layer"]'); if (g) g.replaceChildren(); }); };
    svgs.forEach(svg => {
      svg.addEventListener("pointermove", ev => {
        const r = svg.getBoundingClientRect();
        const mx = (ev.clientX - r.left) * (V.w / r.width);
        if (mx < M.left || mx > V.w - M.right) { hide(); return; }
        const { sy, xAt } = lecScales(svg, data, V, M);
        const x = xAt(mx);
        const pw = pAtLoss(data.series.without, x), pc = pAtLoss(data.series.with, x);
        const g = svg.querySelector('[data-role="hover-layer"]');
        g.replaceChildren();
        g.appendChild(svgEl("line", { x1: mx, y1: M.top, x2: mx, y2: V.h - M.bottom, stroke: "var(--color-ink-3)", "stroke-width": 1, "stroke-dasharray": "3 3" }));
        if (pw != null) g.appendChild(svgEl("circle", { cx: mx, cy: sy(pw), r: 4, fill: "var(--chart-inherent)" }));
        if (pc != null) g.appendChild(svgEl("circle", { cx: mx, cy: sy(pc), r: 4, fill: "var(--chart-residual)" }));
        tip.style.display = "block";
        setTipLines(tip, [
          "Loss ≥ " + fmtMoney(x, data.currency),
          "Without controls: " + (pw != null ? fmtP(pw) : "—"),
          "With controls: " + (pc != null ? fmtP(pc) : "—"),
        ]);
        placeTip(tip, fig, ev);
      });
      svg.addEventListener("pointerleave", hide);
    });
  }

  function hydrateEpc(fig, data, V, M) {
    const svg = fig.querySelector("svg"); if (!svg) return;
    const { pAt, sy } = epcScales(data, V, M);
    const tip = makeTip(fig);
    const hide = () => { tip.style.display = "none"; const g = svg.querySelector('[data-role="hover-layer"]'); if (g) g.replaceChildren(); };
    svg.addEventListener("pointermove", ev => {
      const r = svg.getBoundingClientRect();
      const mx = (ev.clientX - r.left) * (V.w / r.width);
      if (mx < M.left || mx > V.w - M.right) { hide(); return; }
      const pExc = pAt(mx);
      const lw = lossAtExceedance(data.series.without, pExc), lc = lossAtExceedance(data.series.with, pExc);
      const g = svg.querySelector('[data-role="hover-layer"]');
      g.replaceChildren();
      g.appendChild(svgEl("line", { x1: mx, y1: M.top, x2: mx, y2: V.h - M.bottom, stroke: "var(--color-ink-3)", "stroke-width": 1, "stroke-dasharray": "3 3" }));
      if (lw != null) g.appendChild(svgEl("circle", { cx: mx, cy: sy(lw), r: 4, fill: "var(--chart-inherent)" }));
      if (lc != null) g.appendChild(svgEl("circle", { cx: mx, cy: sy(lc), r: 4, fill: "var(--chart-residual)" }));
      tip.style.display = "block";
      setTipLines(tip, [
        "P ≥ " + fmtP(pExc),
        "Without controls: " + (lw != null ? fmtMoney(lw, data.currency) : "—"),
        "With controls: " + (lc != null ? fmtMoney(lc, data.currency) : "—"),
      ]);
      placeTip(tip, fig, ev);
    });
    svg.addEventListener("pointerleave", hide);
  }

  // P2: hover-only tooltip for SINGLE-run LEC/EPC line charts (NO crosshair,
  // NO slider, NO log toggle — those stay exclusive to the dual LEC card).
  // Branches on data.kind ("lec" vs "epc") for axis math, reusing the exact
  // same lecScales/epcScales + pAtLoss/lossAtExceedance the dual cards use —
  // the single series travels as data.series.without (data.series.with is
  // always []), so there is exactly one "value at cursor X" code path, not a
  // parallel one for single-run charts.
  function hydrateCurve(fig, data, V, M) {
    const svg = fig.querySelector("svg"); if (!svg) return;
    const isEpc = data.kind === "epc";
    const tip = makeTip(fig);
    const hide = () => { tip.style.display = "none"; };
    if (isEpc) {
      const { pAt } = epcScales(data, V, M);
      svg.addEventListener("pointermove", ev => {
        const r = svg.getBoundingClientRect();
        const mx = (ev.clientX - r.left) * (V.w / r.width);
        if (mx < M.left || mx > V.w - M.right) { hide(); return; }
        const pExc = pAt(mx);
        const loss = lossAtExceedance(data.series.without, pExc);
        tip.style.display = "block";
        setTipLines(tip, ["P ≥ " + fmtP(pExc), loss != null ? fmtMoney(loss, data.currency) : "—"]);
        placeTip(tip, fig, ev);
      });
    } else {
      const { xAt } = lecScales(svg, data, V, M);
      svg.addEventListener("pointermove", ev => {
        const r = svg.getBoundingClientRect();
        const mx = (ev.clientX - r.left) * (V.w / r.width);
        if (mx < M.left || mx > V.w - M.right) { hide(); return; }
        const x = xAt(mx);
        const p = pAtLoss(data.series.without, x);
        tip.style.display = "block";
        setTipLines(tip, ["Loss ≥ " + fmtMoney(x, data.currency), p != null ? fmtP(p) : "—"]);
        placeTip(tip, fig, ev);
      });
    }
    svg.addEventListener("pointerleave", hide);
  }

  // P2: shared hover tooltip for static server-rendered bars
  // (control_effectiveness_bar, risk_comparison_bar). Bars sit at exact
  // final pixel positions with no domain to reconstruct or interpolate — so,
  // unlike the curve modes, this reads tooltip text straight off each bar's
  // own <title> (the no-JS fallback) instead of a second embedded-JSON data
  // channel that would just duplicate it.
  function hydrateBars(fig) {
    const tip = makeTip(fig);
    const hide = () => { tip.style.display = "none"; };
    fig.querySelectorAll('[data-role="bar"]').forEach(rect => {
      const titleEl = rect.querySelector("title");
      const lines = titleEl ? titleEl.textContent.split("\n") : [];
      // Remove the native <title> once hydrated so the browser's own
      // tooltip doesn't show ALONGSIDE the styled .chart-tooltip (double
      // tooltip) — it stays in the served markup as the no-JS fallback for
      // browsers/agents that never run this script.
      if (titleEl) titleEl.remove();
      rect.addEventListener("pointermove", ev => {
        tip.style.display = "block";
        setTipLines(tip, lines);
        placeTip(tip, fig, ev);
      });
      rect.addEventListener("pointerleave", hide);
    });
  }

  function hydrate(fig) {
    if (fig.dataset.chartHydrated) return;
    fig.dataset.chartHydrated = "1";
    try {
      if (fig.dataset.chartHydrate === "bars") { hydrateBars(fig); return; }
      const dataEl = fig.querySelector("script[data-chart-data]");
      if (!dataEl) return;
      const data = JSON.parse(dataEl.textContent);
      wireCsv(fig, data);  // "Download data" affordance (dual lec/epc cards only — no-ops elsewhere)
      const V = data.view, M = data.view.margin;  // view geometry from JSON, never hard-coded
      if (fig.dataset.chartHydrate === "epc") hydrateEpc(fig, data, V, M);
      else if (fig.dataset.chartHydrate === "curve") hydrateCurve(fig, data, V, M);
      else hydrateLec(fig, data, V, M);
    } catch (err) {
      // Never let one bad figure break the rest of the page.
      console.error("chart hydration failed for", fig, err);
    }
  }

  function hydrateAll(root) {
    (root || document).querySelectorAll("[data-chart-hydrate]").forEach(hydrate);
  }
  document.addEventListener("DOMContentLoaded", () => hydrateAll());
  document.body.addEventListener("htmx:afterSwap", ev => hydrateAll(ev.target));
})();
