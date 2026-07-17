"""Controls CSV import — uploads the FAIR-CAM starter library.

Multipart POSTs go through CSRFMiddleware too — ``csrf_post`` injects
``_csrf`` into the (otherwise empty) ``data`` dict, and ``files=`` is
forwarded to ``client.post`` so httpx builds a multipart body that
carries BOTH the form field and the upload (deviation D2 from the
1.2.4 task brief).
"""

from __future__ import annotations

from pathlib import Path

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.control import Control
from tests.conftest import csrf_post

CSV = Path("docs/reference/fair-cam-controls-library.csv")


async def test_import_skips_blanks_and_imports_rows(
    authed_admin: tuple[AsyncClient, object], db_session: AsyncSession
) -> None:
    client, _ = authed_admin
    assert CSV.exists()
    with CSV.open("rb") as fh:
        r = await csrf_post(
            client,
            "/controls/import",
            {},
            files={"file": ("lib.csv", fh, "text/csv")},
            follow_redirects=False,
        )
    assert r.status_code == 303
    rows = (await db_session.execute(select(Control))).scalars().all()
    # Sanity: at least 50 controls (CSV has ~570 data rows, even with narrative filtering)
    assert len(rows) >= 50
    # Each row has a non-blank name
    assert all(r.name.strip() for r in rows)


async def test_import_reads_type_column_per_row(
    authed_admin: tuple[AsyncClient, object], db_session: AsyncSession
) -> None:
    """Regression: the FAIR-CAM library import previously hardcoded
    type=ControlType.ADMINISTRATIVE for every row because the CSV had no
    Type column. The CSV now has a curated Type column (col 4); the
    importer must honour it. After import, the library should produce a
    MIX of types (specific known-correct mappings asserted below)."""
    from collections import Counter

    from idraa.models.enums import ControlType

    client, _ = authed_admin
    with CSV.open("rb") as fh:
        await csrf_post(client, "/controls/import", {}, files={"file": ("lib.csv", fh, "text/csv")})
    rows = (await db_session.execute(select(Control))).scalars().all()
    by_name = {r.name: r.type for r in rows}

    # Spot-check: clear-cut technical controls.
    assert by_name["Multi-factor Authentication (MFA)"] == ControlType.TECHNICAL
    assert by_name["Network Firewall (NFW)"] == ControlType.TECHNICAL
    assert by_name["Data at Rest Encryption (DRE)"] == ControlType.TECHNICAL
    # Spot-check: clear-cut administrative controls.
    assert by_name["Acceptable Use Policy (AUP)"] == ControlType.ADMINISTRATIVE
    assert by_name["Security Awareness and Training (SAT)"] == ControlType.ADMINISTRATIVE
    assert by_name["Incident Response (IR)"] == ControlType.ADMINISTRATIVE

    # Distribution: must be a mix, not all-administrative (the bug).
    type_counts = Counter(r.type for r in rows)
    assert type_counts[ControlType.TECHNICAL] >= 30, (
        f"Expected most controls TECHNICAL; got {type_counts}"
    )
    assert type_counts[ControlType.ADMINISTRATIVE] >= 15, (
        f"Expected meaningful ADMINISTRATIVE share; got {type_counts}"
    )


async def test_import_reads_annual_cost_column_per_row(
    authed_admin: tuple[AsyncClient, object], db_session: AsyncSession
) -> None:
    """Issue #65: the FAIR-CAM library CSV now carries a curated Annual cost
    (USD) column (col 6) with bucketed order-of-magnitude defaults. The
    importer must honour it. Pre-fix every imported control got annual_cost=0
    and operators had to hand-edit each — for a 61-control library that was 61
    edit clicks before realistic UAT could exercise cost-aware reporting.

    Spot-checks below pin the 6 buckets in the methodology doc at
    docs/reference/fair-cam-controls-library-cost-methodology.md.
    """
    from collections import Counter
    from decimal import Decimal

    client, _ = authed_admin
    with CSV.open("rb") as fh:
        await csrf_post(client, "/controls/import", {}, files={"file": ("lib.csv", fh, "text/csv")})
    rows = (await db_session.execute(select(Control))).scalars().all()
    by_name = {r.name: r.annual_cost for r in rows}

    # Distribution: importer must populate from CSV, not default-to-0.
    cost_counts = Counter(r.annual_cost for r in rows)
    assert cost_counts[Decimal("0")] == 0, (
        f"library should ship with all 61 controls priced; got {cost_counts[Decimal('0')]} zero-cost"
    )
    distinct_buckets = {c for c in cost_counts if c > 0}
    assert len(distinct_buckets) >= 5, (
        f"expected ≥5 distinct cost buckets; got {sorted(distinct_buckets)}"
    )

    # Spot-check one row from each of the 6 documented buckets.
    assert by_name["Acceptable Use Policy (AUP)"] == Decimal("15000")  # admin-light
    assert by_name["Incident Response (IR)"] == Decimal("60000")  # admin-heavy
    assert by_name["Cyber Insurance (CI)"] == Decimal("150000")  # admin-special
    assert by_name["Multi-factor Authentication (MFA)"] == Decimal("30000")  # tech-infra
    assert by_name["Static Application Security Testing (SAST)"] == Decimal(
        "50000"
    )  # tech-hardening
    assert by_name["Endpoint Detection and Response (EDR)"] == Decimal("100000")  # tech-detection


