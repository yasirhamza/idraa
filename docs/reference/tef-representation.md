# TEF representation: bounded PERT

Library `threat_event_frequency` is stored as bounded **PERT** `{distribution:
"PERT", low, mode, high}`, matching the wizard authoring path. TEF is a bounded
frequency estimate (Open FAIR / pyfair convention: BetaPERT for the frequency
node), NOT a heavy-tailed unbounded lognormal.

**History.** #520 (2026-07-08) converted TEF to lognormal + de-templated it.
Milestone A (2026-07-08, this doc) **reverted the lognormal representation** per
the owner ruling that TEF is a bounded estimate — while **keeping #520's
de-templating** (see below). This also reverses Epic B (#326 D5/D6), which had
made the wizard author TEF as native lognormal.

## De-templating preserved

The revert keeps #520's win: **98 distinct TEF nodes** (as of the current
102-entry library), no two archetypes sharing
a frequency, except **4 allowlisted genuine-tie pairs** (kept as identical PERT
triples): `{field-instrument-spoofing, pipeline-scada-integrity}`,
`{casino-ransomware-operational-disruption,
law-firm-privileged-data-ransomware-extortion}`, `{manufacturing-billing-fraud,
hospitality-guest-data-insider}`, `{agri-equipment-physical-tamper,
education-campus-facility-tamper}`. CI guard:
`tests/integration/test_library_loss_differentiation.py::test_tef_globally_distinct_across_library`.

## Revert derivation (library seed)

- **`(low, high)`** = the bounds #520 fed its lognormal fit (the p5/p95). So the
  de-templated distinct bounds carry through byte-for-byte; the PERT `(low, high)`
  are the SME/curator p5/p95 **promoted to hard PERT bounds** (0th/100th pctile of
  the Beta) — a minor epistemic compression, matching the pre-Epic-B convention.
- **`mode`**: the **57 unchanged** entries restore the exact pre-#520 mode (git
  `96e27dc`); the **36 re-spaced** entries re-derive by **relative skew**
  `mode_frac = (old_mode − old_low)/(old_high − old_low)` placed into the new
  `(low, high)` — which preserves the archetype's Beta shape identically.

## Wizard authoring

The wizard authors right-skewed PERT via the **restored pre-Epic-B lognormal→PERT
collapse**: `_fit_lognorm_native` (closed-form, avoids the truncated-scipy
divergence) → `combine_lognorm_trunc` → `lognormal_to_pert_approx`, on `[0, +∞)`
support (`_LOGNORMAL_TO_PERT_PIPELINE`). The collapsed PERT is stored as
`{distribution: "PERT", low, mode, high}` with lognormal-fit provenance in the
sidecar (`build_scenario_payload`'s TEF branch). A symmetric normal-fit PERT would
be wrong (midpoint mode) and ill-conditioned on `[0, ∞)` — the plan-gate
methodology BLOCKER.

Note the wizard `mode` and the library `mode` come from different inputs and are
not expected to match: the wizard has only two SME quantile anchors, so it takes
the **analytic lognormal mode** `exp(meanlog − sdlog²)` (via
`lognormal_to_pert_approx`); the library carries a curated SME `mode` and re-spaces
it by **relative skew** (see revert derivation above). Both are right-skewed and
correct for their respective input regimes.

## Legacy scenarios

Scenarios snapshot their TEF at finalize, so pre-revert scenarios keep their
lognormal TEF until re-authored (snapshot pattern). vuln stays PERT (bounded
[0,1]); PL/SL are unchanged (Milestone B handles the loss overhaul).
