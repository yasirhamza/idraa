"""D12: lognormal is strictly a loss distribution — collapse legacy lognormal TEF.

Owner rule (2026-07-25, post-PR1): in v3 storage, lognormal/lognormal_mixture
are loss-node shapes only; TEF and vulnerability are PERT-only. The wizard has
authored PERT TEF since the tef-pert-revert (Milestone A) and the library is
102/102 PERT (#520 then reverted); exactly ONE stored violation remains — a
scenario whose TEF was snapshotted during the Epic-B native-lognormal window.
This migration collapses any scenario lognormal TEF to PERT via the canonical
``lognormal_to_pert_approx`` rule, INLINED per the migration-stability rule
(untruncated case): low/high = exp(mu -/+ z95*sigma), mode = exp(mu - sigma^2)
clamped into [low, high]. The quantiles are preserved exactly; the PERT mean
is lower than the lognormal mean at the same quantiles (disclosed in D12 —
the same accepted trade as the Milestone-A library-wide revert).

Only the ``threat_event_frequency`` column is touched — loss columns keep
lognormal legally. ``lognormal_mixture`` TEF (zero exist in prod; the D12
validation chokepoint now rejects new ones) is skipped with a WARNING rather
than collapsed: true-mixture quantiles need numeric root-finding that does not
belong inlined in a migration.

Audit-first (the c4e4d441087c pattern): one ``audit_log`` row per collapsed
field, written BEFORE the data write, carrying the full prior dist; the fit
record moves under ``superseded_fit``; ``analyst_pin``-stamped fields are
skipped. Idempotent: PERT TEF is out of scope by kind, so a second run sweeps
zero. downgrade() is a documented no-op — priors live in the audit trail.

Revision ID: b3f8a2d94c1e
Down revision: c4e4d441087c
"""

from __future__ import annotations

import datetime
import json
import logging
import math
import uuid

import sqlalchemy as sa
from alembic import op

revision = "b3f8a2d94c1e"
down_revision = "c4e4d441087c"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")

# Inlined on purpose: migrations replay independent of application constants.
_Z95 = 1.6448536269514722

_FIT_RECORD_KEYS = (
    "pooled_meanlog",
    "pooled_sdlog",
    "component_meanlogs",
    "component_sdlogs",
    "pooling_method",
    "pooled_min_support",
    "pooled_max_support",
    "q_low_quantile",
    "q_high_quantile",
    "n_smes",
    "sme_ids",
    "weights",
    "source",
    "fitter",
    "fitted_at",
    "schema_version",
)

_SELECT_SCENARIOS = sa.text(
    "SELECT id, organization_id, threat_event_frequency, row_version FROM scenarios ORDER BY id"
)
_INSERT_AUDIT = sa.text(
    "INSERT INTO audit_log (id, organization_id, entity_type, entity_id, user_id, "
    "action, changes, timestamp) "
    "VALUES (:id, :org, 'scenario', :entity, NULL, "
    "'scenario.collapse_tef_to_pert', :changes, :ts)"
)


def _apply_scenario_update(conn: sa.Connection, dist_json: str, sid: object) -> None:
    """Seam for the atomicity test — the data write, after the audit write."""
    conn.execute(
        sa.text(
            "UPDATE scenarios SET threat_event_frequency = :dist, "
            "row_version = row_version + 1 WHERE id = :id"
        ),
        {"dist": dist_json, "id": sid},
    )


def _collapse_tef(dist: dict) -> dict | None:
    """PERT triple for a lognormal TEF, or None when out of scope.

    Canonical collapse (lognormal_to_pert_approx, untruncated): quantiles
    preserved, mode = the true lognormal mode clamped into the PERT bounds.
    analyst_pin provenance always wins. Mixtures are handled by the caller
    (WARNING + skip; none exist).
    """
    kind = str(dist.get("distribution", "pert")).lower()
    if kind != "lognormal":
        return None
    meta = dict(dist.get("distribution_fit_metadata") or {})
    if (meta.get("sigma_recalibration") or {}).get("source") == "analyst_pin":
        return None
    mu, s = float(dist["mean"]), float(dist["sigma"])
    low = math.exp(mu - _Z95 * s)
    high = math.exp(mu + _Z95 * s)
    mode = min(max(math.exp(mu - s * s), low), high)
    superseded = {k: meta.pop(k) for k in _FIT_RECORD_KEYS if k in meta}
    if superseded:
        meta["superseded_fit"] = superseded
    meta["tef_pert_collapse"] = {
        "source": "migration_tef_pert_collapse",
        "prior_kind": "lognormal",
        "revision": revision,
    }
    return {
        "distribution": "PERT",
        "low": low,
        "mode": mode,
        "high": high,
        "distribution_fit_metadata": meta,
    }


def _upgrade_scenarios(conn: sa.Connection) -> int:
    swept = 0
    for row in conn.execute(_SELECT_SCENARIOS).mappings().all():
        raw = row["threat_event_frequency"]
        if not raw:
            continue
        try:
            dist = json.loads(raw) if isinstance(raw, str) else raw
            if not isinstance(dist, dict):
                continue
            kind = str(dist.get("distribution", "pert")).lower()
            if kind == "lognormal_mixture":
                logger.warning(
                    "d12-tef-collapse: scenario %s has a lognormal_mixture TEF -- "
                    "skipped (no inline mixture-quantile collapse; handle by hand)",
                    row["id"],
                )
                continue
            new_dist = _collapse_tef(dist)
            if new_dist is None:
                continue
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            logger.warning("d12-tef-collapse: skipped scenario %s (%s)", row["id"], exc)
            continue
        conn.execute(
            _INSERT_AUDIT,
            {
                "id": uuid.uuid4().hex,
                "org": row["organization_id"],
                "entity": row["id"],
                "changes": json.dumps(
                    {
                        "field": "threat_event_frequency",
                        "prior": dist,
                        "kind": ["lognormal", "PERT"],
                    }
                ),
                "ts": datetime.datetime.now(datetime.UTC).isoformat(sep=" "),
            },
        )
        _apply_scenario_update(conn, json.dumps(new_dist), row["id"])
        swept += 1
    logger.info("d12-tef-collapse: collapsed %d scenario TEF fields", swept)
    return swept


def upgrade() -> None:
    _upgrade_scenarios(op.get_bind())


def downgrade() -> None:
    """Documented no-op: the full prior TEF dist is preserved verbatim in the
    audit trail (changes.prior), and restoring lognormal TEF would reintroduce
    the D12 policy violation this migration exists to remove."""
