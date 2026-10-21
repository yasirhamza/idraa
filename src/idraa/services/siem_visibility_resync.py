"""#199 slice 1 — gated post-deploy resync of deployed SIEM controls after migration
b7c1a9e3d2f4 bumped ``security-information-event-management`` (lec_det_visibility
coverage 0.8 -> 0.3). Operator entry point: ``python -m idraa library siem-resync``
(src/idraa/__main__.py). The logic lives here so it ships in the production image
(the Dockerfile copies ``src/``, not ``scripts/``) and is mypy-gated with ``src/idraa``.

Surgical apply on UNTOUCHED library defaults; human consent for everything else
(spec §4.2.3). Every non-deleted ``controls`` row whose ``library_pin.entry_id`` is the
SIEM entry is partitioned into:

  current  library_pin.version >= the entry's version — nothing to resync (the
           re-run-after-apply case)
  A        untouched default: snapshot present, lec_det_visibility row present, its
           current coverage equals the snapshot's, confirmed_by_user_at IS NULL, the
           current assignment SET equals the snapshot's, AND (R-SIEM-16a) the pin is
           exactly one version behind, the snapshot's Visibility coverage is
           OLD_COVERAGE, the entry's is NEW_COVERAGE, every other snapshot
           (capability, coverage, reliability) equals the entry's, AND every
           entry-level field the consent page diffs (control_resync._entry_field_values:
           name, description, annual_cost, nist_csf_functions, iso_27001_domains,
           compliance_mappings incl. cis_safeguards, applicable_industries,
           applicable_org_sizes; `type` skipped as resync_info skips it) equals the
           snapshot's — so the one cell is the WHOLE library delta between the adopted
           and the latest version
  B        as A but confirmed_by_user_at set
  C        user-tuned: the current Visibility coverage differs from the snapshot's, or
           the current assignment set differs from the snapshot's; ALSO the landing
           class of an A whose guarded write found the row changed under it (below)
  D        legacy: no adopted_snapshot (pre-#438 adoption)
  X        other library delta: pinned more than one version back (or an unparsable
           pin version — a non-integer is never normalised), or the snapshot
           differs from the latest entry anywhere but the one cell — an assignment
           triple, the assignment set (a pre-Epic-F snapshot without lec_det_visibility
           included) or an entry-level field (an in-place tag repair with no version
           bump, the f6a2b0d4e8c3 "Effect 2" shape) — the analyst decides on the diff
           page

Why X exists (plan-gate Sec-I2 / A-N1 / Sec2-I2): ``resync_info`` reports stale from
the pin version alone (control_resync.py:135), so re-pinning a control hides every
other library change — assignment OR field — from the consent page. Class A is
admitted only when nothing else changed; R-SIEM-21 (stale=False after apply) is then
honest. The field check imports the consent page's own ``_entry_field_values`` rather
than re-implementing it, so a field the diff gains later is covered automatically.

Dry by default: prints ``mode=<dry-run|apply> db=<dialect>:<file basename>`` (a
non-secret target identifier — never the URL, which on other dialects carries
credentials), one ``<control_id_hex> <class>`` line per control, then the class
counts — never a control/org name, description or stored value. Dry mode installs
``PRAGMA query_only=ON`` and ``PRAGMA busy_timeout=30000`` (a literal, so no Settings
are needed) on every connection WHATEVER the URL form and rewrites a bare
``sqlite+aiosqlite:///<path>`` URL to ``file:<path>?mode=ro&uri=true``. SQLite only:
any other dialect exits 2 before a connection is made (N9). ``--apply`` refuses
(exit 2, no write) when the stored entry's Visibility coverage is not NEW_COVERAGE —
the bump migration has not run, or the stored value drifted (R-SIEM-24). ``run()``
is one catch-all: every failure outside the two refusal branches prints the exception
TYPE only (R-SIEM-26), so a malformed DSN or a Settings error never prints a traceback.

``--apply`` changes class A only, one control per transaction, and is RACE-SAFE BY
CONSTRUCTION rather than by isolation (plan-gate A2-I3 / Sec2-N1): pysqlite issues
BEGIN only before the first DML, so the per-control SELECTs run in autocommit and a
re-read alone cannot exclude an analyst save that lands between read and write. Both
writes are therefore conditional Core UPDATEs guarded on the values just read, each
checked for ``rowcount == 1``: the lec_det_visibility row (``WHERE id = :row AND
control_id = :control AND organization_id = :org AND coverage = :read AND
confirmed_by_user_at IS NULL``) -> ``coverage`` = the entry's ``coverage_default``;
the control row (``WHERE id = :control AND organization_id = :org AND
json_extract(library_pin, '$.version') = :pinned``) -> ``library_pin`` at the entry
version and ``adopted_snapshot`` with that cell and ``version`` updated (the shape
_build_adopted_snapshot and apply_resync write); ``confirmed_by_user_at`` stays NULL.
A ``rowcount`` other than 1 on either rolls the control's transaction back, writes
nothing else for it, and reports it as class C with a ``conflicts`` count — the
analyst decides on the diff page; the audit row's ``old`` value is therefore never a
stale read. The REVERSE window is the app's general no-optimistic-locking property,
not a CLI guarantee: an edit form opened before ``--apply`` and saved after it writes
the analyst's values over the tool's (confirmed, pin at the new version) — the
analyst's later save wins, which is the safe direction, and the two audit rows record
both writes. On success: COMPLETED runs that used the control are flagged stale
(``flag_runs_stale_for_control``, keyed by the control's own organization_id) and one
``AuditWriter`` row is written per control, action ``control.resync_from_library``,
``user_id = NULL`` (system actor), ``changes = {"version": [old, new],
"lec_det_visibility.coverage": [old, new], "runs_flagged_stale": [None, n],
"reason": ["", REASON]}`` — the ``reason`` says no human consented. ``apply_resync``
is deliberately NOT reused: it replaces the whole assignment set and would clobber
tuning on SIEM's other members; the surgical step changes one cell.

Lane notes: no numeric-module import (M-B rule, tests/scripts/test_clerk_config.py)
and no ``assert`` (S101). The module loads org-scoped models and calls AuditWriter,
so it is a security-lane file (security lane manifest glob; escalation-path tag
"org-scoping" — the manifest file names are deliberately not spelled here:
tests/arch/test_clerk_isolation.py scans every tracked file for them). Its one
bare PK fetch (``db.get(Control, …)``) carries the sanctioned ``# org-scope: ok``
reason — this CLI IS the cross-org sweep (threat-model section 7, B7; recorded
there) — and the linter scripts/lint_org_scoped_lookups.py is never edited. The
pre-deploy sweep (scripts/sweep_issue_199_prod_rows.py) imports the pure helpers
below; it cannot apply the entry-field check (raw sqlite3 rows), so its class-A
and class-B counts are upper bounds (the CLI also demotes either class to X on an
entry-level field delta).
"""

