# D-ii-b loss-form research protocol (Epic D, #497)

The protocol for the D-ii-b adversarial (sector × form) loss-magnitude sweep +
the TEF/vulnerability threat-intel table. Adapted from
`docs/reference/loss-anchors/research-protocol.md` (Epic C-ii-b), re-keyed from
per-archetype to **sector × form**. D-ii-a produced the two inputs this protocol
consumes: `data/loss_form_source_corpus.json` (source shortlist) and
`data/loss_form_targets.json` (the work-list).

## Work-list

The D-ii-b targets are exactly the `data/loss_form_targets.json` cells with
`disposition == "needs_fresh_research"` plus the archetypes with
`needs_fresh_research: true`. Cells with `disposition == "defer_to_diii_profile"`
are **NOT** sweep targets — D-iii decides them at profile-authoring time (this is
the demand-scoping that prevents force-fitting a form onto a sector where no
archetype fires it).

## Per-cell procedure

1. **Research** a **primary** loss-magnitude source for the (sector × form) cell,
   starting from the cell's `candidate_sources` and the corpus. A **primary**
   source only: a peer-reviewed / regulatory / vendor publication at a citable
   locator — never a blog or secondary aggregator in the `locator`.
2. **Adversarial citation verification** — a *second, independent* agent
   re-fetches every cited URL / vendored file and confirms (a) the source is
   real and reachable, (b) the cited number appears at the cited **primary**
   locator, and (c) the number is a **single-form slice**, not a **blended**
   headline (a blended sector total or a total-cost-of-breach figure may not be
   pulled into one form — meth M1; see the corpus `single_form_slice` /
   `blended_headline` fields). An un-verifiable or blended citation is rejected.
3. **Tier + record.** Write a row into `data/loss_form_anchor_tables.json` keyed
   by `sector × form`, using the pinned **evidence-tier vocabulary**
   `loss_tier ∈ {paginated, vendor, anecdotal}` (matching Epic C's
   `loss_anchor_tables.json`), with `anchor_type: "none"` for a no-source cell.
   (Note: the *corpus's* `loss_tier: "none"` is a separate "no-repo-source" axis
   and does NOT carry into the anchor table — this resolves the none/anecdotal
   vocabulary collision.)
   - `paginated` — figure/table/page locator.
   - `vendor` — named report + **permalink** + **accessed** date.
   - un-verifiable → `anchor_type: "none"` / `loss_tier: "anecdotal"` with a
     `no_source_reason`, logged, never kept on the citing agent's word.
4. **No cross-sector borrowing** (no cross-sector tail borrowing). A cell's magnitude derives from that sector's
   own cited source. The only permitted refinement is a within-family sub-sector
   multiplier over the row's own NAICS parent (cited, bounded ≤ 10) — never an
   unrelated sector's number.

## Attestation (new archetypes)

For each `needs_fresh_research: true` archetype (the gap-report sub-sector
targets), research **archetype-level** attested threat activity (a documented
incident, a DBIR/IC3 sector pattern for that sub-sector, a CISA advisory, or an
ATT&CK group known to target it) before D-iii-b authors it. Sector-level thinness
alone is not attestation. The four gap-report mis-cited citations are forbidden.

## Security carry-forward (citation-URL rendering, Sec-I1)

`locator` / `secondary_url` values flow corpus → `data/loss_form_anchor_tables.json`
→ the D-iii entry-detail "how this loss was built" render. The consuming template
MUST route them through **`linkify_https`** (https-only allowlist) before any
`href` — Jinja autoescape covers text nodes, not href attributes. Add an XSS
regression over a `javascript:`-laden citation when the render lands.

## Closure

D-ii-b flips the armed `LOSS_FORM_RESEARCH_COMPLETE` gate green when no
`needs_fresh_research` cell or archetype remains:

```
LOSS_FORM_RESEARCH_COMPLETE=1 uv run pytest tests/unit/test_loss_form_targets.py -v
```

## TEF / vulnerability table (also D-ii-b)

Separately, D-ii-b builds the TEF/vulnerability threat-intel table (Verizon DBIR
sector patterns, IC3 counts, CISA advisories, ATT&CK-group targeting) for the
de-templating in D-iii §3. TEF stays PERT (frequency, not magnitude — not under
the lognormal-only bar); vulnerability stays the analyst-judged inherent baseline.
