"""Design-language Phase 1 acceptance tests (issue #59).

Task 1: logomark macro rendered in the sidebar (authenticated shell) and on
the login page (unauthenticated, ``with_wordmark=True``), plus the favicon
served at ``/static/favicon.svg``.

Task 2: breadcrumb-as-eyebrow macro classes (mono/uppercase/tracked, leading
hairline rule, brand-colored current page) and the body-gradient token in
``app.css``. Later tasks in the same epic extend this module with
forms/readout assertions — keep this module the single home for
design-language P1 acceptance tests rather than scattering one-off test
files per task.

Task 4: forms-as-instruments — fieldset-scoped label/numeric-input treatment.

Task 5: readout strips on the wizard review page (macros/readout.html).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.integration._wizard_step3_test_helpers import (
    _bootstrap_wizard_through_step_2,
    _persist_fair_rows_via_steps_3_and_4,
    _user_id_from_org,
)

pytestmark = pytest.mark.asyncio

APP_CSS_PATH = Path(__file__).resolve().parents[2] / "src" / "idraa" / "static" / "css" / "app.css"


async def test_sidebar_renders_logomark(
    authed_analyst: tuple[AsyncClient, object],
    db_session: AsyncSession,
) -> None:
    """The dashboard shell (sidebar) renders the logomark macro's SVG."""
    client, _ = authed_analyst
    r = await client.get("/")
    assert r.status_code == 200
    assert "data-logomark" in r.text
    assert "M3 7 C 11 8, 12 24, 29 26" in r.text


async def test_login_and_favicon(client: AsyncClient) -> None:
    """The login page renders the logomark (with wordmark) + favicon is served."""
    r = await client.get("/login")
    assert r.status_code == 200
    assert "data-logomark" in r.text

    r2 = await client.get("/static/favicon.svg")
    assert r2.status_code == 200
    assert "svg" in r2.text


async def test_breadcrumb_is_eyebrow(
    authed_analyst: tuple[AsyncClient, object],
    db_session: AsyncSession,
) -> None:
    """The breadcrumb macro renders as the deck's mono/uppercase/tracked
    "eyebrow" — a leading hairline rule span, and the current page in the
    brand color — via macro classes, not a `header nav[aria-label]` element
    selector (that approach is deleted from app.css in this task)."""
    client, _ = authed_analyst
    r = await client.get("/scenarios")
    assert r.status_code == 200
    assert "uppercase" in r.text
    assert "tracking-[0.14em]" in r.text
    assert "text-brand" in r.text


async def test_body_gradient_token() -> None:
    """app.css ports the preview's ambient brand-glow gradient behind the
    app shell, expressed entirely through the --color-brand token."""
    css = APP_CSS_PATH.read_text(encoding="utf-8")
    assert "radial-gradient" in css
    assert "var(--color-brand)" in css


async def test_wizard_step4_labels_mono(
    authed_analyst: tuple[AsyncClient, object],
    db_session: AsyncSession,
) -> None:
    """Forms-as-instruments (Task 4): the wizard's step-4 SME-row partial
    (``_fair_params_form_inner.html``) authors ``.text-meta`` spans directly
    inside a ``<fieldset>`` — no ``<label>`` tags, so it is NOT a
    ``macros/form_field.html`` consumer. This proves the ``form fieldset
    label, form fieldset .text-meta`` CSS net in app.css actually reaches
    that hand-authored markup (not just form_field's own explicit classes)."""
    client, org_id = authed_analyst
    user_id = await _user_id_from_org(db_session, org_id)
    tx = await _bootstrap_wizard_through_step_2(client, db_session, user_id)

    r = await client.get(f"/scenarios/new/wizard/step/4?tx={tx}")
    assert r.status_code == 200
    body = r.text
    assert "<fieldset" in body
    assert "text-meta" in body

    css = APP_CSS_PATH.read_text(encoding="utf-8")
    assert "form fieldset label" in css
    assert "form fieldset .text-meta" in css
    assert "text-transform: uppercase" in css


async def test_numeric_inputs_mono() -> None:
    """app.css carries the numeric-input mono rule (money/decimal inputs
    read like tabular readouts) — the net for the ~13 fieldset-bearing
    templates whose numeric inputs aren't rendered via form_field/
    unit_aware_inputs (both of which already inline `font-mono tabular-nums`
    on their own numeric inputs)."""
    css = APP_CSS_PATH.read_text(encoding="utf-8")
    assert 'form input[type="number"]' in css
    assert 'form input[inputmode="decimal"]' in css
    assert "font-variant-numeric: tabular-nums" in css


async def test_wizard_review_uses_readout(
    authed_analyst: tuple[AsyncClient, object],
    db_session: AsyncSession,
) -> None:
    """Task 5: the step-6 review page renders the entered per-fieldset SME
    estimate rows through ``readout_strip`` (``data-readout`` anchor) rather
    than the old bare ``<ul>`` — same values (low-high range, per-row source
    label), same wording, new instrument-panel presentation."""
    client, org_id = authed_analyst
    user_id = await _user_id_from_org(db_session, org_id)
    tx = await _bootstrap_wizard_through_step_2(client, db_session, user_id)

    await _persist_fair_rows_via_steps_3_and_4(
        client,
        db_session,
        tx,
        tef=[("Red Team", 1.0, 12.0)],
        vuln=[("Red Team", 0.05, 0.5)],
        pl=[("Red Team", 100000.0, 5000000.0)],
        sl=[("Red Team", 5000.0, 50000.0)],
    )

    r = await client.get(f"/scenarios/new/wizard/step/6?tx={tx}")
    assert r.status_code == 200, r.text
    body = r.text
    assert "data-readout" in body
    # The entered Primary loss High value (5000000.0, formatted 2dp via the
    # existing format_dist_value("money") filter) renders inside the strip.
    assert "5000000.00" in body
