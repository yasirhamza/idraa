"""PR2 Task 5: backfill capacity `max` onto stored scenario loss fields.

Mints the PR2 capacity-bound cap (D13: ``max = K_CAPACITY * annual_revenue``)
onto every pre-existing ``scenarios.primary_loss`` / ``scenarios.secondary_loss``
field whose stored distribution is ``lognormal`` or ``lognormal_mixture`` and
does not already carry an explicit ``max``. Producers (Tasks 4a/4b/4c) mint
``max`` for every NEW write going forward; this migration is the one-time
backfill for rows written before those producers landed.

**``K_CAPACITY`` and ``Z95`` are INLINED, never imported from
``Settings``/``src.idraa.services.loss_capacity``.** A migration is a
point-in-time replay: it must reproduce the SAME mint identically forever,
independent of whatever the running app's config says on the day it
happens to execute. A deployment that has tuned ``CAPACITY_K`` away from
1.0 (via the ``CAPACITY_K`` env var) will still have THIS migration mint
at ``K_CAPACITY = 1.0`` — that is a deliberate, documented consequence, not
a bug: importing ``Settings.capacity_k`` here would make a historical
migration's output depend on the config of whatever environment happens to
run it, which is the opposite of what a migration is for. Likewise
``Z95 = norm.ppf(0.95)`` is inlined as the literal float rather than
importing ``scipy.stats.norm`` — mirrors the ``b3f8a2d94c1e`` (D12)
precedent's own ``_Z95`` inlining, same numeric value.

**D19 floor — mirrors ``services/fair_cam_validation.py::_validate_capacity_floor``
EXACTLY** (Task 3b), reproduced here rather than imported for the same
reason as ``K_CAPACITY``: comparison happens in LOG SPACE
(``ln(candidate) <= meanlog + Z95*sigma``) so an operator-entered ``mean``
that is large-but-finite can never raise ``OverflowError`` from
``math.exp``. For ``lognormal_mixture`` the floor is checked against
EVERY component's p95 — the accept condition is ``candidate > max_i p95_i``
(reject if the candidate sits at or below ANY single component's p95, not
just the largest-``mean`` component: components carry independent sigma,
so the largest-mean component need not have the largest p95).

**A floor conflict is a skip + WARNING, never a raise.** This migration is
a best-effort backfill, not an authoring surface — a row that fails the
floor stays uncapped exactly as it was before this migration ran, and its
next edit through the D17 expert form (or D18/D19 gates on the wizard/
import paths) will 422 until an analyst re-authors a valid cap or the
loss field's own mean/sigma. The same skip+WARNING (no raise) applies when
the owning org's ``annual_revenue`` is NULL or <= 0 (D14: no invented cap).

**Postgres-safe JSON parse.** ``scenarios.primary_loss``/``secondary_loss``
come back as native ``dict``/``None`` under SQLite's JSON type decorator in
this project's test harness, but as ``str``/``bytes`` under a raw
``psycopg`` connection against Postgres JSON/JSONB columns queried via raw
``sa.text()`` (which bypasses the ORM's JSON ``TypeDecorator``). Any OTHER
raw type is an environment assumption violated (a driver returning
something this migration was never written to expect) and MUST fail loud
— see ``_UnexpectedJsonColumnTypeError`` below, which is deliberately NOT a
subclass of ``TypeError``/``ValueError``/``KeyError`` and is checked
BEFORE the per-row ``try`` block that catches those three for ordinary
malformed-row skips. (The ``b3f8a2d94c1e``/``c4e4d441087c`` precedents'
own per-row ``except`` tuples include ``TypeError`` — a distinct exception
type here is what keeps a genuinely-unexpected column type from being
silently swallowed by that same tuple.) The JSON literal text ``"null"``
(a legitimate no-secondary-loss representation seen in prod per the
``c4e4d441087c`` precedent) parses to Python ``None`` and is skipped
without raising, same as a real SQL ``NULL``.

**Own-org capacity, never first-org-wins.** The driving query JOINs
``organizations`` in the SELECT itself
(``scenarios s JOIN organizations o ON o.id = s.organization_id``) so every
scenario mints from ITS OWN owning org's revenue — never a
first-row/``ORDER BY id LIMIT 1`` shortcut, which would silently mint every
scenario in the database from a single, arbitrary org's revenue.

**Idempotent.** A row that already carries an explicit ``max`` (either
authored before this migration ran, or backfilled by an earlier run of
this same migration) is left untouched — running this migration twice
mints nothing the second time. PERT (and any kind other than
``lognormal``/``lognormal_mixture``) is out of scope by kind and never
gains a ``max`` key.

**Scope: ``scenarios`` only.** ``scenario_library_entries`` are org-agnostic
(D14 — no organization to mint a cap from); ``scenario_library_overrides``
re-derive their ``max`` through the wizard/form paths, which mint it;
``wizard_drafts`` has no loss columns of its own (loss dicts are nested
inside ``state_json`` and materialise into a real ``scenarios`` row only at
finalize, which mints).

Audit-first ordering (the ``c4e4d441087c``/``b3f8a2d94c1e`` pattern): one
``audit_log`` row INSERTed BEFORE the ``scenarios`` UPDATE for every
backfilled field, both inside the migration's single enclosing transaction
so a failure in either write rolls back both. The ``scenarios`` UPDATE
increments ``row_version`` SQL-side (``row_version = row_version + 1``),
never a Python read-modify-write. Raw ``sa.text()`` binds skip the JSON
``TypeDecorator``, so the audit payload is serialised with ``json.dumps``
by hand. The audit row's own generated id uses ``uuid.uuid4().hex``
(no-hyphen hex) — the raw-text-seed UUID foot-gun: a hyphenated
``str(uuid)`` literal INSERTed where the ORM's ``Uuid`` column binds
no-hyphen text produces a row an ORM read can never find again.

Revision ID: ffed7c509563
Down revision: b3f8a2d94c1e
"""

