"""sigma recalibration (PR1 Task 3): library entries + narrow-only scenario
sweep, audit-first.

Two independent halves, both idempotent:

1. **Library entries** (``scenario_library_entries``): blind replay of the
   Task-2 re-authored ``data/seed_library_entries*.json`` PL/SL onto
   ``WHERE slug = :slug AND version = 1`` — the exact pattern of
   ``d9e5a3c7f2b4`` (Milestone B's PERT-conversion replay). The 172 fields
   Task 2 re-authored (18 catastrophic lognormal onto sigma=1.7, 149 capped
   non-vendor + 5 capped vendor PERT onto the implied-sigma-1.7 range) are
   already correct in the JSON; this migration just lands them on rows that
   were seeded before Task 2 landed.

2. **Scenario sweep** (``scenarios``): NARROW-ONLY (spec D6') — a stored
   lognormal/lognormal_mixture loss field whose sigma sits ABOVE
   ``_SIGMA_TARGET`` gets its dispersion narrowed to the within-scenario
   default; anything at or below the target (legitimate SME-elicited
   narrower dispersion) is left alone, and an ``analyst_pin`` provenance
   stamp always wins over the sweep. PERT fields and threat_event_frequency
   are structurally out of scope (only ``primary_loss``/``secondary_loss``
   columns are read, and only lognormal/lognormal_mixture kinds are
   transformed). Medians (log-space mean) are NEVER moved.

Audit-first ordering (rules already litigated, per
``docs/superpowers/plans/2026-07-25-sigma-recal-pr1.md`` Task 3): the
``audit_log`` INSERT happens BEFORE the ``scenarios`` UPDATE for every
changed field — a swallowed audit-write failure after an already-committed
mutation would be silent data destruction. This does NOT mean the audit row
can survive a rolled-back update: both writes share the single enclosing
migration transaction, so a failure in the UPDATE step rolls back the
INSERT too. Audit-first is defense-in-depth on top of that atomicity, not a
substitute for it — note the ``e3a1c4f7b2d9`` (audit-F1) precedent's body is
UPDATE-first; this migration deliberately does NOT copy that ordering.

The entire PRIOR fit record (``distribution_fit_metadata``) is moved under
a ``superseded_fit`` key rather than dropped: after the sweep the pooled fit
no longer describes the stored params, and ``wizard_finalize`` consumes
support/quantile keys on re-edit paths, so leaving stale top-level keys
would be a live hazard. NO ``schema_version`` bump — 3 already means
"mixture" (issue #27 semantics), nothing branches on the sidecar version,
and the ``sigma_recalibration`` stamp is the unambiguous signal that a
sweep happened.

Revision ID: c4e4d441087c
Down revision: e4636e6fb4eb
"""

from __future__ import annotations

import json
import logging
import uuid as _uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from alembic import op

revision = "c4e4d441087c"
down_revision = "e4636e6fb4eb"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")

# Inlined on purpose: migrations replay independent of app constants
# (src/idraa/services/calibration.py:WITHIN_SCENARIO_SIGMA_DEFAULT).
_SIGMA_TARGET = 1.7
_LOSS_FIELDS = ("primary_loss", "secondary_loss")


