# src/idraa/services/wizard_state.py
"""DB-backed wizard state via the wizard_drafts table.

Spec §8.5 + paranoid-review Decision A: state lives in the database
(single source of truth per CLAUDE.md), keyed by composite (user_id,
tx_id). Survives server restart; cleanup_expired sweeps idle drafts.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.scenario_sme_estimate import ScenarioSMEEstimate
from idraa.models.wizard_draft import WizardDraft


@dataclass
class WizardState:
    tx_id: str
    current_step: int = 1
    library_entry_id: str | None = None
    library_entry_version: int | None = None
    override_id: str | None = None
    override_version: int | None = None
    # Step-2 fields
    name: str | None = None
    description: str | None = None
    threat_category: str | None = None
    threat_actor_type: str | None = None
    asset_class: str | None = None
    attack_vector: str | None = None
    tags: list[str] = field(default_factory=list)
    # Step-3 fields (FAIR distributions)
    threat_event_frequency: dict[str, Any] | None = None
    vulnerability: dict[str, Any] | None = None
    primary_loss: dict[str, Any] | None = None
    secondary_loss: dict[str, Any] | None = None
    # Step-4 fields
    mitigating_control_ids: list[str] = field(default_factory=list)
    # Milestone B (#loss-pert-overhaul): loss-magnitude shape for THIS scenario.
    # "capped" (default) -> pl/sl collapse to bounded PERT at finalize;
    # "catastrophic" -> native uncapped lognormal (owner-curated class or
    # analyst override via the step-4 toggle). Seeded from the library entry.
    # Old drafts lack the key in state_json and fall to this default on load.
    loss_shape: str = "capped"
    # Per-fieldset SME (low, high) elicitation rows for the evaluator-style
    # wizard step 3. Shape: {fieldset: [{"sme_id", "low", "high"}, ...]}.
    # Consumed by services.wizard_finalize.process_sme_estimates; merged from
    # step-3 submits by T11 (the finalize route handler). Landed early at T5
    # so the finalize pipeline + its tests can construct WizardState directly.
    sme_estimates: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    # #56 wizard re-elicitation: when set, finalize UPDATES this existing
    # scenario in place instead of creating a new one. target_scenario_id is
    # the hex UUID; target_expected_row_version is the scenario's row_version
    # captured at seed time (the edit form's optimistic-lock primitive,
    # carried through the wizard's lifetime — finalize raises
    # ScenarioVersionConflictError on mismatch). Legacy drafts lack both keys
    # in state_json and fall to None on load (create path, unchanged).
    target_scenario_id: str | None = None
    target_expected_row_version: int | None = None
    # Arch-25 R3 optimistic-lock counter. Sec-18 R2: the step-3 finalize POST
    # echoes this value back via a hidden form field; `advance_step` requires
    # the caller's value to match the row's current `version_token` before
    # writing. Mirrors the `wizard_drafts.version_token` column (DB column is
    # the source of truth; this dataclass field carries the value through the
    # request lifecycle and is NOT serialized into `state_json` — see
    # `_state_json_excluding_version_token`).
    version_token: int = 0

    def basic_fields(self) -> dict[str, Any]:
        """Arch-18 PR2: ScenarioForm-valid dict mirroring the existing
        ``_scenario_form_from_state`` helper below (lines 208-224). Returns
        the descriptive ScenarioForm columns with enum.value conversion so the
        caller can splat into ``ScenarioForm(**state.basic_fields(), ...fair fields...)``.
        """
        import uuid as _uuid

        from idraa.models.enums import AssetClass, ThreatActorType, ThreatCategory

        return {
            "name": self.name or "",
            "description": self.description,
            "threat_category": (
                ThreatCategory(self.threat_category).value
                if self.threat_category
                else "miscellaneous"
            ),
            "threat_actor_type": (
                ThreatActorType(self.threat_actor_type).value if self.threat_actor_type else None
            ),
            "asset_class": (AssetClass(self.asset_class).value if self.asset_class else None),
            "attack_vector": self.attack_vector,
            "library_entry_id": (
                _uuid.UUID(self.library_entry_id) if self.library_entry_id else None
            ),
        }


def _state_json_excluding_version_token(state: WizardState) -> dict[str, Any]:
    """Spec-C PR3 NICE: avoid dual-source-of-truth for ``version_token``.

    The ``version_token`` lives on the ``WizardState`` dataclass so it
    threads cleanly through the request lifecycle (route → service → CAS),
    but the row's ``wizard_drafts.version_token`` column is the only source
    of truth. Persisting the dataclass copy inside ``state_json`` would lag
    the column whenever an atomic CAS bumps the column but the legacy
    blind-write path does not refresh the JSON copy — readers that trust
    JSON would observe stale values. We strip it from the JSON envelope
    here so the column stays authoritative.
    """
    payload = asdict(state)
    payload.pop("version_token", None)
    return payload


class WizardDraftConflictError(RuntimeError):
    """Sec-18 R2 optimistic-lock mismatch on ``WizardStateService.advance_step``.

    Raised when ``expected_version_token`` does not match the row's current
    ``version_token`` — i.e. another tab/request advanced the draft between
    the caller's read and this write. Route handlers map this to HTTP 409.
    """


class WizardStateService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def get_or_create(
        self,
        *,
        user_id: uuid.UUID,
        organization_id: uuid.UUID,
        tx_id: uuid.UUID | None = None,
    ) -> WizardState:
        """Resolve persisted state by (user_id, tx_id) or mint a fresh tx_id.

        r2 BLOCKER 16: ``organization_id`` is now mandatory because ``WizardDraft``
        carries ``organization_id`` for multi-tenancy forward-compat (BLOCKER 14).
        ``tx_id`` is typed as ``uuid.UUID | None`` (not ``str | None``) so the
        route layer's ``request.query_params['tx']`` is parsed once at the
        boundary rather than re-parsed inside the service.
        """
        if tx_id is not None:
            row = (
                await self._db.execute(
                    select(WizardDraft).where(
                        WizardDraft.user_id == user_id,
                        WizardDraft.tx_id == tx_id,
                    )
                )
            ).scalar_one_or_none()
            if row is not None:
                # Whitelist filter against current dataclass fields so a removed field
                # in WizardState does not blow up reads of pre-removal rows.
                # Drop ``version_token`` from the JSON payload (defense in depth
                # against stale legacy rows that still have it embedded) — the
                # row's column copy is the authoritative source for the token.
                known = {f.name for f in dataclasses.fields(WizardState)} - {"version_token"}
                state = WizardState(**{k: v for k, v in row.state_json.items() if k in known})
                state.version_token = row.version_token
                return state
        # New tx_id: mint and persist a default-state row so subsequent
        # gets see it immediately.
        new_tx_id = uuid.uuid4()
        state = WizardState(tx_id=str(new_tx_id))
        self._db.add(
            WizardDraft(
                user_id=user_id,
                tx_id=new_tx_id,
                organization_id=organization_id,
                state_json=_state_json_excluding_version_token(state),
            )
        )
        await self._db.flush()
        return state

    async def advance_step(
        self,
        *,
        user_id: uuid.UUID,
        organization_id: uuid.UUID,
        state: WizardState,
        expected_version_token: int | None = None,
    ) -> None:
        """Upsert the updated state_json. Caller commits.

        r3 BLOCKER 3: ``organization_id`` is now a mandatory kwarg. The
        defensive ``if row is None`` insert path needs the FK value to
        write a schema-valid WizardDraft (organization_id is NOT NULL per
        Decision F). In the typical advance-step path the row exists from
        ``get_or_create``, but a request whose draft was swept by
        ``cleanup_expired`` between get_or_create and advance_step would
        IntegrityError without this. Route handlers pass
        ``current_user.organization_id``.

        Arch-25 R3 + Arch-9 PR1: when ``expected_version_token`` is
        provided, the upsert path is replaced by an atomic SQL ``UPDATE
        ... WHERE version_token = :expected RETURNING version_token``
        executed via the SA Core ``update()`` construct (NOT raw
        ``text()`` — Arch-9 PR1: SA Core preserves the JSON TypeDecorator
        on the ``state_json`` column, raw SQL would double-encode on
        Postgres). The check uses ``len(res.all()) == 1`` rather than the
        plan's literal ``res.rowcount != 1`` because aiosqlite's
        ``ChunkedIteratorResult`` (which SQLAlchemy returns for
        ``UPDATE ... RETURNING`` on async drivers) does NOT expose
        ``rowcount`` — accessing it raises ``AttributeError``. Counting
        the RETURNING rows is semantically equivalent and works on both
        SQLite and Postgres (and matches the standard SA pattern for
        async optimistic-locking).

        Arch-6 PR1: ``expected_version_token`` is optional (``None``
        default). Legacy callers (step-1 / step-2 / IRIS-prefill route
        handlers wired before Sec-18 enforcement) fall through to a
        blind write + token increment so we don't break their
        request-cycle during the transition. The step-3 finalize
        handler (T11) passes the explicit value parsed off the hidden
        form field for the Sec-18 optimistic-lock check.
        """
        new_state_json = _state_json_excluding_version_token(state)

        if expected_version_token is None:
            # Legacy back-compat path — preserves the pre-T9 upsert
            # semantics (re-insert-if-swept guard) but also bumps the
            # version_token column so concurrent CAS callers see motion.
            row = (
                await self._db.execute(
                    select(WizardDraft).where(
                        WizardDraft.user_id == user_id,
                        WizardDraft.tx_id == uuid.UUID(state.tx_id),
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                new_token = state.version_token + 1
                self._db.add(
                    WizardDraft(
                        user_id=user_id,
                        tx_id=uuid.UUID(state.tx_id),
                        organization_id=organization_id,
                        state_json=new_state_json,
                        version_token=new_token,
                    )
                )
                state.version_token = new_token
            else:
                row.state_json = new_state_json
                row.version_token = row.version_token + 1
                state.version_token = row.version_token
            await self._db.flush()
            return

        # Atomic CAS path (Arch-25 R3). One round-trip; works identically
        # on SQLite (no FOR UPDATE needed) and Postgres.
        new_token = expected_version_token + 1
        result = await self._db.execute(
            update(WizardDraft)
            .where(
                WizardDraft.user_id == user_id,
                WizardDraft.tx_id == uuid.UUID(state.tx_id),
                WizardDraft.version_token == expected_version_token,
            )
            .values(
                state_json=new_state_json,
                version_token=new_token,
            )
            .returning(WizardDraft.version_token)
        )
        # aiosqlite + asyncpg both return a row-iterable from
        # UPDATE...RETURNING; counting fetched rows is the portable
        # success/conflict signal. See the docstring above for why we
        # do not use ``result.rowcount``.
        if len(result.all()) != 1:
            raise WizardDraftConflictError(
                f"version_token mismatch: expected {expected_version_token} for draft {state.tx_id}"
            )
        state.version_token = new_token
        await self._db.flush()

    async def clear(self, *, user_id: uuid.UUID, tx_id: uuid.UUID | str) -> None:
        """Remove a specific (user_id, tx_id) state. Other parallel tabs preserved.

        Accepts ``tx_id`` as either ``UUID`` or ``str`` to keep wide compatibility
        with both the route layer (str query-param) and tests (UUID literal).
        """
        tx_uuid = tx_id if isinstance(tx_id, uuid.UUID) else uuid.UUID(tx_id)
        await self._db.execute(
            delete(WizardDraft).where(
                WizardDraft.user_id == user_id,
                WizardDraft.tx_id == tx_uuid,
            )
        )
        await self._db.flush()

    async def cleanup_expired(self, *, max_age_minutes: int) -> int:
        # Drafts-surfaced spec §4: swept periodically by
        # services.run_reaper.sweep_wizard_drafts, on the reaper's cadence
        # (Settings.run_reaper_interval_seconds) plus a boot one-shot.
        """Delete drafts older than max_age_minutes. Returns deleted-count.

        r3 LOW (threat-model #7): a concurrent in-flight wizard step POST
        whose draft is swept here will fail to find its row on the next
        request and degrade to redirect-to-step-1. Acceptable per r1 LOW
        review — the analyst restarts; no data is lost (draft state is
        ephemeral by design). Documented here so future readers don't
        flag this as a bug.

        Callers MUST treat the return value as best-effort. SQLite returns
        -1 in some cases, so DO NOT gate correctness on the count.
        """
        cutoff = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=max_age_minutes)
        result: CursorResult[Any] = await self._db.execute(  # type: ignore[assignment]
            delete(WizardDraft).where(WizardDraft.updated_at < cutoff)
        )
        await self._db.flush()
        # rowcount is dialect-dependent; SQLite returns -1 sometimes. Best
        # effort.
        return result.rowcount if result.rowcount is not None else 0


async def load_sme_rows(
    db: AsyncSession,
    scenario_id: uuid.UUID,
    organization_id: uuid.UUID,
) -> dict[str, list[dict[str, Any]]]:
    """#56: rehydrate persisted per-SME elicitation rows for re-estimation.

    First-ever read path for scenario_sme_estimates (written by
    wizard_finalize.persist_estimates, previously write-only). Returns the
    exact shape WizardState.sme_estimates carries and
    process_sme_estimates consumes: {fieldset: [{sme_id|sme_name, low,
    high}]}. Row order follows recorded_at then id for determinism.
    """
    rows = (
        (
            await db.execute(
                select(ScenarioSMEEstimate)
                .where(
                    ScenarioSMEEstimate.scenario_id == scenario_id,
                    ScenarioSMEEstimate.organization_id == organization_id,
                )
                .order_by(ScenarioSMEEstimate.recorded_at, ScenarioSMEEstimate.id)
            )
        )
        .scalars()
        .all()
    )
    out: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        entry: dict[str, Any] = {"low": r.low, "high": r.high}
        if r.sme_id is not None:
            entry["sme_id"] = str(r.sme_id)
        else:
            entry["sme_name"] = r.sme_name
        out.setdefault(r.fieldset.value, []).append(entry)
    return out


def seed_wizard_state_from_scenario(
    scenario: Any,
    *,
    sme_estimates: dict[str, list[dict[str, Any]]],
    mitigating_control_ids: list[str],
    tx_id: str,
) -> WizardState:
    """#56: build a re-estimation WizardState from a loaded Scenario.

    Pure mapping — callers load the scenario (IDOR-safe, org-scoped), its
    SME rows (load_sme_rows) and control ids, then persist the returned
    state via WizardStateService. current_step=2 skips the library pick:
    provenance flips to EXPERT_JUDGMENT on finalize regardless, so a pin
    would be dead weight. loss_shape derives from the stored primary-loss
    node (storage invariant since #326/#27: catastrophic <=> native
    lognormal family on pl).
    """
    pl = scenario.primary_loss or {}
    kind = str(pl.get("distribution", "")).lower()
    loss_shape = "catastrophic" if kind in ("lognormal", "lognormal_mixture") else "capped"
    if getattr(scenario, "vuln_framing", None) == "legacy_residual":
        # Meth-B1: pre-#339 vuln rows were elicited under the residual
        # wording (control discount baked in). Never rehydrate them — the
        # operator re-enters vuln under the inherent copy, which is what
        # makes finalize's vuln_framing="inherent" stamp truthful.
        sme_estimates = {k: v for k, v in sme_estimates.items() if k != "vuln"}

    def _enum_val(v: Any) -> str | None:
        return getattr(v, "value", v) if v is not None else None

    return WizardState(
        tx_id=tx_id,
        current_step=2,
        target_scenario_id=scenario.id.hex,
        target_expected_row_version=scenario.row_version,
        name=scenario.name,
        description=scenario.description,
        threat_category=_enum_val(scenario.threat_category),
        threat_actor_type=_enum_val(scenario.threat_actor_type),
        asset_class=_enum_val(scenario.asset_class),
        attack_vector=scenario.attack_vector,
        mitigating_control_ids=list(mitigating_control_ids),
        loss_shape=loss_shape,
        sme_estimates=sme_estimates,
    )


# NOTE: build_create_form() was removed 2026-07-07 (#wizard-library-prefill).
# It converted WizardState → ScenarioForm directly from state.threat_event_frequency
# etc., but had ZERO callers — orphaned by the 2026-05-28 SME-row refactor, after
# which the live finalize sources distributions from process_sme_estimates(
# state.sme_estimates). Keeping it invited the very confusion that hid the
# library-prefill bug (curated state fields look "used" but aren't).
