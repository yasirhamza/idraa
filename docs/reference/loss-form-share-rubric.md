# Loss-form share rubric (Epic D-iii-a)

How to assign each archetype's **form-shares** for the envelope×share loss model
(design Amendment A1). This rubric is the reproducible framework the D-iii-a
recalibration applies to all 85 entries (and D-iii-b to new ones), so that share
assignment is consistent and auditable rather than ad-hoc per entry.

## 1. Principles (from Amendment A1)

- **Model.** `primary_loss = E_sector × Σ(active PRIMARY-form shares)`,
  `secondary_loss = E_sector × Σ(active SECONDARY-form shares)`, where `E_sector`
  is the cited IRIS envelope (`data/loss_form_envelopes.json`). Scaling a lognormal
  by constant `c` shifts `μ` by `ln c`, preserves `σ`.
- **Share meaning (calibration anchor).** `share = 1.0` for a form means that form
  alone equals the entire cost of a *full, typical, **industry-typical
  control-naive** incident as scoped by IRIS Figure A3* for that sector (the same
  industry-typical / control-naive frame as the inherent vulnerability baseline).
  Shares are fractions of that reference incident.
- **Coherence bound (REQUIRED).** `Σ(all active shares, primary + secondary) ≤ 1`
  per archetype — no archetype loses more than one full incident (guard-enforced).
- **Epistemic status.** Shares are **analyst judgment, vulnerability-grade** — no
  per-value citation, documented rationale + audit floors + family consistency
  (like the inherent vulnerability baseline). They are the *sole* magnitude
  differentiator across archetypes in a sector, so they carry vulnerability's full
  discipline, not a subset.
- **Disclosed biases (A1), with directions.** (i) The engine samples PL⊥SL and
  sums, so a mixed primary+secondary archetype's tail is **understated** (~half
  variance at Σp≈Σs) — anti-conservative. (ii) `σ_s` is a sector-mixture dispersion
  shared across a sector's archetypes (location-not-shape differentiation), which
  **over-states** each single archetype's own tail — the opposite-sign counterpart
  to (i). Both documented, not silent.

## 2. Assignment procedure (per archetype)

1. Identify the archetype's active forms and each form's `kind` (primary/secondary)
   via the stakeholder test (`loss-magnitude-forms.md`).
2. Start from the **threat-type default profile** (§3) for the entry's
   `threat_event_type`.
3. **Adjust per the archetype's actual nature** (§4) — a reconnaissance or a
   fraud archetype is NOT its threat-type default.
4. Verify `Σshares ≤ 1`, apply the **audit floors** (§5), and keep the profile
   **family-consistent** (similar archetypes → similar profiles; §5).
5. Record the profile in the entry's `loss_form_profile` (D-i column); the
   recalibration computes PL/SL = `E_sector × Σ` reproducibly.

## 3. Threat-type default share profiles

Central analyst-judged defaults (adjust per §4). `P`=primary, `S`=secondary.
`share=1.0` = a full IRIS-scoped incident. Ransomware/major-breach is the
reference "full incident" (Σ≈1.0); everything else is scaled relative to it.

| threat_event_type | form shares (form:share:kind) | Σprimary / Σsecondary / Σ | rationale |
|---|---|---|---|
| **ransomware** | productivity 0.40 P · response 0.25 P · replacement 0.05 P · reputation 0.15 S · fines 0.10 S | 0.70 / 0.25 / **0.95** | reference full incident: BI-dominant + IR/recovery, PII churn/fines if data involved |
| **data_disclosure** | response 0.20 P · response 0.15 S · reputation 0.25 S · fines 0.15 S | 0.20 / 0.55 / **0.75** | breach w/ no operational disruption → response + secondary-stakeholder reactions dominate; no productivity |
| **malware** | productivity 0.30 P · response 0.25 P · reputation 0.15 S | 0.55 / 0.15 / **0.70** | general intrusion; breach-like, lighter than ransomware |
| **denial_of_service** | productivity 0.28 P · response 0.02 P | 0.30 / 0.00 / **0.30** | pure downtime, no data loss, minimal response — differentiates sharply from ransomware |
| **social_engineering** | (phishing→breach) use data_disclosure; **(BEC/wire-fraud) BEYOND-ENVELOPE — see §4/§5** | — | credential-phishing routes to breach; funds-transfer fraud is not in the IRIS breach envelope |
| **insider_misuse** | response 0.20 P · reputation 0.15 S | 0.20 / 0.15 / **0.35** | detection/response + trust/brand hit. **IP/trade-secret loss is BEYOND-ENVELOPE (§5), NOT an in-envelope share** |
| **data_tampering** | productivity 0.25 P · response 0.20 P · reputation 0.10 S | 0.45 / 0.10 / **0.55** | integrity loss → rework/scrap + investigation |
| **physical_tampering** | replacement 0.40 P · productivity 0.20 P · response 0.05 P | 0.65 / 0.00 / **0.65** | physical/equipment damage → replacement-dominant (heavier replacement than ot_integrity) |
| **supply_chain** | productivity 0.35 P · response 0.28 P · reputation 0.15 S | 0.63 / 0.15 / **0.78** | broad blast radius (wider than malware) → higher productivity + response + trust |
| **ot_availability** | productivity 0.55 P · replacement 0.20 P · response 0.15 P | 0.90 / 0.00 / **0.90** | OT outage → major production loss + equipment; near-full incident |
| **ot_safety_tampering** | productivity 0.45 P · replacement 0.25 P · response 0.15 P · fines 0.10 S | 0.85 / 0.10 / **0.95** | safety event → production halt + equipment + safety-regulatory |
| **ot_integrity** | productivity 0.40 P · response 0.15 P · replacement 0.05 P | 0.60 / 0.00 / **0.60** | corrupted process → bad output/rework + investigation |