from __future__ import annotations

import copy
import sys
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any, Literal, cast

from sqlalchemy import CursorResult, event, func, select, update
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

SIEM_SLUG = "security-information-event-management"
SUB_FUNCTION = "lec_det_visibility"
ACTION = "control.resync_from_library"
REASON = "issue-199 SIEM visibility coverage, untouched library default"
OLD_COVERAGE = 0.8  # pinned equal to the migration's _OLD_COVERAGE by a test
NEW_COVERAGE = 0.3  # pinned equal to the migration's _NEW_COVERAGE by a test
_TOL = 1e-9
_SQLITE_PREFIX = "sqlite+aiosqlite:///"

Cls = Literal["A", "B", "C", "D", "X"]
VersionClass = Literal["current", "X", "classify"]
CLASSES: tuple[str, ...] = ("A", "B", "C", "D", "X", "current")
Triple = tuple[float | None, float | None, float | None]
"""(capability, coverage, reliability) of one assignment; None where the column is NULL."""


class EntryNotAtTargetError(RuntimeError):
    """``--apply`` requested while the stored SIEM entry's Visibility coverage is not
    NEW_COVERAGE (bump migration not yet applied, or drift). The message carries no data."""


def close_enough(a: float | None, b: float | None) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return abs(float(a) - float(b)) <= _TOL


@dataclass
class Summary:
    entry_found: bool
    entry_version: int | None = None
    by_class: dict[str, list[str]] = field(default_factory=lambda: {c: [] for c in CLASSES})
    applied: list[str] = field(default_factory=list)
    runs_flagged: int = 0
    # Class-A controls whose guarded write found the row changed under it; they are
    # reported in by_class["C"] as well (R-SIEM-18). Always empty in dry mode.
    conflicts: list[str] = field(default_factory=list)


