# Changelog

All notable changes are documented here. (The project was renamed RiskFlow → Idraa on 2026-07-17; historical entries keep the original naming.)

## Unreleased

- **#131 snapshot reproducibility**: New runs persist V3 snapshots capturing
  per-assignment `unit_type` at write time. Pre-#131 V2 snapshots are
  re-interpreted under the post-#131 `SUB_FUNCTION_UNITS` mapping when
  read — a banner in the run report explains the re-interpretation, and a
  server-side `snapshot_v2_read` log entry preserves a tamper-evident audit
  trail. Historical run results may differ from original outputs for runs
  involving reclassified sub-functions (`LEC_RESP_RESILIENCE`,
  `VMC_ID_THREAT_INTELLIGENCE`, `VMC_ID_CONTROL_MONITORING`,
  `VMC_CORR_TREATMENT_SELECTION`, `DSC_ID_MISALIGNED`, `DSC_CORR_MISALIGNED`).

  Reproducibility scope: V3 locks per-assignment `unit_type` at write time.
  V3 does NOT snapshot τ values, multipliers, or other calibration constants
  — those are treated as live calibration refinements that V3 re-runs will
  adopt. The `unit_type` is locked because it determines the numerical
  interpretation of `capability_value` (an analyst input); τ values are
  derivations from external benchmarks and improve with new evidence.

- **#439 meta→reliability coupling (Slice 2)**: Meta controls (awareness,
  monitoring, decision-support) now credit value through the reliability of
  the controls they operate alongside; direct meta multipliers retired;
  catalog + standalone scores unchanged; existing stored ensemble ranges
  will differ on re-run — re-run analyses to see updated attribution.
