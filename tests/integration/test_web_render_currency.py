# tests/integration/test_web_render_currency.py
"""P3 Task 6: web template + chart rendering in reporting currency.

Tests that GET /runs/{id} for a pinned-EUR run:
  (a) the headline renders '€' (or 'EUR') and NOT '$'.
  (b) the currency_provenance note appears in the rendered HTML.
  (c) NO-DOUBLE-CONVERT pin: a charted LEC x-axis value equals usd*0.92 once
      (not 0.92², not *1) and the axis tickformat is number-only (no '$').
  (d) the page contains no literal '$' symbol for a EUR run.

Setup: uses seed_run_factory with a pinned presentation_fx_snapshot={EUR, 0.92}
and a manually crafted simulation_results payload so we know the exact USD
values and can compute the expected converted values.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.organization import Organization
from idraa.models.risk_analysis_run import RunStatus

# ---------------------------------------------------------------------------
# Test constants
# ---------------------------------------------------------------------------

_EUR_RATE = Decimal("0.92")
_USD_ALE = 1_000_000.0  # known residual ALE in USD
_EUR_ALE = _USD_ALE * float(_EUR_RATE)  # 920000.0

_USD_LEC_LOSS = 500_000.0  # a known LEC point USD loss
_EUR_LEC_LOSS = _USD_LEC_LOSS * float(_EUR_RATE)  # 460000.0

_FX_SNAPSHOT = {
    "code": "EUR",
    "usd_rate": "0.92",
    "as_of_date": "2026-06-14",
    "source": "ECB",
}

_SIMULATION_RESULTS: dict[str, Any] = {
    "base_risk": {
        "annualized_loss_expectancy": 2_000_000.0,
        "mean": 2_000_000.0,
        "median": 1_800_000.0,
        "std_deviation": 500_000.0,
        "var_90": 2_500_000.0,
        "var_95": 3_000_000.0,
        "var_99": 4_000_000.0,
        "var_999": 5_000_000.0,
        "expected_shortfall": {"es_95": 3_500_000.0, "es_99": 4_500_000.0, "es_999": 6_000_000.0},
    },
    "residual_risk": {
        "annualized_loss_expectancy": _USD_ALE,
        "mean": _USD_ALE,
        "median": 900_000.0,
        "std_deviation": 250_000.0,
        "var_90": 1_200_000.0,
        "var_95": 1_500_000.0,
        "var_99": 2_000_000.0,
        "var_999": 2_500_000.0,
        "expected_shortfall": {"es_95": 1_750_000.0, "es_99": 2_200_000.0, "es_999": 3_000_000.0},
    },
    "control_adjustments": [
        {"control_id": str(uuid.uuid4()), "effectiveness": 0.85},
    ],
    "confidence_intervals": {
        "lower_bound": 800_000.0,
        "upper_bound": 1_200_000.0,
        "interval_pct": 95,
    },
    # Known USD values — after conversion at 0.92 these become _EUR_LEC_LOSS = 460000.0
    "loss_exceedance_curve": [
        {"loss": _USD_LEC_LOSS, "probability": 0.5},
        {"loss": 1_000_000.0, "probability": 0.2},
        {"loss": 2_000_000.0, "probability": 0.05},
    ],
    "exceedance_probability_curve": [
        {"percentile": 0.5, "loss": _USD_LEC_LOSS},
        {"percentile": 0.95, "loss": 1_500_000.0},
    ],
}


# ---------------------------------------------------------------------------
# Fixture: a seeded EUR COMPLETED run (via seed_run_factory with a pinned snapshot)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def eur_completed_run(
    authed_admin: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
    seed_user: Any,
    seed_scenario_factory: Any,
) -> Any:
    """A COMPLETED SINGLE run with a pinned EUR snapshot (rate 0.92).

    Creates the scenario in authed_admin's org so the route's ScenarioRepo
    get_for_org lookup succeeds (org_id must match).  Seeds the run manually
    (not via the executor) so we control simulation_results exactly — needed
    for no-double-convert assertions.
    """
    import hashlib
    from datetime import UTC, datetime

    from sqlalchemy import select

    from idraa.models.risk_analysis_run import RiskAnalysisRun, RunType

    _, org_id = authed_admin  # type: ignore[misc]

    # Set the org's preferred_currency to EUR so the route resolver finds a match.
    org = (
        await db_session.execute(select(Organization).where(Organization.id == org_id))
    ).scalar_one()
    org.preferred_currency = "EUR"
    await db_session.flush()

    # Create a scenario in the authed_admin's org (so get_for_org succeeds).
    scenario = await seed_scenario_factory(
        name="eur-render-test-scenario",
        organization_id=org_id,
        created_by=seed_user.id,
    )

    run = RiskAnalysisRun(
        id=uuid.uuid4(),
        organization_id=org_id,
        scenario_id=scenario.id,
        mc_iterations=200,
        inputs_hash=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
        controls_snapshot=[],
        control_ids_used=[],
        status=RunStatus.COMPLETED,
        run_type=RunType.SINGLE,
        created_by=seed_user.id,
        simulation_results=_SIMULATION_RESULTS,
        completed_at=datetime.now(UTC),
        presentation_fx_snapshot=_FX_SNAPSHOT,
    )
    db_session.add(run)
    await db_session.commit()
    await db_session.refresh(run)

    return run


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_eur_run_headline_shows_euro_not_dollar(
    authed_admin: tuple[AsyncClient, uuid.UUID],
    eur_completed_run: Any,
) -> None:
    """(a) Headline renders € / EUR and does NOT contain '$'."""
    client, _ = authed_admin
    r = await client.get(f"/runs/{eur_completed_run.id}")
    assert r.status_code == 200, r.text[:200]
    body = r.text

    # The headline_ale_with_ci_band macro renders via the `money(currency.code)` filter.
    # safe_money_format("EUR") uses Babel which yields the '€' symbol for en locale.
    assert "€" in body or "EUR" in body, "EUR headline: '€' or 'EUR' must appear on a EUR run page"


@pytest.mark.asyncio
async def test_eur_run_provenance_appears(
    authed_admin: tuple[AsyncClient, uuid.UUID],
    eur_completed_run: Any,
) -> None:
    """(b) The currency_provenance string is rendered on the page."""
    client, _ = authed_admin
    r = await client.get(f"/runs/{eur_completed_run.id}")
    assert r.status_code == 200
    body = r.text

    # The provenance note is rendered in _results_panel.html when non-null.
    assert "Converted from USD" in body, (
        "EUR run page must show provenance note 'Converted from USD ...'"
    )
    assert "ECB" in body, "provenance must include the source (ECB)"


@pytest.mark.asyncio
async def test_eur_run_no_double_convert_legacy_tickformat(
    authed_admin: tuple[AsyncClient, uuid.UUID],
    eur_completed_run: Any,
) -> None:
    """(c) NO-DOUBLE-CONVERT: LEC x-axis tick labels are number-only (no '$').

    Regression guard against the retired chart vendor's layout syntax, whose
    tickformat '$.2s' embedded a '$' literally in the rendered JSON — the
    first-party SVG axis (chart_svg._fmt_money) always formats with the
    reporting-currency symbol as a label, never a hardcoded '$'. Finding
    '$.2s' or '$,.0f' in the HTML would mean a template regressed to the old
    literal-dollar tickformat syntax.
    """
    client, _ = authed_admin
    r = await client.get(f"/runs/{eur_completed_run.id}")
    assert r.status_code == 200
    body = r.text

    assert '"$.2s"' not in body, (
        "Legacy tickformat '$.2s' found on a EUR run page — the SVG axis "
        "must format with the reporting-currency symbol (no embedded dollar sign)"
    )
    assert '"$,.0f"' not in body, (
        "Legacy tickformat '$,.0f' found on a EUR run page — the SVG axis "
        "must format with the reporting-currency symbol"
    )


@pytest.mark.asyncio
async def test_eur_run_no_double_convert_lec_data_value(
    authed_admin: tuple[AsyncClient, uuid.UUID],
    eur_completed_run: Any,
) -> None:
    """(c-bis) NO-DOUBLE-CONVERT data value: the first LEC x-axis data point
    is _USD_LEC_LOSS * 0.92 = 460000.0 (once), NOT USD unchanged or 0.92².

    Strategy: the chart's raw points are rendered inline as embedded JSON
    (the SVG figure's ``<script type="application/json" data-chart-data>``
    block). We parse the rendered HTML and find the loss_exceedance_curve
    x-values. The view-model converts at the boundary; the template must NOT
    multiply again.
    """
    client, _ = authed_admin
    r = await client.get(f"/runs/{eur_completed_run.id}")
    assert r.status_code == 200
    body = r.text

    # The rendered data-chart-data JSON embeds the LEC series' raw loss values.
    # We look for the converted EUR value (460000.0) in the JSON blob.
    # If we find the raw USD value 500000.0 instead — no conversion happened.
    # If we find 0.92²-scaled value (423200.0) — double-conversion happened.
    expected_converted = _EUR_LEC_LOSS  # 460000.0
    expected_double_converted = _USD_LEC_LOSS * float(_EUR_RATE) ** 2  # 423200.0

    # Render the value as it would appear in JSON (integer or float)
    # 460000.0 renders as 460000.0 in JSON
    assert str(int(expected_converted)) in body or str(expected_converted) in body, (
        f"Expected EUR-converted LEC value {expected_converted} not found in page. "
        f"Raw USD value ({_USD_LEC_LOSS}) present: {str(int(_USD_LEC_LOSS)) in body}. "
        "Possible: view-model not converting, or route not threading rc."
    )
    assert (
        str(int(expected_double_converted)) not in body
        or str(expected_double_converted) not in body
    ), (
        f"Double-converted value {expected_double_converted} found — template is multiplying again. "
        "Check that chart.html macro uses tickprefix (label only) not a rate multiply."
    )


@pytest.mark.asyncio
async def test_eur_run_no_dollar_sign_anywhere(
    authed_admin: tuple[AsyncClient, uuid.UUID],
    eur_completed_run: Any,
) -> None:
    """(d) The EUR run page must contain no money-format '$' symbol.

    This catches: texttemplate '%{x:$,.0f}', tickformat '$.2s',
    inline '${{ row.base | format_money }}', tolerance annotation '$ N',
    and any other missed money-rendering site.

    Exclusions: Alpine.js uses '$store', '$el', etc. as JS idioms — those are
    JS syntax, not money; exclude lines that contain these Alpine patterns.
    """
    import re as _re

    client, _ = authed_admin
    r = await client.get(f"/runs/{eur_completed_run.id}")
    assert r.status_code == 200
    body = r.text

    # Alpine.js patterns that legitimately contain '$' as JS syntax.
    _alpine_dollar = _re.compile(r"\$(?:store|el|data|refs|watch|dispatch|nextTick)\b")

    dollar_contexts: list[str] = []
    for i, line in enumerate(body.splitlines(), start=1):
        if "$" not in line:
            continue
        # Skip lines that only contain Alpine.js $ identifiers (no money context).
        line_no_alpine = _alpine_dollar.sub("", line)
        if "$" not in line_no_alpine:
            continue
        dollar_contexts.append(f"  line {i}: {line.strip()[:120]}")

    assert not dollar_contexts, (
        "Literal '$' found on EUR run page (money-format context). Every money display "
        "must use the reporting currency symbol (€ for EUR). Offending lines:\n"
        + "\n".join(dollar_contexts[:10])
    )
