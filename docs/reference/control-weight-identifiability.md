---
title: "Why FAIR-CAM composition weights are not identifiable from one organisation's data"
status: tracked reconstruction of the 2026-06-25 identifiability audit (the original lived in an untracked design doc; this is the public record)
last_reviewed: 2026-09-07
related:
  - docs/reference/control-weight-robustness.md
  - docs/reference/fair-departures-register.md (C4, C7)
  - issue #419
---

# Why FAIR-CAM composition weights are not identifiable from one organisation's data

## The claim

The four FAIR-axis routing weights and the κ reliability-coupling gain
(register entries C4 and C7) are **calibrated but not validated**, and they
**cannot be validated** against a single organisation's loss outcomes. The
original audit put the shortfall at roughly three to five orders of magnitude
between the data one organisation can ever observe and the data the
estimation would need. This document records the argument so it can be
evaluated in the open; the conclusion drives two standing policies:

1. No per-organisation calibration layer for these parameters. It would
   relocate the guess into a form that looks measured.
2. Their uncertainty is propagated instead (the weight-robustness ensemble),
   and control value is reported as a range with a rank-stability verdict.

## The argument

### 1. What the weights do

A control group's composed effectiveness `E` reaches a FAIR node as the
multiplier `1 − E·w`. Estimating `w` from outcomes means estimating how much
the node (a loss-event rate, or a loss magnitude) changed *because of* the
control, relative to the counterfactual without it. Everything below follows
from the fact that a single organisation observes only one arm of that
comparison, and observes it rarely.

### 2. Loss events are rare at the scenario level

Per-scenario Loss Event Frequency in the library sits mostly below one event
per year, and for the scenarios that matter most (catastrophic entries) far
below. Over any horizon in which the control estate, the threat landscape,
and the organisation itself stay comparable, the observed count of loss
events for one scenario is a handful.

**Illustration (hand-math, not the original audit's computation).** For a
Poisson count `n`, the relative standard error of the rate estimate is
`1/√n`. To separate `w = 0.8` from `w = 0.6` on the Vulnerability axis at a
typical `E` of 0.5, the multipliers are `0.60` vs `0.70`, a relative
difference of about 15%. The standard error of the difference between two
independent rate estimates with `n` events each is `√(2/n)` in relative
terms; resolving a 15% difference at two standard errors needs
`2·√(2/n) ≤ 0.15`, so `n ≈ 360` loss events **in each arm**, about 720 in
total. At an LEF of 0.1 per year that is on the order of 3,600
organisation-years per arm. Nothing about that arithmetic depends on the
particular weight; it is set by the rarity of loss events.

### 3. The counterfactual arm does not exist

Even with enough events, the organisation never runs the without-control arm.
Before/after comparisons across a control deployment are confounded by
everything else that changed in the interval (other controls, threat
activity, business change), and the FAIR-CAM composition itself says controls
are deployed and act together (§2.3: all controls depend on VMCs and DSCs).

### 4. The parameters co-vary

The weights are not observed separately. A Prevention group affects both TEF
and Vulnerability, and their product is what the loss-event count reflects;
`prevention.tef` and `prevention.vuln` are only jointly constrained by that
count. κ enters through every Loss Event Control's reliability at once. This
is why the robustness ensemble draws same-function weights from **one**
logit-space shock rather than independently: sampling them independently
would understate the instability by partial cancellation.

### 5. Population studies do not close the gap

Cited effectiveness values in the control library come from population-level
efficacy studies (coverage mappings, deployment studies). They anchor
`capability`, not `w`, and they say nothing about how a given organisation's
deployment converts that capability into risk change. The rubric's
non-identifiability disclaimer (`control-function-decomposition-rubric.md`
§5.4) makes the same point for the per-control inputs.

## What follows

- The canonical weights stay **immutable in code** with pinning tests, labelled
  `implementation-calibration`, and the register lists them as CALIBRATION.
- The ensemble's logit-normal kernel is a **perturbation around the
  unvalidated guess**, not a non-informative prior; the robustness doc says
  so (Meth-I1) and the σ = 0.6 width is itself a convention (Meth-I2).
- Dollar figures for control value are **model-relative ranges under
  composition-weight uncertainty**, never validated estimates of realised loss
  reduction. Every surface that shows them says so.
- Absolute ROI point claims for a single control were retired by the
  original audit and must not be reintroduced.

## Evaluating this argument

The claim is falsifiable at the population level, not the single-org level:
a multi-organisation dataset with comparable scenarios and known control
estates could in principle estimate the weights. Until such a dataset exists
in the open, the policies above hold. Anyone who believes a single-org
estimate is feasible should start from the §2 arithmetic and show which of
its inputs is wrong.
