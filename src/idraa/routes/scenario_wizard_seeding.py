"""Wizard SME-row seeding helpers for :mod:`idraa.routes.scenarios`.

Extracted out of ``routes/scenarios.py`` under issue #119 (standing architect
advisory — the file crossed 4,600 lines). Mechanical extraction, NO semantic
change: every function here is byte-identical to its prior home in
``routes/scenarios.py``, just re-homed. ``routes/scenarios.py`` re-imports
these names for its own call sites (the wizard step-1 GET/POST handlers and
the step-3/4 first-visit eager-seed block in ``get_wizard_step``).

These are the functions that build ``state.sme_estimates`` seed rows —
i.e. "seed the wizard's FAIR-param rows from a source" — as distinct from
the wizard step ROUTE handlers themselves (which stay in
``routes/scenarios.py`` per the extraction seam: only helpers move, not
routes, for this seam):

- :func:`_iris_seed_rows` — seed rows from the IRIS industry-baseline
  quantile-pair dict.
- :func:`_library_seed_rows` — seed rows from a library entry's curated
  distributions already loaded onto ``WizardState``.
- :func:`_seed_state_from_library_entry` — shared seeder that resolves a
  library entry, calibrates its FAIR params, and stamps the wizard state's
  scalar + distribution fields (called from both the GET deep-link handler
  and the POST step-1 handler so both paths produce byte-identical seeds).
"""

from __future__ import annotations

import math
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.organization import Organization
from idraa.routes.scenario_loss_pin import _Z_0_95
from idraa.services.calibration import WITHIN_SCENARIO_SIGMA_DEFAULT
from idraa.services.library_calibration import library_calibrated_pre_fill
from idraa.services.run_executor import _dict_to_fair_distribution
from idraa.services.scenario_library import ScenarioLibraryService
from idraa.services.wizard_helpers import _quantile_pair
from idraa.services.wizard_state import WizardState


def _iris_seed_rows(
    iris_form: dict[str, dict[str, float] | None],
    iris_sme_id: str,
) -> dict[str, list[dict[str, Any]]]:
    """Build ``state.sme_estimates`` from an IRIS quantile-pair dict.

    MD-7: IRIS pre-fill REPLACES current rows with a single IRIS-attributed
    row per fieldset. Fieldsets where ``iris_form`` returned ``None``
    (missing data, unsupported distribution_type) are omitted from the
    output so the UI renders them empty rather than as ``(0, 0)``.

    Task 5 (plan 2026-07-25-sigma-recal-pr1): narrow-only re-spread on the
    ``pl``/``sl`` pairs ONLY -- never ``tef``/``vuln``. The IRIS industry
    baseline's PL/SL quantile pair carries the same mis-applied cross-firm
    envelope dispersion Tasks 2-3 re-authored out of the library and
    scenario tables; the wizard's OWN IRIS-seeding path needed the same
    fix so it stops re-introducing the contamination on every new scenario.

    Signature is UNCHANGED (no ``loss_shape`` parameter) -- capped and
    catastrophic shapes converge on the same seeded range (both target
    ``WITHIN_SCENARIO_SIGMA_DEFAULT``), so this fix covers both before
    ``loss_shape`` is even known (it is only assigned on the step-4 POST).

    NARROW-ONLY: a pair whose implied sigma is already <= the default is
    left untouched -- this is what auto-excludes the D10' AGRICULTURE/
    MINING-class IRIS priors (already narrower than the default) with no
    hardcoded exclusion list to drift.
    """
    out: dict[str, list[dict[str, Any]]] = {}
    for fs in ("tef", "vuln", "pl", "sl"):
        pair = iris_form.get(fs)
        if not pair:
            continue
        low, high = pair["low"], pair["high"]
        if fs in ("pl", "sl") and low > 0 < high:
            # Canonical z (NOT _quantile_pair's truncated 1.645, above) --
            # the flow test's pytest.approx(1.7) fails ~8.9e-6 off under the
            # truncated constant.
            s = math.log(high / low) / (2 * _Z_0_95)
            if s > WITHIN_SCENARIO_SIGMA_DEFAULT:
                # Re-emit around the geometric midpoint at the default --
                # valid because _quantile_pair returns symmetric log-
                # quantiles, so the geometric midpoint IS the prior's
                # median (verified round 3, N-M1).
                mid = math.sqrt(low * high)
                low = mid * math.exp(-_Z_0_95 * WITHIN_SCENARIO_SIGMA_DEFAULT)
                high = mid * math.exp(_Z_0_95 * WITHIN_SCENARIO_SIGMA_DEFAULT)
        out[fs] = [{"sme_id": iris_sme_id, "low": low, "high": high}]
    return out