def _recalibrate_dist(dist: dict) -> tuple[dict, dict] | None:
    """(new_dist, prior_dist_verbatim) or None when the field is out of scope.

    NARROW-ONLY (D6'): a sigma at or below the target is left alone — an
    SME-elicited narrower range is legitimate dispersion, not contamination.
    An analyst_pin provenance stamp always wins. Medians (log-space mean) are
    NEVER moved here.
    """
    kind = str(dist.get("distribution", "pert")).lower()
    meta = dict(dist.get("distribution_fit_metadata") or {})
    if (meta.get("sigma_recalibration") or {}).get("source") == "analyst_pin":
        return None
    if kind == "lognormal":
        prior_sigma = float(dist["sigma"])
        if prior_sigma <= _SIGMA_TARGET:
            return None
        new = dict(dist)
        new["sigma"] = _SIGMA_TARGET
    elif kind == "lognormal_mixture":
        comps = dist.get("components") or []
        if not comps or all(float(c["sigma"]) <= _SIGMA_TARGET for c in comps):
            return None
        new = dict(dist)
        new["components"] = [
            {**c, "sigma": _SIGMA_TARGET} if float(c["sigma"]) > _SIGMA_TARGET else dict(c)
            for c in comps
        ]
        prior_sigma = max(float(c["sigma"]) for c in comps)
    else:
        return None  # PERT and anything else: out of scope
    # Move the ENTIRE prior fit record: after the sweep the pooled fit no longer
    # describes the stored params, and wizard_finalize consumes support/quantile
    # keys on re-edit paths -- stale top-level keys are a live hazard. Key list
    # mirrors wizard_finalize's emit (:526-585) incl. legacy shapes.
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
    superseded = {k: meta.pop(k) for k in _FIT_RECORD_KEYS if k in meta}
    if superseded:
        meta["superseded_fit"] = superseded
    # NO schema_version bump: 3 already means "mixture" (#27 semantics), nothing
    # branches on the sidecar version, and the decisions spec does not mandate
    # one -- the sigma_recalibration stamp is the unambiguous signal.
    meta["sigma_recalibration"] = {
        "source": "migration_recalibration",
        "prior_sigma": prior_sigma,
        "revision": revision,
    }
    new["distribution_fit_metadata"] = meta
    return new, dist


_SELECT_SCENARIOS = sa.text(
    "SELECT id, organization_id, row_version, primary_loss, secondary_loss FROM scenarios"
)


def _apply_scenario_update(
    conn: sa.engine.Connection, field: str, dist_json: str, sid: str
) -> None:
    """Module-level seam for the scenario UPDATE — kept distinct from the
    audit-row INSERT so the atomicity test can monkeypatch THIS (not
    ``conn.execute``) to simulate a write failure after the audit row is
    already staged in the same transaction, proving shared-fate rollback."""
    conn.execute(
        sa.text(
            f"UPDATE scenarios SET {field} = :dist, "  # noqa: S608 -- field is one of _LOSS_FIELDS, never user input
            "row_version = row_version + 1 WHERE id = :sid"
        ),
        {"dist": dist_json, "sid": sid},
    )


