"""P2b Task 10: the flat one-click FAIR-CAM library import is retired.

Browse + adopt (`/controls/library`) replaces the old one-click flat import.
The arbitrary user-CSV import (`POST /controls/import`) STAYS — only the
canonical-library one-click route is removed.

Note: ``test_flat_library_import_route_gone`` POSTs via the CSRF-aware
``csrf_post`` helper rather than a bare ``client.post``. The CSRF middleware
rejects an unauthenticated/cookie-less POST with 403 *before* route resolution,
which would mask the route-gone 404. Bootstrapping the CSRF cookie lets the
request reach the router, so the assertion exercises the real claim (no such
route) instead of a CSRF false-positive.
"""

import pytest

from tests.conftest import csrf_post


@pytest.mark.asyncio
async def test_flat_library_import_route_gone(admin_client):
    r = await csrf_post(admin_client, "/controls/import/library", {})
    assert r.status_code == 404  # route removed


@pytest.mark.asyncio
async def test_arbitrary_csv_import_still_present(admin_client):
    r = await admin_client.get("/controls/import")
    assert r.status_code == 200  # the user-CSV import stays
