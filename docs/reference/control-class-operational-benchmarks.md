---
title: "Control-class operational benchmarks"
date: 2026-05-16
issue: 131
purpose: "Forward-reference for analysts during wizard / library curation."
not_imported_by: "fair_cam math (informs library entries + wizard hints only)"
---

# Control-class operational benchmarks

## Purpose

This document lists typical day-counts per `(control_class × sub_function)` pair with primary-source citations, drawn from Phase 0 research of issue #131. It is a **forward-reference** for analysts during wizard input and for library-entry curation (issue #131 Task T4). The document is NOT imported by fair_cam math — the canonical τ table at `fair_cam/calibration/elapsed_time_taus.py` is the source of truth for calibration; this doc informs HOW analysts populate per-control day-count inputs given a control's class and intended sub-function.

## Scope

Columns are limited to the **3 KEPT** canonical-τ sub-functions post-#131:

- `LEC_DET_MONITORING` (canonical τ=194d — exponential mean-anchor per IBM CODB 2024 p10 Fig 4 MTTI mean)
- `LEC_RESP_EVENT_TERMINATION` (canonical τ=64d — exponential mean-anchor per IBM CODB 2024 p10 Fig 4 MTTC mean)
- `VMC_CORR_IMPLEMENTATION` (canonical τ=79.3d — median half-life per DBIR 2024 p21 Fig 19 CISA KEV survival-curve median)

The 6 sub-functions dropped from canonical τ under #131 (`LEC_RESP_RESILIENCE`, `VMC_ID_THREAT_INTELLIGENCE`, `VMC_ID_CONTROL_MONITORING`, `VMC_CORR_TREATMENT_SELECTION`, `DSC_ID_MISALIGNED`, `DSC_CORR_MISALIGNED`) reclassified to `UnitType.PROBABILITY` and appear in this doc ONLY as a reference list at the bottom. They do NOT take day-count inputs anymore.

## Sources

Cell values trace to one of the Phase 0 primary sources (full extractions at `docs/reference/calibration-sources/`):

- IBM Cost of Data Breach 2024 (`ibm_codb_2024.md`)
- Verizon DBIR 2024 (`dbir_2024.md`)
- SANS 2024 D&R Survey + SANS 2023 IR Survey (`sans_ir_2024.md`)
- VERIS / VCDB (`veris_dataset.md`)
- FAIR Institute IRIS 2024 (`iris_2024.md`)

## Cross-tab summary (post-Phase 0, 2026-05-16)

| Class | LEC_DET_MON (τ=194d) | LEC_RESP_TERM (τ=64d) | VMC_CORR_IMPL (τ=79.3d) |
|-------|----------------------|------------------------|--------------------------|
| NS    | — (no class-specific data in Phase 0 sources; canonical τ default applies) | — (canonical default) | — (canonical default) |
| IAM   | — (no class-specific data in Phase 0 sources; canonical τ default applies) | — (canonical default) | — (canonical default) |
| EP    | — (no class-specific data in Phase 0 sources; canonical τ default applies) | — (canonical default) | — (canonical default) |
| EM    | — (no class-specific data in Phase 0 sources; canonical τ default applies) | — (canonical default) | — (canonical default) |
| CL    | — (no class-specific data in Phase 0 sources; canonical τ default applies; IBM CODB 2024 p30 Fig 39 publishes a *storage-location* lifecycle proxy¹ — recorded as wizard hint, not a primary-cited cell) | — (canonical default; proxy¹ same caveat) | — (canonical default) |
| DL    | — (no class-specific data in Phase 0 sources; canonical τ default applies) | — (canonical default) | — (canonical default) |
| SM    | — (no class-specific data in Phase 0 sources; canonical τ default applies) | — (canonical default) | — (canonical default) |
| BR    | — (no class-specific data in Phase 0 sources; canonical τ default applies) | — (canonical default) | — (canonical default) |
| VM    | — (no class-specific data in Phase 0 sources; canonical τ default applies) | — (canonical default) | — (canonical default; DBIR 2024 p21 narrative cites a 15d *target* for critical vulns → overlay candidate, not a primary-cited cell value) |
| TR    | — (training-class controls do not manifest on LEC time-axis; refer to canonical default or rely on overlay multipliers) | — (training-class — same caveat) | — (training-class — same caveat) |
| PS    | — (no class-specific data in Phase 0 sources; canonical τ default applies) | — (canonical default) | — (canonical default) |
| NW    | — (no class-specific data in Phase 0 sources; canonical τ default applies) | — (canonical default) | — (canonical default) |
| EN    | — (no class-specific data in Phase 0 sources; canonical τ default applies) | — (canonical default) | — (canonical default) |
| IR    | — (no class-specific data in Phase 0 sources; canonical τ default applies) | — (canonical default) | — (canonical default) |
| TI    | — (no class-specific data in Phase 0 sources; canonical τ default applies) | — (canonical default) | — (canonical default) |