def pin_matches_entry(pin: object, entry_id_hex: str) -> bool:
    if not isinstance(pin, dict):
        return False
    try:
        return uuid.UUID(str(pin.get("entry_id"))).hex == entry_id_hex
    except (ValueError, TypeError, AttributeError):
        return False


def pin_version(pin: object) -> int | None:
    """The pin's integer ``version``; None (-> class X in both modes) for a missing,
    null, boolean or non-integer value. Every writer stores an int (controls.py:596,
    control_resync.py:304); a string such as ``"2"`` is treated as unparsable rather
    than normalised, so the dry run and ``--apply`` agree (the guarded UPDATE compares
    ``json_extract`` INTEGER to INTEGER and would otherwise report a spurious conflict)."""
    if not isinstance(pin, dict):
        return None
    version: Any = pin.get("version")
    if isinstance(version, bool) or not isinstance(version, int):
        return None
    return version


def snapshot_triples(snapshot: Mapping[str, Any]) -> dict[str, Triple] | None:
    """``{sub_function: (capability, coverage, reliability)}`` from an ``adopted_snapshot``
    (``_build_adopted_snapshot`` shape); None when the snapshot is malformed."""
    rows = snapshot.get("assignments")
    if not isinstance(rows, list):
        return None
    out: dict[str, Triple] = {}
    for a in rows:
        if not isinstance(a, dict) or not isinstance(a.get("sub_function"), str):
            return None
        out[a["sub_function"]] = (a.get("capability"), a.get("coverage"), a.get("reliability"))
    return out


def version_class(
    pinned_version: int | None, entry_version: int, *, pre_bump: bool = False
) -> VersionClass:
    """Post-bump (this CLI, and the sweep after the migration): pin >= entry -> current;
    pin == entry - 1 -> classify; older or unparsable -> X. Pre-bump (the sweep before the
    migration, R-DEP-1a): the stored entry is still at the old version, so pin == entry ->
    classify (it becomes one behind after the bump); older -> X."""
    if pinned_version is None:
        return "X"
    if not pre_bump and pinned_version >= entry_version:
        return "current"
    expected = entry_version if pre_bump else entry_version - 1
    return "classify" if pinned_version == expected else "X"


def classify_siem_control(
    snapshot: Mapping[str, Any] | None,
    assignments: Sequence[tuple[str, float, bool]],
    *,
    target: Mapping[str, Triple],
) -> Cls:
    """Pure partition (module docstring). ``assignments`` = ``(sub_function, coverage,
    confirmed)`` per CURRENT row; ``target`` = the entry the control will be compared with
    (post-bump: the stored latest version; pre-bump sweep: the stored version with the
    Visibility cell moved to NEW_COVERAGE)."""
    if snapshot is None:
        return "D"
    snap = snapshot_triples(snapshot)
    if snap is None or set(snap) != set(target):
        return "X"  # malformed, or the library changed the assignment SET (pre-Epic-F snapshot)
    current = {sf: (cov, confirmed) for sf, cov, confirmed in assignments}
    if set(current) != set(snap) or SUB_FUNCTION not in current:
        return "C"
    cov, confirmed = current[SUB_FUNCTION]
    snap_cov = snap[SUB_FUNCTION][1]
    if snap_cov is None or not close_enough(cov, snap_cov):
        return "C"
    # R-SIEM-16a: the one cell must be the WHOLE library delta.
    if not close_enough(snap_cov, OLD_COVERAGE):
        return "X"
    if not close_enough(target[SUB_FUNCTION][1], NEW_COVERAGE):
        return "X"
    for sf, (s_cap, s_cov, s_rel) in snap.items():
        t_cap, t_cov, t_rel = target[sf]
        if not close_enough(s_cap, t_cap) or not close_enough(s_rel, t_rel):
            return "X"
        if sf != SUB_FUNCTION and not close_enough(s_cov, t_cov):
            return "X"
    return "B" if confirmed else "A"


