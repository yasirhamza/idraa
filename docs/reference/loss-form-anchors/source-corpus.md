# Loss-form source corpus (Epic D-ii-a companion)

Human-readable rendering of `data/loss_form_source_corpus.json` — the repo-internal
sources that carry a FAIR loss-**form** magnitude, grouped by form. This is a
curation artifact (not runtime seed data). See `data/loss_form_source_corpus.README`
for the row schema, the blended-double-pull rule, the Sec-I1 https-allowlist
tripwire, and the forbidden-cite list.

The repo's form-level loss evidence is deliberately thin — most forms have **no**
clean per-sector source. That is the honest D-ii-a state and the reason D-ii-b's
fresh (sector × form) sweep exists.

## response

- **IBM Cost of a Data Breach 2025** — *paginated* (page-6 key findings +
  methodology page-57, the 4 activity-based cost centers). Carries breach
  **response** as 3 of the 4 cost centers: detection & escalation + notification +
  post-breach response. **Global**, not per-sector — the per-sector response
  dollar split is a D-ii-b re-confirmation target. Covers all 13 core sectors
  coarsely. `single_form_slice`: response cost centers (NOT lost-business).

## productivity

- **IBM Cost of a Data Breach 2025** — *paginated*, **blended**. The
  **lost-business** cost center (business disruption + churn). `blended_headline`:
  it blends productivity + reputation; catalogued here for productivity only
  (`single_form_slice` distinct from the response row).
- **Cyentia IRIS 2025** — *paginated* (Figure A3, p. 35), **blended**. Per-sector
  TOTAL loss (p50 + p95) for 12 sectors (all but food_agriculture); already the
  Epic C aggregate anchor. Here it is the productivity/BI-dominant slice of the
  blended sector total — NOT additively reusable for a separate response pull.

## replacement

- *none* — no repo source for asset/equipment/data replacement magnitude. D-ii-b
  research target (OT/ICS equipment replacement for energy/manufacturing; data
  reconstruction cost).

## fines

- *none* — no repo source for regulatory **fines & judgments** magnitude. (IC3
  2025 carries direct fraud/BEC loss, which excludes productivity/BI and is not a
  single FAIR form — it is not catalogued as a fines source.) D-ii-b research
  target (HIPAA / GDPR / state-AG settlement schedules).

## competitive_advantage

- *none* — no clean repo source for IP / trade-secret loss magnitude. The two
  existing IP-exfil seed entries cite the gap-report **forbidden** mis-cites and
  must not be back-filled from. D-ii-b research target (adjudicated trade-secret
  damages).

## reputation

- *none* — no standalone repo reputation-loss source (CODB lost-business blends it
  into productivity). D-ii-b research target (churn / brand studies per sector).