¹ IBM CODB 2024 p30 Fig 39 publishes storage-location *total breach lifecycle* (multi-env 283d, public-cloud 268d, private-cloud 247d, on-prem 224d). These are NOT LEC-sub-function-segmented — recorded for downstream wizard hints and overlay-multiplier consideration, but NOT usable as primary-cited LEC cell values per the strict-cite gate.

### Strict-drop rule

If a cell cannot be primary-cited, the cell renders "—" with the note `"no class-specific data in Phase 0 sources; canonical τ default applies."` Do NOT invent values or borrow heuristics. This mirrors the canonical τ table's strict primary-cite gate.

### IND (industrial sector) — included for completeness

`IND` is technically a sector classification, not a control-class prefix in the v3 library taxonomy. It is included as a per-class section below for completeness because it is the only Phase-0-citable row that supplies primary-cited values for two of the three KEPT sub-functions. It informs sector overlays (per CLAUDE.md "Layered override beats canonical CRUD") rather than per-class library entries.

## NS (Network Security — Next-Generation Firewall class)

| Sub-function | Value (days) | Statistic anchor | Source |
|---|---|---|---|
| LEC_DET_MONITORING | — | n/a | No NS-class MTTI breakout in Phase 0 sources; canonical τ=194d default applies |
| LEC_RESP_EVENT_TERMINATION | — | n/a | No NS-class MTTC breakout in Phase 0 sources; canonical τ=64d default applies |
| VMC_CORR_IMPLEMENTATION | — | n/a | No NS-class patch-cycle median in Phase 0 sources; canonical τ=79.3d default applies |

Rationale: Phase 0 sources publish breach-lifecycle data segmented by **industry sector** (IBM CODB p10) and **attack vector** (IBM CODB p14 Fig 8), but not by control class. NS-class refinement remains a deferred research target.

## IAM (Identity and Access Management — Multi-Factor Authentication class)

| Sub-function | Value (days) | Statistic anchor | Source |
|---|---|---|---|
| LEC_DET_MONITORING | — | n/a | No IAM-class MTTI breakout in Phase 0 sources; canonical τ=194d default applies |
| LEC_RESP_EVENT_TERMINATION | — | n/a | No IAM-class MTTC breakout in Phase 0 sources; canonical τ=64d default applies |
| VMC_CORR_IMPLEMENTATION | — | n/a | No IAM-class patch-cycle median in Phase 0 sources; canonical τ=79.3d default applies |

Rationale: IBM CODB 2024 p14 Fig 8 publishes per-attack-vector lifecycles ("Stolen or compromised credentials" 292d total — MTTI 229d / MTTC 63d), which is an attack-vector hint adjacent to IAM but NOT a control-class segmented value. Recorded as overlay candidate, not a primary-cited cell.

## EP (Endpoint Protection — EDR/XDR class)

| Sub-function | Value (days) | Statistic anchor | Source |
|---|---|---|---|
| LEC_DET_MONITORING | — | n/a | No EP-class MTTI breakout in IBM CODB 2024, DBIR 2024, SANS 2024 D&R / 2023 IR Survey, VCDB-public-aggregates, or IRIS-public-summary; canonical τ=194d default applies |
| LEC_RESP_EVENT_TERMINATION | — | n/a | No EP-class MTTC breakout in Phase 0 sources; canonical τ=64d default applies |
| VMC_CORR_IMPLEMENTATION | — | n/a | No EP-class patch-cycle breakout; DBIR 2024 CISA KEV survival curve is environment-wide, not EP-class; canonical τ=79.3d default applies |

