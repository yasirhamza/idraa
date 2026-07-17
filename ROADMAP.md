# Idraa — Roadmap

_Refreshed at the public seed, 2026-07-17. The detailed backlog is tracked in the
project's private development archive; this file gives the public shape of where
the product is and where it is going._

## Where it is

MVP shipped and in production UAT. Delivered beyond MVP:

- Native Monte Carlo FAIR engine (pyfair removed), full sample persistence,
  VaR/ES tail ladder, loss-exceedance analytics
- Curated 102-entry scenario library — primary-cited FAIR distributions
  (IRIS sector anchors), OT/ICS scenarios first-class, three-tier provenance,
  per-org override layer with versioning + audit
- FAIR-CAM control modeling with NIST CSF / CIS v8 crosswalks and
  MITRE ATT&CK mappings
- Per-control Shapley attribution and if-removed (leave-one-out) analysis
- Weight-robustness ensembles (logit-normal perturbation ranges)
- Enterprise PDF reports + verification workbooks (Excel)
- First-party server-rendered SVG charts (no charting dependency)
- Mobile-responsive UI; multi-currency support

## Where it is going

- Qualitative risk-register converter — an on-ramp that drafts FAIR scenarios
  from qualitative registers (priors for review, never auto-final)
- Supply-chain security posture hardening (SCA gate, SBOM, digest pinning)
- Continued library expansion and calibration refresh cycles
- Launch: licensing decision, custom domain, public issue tracking

Security-relevant hardening items are tracked privately until released; see
[SECURITY.md](SECURITY.md) for how to report a vulnerability.