def _upgrade_scenarios(conn: sa.engine.Connection) -> int:
    """Narrow-only sweep over every scenario's primary_loss/secondary_loss.

    Per field: audit row INSERTed BEFORE the scenario UPDATE. Per-row/field
    parse failures are scoped to JSON parse/shape errors only — exactly the
    ``e3a1c4f7b2d9`` (audit-F1) precedent's tuple; ``ValueError`` is
    load-bearing because ``_recalibrate_dist`` calls ``float()`` on sigma
    fields. Returns the count of fields swept (for logging/verification).
    """
    swept = 0
    rows = conn.execute(_SELECT_SCENARIOS).fetchall()
    for srow in rows:
        scenario_id, org_id = srow[0], srow[1]
        raw_fields = {"primary_loss": srow[3], "secondary_loss": srow[4]}
        for field, raw in raw_fields.items():
            if not raw:
                continue
            try:
                dist: Any = json.loads(raw)
                # AMENDMENT (T3 execution, orchestrator ruling on the
                # implementer's verified stop -- docs/superpowers/plans/
                # 2026-07-25-sigma-recal-pr1.md Task 3 code comment): 10 real
                # prod scenarios store secondary_loss as the literal JSON
                # text "null" (truthy 4-char string, parses to Python None),
                # which crashed the dry run with an AttributeError the tuple
                # below rightly does not catch. Mirrors the F1 precedent's
                # own isinstance guard; a non-dict value is a legitimate
                # no-secondary-loss representation -> silent skip, same as
                # SQL NULL (no WARNING, no audit row). Widening the tuple to
                # AttributeError was REJECTED (would mask genuine attribute
                # bugs in the migration).
                if not isinstance(dist, dict):
                    continue
                result = _recalibrate_dist(dist)
            except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                logger.warning(
                    "sigma_recalibration: scenario %s field %s skipped (%s: %s)",
                    scenario_id,
                    field,
                    type(exc).__name__,
                    exc,
                )
                continue
            if result is None:
                continue
            new_dist, prior_dist = result
            prior_sigma = new_dist["distribution_fit_metadata"]["sigma_recalibration"][
                "prior_sigma"
            ]
            changes = {"field": field, "prior": prior_dist, "sigma": [prior_sigma, _SIGMA_TARGET]}
            # Audit-first: INSERT before the UPDATE. Full 8-column list —
            # organization_id + entity_type are NOT NULL with no server
            # defaults (AuditLog has none at all), so id/timestamp are bound
            # too. Raw sa.text() binds do NOT apply the JSON TypeDecorator,
            # so :ch is pre-serialised text (Arch-I1 equivalent).
            conn.execute(
                sa.text(
                    "INSERT INTO audit_log "
                    "(id, organization_id, entity_type, entity_id, user_id, action, changes, timestamp) "
                    "VALUES (:id, :org, 'scenario', :eid, NULL, 'scenario.recalibrate_loss_sigma', :ch, :ts)"
                ),
                {
                    "id": _uuid.uuid4().hex,
                    "org": org_id,
                    "eid": scenario_id,
                    "ch": json.dumps(changes),
                    "ts": datetime.now(UTC).isoformat(sep=" "),
                },
            )
            _apply_scenario_update(conn, field, json.dumps(new_dist), scenario_id)
            swept += 1
    return swept


_UPDATE_LIBRARY_ENTRY = sa.text(
    "UPDATE scenario_library_entries SET primary_loss = :pl, secondary_loss = :sl "
    "WHERE slug = :slug AND version = 1"
)


def _seed_library_entries() -> dict[str, dict]:
    """Read the CURRENT (Task-2 re-authored) seed JSONs — mirrors
    ``d9e5a3c7f2b4._seed()`` exactly (same path-resolution + fallback)."""

    def _paths(root: Path) -> list[Path]:
        return [
            root / "data" / n
            for n in ("seed_library_entries.json", "seed_library_entries_extension.json")
        ]

    paths: list[Path] | None = None
    try:
        import idraa

        cand = _paths(Path(idraa.__file__).resolve().parent.parent.parent)
        if all(p.exists() for p in cand):
            paths = cand
    except Exception:  # pragma: no cover - fallback
        paths = None
    if paths is None:
        paths = _paths(Path(__file__).resolve().parent.parent.parent)
    rows: list[dict] = []
    for p in paths:
        rows.extend(json.loads(p.read_text(encoding="utf-8")))
    return {r["slug"]: r for r in rows}


def _upgrade_library_entries(bind: sa.engine.Connection) -> None:
    seed = _seed_library_entries()
    for slug, entry in seed.items():
        bind.execute(
            _UPDATE_LIBRARY_ENTRY,
            {
                "pl": json.dumps(entry["primary_loss"]),
                "sl": json.dumps(entry["secondary_loss"]) if entry.get("secondary_loss") else None,
                "slug": slug,
            },
        )


def upgrade() -> None:
    bind = op.get_bind()
    _upgrade_library_entries(bind)
    swept = _upgrade_scenarios(bind)
    logger.warning("sigma_recalibration: %d scenario loss field(s) swept", swept)


def downgrade() -> None:
    """Documented NO-OP: content migration, forward-only. The library-entry
    half's pre-recalibration values are recoverable from git history (same
    rationale as ``d9e5a3c7f2b4``); the scenario-sweep half's pre-sweep
    dispersion is preserved verbatim in the ``audit_log`` rows (action
    ``scenario.recalibrate_loss_sigma``) for forensic rollback, exactly the
    ``e3a1c4f7b2d9`` (audit-F1) downgrade rationale."""