Rationale: Phase 0 sources publish breach-lifecycle data segmented by **industry sector** (IBM CODB p10) and **attack vector** (IBM CODB p14 Fig 8: stolen-credentials 292d, malicious-insider 287d, phishing 261d, social-engineering 257d, zero-day 252d) — but not by **control class**. EP-class refinement remains a deferred research target (re-investigate when IRIS full report becomes accessible or Mandiant M-Trends publishes EP-segmented telemetry).

## EM (Email Security — Email Gateway class)

| Sub-function | Value (days) | Statistic anchor | Source |
|---|---|---|---|
| LEC_DET_MONITORING | — | n/a | No EM-class MTTI breakout in Phase 0 sources; canonical τ=194d default applies |
| LEC_RESP_EVENT_TERMINATION | — | n/a | No EM-class MTTC breakout in Phase 0 sources; canonical τ=64d default applies |
| VMC_CORR_IMPLEMENTATION | — | n/a | No EM-class patch-cycle median in Phase 0 sources; canonical τ=79.3d default applies |

Rationale: IBM CODB 2024 p14 Fig 8 publishes phishing-vector lifecycle (261d total — MTTI 195d / MTTC 66d) and social-engineering-vector lifecycle (257d total — MTTI 197d / MTTC 60d). These are attack-vector hints adjacent to EM controls but NOT a control-class segmented value. DBIR 2024 p9/p40 Fig 39 phishing time-to-fall (<60s) is sub-second granularity below the LEC day-axis, so not extractable as a cell value. Recorded as overlay candidates, not primary-cited cells.

## CL (Cloud Security — CSPM class)

| Sub-function | Value (days) | Statistic anchor | Source |
|---|---|---|---|
| LEC_DET_MONITORING | — | n/a | No CL-class MTTI breakout in Phase 0 sources; IBM CODB 2024 p30 Fig 39 publishes a storage-location lifecycle proxy (multi-env 283d, public-cloud 268d, private-cloud 247d, on-prem 224d) — NOT LEC-sub-function-segmented; canonical τ=194d default applies |
| LEC_RESP_EVENT_TERMINATION | — | n/a | Same Fig 39 proxy caveat; canonical τ=64d default applies |
| VMC_CORR_IMPLEMENTATION | — | n/a | No CL-class patch-cycle median in Phase 0 sources; canonical τ=79.3d default applies |

Rationale: The IBM CODB 2024 Fig 39 storage-location breakouts are *total breach lifecycle*, not sub-function-segmented MTTI/MTTC, and the storage-location ≠ control-class. Best routed to a future `multi_environment_overlay` (per CLAUDE.md "Layered override beats canonical CRUD") rather than a CL-class primary-cited cell. SANS 2024 D&R Survey notes "limited cloud-security expertise" (56%) and "multi-cloud management complexity" (51%) — qualitative anchors, not τ inputs.

## DL (Data Loss Prevention — DLP class)

| Sub-function | Value (days) | Statistic anchor | Source |
|---|---|---|---|
| LEC_DET_MONITORING | — | n/a | No DL-class MTTI breakout in Phase 0 sources; canonical τ=194d default applies |
| LEC_RESP_EVENT_TERMINATION | — | n/a | No DL-class MTTC breakout in Phase 0 sources; canonical τ=64d default applies |
| VMC_CORR_IMPLEMENTATION | — | n/a | No DL-class patch-cycle median in Phase 0 sources; canonical τ=79.3d default applies |

Rationale: VERIS schema (`vz-risk/veris@cbe9bd7`) defines `timeline.exfiltration` as a potentially-relevant DL-adjacent field, but VCDB population of that field is sparse (Farhang & Grossklags 2017 reported only ~150 incidents with usable discovery-time values out of ~10,000), so no VCDB-aggregate DL median is defensible without re-running the aggregation against the current snapshot. Deferred.

## SM (Security Monitoring — SIEM/SOAR class)

