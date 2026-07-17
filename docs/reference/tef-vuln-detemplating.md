# TEF + vulnerability de-templating reference (Epic D §3, D-ii-b)

The threat-intel half of D-ii-b. D-i found TEF drawn from ~41 buckets (top bucket
10 entries) and vulnerability from ~25 buckets (top bucket 10) across the 85 seed
entries — shared "templates" applied across dissimilar archetypes. Epic D §3
de-templates both: **differentiate per archetype.** Neither TEF nor vulnerability
is a loss *magnitude*, so neither goes through the envelope×share machinery
(design Amendment A1) — they are their own PERT / inherent-baseline layers. This
reference gives D-iii (a) the available directional threat-intel signals and
(b) the de-templating rubric.

## Honesty note (why this is a rubric, not a frequency table)

Per-(sector × archetype) **absolute** TEF frequencies are not cleanly sourceable
in public data — exactly the finding that reshaped the loss side (Amendment A1).
In-repo threat-intel gives **directional** signals (which threat-types / patterns
are relatively common), not per-archetype annual frequencies. So TEF
differentiation is analyst judgment *informed* by directional signals + cited
where one applies — parity with the loss-envelope×share model (cited anchor +
documented analyst-judged differentiation). This reference does NOT fabricate a
per-archetype frequency table.

## TEF (Threat Event Frequency) — stays PERT, differentiated per archetype

TEF = how often the threat event occurs for the archetype (events/year). It stays
a **PERT** distribution (frequency, not magnitude — NOT under the lognormal /
envelope machinery) and does NOT interact with the envelope×share loss model.

### Available directional signals (in-repo, cited)

- **IC3 2025** (`docs/reference/calibration-sources/ic3_2025.md`) — crime-type
  complaint frequency (total complaints 1,008,597; BEC / ransomware / investment
  fraud breakdown, p.6-8). Directional relative frequency by threat-**TYPE**
  (national, not per-sector). Use: a BEC/fraud archetype's TEF is informed by
  IC3's high BEC complaint volume; a niche archetype is not.
- **Verizon DBIR 2024** (`docs/reference/calibration-sources/dbir_2024.md` + the
  report's Industries section) — per-industry breach **patterns** (system
  intrusion / social engineering / basic web app / misc errors / privilege
  misuse). Directional: which patterns dominate a sector. (The vendored note
  carries τ/remediation timing; per-industry pattern shares live in the report's
  Industries section — cite the report; fetch only if a specific share is needed.)
- **#475 ATT&CK crosswalk** (`data/seed_attack_full_mappings.json`, 215 rows) —
  per-archetype technique mappings + provenance. This is a technique-**plausibility**
  signal, NOT a frequency: an archetype with rich, cited ATT&CK mappings against a
  sector that known ATT&CK groups target is a more-attested (higher-TEF-plausibility)
  archetype than one with thin / expert-estimate mappings.
- **CISA sector advisories** (`docs/reference/calibration-sources/sub_sector_*.md`
  notes — e.g. AA21-131A Colonial Pipeline, ICS advisories for OT sub-sectors) —
  sector-specific active-threat signals. Use: an OT archetype in a
  CISA-advisory-covered sub-sector carries elevated attested TEF.

### De-templating rubric (D-iii)

1. **Retire the shared TEF buckets:** no two dissimilar archetypes share an
   identical TEF PERT without a documented justification (the D-i differentiation
   discipline extended to TEF).
2. **Rank within a sector by directional signal:** high-frequency (IC3-attested
   crime type / DBIR-dominant pattern / active CISA advisory) → higher TEF mode;
   background / niche (reconnaissance, exotic supply-chain) → lower.
3. **Cite where a signal applies, analyst-judge otherwise:** where a directional
   signal informs the value, cite it in the entry rationale; otherwise
   analyst-judged-differentiated with a one-line documented reason. TEF stays PERT.
4. **No magnitude coupling:** TEF is frequency; it does not touch the envelope,
   the shares, or the loss lognormals.

## Vulnerability — stays the analyst-judged inherent baseline (§1b), differentiated

Per §1b and `docs/reference/vulnerability-semantics.md`: vulnerability is the
**inherent (control-naive) industry-baseline** susceptibility
`P(threat event → loss event)` for the archetype. It is **not** loss-data-sourced
and gets **no** data-source tier ladder — de-templating vulnerability is NOT a
sourcing exercise.

- **De-templating = differentiate the shared vulnerability buckets per archetype**
  via the inherent-baseline sanity check, analyst-judged, no new sources.
- The vulnerability-semantics audit floors apply (implausibly-low values that read
  as residual / control-adjusted are re-curated upward — the #338 audit).
- Vulnerability is never residual/control-adjusted (the engine applies controls via
  a sample-level vuln multiplier on the UNCHANGED stored value — a residual value
  double-counts; `fair_core.py:267 (untouched), :532-536 (sample-level multiply)`).

## What this is NOT

- NOT a fabricated per-archetype absolute-frequency table (directional signals +
  analyst judgment is the honest approach — parity with Amendment A1's loss model).
- NOT loss magnitude (that is the envelope table `data/loss_form_envelopes.json`
  + the archetype form-shares).
- NOT a new vulnerability data source (vuln stays analyst-judged inherent baseline).


## Within-sector distinctness vs. coarse-scale precision (D-iii detemplating, 2026-07-07)

When de-templating forces every same-sector archetype onto a DISTINCT value, the
bounded coarse scales (vulnerability ∈[0,1]; secondary-loss shares) cannot express
that distinctness in a dense sector without sub-0.05 gaps. Those gaps encode an
**ORDERING** (distinctness-driven, direction grounded in attack-path / loss-effect),
**NOT** calibrated 0.02-resolution confidence — vulnerability is coarse, analyst-judged,
control-naive by construction. Consumers should read the rank, not the exact gap.
TEF is exempt (its within-sector range spans orders of magnitude). Where same-sector
archetypes genuinely share one inherent baseline, they are ALLOWLISTED (documented
shared value) rather than assigned an off-grid distinct number — the honest treatment
of a real tie.
