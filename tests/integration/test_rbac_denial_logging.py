"""C1: an RBAC denial (require_role 403) emits a structured WARNING so
privilege-probing / id-enumeration is visible to operators. It previously
left zero trace — the 403 returned with no log and no audit row.

WARNING (not a DB AuditLog row) is deliberate: it keeps the signal bounded by
log rotation rather than turning an unauthenticated prober into a DB-write
amplifier. The log carries the actor UUID, role, required roles, and path —
never the raw email (audit-redaction invariant).
"""

from __future__ import annotations

import logging

from httpx import AsyncClient

from tests.conftest import csrf_post


async def test_forbidden_role_logs_rbac_denied(analyst_client: AsyncClient, caplog) -> None:
    # /qualitative-bands POST is admin-only with NO step-up gate, so an analyst
    # is 403'd cleanly at require_role (no step-up redirect to muddy the test).
    with caplog.at_level(logging.WARNING, logger="idraa.routes.deps"):
        r = await csrf_post(analyst_client, "/qualitative-bands", {}, follow_redirects=False)
    assert r.status_code == 403

    denials = [rec for rec in caplog.records if "rbac_denied" in rec.getMessage()]
    assert denials, "RBAC 403 produced no rbac_denied log record"
    msg = denials[-1].getMessage()
    assert "/qualitative-bands" in msg
    assert "role=analyst" in msg
    assert "required=" in msg
    # The email must never appear — only the UUID actor id.
    assert "@" not in msg