| Sub-function | Value (days) | Statistic anchor | Source |
|---|---|---|---|
| LEC_DET_MONITORING | — | n/a | No SM-class MTTI breakout in Phase 0 sources; canonical τ=194d default applies |
| LEC_RESP_EVENT_TERMINATION | — | n/a | No SM-class MTTC breakout in Phase 0 sources; canonical τ=64d default applies |
| VMC_CORR_IMPLEMENTATION | — | n/a | No SM-class patch-cycle median in Phase 0 sources; canonical τ=79.3d default applies |

Rationale: IBM CODB 2024 p16 Fig 13 publishes lifecycle by discovery channel: security-teams-and-tools detected breaches at MTTI 178d / MTTC 50d, attacker-disclosure at MTTI 212d / MTTC 77d, benign-third-party at MTTI 179d / MTTC 61d. These are *discovery-channel* metrics, not SM-class control-segmented values, so they inform a future `internal_detection_overlay` rather than a primary-cited SM cell.

## BR (Backup and Recovery class)

| Sub-function | Value (days) | Statistic anchor | Source |
|---|---|---|---|
| LEC_DET_MONITORING | — | n/a | BR-class controls do not manifest on the LEC detection-time axis; canonical τ=194d default applies |
| LEC_RESP_EVENT_TERMINATION | — | n/a | BR-class controls do not manifest on the LEC event-termination axis; canonical τ=64d default applies |
| VMC_CORR_IMPLEMENTATION | — | n/a | No BR-class patch-cycle median in Phase 0 sources; canonical τ=79.3d default applies |

Rationale: BR-class controls primarily drive `LEC_RESP_RESILIENCE` (recovery), which reclassified to `UnitType.PROBABILITY` under #131 — see the reclassified-sub-functions reference list at the bottom of this doc. IBM CODB 2024 p22 Fig 24 publishes a right-censored time-to-recovery distribution (only 12% of breached orgs fully recovered at survey time; modal bucket 126–150 days) but that is conditional on full recovery and not class-segmented, so it does not support a BR-class cell value here.

## VM (Vulnerability Management — VM platform class)

| Sub-function | Value (days) | Statistic anchor | Source |
|---|---|---|---|
| LEC_DET_MONITORING | — | n/a | No VM-class MTTI breakout in Phase 0 sources; canonical τ=194d default applies |
| LEC_RESP_EVENT_TERMINATION | — | n/a | No VM-class MTTC breakout in Phase 0 sources; canonical τ=64d default applies |
| VMC_CORR_IMPLEMENTATION | — | n/a | DBIR 2024 p21 Fig 19 CISA KEV remediation median (55d) is environment-wide, not VM-class; canonical τ=79.3d default applies. DBIR 2024 p21 narrative cites a 15d *target* for critical vulns → overlay candidate (`critical_patch_sla_overlay`), not a primary-cited cell value |

Rationale: DBIR 2024 is the canonical source for `VMC_CORR_IMPLEMENTATION` τ — but the published survival curve aggregates all surveyed orgs and does not separate behavior by VM-platform class. The 15-day critical-vuln target is normative, not observed, so it is not a primary-cited cell value; it slots into a future overlay layer per CLAUDE.md.

## TR (Training — Security Awareness class)

| Sub-function | Value (days) | Statistic anchor | Source |
|---|---|---|---|
| LEC_DET_MONITORING | — | n/a | Training-class controls do not directly manifest on the LEC detection-time axis; canonical τ=194d default applies |
| LEC_RESP_EVENT_TERMINATION | — | n/a | Training-class controls do not directly manifest on the LEC event-termination axis; canonical τ=64d default applies |
| VMC_CORR_IMPLEMENTATION | — | n/a | Training-class controls do not implement patches; canonical τ=79.3d default applies |

Rationale: Training-class controls modulate the probability of an event (phishing-resistance, secure-coding awareness) rather than the elapsed time of a sub-function. They are better modeled via PROBABILITY-typed sub-functions or via overlays on adjacent LEC sub-functions. DBIR 2024 phishing-simulation report rate (~20% of simulation recipients reported the lure) is a probability hint, not a day-count.

