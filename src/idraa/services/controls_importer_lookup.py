"""FAIR-CAM CSV sub-function path → FairCamSubFunction lookup (issue #68).

Strict dict lookup against lowercased + whitespace-normalized path
strings. The canonical CSV at docs/reference/fair-cam-controls-library.csv
was cleaned (#68 step 1) to use one canonical phrasing per sub-function
— no fuzzy matching, no synonym table.

VIRTUAL_REJECT is returned for paths that name the virtual
DSC_CORR_MISALIGNED sub-function (enums.py — no Control may be assigned
to it). Callers must skip the assignment + log warning.

Coverage of the canonical CSV is enforced by
tests/unit/test_controls_importer_lookup.py — adding a new path string
to the CSV without updating this table fails CI.

NOTE: ControlDomain decoding lives in idraa.models.enums
(``subfunction_to_domain``) — pure enum decoder, not importer plumbing.
Import it from there if you need primary-domain derivation.
"""

from __future__ import annotations

import re
from typing import Final

from idraa.models.enums import FairCamSubFunction


class VirtualRejectSentinel:
    """Singleton sentinel for the virtual DSC_CORR_MISALIGNED path.

    Compare with ``is``. Public so it appears in ``dict`` value type
    annotations without exposing a private class.
    """

    __slots__ = ()

    def __repr__(self) -> str:
        return "VIRTUAL_REJECT"


VIRTUAL_REJECT: Final = VirtualRejectSentinel()


_WHITESPACE_RUN = re.compile(r"\s+")


def normalize_path(raw: str) -> str:
    """Lowercase + collapse interior whitespace + strip."""
    return _WHITESPACE_RUN.sub(" ", raw).strip().lower()


# Keys are pre-normalized (lowercased + collapsed-whitespace).
PATH_TO_SUB_FUNCTION: Final[dict[str, FairCamSubFunction | VirtualRejectSentinel]] = {
    # LEC (9)
    "lec - prevention - avoidance": FairCamSubFunction.LEC_PREV_AVOIDANCE,
    "lec - prevention - deterrence": FairCamSubFunction.LEC_PREV_DETERRENCE,
    "lec - prevention - resistance": FairCamSubFunction.LEC_PREV_RESISTANCE,
    "lec - detection - visibility": FairCamSubFunction.LEC_DET_VISIBILITY,
    "lec - detection - monitoring": FairCamSubFunction.LEC_DET_MONITORING,
    "lec - detection - recognition": FairCamSubFunction.LEC_DET_RECOGNITION,
    "lec - response - event termination": FairCamSubFunction.LEC_RESP_EVENT_TERMINATION,
    "lec - response - resilience": FairCamSubFunction.LEC_RESP_RESILIENCE,
    "lec - response - loss reduction": FairCamSubFunction.LEC_RESP_LOSS_REDUCTION,
    # VMC (5)
    "vmc - prevention - reduce change frequency": FairCamSubFunction.VMC_PREV_REDUCE_CHANGE_FREQ,
    "vmc - prevention - reduce variance probability": FairCamSubFunction.VMC_PREV_REDUCE_VARIANCE_PROB,
    "vmc - identification - threat capability monitoring": FairCamSubFunction.VMC_ID_THREAT_INTELLIGENCE,
    "vmc - identification - control monitoring": FairCamSubFunction.VMC_ID_CONTROL_MONITORING,
    "vmc - correction - implementation": FairCamSubFunction.VMC_CORR_IMPLEMENTATION,
    # DSC (8 non-virtual)
    "dsc - prevent misaligned decisions - define expectations and objectives": FairCamSubFunction.DSC_PREV_DEFINED_EXPECTATIONS,
    "dsc - prevent misaligned decisions - communicate expectations and objectives": FairCamSubFunction.DSC_PREV_COMMUNICATION,
    "dsc - prevent misaligned decisions - provide situational awareness - provide data - provide asset data": FairCamSubFunction.DSC_PREV_SA_DATA_ASSET,
    "dsc - prevent misaligned decisions - provide situational awareness - provide data - provide threat data": FairCamSubFunction.DSC_PREV_SA_DATA_THREAT,
    "dsc - prevent misaligned decisions - provide situational awareness - provide data - provide control data": FairCamSubFunction.DSC_PREV_SA_DATA_CONTROLS,
    "dsc - prevent misaligned decisions - provide situational awareness - analysis": FairCamSubFunction.DSC_PREV_SA_ANALYSIS,
    "dsc - prevent misaligned decisions - provide situational awareness - reporting": FairCamSubFunction.DSC_PREV_SA_REPORTING,
    "dsc - prevent misaligned decisions - incentives": FairCamSubFunction.DSC_PREV_INCENTIVES,
    # Virtual (1)
    "dsc - correct misaligned decisions": VIRTUAL_REJECT,
}