from __future__ import annotations

import json
import logging
import math
import uuid
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from alembic import op

revision = "ffed7c509563"
down_revision = "b3f8a2d94c1e"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")

# Inlined on purpose -- see the module docstring's "K_CAPACITY and Z95 are
# INLINED" section. NOT imported from Settings.capacity_k /
# services.loss_capacity.capacity_max_for_org / scipy.stats.norm.
_K_CAPACITY: float = 1.0
_Z95: float = 1.6448536269514722

_LOSS_FIELDS = ("primary_loss", "secondary_loss")

_SELECT_SCENARIOS = sa.text(
    "SELECT s.id AS id, s.organization_id AS organization_id, "
    "s.row_version AS row_version, s.primary_loss AS primary_loss, "
    "s.secondary_loss AS secondary_loss, o.annual_revenue AS annual_revenue "
    "FROM scenarios s JOIN organizations o ON o.id = s.organization_id"
)


class _UnexpectedJsonColumnTypeError(Exception):
    """Raised when a loss-field column value is neither str/bytes (needs
    ``json.loads`` -- e.g. Postgres via a raw driver) nor dict/None
    (already decoded -- e.g. SQLite's JSON type decorator, or a real SQL
    NULL). Deliberately NOT a subclass of TypeError/ValueError/KeyError:
    those three are exactly the tuple the per-row skip handler below
    catches for ordinary malformed-row conditions, and a genuinely
    unexpected column type must never be silently absorbed by that tuple
    the way a bare TypeError would be. See the module docstring."""


def _parse_json_column(raw: Any) -> dict[str, Any] | None:
    """Postgres-safe JSON parse. str/bytes -> ``json.loads`` (may itself
    raise ``json.JSONDecodeError`` for genuinely malformed text -- the
    caller's per-row ``try`` handles that as an ordinary skip); dict/None
    -> used as-is. Any other type raises ``_UnexpectedJsonColumnTypeError``.

    The literal JSON text ``"null"`` parses to Python ``None`` here, same
    as a real SQL NULL -- the caller's ``isinstance(parsed, dict)`` check
    turns both into a silent, no-raise skip.
    """
    if raw is None:
        return None
    if isinstance(raw, (str, bytes)):
        parsed: Any = json.loads(raw)
    elif isinstance(raw, dict):
        parsed = raw
    else:
        raise _UnexpectedJsonColumnTypeError(
            f"unexpected loss-field column type {type(raw).__name__!r} "
            "(expected str, bytes, dict, or None)"
        )
    return parsed if isinstance(parsed, dict) else None


def _worst_ln_p95(dist: dict[str, Any], kind: str) -> float:
    """log-space p95 to compare the candidate cap against -- mirrors
    ``fair_cam_validation._validate_capacity_floor`` exactly (Task 3b).

    lognormal: ``meanlog + Z95*sigma``. lognormal_mixture: the MAXIMUM
    (worst/largest) p95 over every component -- NOT the largest-``mean``
    component, since components carry independent sigma and the
    largest-mean component need not have the largest p95. Missing/
    non-numeric keys raise KeyError/TypeError/ValueError, which the
    caller's per-row ``try`` catches as an ordinary malformed-row skip
    (mirrors the ``c4e4d441087c`` precedent's own ``float(dist["sigma"])``
    let-it-raise-into-the-catch idiom).
    """
    if kind == "lognormal":
        return float(dist["mean"]) + _Z95 * float(dist["sigma"])
    # kind == "lognormal_mixture"
    components = dist["components"]
    if not isinstance(components, list) or not components:
        raise ValueError(
            f"lognormal_mixture components must be a non-empty list, got {components!r}"
        )
    return max(float(c["mean"]) + _Z95 * float(c["sigma"]) for c in components)