## PS (Physical Security — Physical Access Control class)

| Sub-function | Value (days) | Statistic anchor | Source |
|---|---|---|---|
| LEC_DET_MONITORING | — | n/a | No PS-class MTTI breakout in Phase 0 sources; canonical τ=194d default applies |
| LEC_RESP_EVENT_TERMINATION | — | n/a | No PS-class MTTC breakout in Phase 0 sources; canonical τ=64d default applies |
| VMC_CORR_IMPLEMENTATION | — | n/a | No PS-class patch-cycle median in Phase 0 sources; canonical τ=79.3d default applies |

Rationale: IBM CODB 2024 p13 Fig 7 publishes "Physical security compromise" attack-vector cost/frequency (6%, USD 4.19M) but does NOT publish per-vector lifecycle days for it (only the top 5 attack vectors get a lifecycle row in Fig 8). No primary-cited cell available.

## NW (Network Segmentation — Micro-segmentation class)

| Sub-function | Value (days) | Statistic anchor | Source |
|---|---|---|---|
| LEC_DET_MONITORING | — | n/a | No NW-class MTTI breakout in Phase 0 sources; canonical τ=194d default applies |
| LEC_RESP_EVENT_TERMINATION | — | n/a | No NW-class MTTC breakout in Phase 0 sources; canonical τ=64d default applies |
| VMC_CORR_IMPLEMENTATION | — | n/a | No NW-class patch-cycle median in Phase 0 sources; canonical τ=79.3d default applies |

Rationale: Phase 0 sources do not publish network-segmentation-tier breakouts of breach lifecycle. Re-investigate when Mandiant M-Trends publishes segmentation-stratified dwell-time telemetry.

## EN (Encryption — Data Encryption at Rest class)

| Sub-function | Value (days) | Statistic anchor | Source |
|---|---|---|---|
| LEC_DET_MONITORING | — | n/a | No EN-class MTTI breakout in Phase 0 sources; canonical τ=194d default applies |
| LEC_RESP_EVENT_TERMINATION | — | n/a | No EN-class MTTC breakout in Phase 0 sources; canonical τ=64d default applies |
| VMC_CORR_IMPLEMENTATION | — | n/a | No EN-class patch-cycle median in Phase 0 sources; canonical τ=79.3d default applies |

Rationale: Encryption-class controls reduce loss magnitude (impacted-records → unreadable-records) rather than modulating LEC/VMC elapsed-time sub-functions. They route through FAIR `Loss Magnitude` rather than `Loss Event Frequency`, so the absence of cells here is by design, not by data gap.

## IR (Incident Response — IR Team & Procedures class)

| Sub-function | Value (days) | Statistic anchor | Source |
|---|---|---|---|
| LEC_DET_MONITORING | — | n/a | No IR-class MTTI breakout in Phase 0 sources; canonical τ=194d default applies |
| LEC_RESP_EVENT_TERMINATION | — | n/a | No IR-class MTTC breakout in Phase 0 sources; canonical τ=64d default applies |
| VMC_CORR_IMPLEMENTATION | — | n/a | No IR-class patch-cycle median in Phase 0 sources; canonical τ=79.3d default applies |

Rationale: SANS 2023 IR Survey reports "top 25% of orgs detect incidents within 60 minutes" and "more than half within five hours" — qualitative anchors only; partner-mirror page-citation unavailable without gated PDF access. SANS 2023 also reports "54% of orgs remediate within 24 hours after containment" — sub-day granularity below the LEC day-axis for canonical τ, and is post-containment cleanup, not full containment. Reaffirms canonical defaults; no IR-class primary-cited cell available.

## TI (Threat Intelligence — Threat Intelligence Platform class)

| Sub-function | Value (days) | Statistic anchor | Source |
|---|---|---|---|
| LEC_DET_MONITORING | — | n/a | No TI-class MTTI breakout in Phase 0 sources; canonical τ=194d default applies |
| LEC_RESP_EVENT_TERMINATION | — | n/a | No TI-class MTTC breakout in Phase 0 sources; canonical τ=64d default applies |
| VMC_CORR_IMPLEMENTATION | — | n/a | No TI-class patch-cycle median in Phase 0 sources; canonical τ=79.3d default applies |