def entry_fields_match(snapshot: Mapping[str, Any], entry_fields: Mapping[str, Any]) -> bool:
    """R-SIEM-16a (iv): True when every entry-level field the consent page diffs equals the
    adopted snapshot's value, i.e. ``resync_info`` would show no ``library_changed`` field row.
    ``entry_fields`` is ``control_resync._entry_field_values(entry)`` (the consent page's own
    oracle); ``type`` is skipped exactly as ``resync_info`` skips it. A key missing from the
    snapshot compares as None and therefore mismatches. Pure."""
    return all(snapshot.get(key) == value for key, value in entry_fields.items() if key != "type")


def dry_run_url(url: str) -> str:
    if url.startswith(_SQLITE_PREFIX) and "?" not in url:
        return f"{_SQLITE_PREFIX}file:{url[len(_SQLITE_PREFIX) :]}?mode=ro&uri=true"
    return url


def _target_label(url: str) -> str:
    """``<dialect>:<file basename>`` — never the URL (a DSN may carry credentials)."""
    u = make_url(url)
    database = (u.database or "").removeprefix("file:")
    return f"{u.get_backend_name()}:{PurePosixPath(database).name}"


def _query_only(dbapi_conn: object, _record: object) -> None:
    cur = dbapi_conn.cursor()  # type: ignore[attr-defined]
    cur.execute("PRAGMA query_only=ON")
    # A2-N3: against the live WAL database the dry run would otherwise rely on sqlite3's
    # 5 s default; a literal (not Settings) so `--database-url` dry runs need no env.
    cur.execute("PRAGMA busy_timeout=30000")
    cur.close()


def build_engine(url: str, *, apply: bool) -> AsyncEngine:
    """Apply: the project's SQLite pragmas (FK, WAL, synchronous, Settings busy_timeout).
    Dry: ``PRAGMA query_only=ON`` + ``busy_timeout=30000`` on every connection, so a write
    raises whatever the URL says and a contended read waits instead of failing."""
    from idraa.db import _install_sqlite_pragmas, strict_json_dumps

    engine = create_async_engine(url, future=True, json_serializer=strict_json_dumps)
    if apply:
        _install_sqlite_pragmas(engine)
    else:
        event.listen(engine.sync_engine, "connect", _query_only)
    return engine


def _f(value: float | None) -> float | None:
    return None if value is None else float(value)


async def entry_triples(db: AsyncSession, entry_id: uuid.UUID, version: int) -> dict[str, Triple]:
    from idraa.models.control_library import ControlLibraryEntryAssignment

    rows = (
        (
            await db.execute(
                select(ControlLibraryEntryAssignment).where(
                    ControlLibraryEntryAssignment.library_entry_id == entry_id,
                    ControlLibraryEntryAssignment.library_entry_version == version,
                )
            )
        )
        .scalars()
        .all()
    )
    return {
        r.sub_function.value: (
            _f(r.capability_default),
            _f(r.coverage_default),
            _f(r.reliability_default),
        )
        for r in rows
    }


async def _before_write(control_id: uuid.UUID) -> None:
    """Test seam, a no-op in production: awaited after the classifier read and before the
    guarded UPDATEs so a test can commit a concurrent analyst edit in between (R-T-22)."""


