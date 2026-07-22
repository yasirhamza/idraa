from __future__ import annotations

from httpx import AsyncClient


async def test_sidebar_links_account_security(admin_client: AsyncClient) -> None:
    """The sidebar footer must link to /account/security for signed-in users.

    Under AUTH_MFA_POLICY=optional (the soft-launch rollout state) the
    enrollment interstitial never fires, so this link is the ONLY way users
    discover MFA/passkey enrollment. Regression-guards the soft-launch path.
    """
    r = await admin_client.get("/", follow_redirects=False)
    assert r.status_code == 200
    assert 'href="/account/security"' in r.text