Rationale: DBIR 2024 p22 Fig 20 publishes median time-to-first-scan latency (CISA KEV CVEs 5d, non-KEV 68d) — this informs a future `cisa_kev_subscriber_overlay` on `VMC_ID_THREAT_INTELLIGENCE` τ. But `VMC_ID_THREAT_INTELLIGENCE` reclassified to `UnitType.PROBABILITY` under #131 (see reclassified-sub-functions list at bottom), so the figure is overlay-only, not a primary-cited cell for the 3 KEPT sub-functions.

## IND (Industrial sector — the one row with a primary-cited value, for completeness)

| Sub-function | Value (days) | Statistic anchor | Source |
|---|---|---|---|
| LEC_DET_MONITORING | 199 | Mean | IBM CODB 2024 p6 (industrial-sector highlight) — MTTI mean. Statistic labeled "Mean time to identify" on p10 Figure 4 where global MTTI/MTTC means are defined; page-6 industrial-sector callout inherits the same statistic definition. |
| LEC_RESP_EVENT_TERMINATION | 73 | Mean | IBM CODB 2024 p6 (industrial-sector highlight) — MTTC mean. Statistic labeled "Mean time to contain" on p10 Figure 4 where global MTTI/MTTC means are defined; page-6 industrial-sector callout inherits the same statistic definition. |
| VMC_CORR_IMPLEMENTATION | — | n/a | No industrial-sector patch-cycle median in Phase 0 sources; canonical τ=79.3d default applies |

The IND values 199/73 are **means**, not medians — they share the same calibration-philosophy footing as the canonical τ values 194d/64d (`LEC_DET_MONITORING`/`LEC_RESP_EVENT_TERMINATION`), which are also IBM CODB means anchored under the exponential mean-lifetime philosophy (τ = mean). Under this anchor: opeff(t = 199 days) ≈ exp(-199/194) ≈ 0.359 for industrial-sector LEC_DET_MONITORING — the IND-segmented value is slightly worse than the global 194d default. Earlier draft tables labeled these cells "Median (days)" — corrected here in lockstep with design doc §4 (issue #131 T3).

Sub-sector deeper-dives (chemical manufacturing, electric utility, oil-and-gas, pipeline, water utility — see `docs/reference/calibration-sources/sub_sector_*.md`) provide narrative context for the IND row but do NOT supply control-class breakouts and so do not produce additional cell values in the cross-tab summary.

## Reclassified sub-functions (no canonical day-count calibration post-#131)

The following sub-functions reclassified from `UnitType.ELAPSED_TIME` to `UnitType.PROBABILITY` under issue #131. Analysts enter bounded [0,1] effectiveness, NOT day counts. No benchmark cells appear for these:

- `LEC_RESP_RESILIENCE` (`evidentially-deferred` — right-censored recovery distribution per IBM CODB p22 Fig 24)
- `VMC_ID_THREAT_INTELLIGENCE` (`evidentially-deferred` — no canonical TI-feed-lag median; DBIR scan-latency informs an overlay only)
- `VMC_ID_CONTROL_MONITORING` (`evidentially-deferred` — no measured cadence in Phase 0 sources)
- `VMC_CORR_TREATMENT_SELECTION` (`evidentially-deferred` — no clean treatment-selection elapsed-time median)
- `DSC_ID_MISALIGNED` (`evidentially-deferred` — governance metric, not surveyed)
- `DSC_CORR_MISALIGNED` (`standard-virtual` — FAIR-CAM Standard §5.3 virtual sub-function)

Library entries on these sub-functions use the `# calib:override_required` provenance marker (issue #131 T4) and default to implementation-default 0.5 effectiveness pending per-org override.

## Future research targets

The dominant Phase-0 gap is **class-segmented breach-lifecycle data**. Re-investigate when:

- IRIS 2024 full report becomes accessible (paywall lifted or institutional access).
- Mandiant M-Trends publishes EP-class or vendor-class segmented dwell-time telemetry.
- Per-org override layer (issue #131 follow-up) accumulates organization-specific medians sufficient to seed class-segmented defaults.