def _apply_scenario_update(
    conn: sa.engine.Connection, field: str, dist_json: str, sid: object
) -> None:
    """Module-level seam for the scenario UPDATE, kept distinct from the
    audit-row INSERT (mirrors the ``c4e4d441087c``/``b3f8a2d94c1e``
    precedents) -- SQL-side ``row_version`` increment, never a Python
    read-modify-write."""
    conn.execute(
        sa.text(
            f"UPDATE scenarios SET {field} = :dist, "  # noqa: S608 -- field is one of _LOSS_FIELDS, never user input
            "row_version = row_version + 1 WHERE id = :sid"
        ),
        {"dist": dist_json, "sid": sid},
    )


_INSERT_AUDIT = sa.text(
    "INSERT INTO audit_log "
    "(id, organization_id, entity_type, entity_id, user_id, action, changes, timestamp) "
    "VALUES (:id, :org, 'scenario', :eid, NULL, 'scenario.backfill_capacity_max', :ch, :ts)"
)


def _upgrade_scenarios(conn: sa.engine.Connection) -> int:
    """Per-scenario, per-loss-field backfill sweep. Returns the count of
    fields minted (for the summary WARNING logged by ``upgrade()``)."""
    minted = 0
    rows = conn.execute(_SELECT_SCENARIOS).mappings().all()
    for row in rows:
        scenario_id = row["id"]
        org_id = row["organization_id"]
        revenue_raw = row["annual_revenue"]
        try:
            revenue: float | None = float(revenue_raw) if revenue_raw is not None else None
        except (TypeError, ValueError):
            revenue = None

        for field in _LOSS_FIELDS:
            raw = row[field]
            # Type check for the field's raw value happens INSIDE
            # _parse_json_column, but _UnexpectedJsonColumnTypeError is excluded
            # from the except tuple below (and is not a subclass of any
            # member of it) -- so it propagates out of this loop, out of
            # _upgrade_scenarios, and out of the migration entirely. See
            # the module docstring's "Postgres-safe JSON parse" section.
            try:
                dist = _parse_json_column(raw)
            except json.JSONDecodeError as exc:
                logger.warning(
                    "capacity_max_backfill: scenario %s field %s skipped (malformed JSON: %s)",
                    scenario_id,
                    field,
                    exc,
                )
                continue
            if not isinstance(dist, dict):
                continue  # SQL NULL or literal "null" text -- silent skip

            kind = str(dist.get("distribution", "pert")).lower()
            if kind not in ("lognormal", "lognormal_mixture"):
                continue  # PERT and anything else: out of scope, no max emitted

            if dist.get("max") is not None:
                continue  # idempotent: never overwrite an explicit max

            if revenue is None or revenue <= 0:
                logger.warning(
                    "capacity_max_backfill: scenario %s field %s skipped -- "
                    "org %s annual_revenue is %r (NULL or <= 0, no cap minted)",
                    scenario_id,
                    field,
                    org_id,
                    revenue_raw,
                )
                continue

            try:
                ln_p95 = _worst_ln_p95(dist, kind)
            except (KeyError, TypeError, ValueError) as exc:
                logger.warning(
                    "capacity_max_backfill: scenario %s field %s skipped (%s: %s)",
                    scenario_id,
                    field,
                    type(exc).__name__,
                    exc,
                )
                continue

            candidate = _K_CAPACITY * revenue
            if math.log(candidate) <= ln_p95:
                logger.warning(
                    "capacity_max_backfill: scenario %s field %s skipped -- "
                    "floor conflict (candidate cap %.6g at or below the "
                    "distribution's p95, D19)",
                    scenario_id,
                    field,
                    candidate,
                )
                continue

            new_dist = dict(dist)
            new_dist["max"] = candidate
            changes = {"field": field, "prior": dist, "max": candidate}
            # Audit-first: INSERT before the UPDATE, both inside the
            # migration's single enclosing transaction (shared-fate
            # rollback -- see the module docstring).
            conn.execute(
                _INSERT_AUDIT,
                {
                    "id": uuid.uuid4().hex,
                    "org": org_id,
                    "eid": scenario_id,
                    "ch": json.dumps(changes),
                    "ts": datetime.now(UTC).isoformat(sep=" "),
                },
            )
            _apply_scenario_update(conn, field, json.dumps(new_dist), scenario_id)
            minted += 1
    return minted


def upgrade() -> None:
    bind = op.get_bind()
    minted = _upgrade_scenarios(bind)
    logger.warning("capacity_max_backfill: %d scenario loss field(s) minted a capacity max", minted)


def downgrade() -> None:
    """Documented NO-OP: content migration, forward-only. Every backfilled
    field's prior (max-less) dict is preserved verbatim in the audit_log
    row (action ``scenario.backfill_capacity_max``) for forensic rollback,
    exactly the ``c4e4d441087c``/``b3f8a2d94c1e`` downgrade rationale."""
