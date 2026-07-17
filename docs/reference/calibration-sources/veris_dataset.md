---
title: "VERIS Community Database (VCDB)"
year: 2026
url: https://github.com/vz-risk/VCDB
accessed: 2026-05-15
permalink: https://github.com/vz-risk/VCDB/tree/5a6473980ab6f0ad151d8fd2c7b0e9a818aecb95
methodology_summary: "Community-curated public-disclosure-only incident corpus. Each incident encoded as a JSON document conforming to the VERIS schema (Verizon's Vocabulary for Event Recording and Incident Sharing). Public sources only (HHS, state AG breach notifications, media, press releases). 8,000+ incidents covering 2003-present, with explicit known sampling bias toward US healthcare (phidbr sub-source) and selected priority incidents."
---

# VERIS Community Database (VCDB) — Reference Data

**Source:** Verizon RISK Team / VERIS community, "VERIS Community Database" GitHub repository.
[github.com/vz-risk/VCDB](https://github.com/vz-risk/VCDB) — commit `5a64739` (2026-02-17), accessed 2026-05-15.
Sister repository: [github.com/vz-risk/veris](https://github.com/vz-risk/veris) (the VERIS schema), commit `cbe9bd7` (2026-02-12), accessed 2026-05-15.

**Population covered:** Publicly disclosed security incidents and breaches that VCDB curators could code from open sources (US HHS breach notification list, state Attorneys General notifications, news media, press releases). 8,000+ incidents per [verisframework.org/vcdb.html](https://verisframework.org/vcdb.html) (accessed 2026-05-15). Coverage spans 2003-present; the repo description simply says "VERIS Community Database" with no published cutoff date — updates are continuous per `NEWS.md` (post-2018 release cadence "between daily and quarterly").

**Methodology summary:** VCDB curators encode each public-disclosure incident as a JSON document validated against the VERIS schema (`vcdb-merged.json` at repo root, 131 KB). The schema covers actors, actions, assets, attributes, timeline, victim demographics, and discovery method. Three publication directories exist (`data/json/submitted`, `data/json/validated`, `data/json/overridden`) — only `validated` and `overridden` feed the joined CSV/verisr distributions per `data/json/README` (accessed at commit `5a64739`). CSV release is `data/csv/vcdb.csv.zip` (15 MB compressed, accessed 2026-05-15).

**Why this is reference-only (not calibration) for v3 phase 1:** Two reasons. (a) Sampling bias: VCDB README explicitly warns that healthcare (`plus.sub_source == "phidbr"`) and "priority" incidents are non-randomly oversampled. Published 2013-2018 composition shows healthcare was 11-26% of post-2014 records (composition table in README, lines 14-22 at commit `5a64739`). (b) Sparse timeline population: the four FAIR-CAM-relevant fields (`timeline.compromise`, `timeline.exfiltration`, `timeline.discovery`, `timeline.containment`) are schema-defined but rarely populated in publicly-disclosed records — Farhang & Grossklags 2017 (arxiv 1706.00302) reported only 150 incidents with discovery-time values and 59 incidents with specific containment-time values, against a then-corpus of ~10,000. Use VCDB schema as a vocabulary reference and as a raw research input; do NOT cite VCDB-aggregate medians for canonical τ calibration without re-running the aggregation against the current snapshot.

## Headlines

| Metric | Value | Source |
| --- | --- | --- |
| Total incidents | "well over 8,000 individual incidents" | [verisframework.org/vcdb.html](https://verisframework.org/vcdb.html) accessed 2026-05-15 |
| Validated JSON files at HEAD | ≥ 1,000 (GitHub API page cap; repo size 1.58 GB suggests substantially more) | `gh api repos/vz-risk/VCDB/contents/data/json/validated`, commit `5a64739` |
| Default branch | `master` | `gh api repos/vz-risk/VCDB`, commit `5a64739` |
| Latest commit date (snapshot) | 2026-02-17 | commit `5a64739` |
| Last push | 2026-02-17 | repo metadata |
| Schema version (active) | VERIS 1.3.3 | `NEWS.md` 2018-11-29 entry + schema files at commit `5a64739` |
| License | NOASSERTION (LICENSE.txt present but not SPDX-recognized) | `gh api repos/vz-risk/VCDB/license` accessed 2026-05-15 |
| Sampling bias (documented) | Healthcare and "priority" incidents oversampled non-randomly | `README.md` lines 11-22 at commit `5a64739` |

## Repository structure (accessed 2026-05-15, commit `5a64739`)

| Path | Contents |
| --- | --- |
| `data/json/submitted/` | Per-incident JSON not yet validated |
| `data/json/validated/` | Per-incident JSON conforming to VERIS schema (the canonical research set) |
| `data/json/overridden/` | JSON that failed automated validation but is manually verified correct |
| `data/json/README` | Documents the submitted/validated/overridden split (post-2018 pipeline change) |
| `data/csv/vcdb.csv.zip` | Joined CSV export of validated+overridden JSON (15 MB compressed) |
| `data/joined/` | Joined JSON of validated+overridden (empty dir listing — content via clone) |
| `data/verisr/` | R package `verisr` data frame export |
| `vcdb.json` / `vcdb-merged.json` / `vcdb-labels.json` / `vcdb-enum.json` | VCDB schema files (verisc-derived + vcdb-specific diffs) |
| `vcdb_diff.json` / `vcdb_diff-labels.json` | VCDB-only schema overlay on top of verisc |
| `NEWS.md` | Schema/process changelog (most recent entry documents 2018 pipeline change) |
| `campaigns.md`, `keynames-real.txt`, `vcdb-keynames-real.txt` | Curator references |

Sister schema repo `vz-risk/veris` (commit `cbe9bd7`):

| Path | Contents |
| --- | --- |
| `verisc.json` | Core VERIS schema (46 KB) |
| `verisc-enum.json` | Enumerated value lists for each schema field |
| `verisc-merged.json` | Schema + enums combined |
| `verisc-labels.json` | Human-readable labels |
| `changelog` | Schema-version history |

## VERIS schema fields relevant to τ calibration

All citations below: file path within `vz-risk/veris@cbe9bd7`, line numbers in the deserialized file.

| Field | Type | Definition (verbatim from schema) | Source (path + line) |
| --- | --- | --- | --- |
| `timeline.incident` | object | "When did this incident initially occur?" — required `year` plus optional `month`/`day`/`time` | `verisc.json` lines 36-65 |
| `timeline.compromise` | object `{unit, value}` | "How long from the first action to the first compromise of an attribute?" | `verisc.json` lines 66-81 |
| `timeline.exfiltration` | object `{unit, value}` | "How long from initial compromise to first known data exfiltration?" | `verisc.json` lines 82-97 |
| `timeline.discovery` | object `{unit, value}` | "How long from compromise until the incident was discovered by the victim organization?" | `verisc.json` lines 98-113 |
| `timeline.containment` | object `{unit, value}` | "How long did it take the organization to contain the incident once it was discovered?" | `verisc.json` lines 114-131 |
| `action` (top-level keys = attack class) | object | One or more of `hacking`, `malware`, `social`, `error`, `misuse`, `physical`, `environmental`, `unknown` | `verisc-enum.json` lines (action.*) |

Enumeration values for every `timeline.*.unit` field (compromise, exfiltration, discovery, containment), `verisc-enum.json` lines 14-71:

| Allowed value | Meaning |
| --- | --- |
| `Seconds` | Numeric `value` is seconds |
| `Minutes` | Numeric `value` is minutes |
| `Hours` | Numeric `value` is hours |
| `Days` | Numeric `value` is days |
| `Weeks` | Numeric `value` is weeks |
| `Months` | Numeric `value` is months |
| `Years` | Numeric `value` is years |
| `Never` | Bucketed sentinel — never occurred (e.g., not contained, or not exfiltrated) |
| `NA` | Field not applicable to this incident |
| `Unknown` | Curator could not determine from public disclosure |

**Implications for τ calibration:** the `Never` / `NA` / `Unknown` sentinels are populated in roughly 25-65% of the records that have any timeline data at all (per Farhang & Grossklags 2017 figures cited below) — any τ derivation against VCDB must explicitly handle these as censored/missing rather than as zero or as the longest finite value.

## Action-class buckets in VERIS (the seven attack classes)

From `verisc-enum.json`, `action` object top-level keys. These are the categorical buckets a v3 per-class median table would be keyed on, when local-clone analysis becomes in-scope.

| `action.*` class | Sub-keys present |
| --- | --- |
| `hacking` | `variety`, `vector`, `result` |
| `malware` | `variety`, `vector`, `result` |
| `social` | `variety`, `vector`, `target`, `result` |
| `error` | `variety`, `vector` |
| `misuse` | `variety`, `vector`, `result` |
| `physical` | `variety`, `vector`, `result` |
| `environmental` | `variety` |
| `unknown` | `result` |

An incident may have multiple action classes simultaneously (e.g., `social` plus `malware` for phishing-delivered malware), so per-class aggregation must count an incident once per class it belongs to.

## Published aggregate analyses (accessible via WebFetch)

VCDB itself does NOT publish a maintained aggregate analysis of timeline medians. The verisframework.org definitions page is purely definitional. Aggregate analyses live in:

| Publication | Year | What it reports re: VCDB timelines | Cite |
| --- | --- | --- | --- |
| Verizon DBIR (annual) | 2008-present | DBIR is the canonical Verizon-published aggregate analysis of VERIS-encoded incidents (VCDB + non-public partner data). The DBIR "Breach Detection Deficit" figure (e.g., 2020 DBIR Fig. 12) is the most cited time-to-discovery visualization. Researched separately under task T0b. | [verizon.com/business/resources/reports/dbir/](https://www.verizon.com/business/resources/reports/dbir/) |
| Farhang & Grossklags, "When to Invest in Security?" | 2017 | Empirical derivation of detection-time and containment-time distributions from VCDB. Reports: 150 VCDB entries with `timeline.discovery` populated; 258 entries with `timeline.containment` non-empty, 175 after filtering Unknown/NA, 59 with specific numeric values. Focused on `malware` (439 entries) and `hacking` (1,655 entries) action classes. VCDB snapshot date not extractable from the abstract page — full-PDF text extraction failed via WebFetch (binary PDF stream); the abstract confirms "we are able to derive distributions for some of the factors regarding the timing of security breaches" and assesses VERIS timing data collection as "insufficient." | [arxiv.org/abs/1706.00302](https://arxiv.org/abs/1706.00302) (paper); WebSearch summary 2026-05-15 for the figure counts |
| HALOCK "Assessing Cyber Risks Using Verizon's VCDB" | 2025 (footer date 2025-06-26) | Describes VCDB ("more than 10,000 records … 2,500 columns") in context of HALOCK's HIT Index methodology. Does NOT provide per-class timeline medians. Refers readers to DBIR Appendix D. | [halock.com/assessing-cyber-risks-using-verizons-vcdb/](https://www.halock.com/assessing-cyber-risks-using-verizons-vcdb/) |
| verisframework.org Discovery & Response page | undated | Purely definitional. Explains `time to compromise`, `time to exfiltration`, `time to discovery`, `time to containment` as offsets from "initial compromise." Recommends using days rather than weeks/months/years for precision. No medians. | [verisframework.org/discovery.html](https://verisframework.org/discovery.html) |

## Per-attack-class median table (accessible aggregates only)

The honest table. VCDB's canonical repository publishes ZERO per-attack-class timeline medians directly — all such tables in the wild are derived analyses (DBIR, Farhang & Grossklags, academic). Within scope for this WebFetch-only research pass, the only per-class values reachable are:

| Attack class | Metric | Value | N (entries with metric) | Source |
| --- | --- | --- | --- | --- |
| `hacking` | Total VCDB entries with class tag | 1,655 | — | Farhang & Grossklags 2017, per [WebSearch summary 2026-05-15](https://www.google.com/search?q=VCDB+timeline+discovery+containment+median+attack+class+analysis) |
| `malware` | Total VCDB entries with class tag | 439 | — | Farhang & Grossklags 2017, per WebSearch summary 2026-05-15 |
| (all classes) | Entries with `timeline.discovery` populated | — | 150 | Farhang & Grossklags 2017, per WebSearch summary 2026-05-15 |
| (all classes) | Entries with `timeline.containment` non-empty | — | 258 (raw) / 175 (after Unknown/NA filter) / 59 (specific numeric values) | Farhang & Grossklags 2017, per WebSearch summary 2026-05-15 |

**Sanity check against the live corpus (commit `5a64739`):** two `data/json/validated` records sampled at random (`0008DADB-E83D-4278-A19A-CEE01610CF43.json` action=`physical`, `000D403E-2DC9-4EA7-9294-BD3938D1C3C7.json` action=`error`) both have `timeline = {"incident": {"year": 2014}}` — the compromise/discovery/containment sub-objects are absent. This is consistent with the F&G 2017 finding that timeline-detail population is sparse and explains why no maintained per-class median table is publicly republished.

**Median values are NOT included in this table because none are published as VCDB-source-of-truth aggregates at the access methods in scope (WebFetch + gh api).** Any per-class medians a v3 calibration needs must come from (a) DBIR (separate task), or (b) a local-clone aggregation pass against `data/csv/vcdb.csv.zip` deferred per scope.

## Out of scope for this research pass

- **Local-clone aggregation.** Computing per-attack-class medians for `timeline.discovery`, `timeline.containment`, `timeline.compromise`, `timeline.exfiltration` against the current snapshot requires either (a) downloading `data/csv/vcdb.csv.zip` (15 MB) and computing on the joined CSV, or (b) iterating all `data/json/validated/*.json` files via local clone. Both exceed the WebFetch-only scope of this τ research pass. Deferred for future per-org override calibration when a v3 user opts into VCDB-derived τ.
- **Farhang & Grossklags 2017 full-paper extraction.** Their per-class distribution figures are reported only inside the arxiv PDF (`/pdf/1706.00302`), which did not parse cleanly via WebFetch (binary PDF stream). The abstract page yielded only narrative summary, not table data. The sample-size figures (150 / 258 / 175 / 59 / 1,655 / 439) cited above come from a WebSearch-result summary, not direct extraction from the paper. A future pass with proper PDF text extraction (e.g., `pdftotext` on a downloaded copy) is needed for the actual median values.
- **Time-normalization of `unit` field values.** Curators may record `2 Months` versus `60 Days` versus `0.16 Years` for the same elapsed duration; verisframework.org/discovery.html recommends sub-day precision but does not mandate it. Any local-clone aggregation must normalize all `(value, unit)` pairs to a common base unit before computing medians.
- **Sub-source filtering.** The README explicitly flags healthcare (`phidbr`) and priority incidents as non-randomly sampled. A bias-corrected per-class median computation must filter `plus.sub_source != "phidbr"` and `plus.sub_source != "priority"`, or weight inversely. Out of scope for this pass.

## Known anomalies / errata

- **License is NOASSERTION.** GitHub does not recognize the LICENSE.txt under VCDB as a known open-source license — practical usage allowance is therefore ambiguous. Cite the file rather than relying on assumed CC-BY or similar. Source: `gh api repos/vz-risk/VCDB/license` accessed 2026-05-15.
- **Validated-JSON listing capped at 1000 by GitHub API.** `gh api repos/vz-risk/VCDB/contents/data/json/validated` returns exactly 1000 entries per page and pagination is non-trivial; total file count requires clone or repo tarball. Repo total size 1.58 GB suggests substantially more validated records than 1000.
- **2018 pipeline split.** Pre-2018 VCDB was quarterly+manual; post-2018 it's continuous+automated with the `submitted`/`validated`/`overridden` directory split. Records before the cutoff may have different validation rigor than records after. `NEWS.md` 2018-11-27 entry documents the transition.
- **Sampling-correction table is stale.** The README's bias-correction composition table is dated "as of Jan 13, 2018" — no updated composition figures have been added in the 8 years since. Any 2026-era bias correction must be re-derived from the current corpus.
- **`Never` ≠ infinity.** The unit-enum `Never` value (e.g., `containment.unit == "Never"`) is a sentinel for never-contained; numerically substituting a large finite value (say, `9999 Days`) would skew medians. Always treat as a censored observation in τ-distribution fits.

## When this source informs an overlay or calibration override

- **Canonical τ for ELAPSED_TIME sub-functions:** NOT used as a canonical-layer source for v3 phase 1 — see "Why this is reference-only" above. Schema vocabulary used for v3-internal incident-event encoding when v3 grows an incident-history feature.
- **Benchmark doc cells:** None at this time. If a v3 user later opts into VCDB-derived per-class detection-time medians as a per-org override, those values would need to come from a local-clone aggregation pass (out of scope) — populate this section then.
- **Overlays:** None. VCDB's sampling bias (healthcare oversample) makes it unsuitable as the source of a cross-cutting industry-overlay multiplier without explicit re-weighting.
- **Bidirectional citation:** No overlay or override currently lists this file in its `sources` field. Update when (and if) one does.