def _library_seed_rows(
    state: WizardState,
    library_sme_id: str,
) -> dict[str, list[dict[str, Any]]]:
    """Build ``state.sme_estimates`` from a library entry's CURATED distributions
    (#wizard-library-prefill).

    Mirrors ``_iris_seed_rows`` but sources each fieldset's {low, high} p5/p95
    pair from the entry's own distribution (``state.threat_event_frequency`` /
    ``vulnerability`` / ``primary_loss`` / ``secondary_loss``, seeded by
    ``_seed_state_from_library_entry``) via the SAME analytic ``_quantile_pair``
    extractor the IRIS path uses — so a library-derived scenario carries the
    archetype's threat-specific values instead of the threat-blind IRIS baseline.

    Fieldsets whose curated dict is empty/None, or whose distribution_type
    ``_quantile_pair`` cannot handle, are omitted (render-empty contract,
    matching ``_iris_seed_rows`` + its ``_safe`` swallow)."""
    fieldset_dists: list[tuple[str, dict[str, Any] | None]] = [
        ("tef", state.threat_event_frequency),
        ("vuln", state.vulnerability),
        ("pl", state.primary_loss),
        ("sl", state.secondary_loss),
    ]
    rows: dict[str, list[dict[str, Any]]] = {}
    for fs, dist_dict in fieldset_dists:
        if not dist_dict:
            continue
        try:
            pair = _quantile_pair(_dict_to_fair_distribution(dist_dict))
        except (ValueError, KeyError, TypeError, ArithmeticError):
            # Malformed/degenerate curated dist (bad type, None value, or an
            # OverflowError from a pathological lognormal) → omit the fieldset,
            # matching the IRIS `_safe` render-empty contract + the #306
            # finite-guard philosophy. Unreachable for real (finite-validated)
            # library entries; defense-in-depth.
            continue
        rows[fs] = [{"sme_id": library_sme_id, "low": pair["low"], "high": pair["high"]}]
    return rows


async def _seed_state_from_library_entry(
    db: AsyncSession,
    state: WizardState,
    entry_id: uuid.UUID,
    org_row: Organization,
) -> str:
    """Shared seeder: resolve entry, calibrate FAIR params, stamp scalar fields.

    Called from BOTH the GET deep-link handler AND the POST step-1 handler so
    the two paths produce byte-identical state seeds.  This is a mechanical
    extraction of the inline block that previously lived only in
    ``post_wizard_step_1`` (~lines 969-1011); the calibration math and field
    assignment order are unchanged.

    Returns the resolved entry's name so callers can use it for name-update
    logic (e.g. the POST path prepends it to the scenario name).

    The caller is responsible for:
    - raising HTTP 404 if the entry doesn't exist / isn't published (done
      differently in GET vs POST callers — GET degrades gracefully, POST
      raises HTTPException 404).
    - persisting state (advance_step + db.commit).

    Raises LibraryEntryNotFoundError / LibraryEntryStatusError so callers can
    translate to the appropriate HTTP response.
    """
    svc = ScenarioLibraryService(db)
    resolved = await svc.resolve_for_clone(
        entry_id=entry_id,
        organization_id=org_row.id,
    )

    state.library_entry_id = str(resolved.entry.id)
    state.library_entry_version = resolved.entry.version
    state.override_id = str(resolved.override.id) if resolved.override else None
    state.override_version = resolved.override.version if resolved.override else None

    # Org revenue-tier loss scaling was removed 2026-07-07 — the IRIS sector
    # envelope IS the calibration; PL/SL are entry-absolute here. TEF/Vuln stay
    # archetype-curated; controls modulate risk at MC time. No calibration
    # metadata is computed or stashed (no banner).
    form_dict, _calibration_metadata = library_calibrated_pre_fill(
        resolved.entry, resolved.override
    )
    state.threat_event_frequency = form_dict["tef"]
    state.vulnerability = form_dict["vuln"]
    state.primary_loss = form_dict["pl"]
    state.secondary_loss = form_dict["sl"]

    # Pre-fill step-2 scalar fields from canonical entry.
    state.threat_category = resolved.entry.threat_event_type.value
    state.threat_actor_type = resolved.entry.threat_actor_type.value
    state.asset_class = resolved.entry.asset_class.value
    state.attack_vector = resolved.entry.attack_vector

    # Milestone B (#loss-pert-overhaul): seed the scenario-level loss shape
    # from the entry's curated class; the analyst can override via the step-4
    # toggle.
    state.loss_shape = resolved.entry.loss_shape

    return resolved.entry.name
