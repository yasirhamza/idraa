# Inherent-Baseline Vulnerability Audit Findings

**Epic C #335 §1b — generated 2026-06-10 by `scripts/audit_library_vulnerability.py`**

> **Canonical frame.** The source-of-truth vulnerability decision is
> `docs/reference/fair-cam-methodology.md` "Vulnerability anchor: control-naive
> inherent" (#339). Per #339, the IRIS-derived anchor is a controlled-world
> conversion rate used as a pragmatic inherent proxy — biased low (risk-
> understating) by construction; modelled controls are deltas from an industry-typical baseline,
> NOT a literal zero-control state. Flagged entries below are **anchored low**
> under that frame and are **candidates for UPWARD re-curation**, tracked at
> **#338** (deferred to Epic C #335). They are **not** "residual contamination."

This document records the findings from running the inherent-baseline heuristic
audit over the 44 seed library entries (44 entries across
`data/seed_library_entries.json` + `data/seed_library_entries_extension.json`).

The audit **reports only** — it does not change any seed entry.  The seed
re-curation is deferred to Epic C (#335), tracked at **#338**, which will
adjudicate each flagged entry and re-curate upward where needed.

---

## Heuristic and Rationale

**Library-entry `vulnerability` is the INHERENT (control-naive) susceptibility**
— `P(threat event → loss event)` at a **pre-(modeled-)control)** posture (pre the
org's own modelled controls).  It is never a residual / control-adjusted value
(storing a residual would cause double-counting when the engine multiplies by
`vuln_mult`; see `docs/reference/vulnerability-semantics.md` for the full
architectural argument and #339 for the canonical decision).

**Heuristic (flag trigger):**

> `mode < 0.10` OR `high < 0.20` → flagged as **anchored low** under the
> canonical control-naive inherent frame (#339): a candidate for **UPWARD
> re-curation** (#338), pending analyst review.

**Rationale:** These flagged values are curated at a **controlled / typical
posture** — for a typical cyber scenario archetype at a control-naive,
industry-typical posture, a PERT vulnerability mode below 10 % or a
90th-percentile (high) below 20 % is an implausibly low inherent susceptibility.
Most standard archetypes (credential attacks, phishing, malware, ransomware) have
control-naive mode values in the 20–60 % range; values this far below that range
read as having been calibrated against a controlled posture rather than the
control-naive industry-typical baseline, and are candidates for upward
re-curation under #338.

**These thresholds are analyst-judgment floors, not citation-derived.**
Vulnerability is not a loss-data-sourced parameter (unlike the loss-magnitude
tier ladder in `docs/reference/loss-magnitude-tiering.md`); there is no
published empirical distribution of inherent pre-control vulnerabilities to
anchor floors against.  The values 0.10 (mode) and 0.20 (high) are conservative
review triggers chosen by the methodology author.

---

## Flag-for-Review, Not Auto-Verdict

A flag means "an analyst should confirm this entry's intent" — it is
review-bait, not a verdict.  Some archetypes have legitimately low inherent
vulnerabilities due to structural/architectural constraints that are
**not** FAIR-CAM modeled controls.

**Worked counter-example** (from `docs/reference/vulnerability-semantics.md`):

> **Scenario:** An air-gapped, physically-isolated OT historian (e.g. a
> read-only data-archival node in a process-manufacturing plant with no
> external network path, locked server room, and strict two-person access
> policy).
>
> **Archetype:** `ot-historian-physical-tampering` — threat actor requires
> physical access; the asset's physical perimeter is the primary barrier.
>
> **Inherent vulnerability:** `{distribution: PERT, low: 0.01, mode: 0.04,
> high: 0.10}`.
>
> **Why it is genuinely low (not residual):** the pre-(modeled-)control
> `P(threat event → loss event)` for a successful physical intrusion by a
> motivated adversary who has *already reached the server room* is still low
> because (a) the physical access barrier is structural/architectural (not a
> modeled FAIR-CAM control), and (b) successful exfiltration from an air-gapped
> historian requires additional technical steps beyond physical presence.  This
> is an inherent archetype characteristic, not the result of layering
> administrative or detective controls on top of a higher-baseline scenario.
>
> **Correct reviewer response:** confirm the air-gap context is documented in
> `calibration_anchor` and clear the flag — do NOT raise the value to meet
> the floor.

A legitimate low-vulnerability entry should always have an explicit
`calibration_anchor` note explaining the structural/architectural reason for the
low baseline so a future curator can distinguish it from a residual value.

---

## Flagged Entries (upward-re-curation queue — #338)

2 of 44 entries triggered the heuristic.

| # | Slug | Distribution | low | mode | high | Triggered rule(s) | Likely cause (preliminary) |
|---|------|:---:|---:|---:|---:|---|---|
| 1 | `credential-stuffing-consumer-portal` | PERT | 0.001 | 0.005 | 0.02 | `mode 0.005 < 0.10`; `high 0.02 < 0.20` | Both rules fire. `mode = 0.005` and `high = 0.02` are extremely low for a control-naive credential-stuffing posture — a consumer portal at the industry-typical baseline has far higher per-event P(breach). Anchored low; strong candidate for **upward re-curation** (#338). |
| 2 | `bec-fraud-financial` | PERT | 0.02 | 0.08 | 0.20 | `mode 0.08 < 0.10` | Mode rule only (`high` is exactly at the 0.20 floor, not below). BEC success rate against a financial-sector target at a control-naive, industry-typical posture should be substantially higher than 8 %. Anchored low — reads as calibrated against a controlled posture; candidate for **upward re-curation** (#338). |

---

## Entries That Passed (42 of 44)

All remaining 42 entries have `mode ≥ 0.10` AND `high ≥ 0.20` and pass the
inherent-baseline heuristic.  No action required for these entries from the
vulnerability-semantics perspective (loss-magnitude calibration is a separate
audit track handled by C-ii-b).

---

## How to Clear a Flag (#338 re-curation guide)

For each flagged entry, the #338 curator should:

1. **Review the `calibration_anchor` field** — does it document a
   structural/architectural reason for the low inherent baseline (like an
   air-gap constraint)?  If yes, the low value may be legitimate: confirm,
   add an explicit note, and mark the flag resolved-legitimate.

2. **If no structural reason exists**, the value is anchored low (curated at a
   controlled posture): **re-curate it upward** to a control-naive,
   industry-typical baseline estimate calibrated to the scenario at a
   pre-(modelled-)control posture, citing the methodology rationale (e.g.
   published threat-intelligence on industry-typical success rates for this
   threat/asset pairing). This is the upward re-curation #339 mandates.

3. **Do not raise a value just to clear the threshold** if the archetype has a
   genuine structural low.  The heuristic is a review trigger — the correct
   outcome is either a documented legitimate-low or a corrected inherent value,
   not a threshold-clearing upward edit.

---

## See Also

- `docs/reference/vulnerability-semantics.md` — full architectural decision on
  inherent vs residual semantics, engine double-count proof, and the audit
  heuristic rationale.
- `docs/reference/fair-cam-methodology.md` "Vulnerability anchor: control-naive
  inherent" (#339) — the CANONICAL vulnerability framing decision.
- `scripts/audit_library_vulnerability.py` — re-runnable audit; re-run after
  #338 upward re-curation to confirm zero remaining flags.
- `docs/reference/loss-magnitude-tiering.md` — analogous tiering for loss
  magnitude (separate audit track).
