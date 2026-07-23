"""repair divergent-fitter scenario distribution nodes (audit F1)

Until #336 the wizard fit tef/pl/sl via ``fit_lognorm_trunc`` (Nelder-Mead
from a fixed ``x0=[0.01, 1.0]``), which DIVERGES for dollar-scale anchors —
the stored PERT collapse then carried garbage (prod signature:
``PERT(low=5.26e-08, mode=5.26e-08, high=20158366)`` where the analyst
entered ``(388920, 20158367)``; ~5 scenarios, 2026-06-10 audit). #336 fixed
the live path (closed-form ``lognormal_from_quantiles``); this migration
repairs the rows the divergent fitter already wrote.

Per spec (internal design doc 2026-06-10-audit-remediation-f1-f2-design)
(plan-gate-applied):

- Candidates: any tef/pl/sl node whose sidecar says
  ``fitter == "lognorm_trunc"``. All such nodes are PERT-shaped by
  construction (the pre-#336 pipeline always PERT-collapsed); a candidate
  WITHOUT a ``low`` key is an unexpected inconsistency -> WARNING + skip.
- Re-derivation: from the persisted ``scenario_sme_estimates`` rows —
  dedup latest-per-identity, MD-6 cleaning floors, per-pair closed-form
  untruncated lognormal, equal-weight mean of (meanlog, sigma) — exactly
  the current pipeline semantics (#343 pooling debate explicitly out of
  scope). Math INLINED; no app/scipy imports (migration stability).
  # verified: scipy.stats.norm.ppf(0.95) = 1.6448536269514722
- Repair criterion: |ln(stored_low / rederived_p5)| > ln(10). Corruption
  signature ~= e^29.6; converged fits have stored_low ~= rederived_p5
  (ratio ~= 1) so false positives are impossible (plan-gate Meth verified).
- Guards: never write sigma <= 0 or sigma > 10 (inline Sec-I2 equivalent);
  no SME rows -> skip; per-row/field try/except -> WARNING + skip (a single
  malformed row must not abort the migration).
- Audit: one ``audit_log`` row per repaired scenario, ``user_id = NULL``
  (system actor), ids bound as NO-HYPHEN hex (the recurring seed-UUID
  foot-gun), ``changes = {field: [old_node, new_node]}``.
- row_version bump on repaired scenarios. Downgrade: documented NO-OP
  (repair is directional; audit rows preserve the old nodes).

Post-deploy operator step (SC-I3, drift-logged): identify aggregate runs
whose ``aggregate_scenario_ids`` include a repaired scenario and re-run
them. Format note: ``audit_log.entity_id`` is no-hyphen hex while
``risk_analysis_runs.aggregate_scenario_ids`` (a JSON list) stores
hyphenated str(UUID) — normalise with REPLACE when matching::

    SELECT r.id, r.created_at
    FROM risk_analysis_runs r
    WHERE r.scenario_id IS NULL
      AND EXISTS (
        SELECT 1 FROM audit_log a
        WHERE a.action = 'scenario.repair_distribution'
          AND instr(REPLACE(r.aggregate_scenario_ids, '-', ''), a.entity_id) > 0
      );

Re-run each listed aggregate from /analyses with the same scenario set;
the stale runs remain as the immutable pre-repair record.

Revision ID: e3a1c4f7b2d9
Down revision: d6b8e2f0a719
"""

from __future__ import annotations

import json
import logging
import math
import uuid as _uuid
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision = "e3a1c4f7b2d9"
down_revision = "d6b8e2f0a719"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")

# scipy.stats.norm.ppf(0.95) — inlined so the migration never imports
# scipy/app code (stability over time).
Z_0_95 = 1.6448536269514722

_FIELDS = ("threat_event_frequency", "primary_loss", "secondary_loss")
_FIELD_TO_FIELDSET = {
    "threat_event_frequency": "tef",
    "primary_loss": "pl",
    "secondary_loss": "sl",
}


def _clean_pair(low: float, high: float, fieldset: str) -> tuple[float, float]:
    """MD-6 cleaning floors — mirrors fair_cam clean_quantile_pair for the
    lognormal fieldsets (vuln is never a candidate here)."""
    if fieldset == "tef":
        if low == 0:
            low = 0.1
        if high == 0:
            high = 1.0
    else:  # pl / sl
        floor = 1000.0
        low = max(low, floor)
        high = max(high, floor)
    return low, high


