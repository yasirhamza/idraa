# FAIR-CAM controls library — annual cost defaults (methodology)

The `Annual cost (USD)` column in `fair-cam-controls-library.csv` ships with
bucketed order-of-magnitude defaults so a fresh import produces a usable
cost-aware run without forcing operators to hand-edit 61 controls before
the first UAT (issue #65).

These are NOT per-vendor priced quotes. Treat each value as a starting point
that ops MUST override per environment. The buckets reflect category-level
order-of-magnitude OPEX (vendor licence + carry cost for required staff time);
they do not amortise capex, integration, or training.

## Buckets

| Bucket               | USD/yr  | Coverage                              | Count |
|----------------------|---------|---------------------------------------|-------|
| Admin — light        | 15,000  | Policies, awareness, annual programs  | 11    |
| Admin — heavy        | 60,000  | Operational programs with FTE         | 7     |
| Admin — special      | 150,000 | Analyst FTE / insurance premium       | 2     |
| Technical — infra    | 30,000  | Table-stakes networking + endpoint    | 15    |
| Technical — harden.  | 50,000  | Hardening / coding / posture          | 10    |
| Technical — detect.  | 100,000 | EDR/SIEM/SOAR class — vendor + ops    | 13    |
| Technical — engmt.   | 60,000  | Pen-test / red-team / purple-team     | 3     |

61 controls total. The CSV ships with 6 distinct dollar values — "Admin —
heavy" and "Technical — engagement" both land at 60,000 since the
ballpark OPEX of a full-time program-coordinator FTE and a recurring
annual pen-test / red-team retainer turn out comparable for a mid-sized
org. They are kept as separate conceptual buckets because the override
levers differ (FTE allocation vs. vendor-engagement scope). Distribution is intentionally tilted toward
technical-infra and technical-detection — that matches the typical
small-to-mid enterprise security spend mix.

## How to override

- **Per-control via UI:** edit `Control.annual_cost` on the control's
  detail page. Persists to the org's DB.
- **Per-environment via CSV:** edit the CSV before import. Empty or
  missing cell → 0; non-numeric → 0 (with importer warning); negative →
  row skipped.

## Why bucketed defaults rather than per-vendor research

Per-control vendor pricing varies by deployment scale, contract length,
and integration depth — easily 10× between a 200-seat org and a 50,000-
seat org. Pinning a single per-control number to a public quote would
imply false precision. Buckets keep the defaults defensible and force
the override conversation early.

Refining these defaults with real procurement data is a follow-up that
should be scoped per org during the cost-aware-reporting phase, not
checked into a one-size-fits-all library.