async def _apply_class_a(
    db: AsyncSession,
    *,
    control: Any,
    rows: Sequence[Any],
    pinned: int,
    entry_id: uuid.UUID,
    entry_version: int,
    new_cov: float,
    summary: Summary,
) -> bool:
    """Guarded write of ONE class-A control (R-SIEM-18). Returns True after commit; False after
    rolling the control's transaction back because a guarded UPDATE did not hit exactly one row
    (a concurrent analyst tune / confirm / resync landed between read and write)."""
    from idraa.models.control import Control
    from idraa.models.control_function_assignment import ControlFunctionAssignment
    from idraa.services.audit import AuditWriter
    from idraa.services.control_library import flag_runs_stale_for_control

    # Capture everything we need from the ORM objects BEFORE any write: a rollback expires
    # them, and an expired attribute cannot be lazily refreshed on an async session.
    control_id: uuid.UUID = control.id
    org_id: uuid.UUID = control.organization_id
    vis = next(r for r in rows if r.sub_function.value == SUB_FUNCTION)
    vis_id: uuid.UUID = vis.id
    old_cov = float(vis.coverage)
    snapshot = copy.deepcopy(control.adopted_snapshot or {})
    for a in snapshot.get("assignments", []):
        if a.get("sub_function") == SUB_FUNCTION:
            a["coverage"] = new_cov
    snapshot["version"] = entry_version
    new_pin = {"entry_id": str(entry_id), "version": entry_version}

    await _before_write(control_id)
    # (a) the Visibility row, guarded on the coverage just read and on "still unconfirmed",
    # and keyed to its control AND its own organization_id (the row carries one through
    # OrgMixin, control_function_assignment.py — defence in depth, risk S1, Sec4-I1).
    # AsyncSession.execute is typed Result, which has no rowcount: cast to CursorResult
    # (the services/webauthn_challenge.py precedent).
    vis_result = cast(
        CursorResult[Any],
        await db.execute(
            update(ControlFunctionAssignment)
            .where(
                ControlFunctionAssignment.id == vis_id,
                ControlFunctionAssignment.control_id == control_id,
                ControlFunctionAssignment.organization_id == org_id,
                ControlFunctionAssignment.coverage == old_cov,
                ControlFunctionAssignment.confirmed_by_user_at.is_(None),
            )
            .values(coverage=new_cov)
            .execution_options(synchronize_session=False)
        ),
    )
    if vis_result.rowcount != 1:
        await db.rollback()
        return False
    # (b) the control row, keyed by its own id AND organization_id (risk S1, defence in
    # depth) and guarded on the pin version just read (SQLite JSON1 path extraction; the
    # CLI is SQLite-only, N9). A concurrent apply_resync re-pins -> rowcount 0 -> C.
    control_result = cast(
        CursorResult[Any],
        await db.execute(
            update(Control)
            .where(
                Control.id == control_id,
                Control.organization_id == org_id,
                func.json_extract(Control.library_pin, "$.version") == pinned,
            )
            .values(library_pin=new_pin, adopted_snapshot=snapshot)
            .execution_options(synchronize_session=False)
        ),
    )
    if control_result.rowcount != 1:
        await db.rollback()
        return False
    flagged = await flag_runs_stale_for_control(db, organization_id=org_id, control_id=control_id)
    await AuditWriter(db).log(
        organization_id=org_id,
        entity_type="control",
        entity_id=control_id,
        action=ACTION,
        changes={
            "version": [pinned, entry_version],
            f"{SUB_FUNCTION}.coverage": [old_cov, new_cov],
            "runs_flagged_stale": [None, flagged],
            "reason": ["", REASON],
        },
        user_id=None,
    )
    await db.commit()
    summary.applied.append(control_id.hex)
    summary.runs_flagged += flagged
    return True