def _closed_form_fit(low: float, high: float) -> tuple[float, float]:
    """Closed-form untruncated two-quantile lognormal at p5/p95.

    Bit-equivalent to fair_cam lognormal_from_quantiles(low, high, .05, .95):
    at symmetric quantiles z_low + z_high == 0, so mean collapses to the
    log-midpoint and sigma to the log-span over 2*Z_0_95.
    """
    meanlog = (math.log(low) + math.log(high)) / 2.0
    sigma = (math.log(high) - math.log(low)) / (2.0 * Z_0_95)
    return meanlog, sigma


def _dedup_latest(rows: list[dict]) -> list[dict]:
    """Latest-per-identity — mirrors services.wizard_finalize._dedup_latest_per_sme.

    Identity: sme_id if set, else casefolded sme_name. Later recorded_at wins
    (rows arrive ordered by recorded_at ASC, so dict insertion order suffices).
    """
    seen: dict[str, dict] = {}
    for r in rows:
        key = r["sme_id"] if r["sme_id"] is not None else f"freetext:{(r['sme_name'] or '').casefold()}"
        seen[key] = r
    return list(seen.values())


def _row_identity(r: dict) -> str:
    """Stable per-row identity UUID string — mirrors
    services.wizard_finalize.row_identity_uuid so the repaired sidecar's
    ``sme_ids`` matches the live pipeline (PR-gate Meth-I-1): FK rows use the
    stored sme_id; free-text rows get the same uuid5(NAMESPACE_DNS,
    "freetext:<casefolded name>") synth UUID the live path emits, keeping
    ``len(sme_ids) == n_smes`` invariant for downstream audit tooling.
    """
    if r["sme_id"] is not None:
        # Normalise the raw SQLite no-hyphen hex to str(UUID) hyphenated —
        # the format the live pipeline's str(row_identity_uuid(...)) emits —
        # so repaired sidecars are format-identical to fresh ones.
        return str(_uuid.UUID(str(r["sme_id"])))
    return str(_uuid.uuid5(_uuid.NAMESPACE_DNS, f"freetext:{(r['sme_name'] or '').casefold()}"))


