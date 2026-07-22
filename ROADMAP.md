# Idraa — Roadmap

_Refreshed 2026-07-23. The detailed backlog lives in this repository's public
issue tracker; this file gives the public shape of where the product is and
where it is going._

## Where it is

MVP shipped and in production UAT. Delivered beyond MVP:

- Native Monte Carlo FAIR engine (pyfair removed), full sample persistence,
  VaR/ES tail ladder, loss-exceedance analytics
- Curated 102-entry scenario library — primary-cited FAIR distributions
  (IRIS sector anchors), OT/ICS scenarios first-class, three-tier provenance,
  per-org override layer with versioning + audit
- Qualitative risk-register converter — drafts FAIR scenarios from an existing
  qualitative register as priors for analyst review (never auto-final)
- Multi-SME elicitation with mixture-pooled estimates and guided re-estimation
- FAIR-CAM control modeling with NIST CSF / CIS v8 crosswalks, MITRE ATT&CK
  mappings, and an ATT&CK coverage view
- Per-control Shapley attribution and if-removed (leave-one-out) analysis,
  with control values reported as weight-robustness ranges (logit-normal
  perturbation), never single points
- Strong authentication — passkeys (WebAuthn) + TOTP MFA for every user, with
  step-up re-authentication for sensitive operations
- Evaluation self-hosting — Docker Compose quickstart, `.env.example` operator
  reference, fail-loud production boot guards
- Enterprise PDF reports + independent in-Excel verification workbooks
- First-party server-rendered SVG charts (no charting dependency)
- Mobile-responsive UI with an installable web-app manifest; multi-currency
  support; product design language (graphite palette, sonar-arcs identity)
- Supply-chain gates in CI — dependency review, secret scanning, and workflow
  SAST behind branch protection; CycloneDX SBOM generated on every merge to
  main

## Where it is going

- Register on-ramp, phase 2 — assisted mapping from qualitative register rows
  to library archetypes
- Generalized enterprise risk — extending the FAIR chassis beyond
  information/OT risk
- Elicitation depth — SME self-elicitation sessions, calibration training with
  weighted opinion pooling, ordinal-label (VL–VH) input mode, live
  distribution previews during elicitation
- Analytics — what-if / sensitivity analysis via scenario cloning, bootstrap
  confidence bands on loss-exceedance curves
- Calibration refresh cycles — IC3 2025 anchors, ATT&CK-gap library
  candidates, ISO 27001 (Annex A) crosswalk
- SBOM publication + attestation (SLSA provenance)
- Launch: licensing decision

Security-relevant hardening items are tracked privately until released; see
[SECURITY.md](SECURITY.md) for how to report a vulnerability.
