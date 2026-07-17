"""v3 IndustryType ↔ fair_cam IndustryType translation map.

Both use NAICS-2-aligned values since Phase 1.5a D2 alignment fix. The
mapping is intentionally hand-curated rather than derived: a KeyError
here is the signal that an industry needs an explicit decision (closest
NAICS-2? new fair_cam member? reject?).

Lifted out of services/scenario_calibration.py in PR π F5b so the map
survives the F6 deletion of the calibration runtime. Consumed by:
- routes/scenario_form_helpers.py (drives INDUSTRY_CHOICES)
- routes/scenarios.py (reverse-maps org.industry_type → v3 slug)
- services/wizard_helpers.py (PR π F7 — IRIS pre-fill helper)
"""

from __future__ import annotations

from fair_cam.parameters.industry_calibration import IndustryType as FairCamIndustryType

V3_TO_FAIR_CAM_INDUSTRY: dict[str, FairCamIndustryType] = {
    "manufacturing": FairCamIndustryType.MANUFACTURING,
    "healthcare": FairCamIndustryType.HEALTHCARE,
    "financial": FairCamIndustryType.FINANCIAL,
    "information": FairCamIndustryType.INFORMATION,
    "utilities": FairCamIndustryType.UTILITIES,
    "retail": FairCamIndustryType.RETAIL,
    "public": FairCamIndustryType.PUBLIC,
    "education": FairCamIndustryType.EDUCATION,
    "other": FairCamIndustryType.OTHER,
}
