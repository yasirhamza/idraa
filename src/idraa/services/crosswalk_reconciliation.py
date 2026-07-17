"""Map the FAIR Institute spreadsheets' FAIR-CAM function column labels to the
codebase FairCamSubFunction enum. Quirks handled HERE, never by editing source:
the "Event Termintion" typo (both sheets), and the NIST "Threat Capability Intel"
vs CIS "Threat Intel" split for the same VMC Identification function.

Methodology: this mapping is the crosswalk's credibility. Every non-virtual
FairCamSubFunction must be reachable; the virtual DSC_CORR_MISALIGNED must NOT.
Pinning-tested in tests/services/test_crosswalk_reconciliation.py."""

from __future__ import annotations

import re

from idraa.models.enums import FairCamSubFunction as F

_WS = re.compile(r"\s+")


def normalize_label(raw: str) -> str:
    return _WS.sub(" ", raw).strip().lower()


SPREADSHEET_LABEL_TO_SUBFUNCTION: dict[str, F] = {
    # LEC
    "avoidance": F.LEC_PREV_AVOIDANCE,
    "deterrence": F.LEC_PREV_DETERRENCE,
    "resistance": F.LEC_PREV_RESISTANCE,
    "visibility": F.LEC_DET_VISIBILITY,
    "monitoring": F.LEC_DET_MONITORING,
    "recognition": F.LEC_DET_RECOGNITION,
    "event termintion": F.LEC_RESP_EVENT_TERMINATION,  # sic — sheet typo
    "event termination": F.LEC_RESP_EVENT_TERMINATION,  # defensive
    "resilience": F.LEC_RESP_RESILIENCE,
    "loss reduction": F.LEC_RESP_LOSS_REDUCTION,
    # VMC
    "reduce chg freq": F.VMC_PREV_REDUCE_CHANGE_FREQ,
    "reduce change frequency": F.VMC_PREV_REDUCE_CHANGE_FREQ,
    "reduce var prob": F.VMC_PREV_REDUCE_VARIANCE_PROB,
    "reduce variance probability": F.VMC_PREV_REDUCE_VARIANCE_PROB,
    "threat capability intel": F.VMC_ID_THREAT_INTELLIGENCE,  # NIST label
    "threat capability intelligence": F.VMC_ID_THREAT_INTELLIGENCE,
    "threat intel": F.VMC_ID_THREAT_INTELLIGENCE,  # CIS label (gate M1)
    "controls monitoring": F.VMC_ID_CONTROL_MONITORING,
    "treatment selection & prioritization": F.VMC_CORR_TREATMENT_SELECTION,
    "treatment selection and prioritization": F.VMC_CORR_TREATMENT_SELECTION,
    "implementation": F.VMC_CORR_IMPLEMENTATION,
    # DSC — Prevention (incl. Situational Awareness leaves) + Identify
    "define exp's & obj's": F.DSC_PREV_DEFINED_EXPECTATIONS,
    "communicate exp's & obj's": F.DSC_PREV_COMMUNICATION,
    "asset": F.DSC_PREV_SA_DATA_ASSET,  # Provide Situational Awareness > Provide Data > Asset
    "threat": F.DSC_PREV_SA_DATA_THREAT,
    "controls": F.DSC_PREV_SA_DATA_CONTROLS,
    "analysis": F.DSC_PREV_SA_ANALYSIS,
    "reporting": F.DSC_PREV_SA_REPORTING,
    "ensure capability": F.DSC_PREV_ENSURE_CAPABILITY,
    "incentives": F.DSC_PREV_INCENTIVES,
    "identify misaligned decisions": F.DSC_ID_MISALIGNED,
}


def resolve_label(raw: str) -> F:
    key = normalize_label(raw)
    try:
        return SPREADSHEET_LABEL_TO_SUBFUNCTION[key]
    except KeyError as exc:
        raise KeyError(f"unmapped FAIR-CAM column label: {raw!r} (normalized {key!r})") from exc