async def test_reimport_skips_duplicates_by_name(
    authed_admin: tuple[AsyncClient, object], db_session: AsyncSession
) -> None:
    client, _ = authed_admin
    with CSV.open("rb") as fh:
        await csrf_post(
            client,
            "/controls/import",
            {},
            files={"file": ("lib.csv", fh, "text/csv")},
        )
    count_first = (await db_session.execute(select(Control))).scalars().all()
    with CSV.open("rb") as fh:
        await csrf_post(
            client,
            "/controls/import",
            {},
            files={"file": ("lib.csv", fh, "text/csv")},
        )
    count_second = (await db_session.execute(select(Control))).scalars().all()
    assert len(count_first) == len(count_second)


async def test_import_rejects_oversized_upload(
    authed_admin: tuple[AsyncClient, object], db_session: AsyncSession
) -> None:
    """Uploads > MAX_UPLOAD_BYTES are rejected with 413 before import runs."""
    from idraa.routes.deps import MAX_UPLOAD_BYTES

    client, _ = authed_admin
    oversized = b"name,description,domain\n" + b"x," * (MAX_UPLOAD_BYTES // 2 + 100)
    r = await csrf_post(
        client,
        "/controls/import",
        {},
        files={"file": ("huge.csv", oversized, "text/csv")},
        follow_redirects=False,
    )
    assert r.status_code == 413
    # No rows created.
    rows = (await db_session.execute(select(Control))).scalars().all()
    assert rows == []


async def test_non_admin_cannot_import(
    authed_admin: tuple[AsyncClient, object], db_session: AsyncSession
) -> None:
    """ANALYST role cannot hit /controls/import — ADMIN-only endpoint."""
    from sqlalchemy import select as _select

    from idraa.models.enums import UserRole
    from idraa.models.organization import Organization
    from idraa.services.auth import SESSION_COOKIE
    from tests.factories import create_user, login_client_as

    client, _ = authed_admin
    org = (await db_session.execute(_select(Organization))).scalar_one()
    analyst = await create_user(db_session, org, email="analyst@test.local", role=UserRole.ANALYST)
    cookie = await login_client_as(db_session, analyst)
    client.cookies.set(SESSION_COOKIE, cookie)

    r = await client.get("/controls/import")
    assert r.status_code == 403

    # And the POST is also locked (even with a valid-looking body).
    r = await csrf_post(
        client,
        "/controls/import",
        {},
        files={"file": ("tiny.csv", b"name,description,domain\n", "text/csv")},
        follow_redirects=False,
    )
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# PR iota: importer audit-action sentinel + assignment creation (spec §8.2, OQ5)
# ---------------------------------------------------------------------------


def test_importer_audit_action_is_dotted_not_legacy() -> None:
    """Sentinel: controls_importer.py must use action='control.import', not legacy 'import'.

    OQ5 (spec §8.2): The rename from 'import' to 'control.import' aligns with the
    project-wide <entity>.<verb> taxonomy. This sentinel guards the dotted action
    string so future refactors preserve the hygiene applied in this PR.
    """
    import inspect

    import idraa.services.controls_importer as importer_mod

    source = inspect.getsource(importer_mod)
    assert 'action="control.import"' in source, (
        "controls_importer.py must use action='control.import' (OQ5). "
        "The rename from 'import' was applied in the PR iota hygiene sweep. "
        "Do not revert this change."
    )
    assert 'action="import"' not in source, (
        "Found legacy action='import' in controls_importer.py. "
        "OQ5 renamed this to 'control.import' — revert to the post-rename state."
    )