async def resync(db: AsyncSession, *, apply: bool) -> Summary:
    from idraa.models.control import Control
    from idraa.models.control_function_assignment import ControlFunctionAssignment
    from idraa.models.enums import EntityStatus
    from idraa.services.control_library import ControlLibraryService
    from idraa.services.control_resync import _entry_field_values

    entry = await ControlLibraryService(db).get_published_by_slug(SIEM_SLUG)
    if entry is None:
        return Summary(entry_found=False)
    target = await entry_triples(db, entry.id, entry.version)
    new_cov = target.get(SUB_FUNCTION, (None, None, None))[1]
    if new_cov is None:
        raise RuntimeError(
            "SIEM entry carries no lec_det_visibility coverage at its latest version"
        )
    if apply and not close_enough(new_cov, NEW_COVERAGE):
        raise EntryNotAtTargetError("stored SIEM Visibility coverage is not the slice-1 target")
    # R-SIEM-16a (iv): the consent page's own entry-level field set (Sec2-I2).
    entry_fields = _entry_field_values(entry)
    summary = Summary(entry_found=True, entry_version=entry.version)
    # Plain locals for everything the loop reads from `entry`: a rollback inside
    # _apply_class_a expires every ORM object in the session, including `entry`, and an
    # expired attribute cannot be lazily refreshed on an async session (MissingGreenlet).
    # Nothing below touches `entry` again (plan-gate A3-I1).
    entry_id: uuid.UUID = entry.id
    entry_version: int = entry.version
    entry_id_hex = entry_id.hex

    control_ids = (
        (
            await db.execute(
                select(Control.id).where(
                    Control.status != EntityStatus.DELETED, Control.library_pin.is_not(None)
                )
            )
        )
        .scalars()
        .all()
    )
    for cid in sorted(control_ids, key=lambda c: c.hex):
        # Fresh read per control (plan-gate Sec-N5). The bare PK lookup is deliberate: this
        # CLI IS the cross-org sweep (threat-model section 7, B7 — recorded there); every
        # write in _apply_class_a is keyed by the fetched row's own organization_id.
        control = await db.get(  # org-scope: ok — B7 operator sweep; writes keyed by control.organization_id
            Control, cid, populate_existing=True
        )
        if control is None or not pin_matches_entry(control.library_pin, entry_id_hex):
            continue
        cid_hex = control.id.hex  # captured before any write: a rollback expires `control`
        pinned = pin_version(control.library_pin)
        vc = version_class(pinned, entry_version)
        if vc == "current":
            summary.by_class["current"].append(cid_hex)
            continue
        rows = (
            (
                await db.execute(
                    select(ControlFunctionAssignment).where(
                        ControlFunctionAssignment.control_id == control.id
                    )
                )
            )
            .scalars()
            .all()
        )
        cls: str = "X"
        if vc != "X":
            cls = classify_siem_control(
                control.adopted_snapshot,
                [
                    (r.sub_function.value, float(r.coverage), r.confirmed_by_user_at is not None)
                    for r in rows
                ],
                target=target,
            )
            if cls in ("A", "B") and not entry_fields_match(
                control.adopted_snapshot or {}, entry_fields
            ):
                cls = "X"  # an entry-level field delta the consent page would show (R-SIEM-16a iv)
        if cls == "A" and apply and pinned is not None:
            written = await _apply_class_a(
                db,
                control=control,
                rows=rows,
                pinned=pinned,
                entry_id=entry_id,
                entry_version=entry_version,
                new_cov=new_cov,
                summary=summary,
            )
            if not written:
                cls = "C"  # changed under us between read and write: the analyst decides
                summary.conflicts.append(cid_hex)
        summary.by_class[cls].append(cid_hex)
    return summary


def print_summary(summary: Summary, *, apply: bool, target: str) -> None:
    mode = "apply" if apply else "dry-run"
    head = f"issue-199 siem visibility resync mode={mode} db={target}"
    if not summary.entry_found:
        print(f"{head} entry_missing=1")
        return
    print(f"{head} entry_version={summary.entry_version}")
    for cls in CLASSES:
        for cid in summary.by_class[cls]:
            print(f"{cid} {cls}")
    counts = " ".join(f"{cls}={len(summary.by_class[cls])}" for cls in CLASSES)
    print(
        f"classes {counts} applied={len(summary.applied)} "
        f"runs_flagged={summary.runs_flagged} conflicts={len(summary.conflicts)}"
    )


async def run(*, apply: bool, database_url: str | None) -> int:
    """CLI body. Exit 0 ok; 2 unsupported dialect / apply refused; 3 internal error (type only).

    The WHOLE body — settings resolution, URL parsing, the dialect check, engine construction,
    the resync and the summary print — sits inside one catch-all (R-SIEM-26): a malformed
    ``--database-url`` (SQLAlchemy ArgumentError) or a Settings ValidationError (pydantic prints
    ``input_value=``) must never reach the terminal as a traceback carrying the DSN or a secret.
    """
    engine: AsyncEngine | None = None
    target = "unresolved"
    try:
        from idraa.config import get_settings

        url = database_url or get_settings().database_url
        dialect = make_url(url).get_backend_name()
        if dialect != "sqlite":
            print(
                f"issue-199 siem visibility resync: unsupported dialect {dialect}; sqlite only (N9)",
                file=sys.stderr,
            )
            return 2
        target = _target_label(url)
        if not apply:
            url = dry_run_url(url)
        engine = build_engine(url, apply=apply)
        sm = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with sm() as db:
            summary = await resync(db, apply=apply)
        print_summary(summary, apply=apply, target=target)
        return 0
    except EntryNotAtTargetError:
        print(
            f"issue-199 siem visibility resync mode=apply db={target} refused: the stored SIEM "
            "entry is not at the slice-1 Visibility coverage (run the bump migration first)",
            file=sys.stderr,
        )
        return 2
    except Exception as exc:  # disclosure rule: type only, never the message
        print(f"internal error: {type(exc).__name__}", file=sys.stderr)
        return 3
    finally:
        if engine is not None:
            await engine.dispose()