def upgrade() -> None:
    bind = op.get_bind()
    scenarios = bind.execute(
        sa.text(
            "SELECT id, organization_id, row_version, "
            "threat_event_frequency, primary_loss, secondary_loss FROM scenarios"
        )
    ).fetchall()

    repaired_total = 0
    for srow in scenarios:
        scenario_id, org_id, row_version = srow[0], srow[1], srow[2]
        raw_nodes = dict(zip(_FIELDS, srow[3:6], strict=True))
        changes: dict[str, list] = {}
        new_nodes: dict[str, str] = {}

        for field, raw in raw_nodes.items():
            if not raw:
                continue
            # Plan-gate SC-B2 + Sec-F1-I1: a single malformed row/field must
            # never abort the migration — WARNING + skip the field.
            try:
                node = json.loads(raw)
                meta = (node.get("distribution_fit_metadata") or {}) if isinstance(node, dict) else {}
                if meta.get("fitter") != "lognorm_trunc":
                    continue
                stored_low = node.get("low")
                if not isinstance(stored_low, (int, float)) or stored_low <= 0:
                    # lognorm_trunc candidates are PERT-shaped by construction;
                    # anything else is an unexpected schema inconsistency.
                    logger.warning(
                        "repair_divergent_lognorm_fits: scenario %s field %s has "
                        "fitter=lognorm_trunc but no usable PERT low (%r) — skipped",
                        scenario_id, field, stored_low,
                    )
                    continue

                fieldset = _FIELD_TO_FIELDSET[field]
                sme_rows = [
                    {"sme_id": r[0], "sme_name": r[1], "low": r[2], "high": r[3]}
                    for r in bind.execute(
                        sa.text(
                            "SELECT sme_id, sme_name, low, high FROM scenario_sme_estimates "
                            "WHERE scenario_id = :sid AND fieldset = :fs ORDER BY recorded_at ASC"
                        ),
                        {"sid": scenario_id, "fs": fieldset},
                    ).fetchall()
                ]
                deduped = _dedup_latest(sme_rows)
                if not deduped:
                    logger.warning(
                        "repair_divergent_lognorm_fits: scenario %s field %s is a "
                        "lognorm_trunc candidate but has no SME rows — skipped",
                        scenario_id, field,
                    )
                    continue

                fits = []
                for r in deduped:
                    lo, hi = _clean_pair(float(r["low"]), float(r["high"]), fieldset)
                    if not (lo > 0 and hi >= lo):
                        raise ValueError(f"unusable SME pair ({r['low']}, {r['high']})")
                    fits.append(_closed_form_fit(lo, hi))
                meanlog = sum(f[0] for f in fits) / len(fits)
                sigma = sum(f[1] for f in fits) / len(fits)

                # Inline Sec-I2 storage-guard equivalent: never write a node
                # validate_fair_distributions would reject.
                if not (0.0 < sigma <= 10.0) or not math.isfinite(meanlog):
                    logger.warning(
                        "repair_divergent_lognorm_fits: scenario %s field %s "
                        "re-derived sigma=%.4f outside (0, 10] — NOT repaired",
                        scenario_id, field, sigma,
                    )
                    continue

                rederived_p5 = math.exp(meanlog - Z_0_95 * sigma)
                if abs(math.log(stored_low / rederived_p5)) <= math.log(10.0):
                    continue  # converged legacy fit — leave byte-identical

                fitted_at = datetime.now(UTC).isoformat()
                new_node = {
                    "distribution": "lognormal",
                    "mean": meanlog,
                    "sigma": sigma,
                    "distribution_fit_metadata": {
                        "source": "quantile_lognormal_pool",
                        "fitter": "lognorm_native",
                        "schema_version": 2,
                        "q_low_quantile": 0.05,
                        "q_high_quantile": 0.95,
                        "pooled_meanlog": meanlog,
                        "pooled_sdlog": sigma,
                        "pooled_min_support": 0.0,
                        "pooled_max_support": None,
                        "n_smes": len(deduped),
                        "sme_ids": [_row_identity(r) for r in deduped],
                        "weights": [1.0] * len(deduped),
                        "fitted_at": fitted_at,
                        "repaired_from_fitter": "lognorm_trunc",
                        "repair_reason": "divergent_optimizer_fit",
                        "repaired_by_migration": revision,
                    },
                }
                changes[field] = [node, new_node]
                # Arch-I1: bind json.dumps(...) as TEXT — never a Python dict
                # (raw sa.text() bypasses the JSON TypeDecorator).
                new_nodes[field] = json.dumps(new_node)
            except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                logger.warning(
                    "repair_divergent_lognorm_fits: scenario %s field %s "
                    "skipped (%s: %s)",
                    scenario_id, field, type(exc).__name__, exc,
                )
                continue

        if not new_nodes:
            continue

        set_clause = ", ".join(f"{f} = :{f}" for f in new_nodes)
        bind.execute(
            sa.text(
                f"UPDATE scenarios SET {set_clause}, row_version = :rv WHERE id = :sid"  # noqa: S608
            ),
            {**new_nodes, "rv": (row_version or 1) + 1, "sid": scenario_id},
        )
        # Audit row — user_id NULL (system actor). Ids bound as NO-HYPHEN hex
        # (uuid4().hex); entity_id is the scenario id exactly as SELECTed
        # (already no-hyphen hex in SQLite storage) — no re-parse round-trip.
        bind.execute(
            sa.text(
                "INSERT INTO audit_log "
                "(id, organization_id, entity_type, entity_id, user_id, action, changes, timestamp) "
                "VALUES (:id, :org, 'scenario', :eid, NULL, 'scenario.repair_distribution', :ch, :ts)"
            ),
            {
                "id": _uuid.uuid4().hex,
                "org": org_id,
                "eid": scenario_id,
                "ch": json.dumps(changes),
                "ts": datetime.now(UTC).isoformat(sep=" "),
            },
        )
        repaired_total += 1
        logger.warning(
            "repair_divergent_lognorm_fits: scenario %s repaired fields %s",
            scenario_id, sorted(new_nodes),
        )

    logger.warning("repair_divergent_lognorm_fits: %d scenario(s) repaired", repaired_total)


def downgrade() -> None:
    """Documented NO-OP: the repair is directional; the pre-repair nodes are
    preserved verbatim in the audit_log rows (action
    'scenario.repair_distribution') if forensic rollback is ever needed."""
