"""Issue #129 T5 — _assignment_row HTMX dispatch tests."""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.user import User


@pytest.mark.asyncio
async def test_no_sub_function_renders_default_widget(authed_analyst):
    """Legacy path: ?index=N alone returns the existing capability widget.

    Scope the unit-keyword assertions to the capability block — after the
    combobox-race fix inlined `sub_function_groups_json` into the row's
    x-data, the response body now embeds every sub-function description
    (including the strings "days" and "per event") regardless of which
    widget renders. The widget-itself contract is what matters here.
    """
    client, _ = authed_analyst
    r = await client.get("/controls/_assignment_row?index=2")
    assert r.status_code == 200
    # PR 2 changed the label from `<label class="label"><span class="label-text">…`
    # to `<label class="text-meta text-ink-2">Capability…`, so the split anchor
    # must not include the closing `<` (unit suffix may follow).
    cap_block = r.text.split(">Capability")[1].split(">Coverage<")[0]
    assert "days" not in cap_block
    assert "$/event" not in cap_block


@pytest.mark.asyncio
async def test_coverage_reliability_inputs_bounded_0_1(authed_analyst):
    """Coverage & reliability are proportions in [0,1]: their inputs must carry
    min="0"/max="1" and a decimal step. Regression for the form_field number
    default (step=1 -> rejects 0.9 with "nearest 0.8/1.8"; no max -> allows >1)."""
    client, _ = authed_analyst
    r = await client.get("/controls/_assignment_row?index=0")
    assert r.status_code == 200
    html = r.text
    for field in ("coverage", "reliability"):
        idx = html.index(f'name="assignments[0][{field}]"')
        start = html.rindex("<input", 0, idx)
        tag = html[start : html.index(">", idx)]
        assert 'min="0"' in tag, f"{field} input missing min=0: {tag}"
        assert 'max="1"' in tag, f"{field} input missing max=1 (would allow >1): {tag}"
        assert 'step="0.01"' in tag, f"{field} input missing decimal step: {tag}"
        assert 'step="1"' not in tag, f"{field} input has step=1 (rejects 0.9): {tag}"


@pytest.mark.asyncio
async def test_elapsed_time_sub_function_renders_days_widget(authed_analyst):
    client, _ = authed_analyst
    r = await client.get("/controls/_assignment_row?index=0&sub_function=lec_det_monitoring")
    assert r.status_code == 200
    assert "days" in r.text
    assert 'step="0.5"' in r.text
    # The capability widget itself must not be bounded to [0,1] — days are an
    # unbounded magnitude. Coverage/reliability inputs further down the row
    # legitimately carry max="1", so scope the assertion to the capability
    # block (between the Capability label and the Coverage label).
    # PR 2 changed the label from `<label class="label"><span class="label-text">…`
    # to `<label class="text-meta text-ink-2">Capability (days)…`, so the
    # split anchor must not include the closing `<` (unit suffix follows).
    cap_block = r.text.split(">Capability")[1].split(">Coverage<")[0]
    assert 'max="1"' not in cap_block


@pytest.mark.asyncio
async def test_currency_sub_function_renders_dollar_widget(authed_analyst):
    """PR 2 overhauled the currency widget: overlay-prefix $ pattern (type=text
    inputmode=numeric) replaced the old type=number step=1000 input.  "per event"
    moved from an inline span into the label suffix `($/event)` so the narrow
    Capability cell doesn't truncate long dollar amounts."""
    client, _ = authed_analyst
    r = await client.get("/controls/_assignment_row?index=0&sub_function=lec_resp_loss_reduction")
    assert r.status_code == 200
    # $ prefix is present in the rendered widget.
    assert "$" in r.text
    # Unit context is in the label suffix, not inline "per event" span.
    assert "$/event" in r.text
    # type=text + inputmode=numeric (no step attribute on currency inputs).
    assert 'inputmode="numeric"' in r.text


@pytest.mark.asyncio
async def test_probability_sub_function_renders_bounded_widget(authed_analyst):
    client, _ = authed_analyst
    r = await client.get("/controls/_assignment_row?index=0&sub_function=lec_prev_avoidance")
    assert r.status_code == 200
    # Scope to the capability block — coverage/reliability also carry
    # min="0" max="1" so a global-text assertion would not regression-catch
    # a macro that fails to render its [0,1] bounds.
    # PR 2 changed the label; use prefix anchor without closing `<`.
    cap_block = r.text.split(">Capability")[1].split(">Coverage<")[0]
    assert 'min="0"' in cap_block
    assert 'max="1"' in cap_block
    assert 'step="0.01"' in cap_block


@pytest.mark.asyncio
async def test_unknown_sub_function_400(authed_analyst):
    """Garbage sub_function string returns 400, not 500."""
    client, _ = authed_analyst
    r = await client.get("/controls/_assignment_row?index=0&sub_function=garbage")
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_unauthed_redirect(
    anonymous_client: AsyncClient,
    admin_user: User,
    db_session: AsyncSession,
) -> None:
    """Unauthenticated request redirects to /login (303).

    ``admin_user`` seeds a user so the setup_guard middleware doesn't 307 to
    /setup; the route's require_role then raises 401, which the app's auth
    redirect handler translates to 303 /login?next=... for HTML callers.
    Commit so the client's separate engine sees the seeded user.
    """
    await db_session.commit()
    r = await anonymous_client.get("/controls/_assignment_row?index=0", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith("/login")
