# Loss-Anchor Research Protocol

> **Scope:** Epic C-ii-b (#335) — per-sector research sweep producing
> `data/loss_anchor_tables.json`. This document is the COMMITTED procedure
> reference that every sector task (Tasks 2–14) runs uniformly. Tasks 2–14
> dispatch subagents; they do NOT modify this document.

---

## Purpose

Produce one verified, honestly-tiered loss-magnitude anchor row per archetype in
`data/target_archetypes.json` (82 rows across 13 sectors). Each row feeds C-iii
curation, which authors full library entries with calibrated loss distributions.
The dominant risk is **citation fabrication** — every cited anchor is
independently re-fetched by a second agent; un-verifiable → TIER-3/none + logged.

---

## §1  Research Pass (Agent 1, web-enabled)

For each archetype in the sector (read from `data/target_archetypes.json`): slug,
threat_type, asset_class, actor, **is_ot** — OT archetypes (`is_ot: true`) have a
different loss-source landscape and must be researched accordingly.

**Attempt sources in this order:**

1. **Vendored corpus** (`docs/reference/calibration-sources/source-catalogue.md`):
   - IRIS-2025 module figures for the archetype's 3 cited sectors.
   - `sub_sector_*` TIER-2 multipliers within the row's own NAICS family.
   - IBM CODB only via Policy B with an independently-sourced median (see tiering
     framework `docs/reference/loss-magnitude-tiering.md`).
2. **Targeted web research** for a sector/archetype loss statistic:
   - NetDiligence, Coveware, Sophos, Advisen-class vendor reports.
   - Regulator/insurer publications.

**Record per archetype:**

- Loss values + `anchor_type` + `loss_tier` + `citations` list.
- Citation shape by tier:
  - `paginated` → `locator` = figure/table/page reference (e.g. `"Figure 3, p. 12"`).
  - `vendor`/web → `locator` = permalink URL + `accessed` = ISO date
    (e.g. `"accessed": "2026-06-10"`).
  - `multiplier_over_baseline` rows: ≥2 citations, one with `"supports": "multiplier"`.

**Rules:**

- **Honest tiers: TIER-2/3 dominant is EXPECTED.** Most archetypes lack paginated
  anchors. Do not inflate a tier.
- No value without a citation.
- **No cross-sector tail borrowing.** `baseline_sector` for
  `multiplier_over_baseline` rows MUST be the row's own sector or its NAICS family
  (sub-sector refinement over the row's own parent is the only legitimate use).
  Using an unrelated sector's baseline is FORBIDDEN (B-METH-4).
- Aggregate totals (IC3-style) and bare means are NOT percentile anchors.
- If nothing citeable → `anchor_type: "none"` + `no_source_reason` + `loss_tier:
  "anecdotal"`.

### PRIMARY-SOURCE RULE (B-METH-7 — the dominant LLM-research failure mode)

If a found source is a **secondary reference** (blog / news / roundup / press
release quoting a study), the citation `locator` MUST point to the **PRIMARY**
(report name + year + figure/page if paginated; named vendor + year if vendor
report). The secondary may be recorded as `secondary_url` for traceability only.

**If the primary is unreachable or not publicly identifiable, the row is
TIER-3/none regardless of how many secondaries corroborate the number.**

A secondary URL in the `locator` field = verification failure. The verify agent
will demote the row.

---

## §2  Adversarial Verify Pass (Agent 2, web-enabled, independent)

The verify agent receives the proposed rows + citation strings **ONLY** — no
web-fetch results or page excerpts from the research pass (B-METH-6). It MUST
independently re-fetch every cited URL / re-open every vendored file.

**For EVERY citation, confirm:**

1. The source is real and reachable.
2. The cited number appears at the cited locator.
3. The locator is a PRIMARY source (not a blog/news/roundup quoting a study).

**Verdict per row:**

- `verified: true` only if ALL its citations check out on all three checks above.
- Otherwise, DEMOTE the row:
  - `anchor_type: "none"`
  - `loss_tier: "anecdotal"`
  - `no_source_reason: "citation failed verification: <detail>"`

Never keep an anchor on the citing agent's word alone.

---

## §3  Methodology Gate (Agent 3)

Per-sector review covering:

- **Tier honesty:** reported tier matches the actual source type.
- **σ-policy fit:** Policy A (`quantile_pair`) or Policy B (`median_mean`) per
  `docs/reference/loss-magnitude-tiering.md`; the citation's described statistic
  MATCHES the `anchor_type` (B-METH-3).
- **Citation shape + primacy:** paginated citations have figure/table/page locators;
  vendor/web citations have permalinks + accessed-dates; no secondary in `locator`.
- **Multiplier bounds + NAICS family:** multiplier ∈ (0, 10]; `baseline_sector` in
  the row's own sector/NAICS family.
- **No overclaim:** no adjacent-domain (actuarial/Solvency II/market-risk) concepts
  without FAIR grounding.

Fix all BLOCKER/IMPORTANT findings. Re-review the sector if any were found (iterate
to 0 BLOCKER/IMPORTANT before committing the sector block).

---

## §4  Commit Procedure (Arch-I2 — prevents format drift across 13 commits)

Read the current array, extend it with the new sector rows, write with the pinned
format:

```python
import json
from pathlib import Path

path = Path("data/loss_anchor_tables.json")
data = json.loads(path.read_text(encoding="utf-8"))
data.extend(new_sector_rows)
path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
```

Then run the validators:

```bash
uv run pytest tests/unit/test_loss_anchor_tables.py -v
```

Commit only on green:

```bash
git add data/loss_anchor_tables.json
git commit -m "feat(loss-anchors): <sector> sector anchors — N rows, M verified (#335 C-ii-b)"
```

---

## C-iii Handoff Constraint (Sec-I1)

Citation `locator` and `secondary_url` values from this file end up in
`source_citations` and may later be rendered as hyperlinks in C-iii templates.

**C-iii MUST allowlist URL schemes (`https://` only) before any `href` use.**
Jinja's `autoescape` covers text nodes but does NOT sanitize `href` attribute
values — a `javascript:` or `data:` URI in a `locator` field would pass autoescape
and execute. The allowlist check belongs at the template layer before any
`href="{{ citation.locator }}"` rendering.

**Status (2026-06-12): IMPLEMENTED** — gate is `riskflow.formatting.linkify_https` (https-only scheme allowlist, explicit `urlsplit` check), applied in `templates/library/entry_detail.html` citations block; regression tests in `tests/unit/test_formatting_linkify.py` + `tests/integration/test_library_routes.py`. Issue #349 closed.