Notes on the table:
- **`response` appears twice for `data_disclosure`** (internal-IR primary + notification/legal secondary) — these are two `loss_form_profile` entries with the same machine key `form=response` but distinct `kind` (the (form,kind)-uniqueness guard permits this); NOT a new "response-notification" key.
- **Σprimary ties across threat-type DEFAULTS are expected and resolved at the entry level.** After differentiating the former malware≡supply_chain collision (nudged above), remaining Σprimary ties (e.g. `data_disclosure` ≈ `insider_misuse` at 0.20; no two full profiles are byte-identical now) are STARTING points only — §4 per-entry adjustment differentiates two genuinely-different co-sector archetypes, and the D-i slug-keyed differentiation guard + the A1 share-sum-tie allowlist are the backstop. The defaults are not final per-entry values.
- **Regime note — competitive_advantage is always beyond-envelope.** `competitive_advantage` is BEYOND IRIS's cost scope (A1.2 M-A1-5), so it may never be taken as a share of the envelope. An insider_misuse archetype that exposes trade secrets carries its IP loss via §5 beyond-envelope FW (own source), NOT an in-envelope competitive_advantage share.

## 4. Per-entry adjustments (the threat-type default is a STARTING point)

- **Reconnaissance / scanning** (e.g. `ot-network-scanning-reconnaissance`, typed
  `ot_availability`): NOT the ot_availability 0.90 default. Recon fires response
  only at a near-zero share — **response 0.03 P, Σ≈0.03**. This is the flagship
  recon-vs-ransomware differentiation A1 exists to produce; do not inherit the
  threat-type default for a recon archetype.
- **IP / trade-secret theft** (e.g. `crop-science-ip-exfiltration`): the loss is
  competitive-advantage erosion NOT captured by the IRIS breach envelope →
  **BEYOND-ENVELOPE** (§ below), not an envelope share.
- **BEC / wire fraud** (funds-transfer): the loss is the transferred amount (IC3
  fraud loss), not part of the IRIS breach envelope → **BEYOND-ENVELOPE**.
- **Catastrophic vs. typical**: an archetype materially worse than a typical
  sector incident may push Σ toward 1.0; it may not exceed it (the envelope already
  captures tail severity via σ_s). Never set Σ>1 to model "worse than typical."

## 5. Beyond-envelope archetypes (FW, not shares)

Where an archetype's loss lies OUTSIDE IRIS Figure A3's documented cost scope
(per A1.2, regime tied to the source's scope), it is **not** an envelope share.
It carries its **own** loss distribution and composes via `compose_forms_to_lognormal`
(D-i FW) on the appropriate PL/SL side:

- **BEC / wire fraud** → the fraudulently-transferred amount (IC3-magnitude class),
  own lognormal, primary.
- **IP / trade-secret theft** → adjudicated trade-secret damages, own lognormal,
  competitive-advantage. Its `kind` (primary vs secondary) is **justified per entry
  via the stakeholder test** (`loss-magnitude-forms.md` — direct erosion of the
  org's own differentiator vs a secondary-market reaction), never assumed.
- **reputation / churn** for a churn-heavy archetype in a sector whose IRIS envelope
  is BI/churn-light MAY be beyond-envelope if its own source exists; otherwise it is
  a documented envelope-share with the churn-light caveat noted.

These stay TIER-3/analyst-judged unless a real own-source exists — no fabrication.

## 6. Audit floors + family consistency

- **Σshares ≤ 1** (hard, guard-enforced).
- **Dominant-form floor:** the archetype's largest-share form should not be
  implausibly small for a material archetype (a `dominant` composition_role form
  at share < 0.10 for a high-impact archetype is a red flag).
- **Non-catastrophic ceiling:** a routine archetype should not sit at Σ=1.0 (that
  is reserved for the reference full incident).
- **Family pinning:** two archetypes of the same class in the same sector get the
  same default profile unless a documented reason differentiates them; a pinning
  test asserts family consistency so shares can't silently drift entry-to-entry.
- **Differentiation:** distinct archetypes must not collapse to an identical
  `Σprimary` (→ identical PL lognormal) — the D-i slug-keyed differentiation guard
  (with the A1 share-sum-tie allowlist) enforces this.
