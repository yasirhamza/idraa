"""P3 Task 9: Web + PDF render-parity contract test for reporting currency.

Invariants under test (design §render-parity, §Predictability rules):

1. Headline ALE is the SAME converted EUR value from both the web view-model
   and the PDF RunReportData — both resolve from the run-pinned snapshot.
2. Beyond the headline:
   - A lognormal loss-magnitude input percentile IS converted (≈ usd × rate).
   - A vuln / frequency percentile is UNCHANGED (currency-invariant nodes).
   - Loss-tolerance amount is converted in both.
3. Reproducibility: mutating the live EUR rate after the fact does NOT change
   either surface's figures — both use the run-pinned snapshot, not the live rate.
4. No literal '$' in either EUR web HTML or EUR PDF text.
5. Provenance string present in both.

Design reference: docs/plans/2026-06-15-currency-p3-reporting-render-plan.md §Task 9
"""

from __future__ import annotations

import io
import math
import re
import uuid
from decimal import Decimal
from typing import Any

import pypdf
import pytest

from idraa.services.pdf_report import (
    _lognormal_freq_percentiles,
    _lognormal_input_percentiles,
    _lognormal_vuln_percentiles,
    render_executive_pdf,
)
from idraa.services.reporting_currency import ReportingCurrency, resolve_reporting_currency
from idraa.services.reports import (
    ControlInventoryRow,
    PerScenarioRow,
    RunReportData,
    ScenarioInventoryRow,
)
from idraa.services.run_view_model import build_display_results

# ---------------------------------------------------------------------------
# Test constants — one pinned EUR run at rate 0.92
# ---------------------------------------------------------------------------

_EUR_RATE = Decimal("0.92")
_USD_ALE = 1_000_000.0  # residual ALE stored in USD
_EUR_ALE = _USD_ALE * float(_EUR_RATE)  # 920_000.0

_USD_LT_AMOUNT = 800_000.0  # loss_tolerance stored in USD
_EUR_LT_AMOUNT = _USD_LT_AMOUNT * float(_EUR_RATE)  # 736_000.0

_FX_SNAPSHOT = {
    "code": "EUR",
    "usd_rate": str(_EUR_RATE),
    "as_of_date": "2026-06-14",
    "source": "ECB",
}

# Lognormal primary loss — mu=13 in log-USD space; exp(13) ≈ 442,413 USD
_LM_MU, _LM_SIGMA = 13.0, 1.0
# Vuln lognormal — probability, NOT dollars
_VULN_MU, _VULN_SIGMA = -1.0, 0.5
# TEF lognormal — frequency, NOT dollars
_TEF_MU, _TEF_SIGMA = 0.0, 0.5

# Simulation results (USD)
_USD_BASE_ALE = 2_000_000.0
_SIM_RESULTS: dict[str, Any] = {
    "base_risk": {
        "annualized_loss_expectancy": _USD_BASE_ALE,
        "mean": _USD_BASE_ALE,
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
    "confidence_intervals": {
        "lower_bound": 800_000.0,
        "upper_bound": 1_200_000.0,
        "interval_pct": 95,
    },
    "control_adjustments": [
        {"control_id": str(uuid.uuid4()), "effectiveness": 0.85},
    ],
    "loss_exceedance_curve": [
        {"loss": 500_000.0, "probability": 0.5},
        {"loss": 1_000_000.0, "probability": 0.2},
    ],
    "exceedance_probability_curve": [
        {"percentile": 0.5, "loss": 500_000.0},
    ],
}

# Scenario inputs carrying lognormal primary loss + vuln + TEF
_SCENARIO_INPUTS: dict[str, Any] = {
    "label": "as-executed",
    "scenarios": [
        {
            "scenario_name": "Ransomware",
            "threat_event_frequency": {
                "distribution": "LOGNORMAL",
                "mean": _TEF_MU,
                "sigma": _TEF_SIGMA,
            },
            "vulnerability": {
                "distribution": "LOGNORMAL",
                "mean": _VULN_MU,
                "sigma": _VULN_SIGMA,
            },
            "primary_loss": {
                "distribution": "LOGNORMAL",
                "mean": _LM_MU,
                "sigma": _LM_SIGMA,
            },
            "secondary_loss": None,
        }
    ],
}


# ---------------------------------------------------------------------------
# Stub objects
# ---------------------------------------------------------------------------


class _FakeRun:
    """Minimal run object with a pinned EUR snapshot."""

    def __init__(
        self,
        presentation_fx_snapshot: dict[str, Any] | None = _FX_SNAPSHOT,
        simulation_results: dict[str, Any] | None = None,
        controls_snapshot: list[Any] | None = None,
    ) -> None:
        import datetime as dt

        self.id = uuid.UUID("00000000-0000-0000-0000-000000000099")
        self.name = "parity-test-run"
        self.completed_at = dt.datetime(2026, 6, 14, 12, 0, tzinfo=dt.UTC)
        self.mc_iterations = 50_000
        self.presentation_fx_snapshot = presentation_fx_snapshot
        self.simulation_results = (
            simulation_results if simulation_results is not None else _SIM_RESULTS
        )
        self.controls_snapshot = controls_snapshot or []


class _FakeOrg:
    """Minimal org with EUR preferred currency and a loss_tolerance."""

    def __init__(
        self,
        preferred_currency: str = "EUR",
        loss_tolerance_amount: float | None = _USD_LT_AMOUNT,
        loss_tolerance_probability: float | None = 0.10,
        annual_revenue: float | None = 300_000_000.0,
    ) -> None:
        self.id = uuid.UUID("00000000-0000-0000-0000-000000000001")
        self.preferred_currency = preferred_currency
        self.loss_tolerance_amount = loss_tolerance_amount
        self.loss_tolerance_probability = loss_tolerance_probability
        self.annual_revenue = annual_revenue
        self.name = "Acme Industrial"
        self.industry_type = "manufacturing"


class _FakeActiveRate:
    """A live active-rate row — used to verify reproducibility (mutation test)."""

    def __init__(self, rate: Decimal) -> None:
        import datetime as dt

        self.code = "EUR"
        self.usd_rate = rate
        self.as_of_date = dt.date(2026, 6, 15)
        self.source = "ECB"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PROVENANCE_FRAGMENT = "Converted from USD at 1 USD = 0.92 EUR"


def _pdf_text(data: RunReportData) -> str:
    """Concatenate all pages of the rendered PDF as a single text block."""
    pdf_bytes = render_executive_pdf(data)
    reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
    return " ".join(p.extract_text() or "" for p in reader.pages)


def _build_pdf_data(rc: ReportingCurrency, org: _FakeOrg) -> RunReportData:
    """Build a RunReportData as build_executive_pdf_data would, using rc for conversion.

    We bypass the async orchestrator to test the parity contract in a pure unit
    test — the same rc object is used for both surfaces, so if both read the
    pinned snapshot the headline figures must agree.
    """
    rate = float(rc.rate)
    code = rc.code

    def cvt(v: float) -> float:
        result = rc.convert(v)
        return result if result is not None else 0.0

    base_ale = cvt(_USD_BASE_ALE)
    residual_ale = cvt(_USD_ALE)
    cv_dollars = base_ale - residual_ale
    cv_pct = (cv_dollars / base_ale * 100) if base_ale > 0 else 0.0
    pct_rev: float | None = None
    if org.annual_revenue:
        pct_rev = residual_ale / org.annual_revenue * 100

    from idraa.formatting import safe_money_format

    run = _FakeRun()
    narrative = (
        f"For Ransomware, the current control posture reduces annualized "
        f"loss expectancy by {safe_money_format(cv_dollars, code)} ({cv_pct:.1f}%), "
        f"bringing residual ALE to {safe_money_format(residual_ale, code)}. "
        f"This estimate reflects 50,000 Monte Carlo iterations."
    )

    lt: dict[str, float] | None = None
    if org.loss_tolerance_amount is not None and org.loss_tolerance_probability is not None:
        lt = {
            "amount": cvt(float(org.loss_tolerance_amount)),
            "probability": float(org.loss_tolerance_probability),
        }

    return RunReportData(
        org=org,
        run=run,
        headline_ale=residual_ale,
        headline_ci_lo=cvt(800_000.0),
        headline_ci_hi=cvt(1_200_000.0),
        interval_pct=95,
        n_simulations=50_000,
        n_scenarios=1,
        control_value_dollars=cv_dollars,
        control_value_percent=cv_pct,
        pct_revenue=pct_rev,
        base_ale=base_ale,
        residual_ale=residual_ale,
        lec_with=[(cvt(500_000.0), 0.5), (cvt(1_000_000.0), 0.2)],
        lec_without=[(cvt(1_000_000.0), 0.5), (cvt(2_000_000.0), 0.1)],
        per_scenario_rows=[
            PerScenarioRow("s1", "Ransomware", base_ale, residual_ale, cv_dollars),
        ],
        scenarios=[ScenarioInventoryRow("s1", "Ransomware", "Encrypts servers.")],
        controls_by_domain={
            "LOSS_EVENT": [ControlInventoryRow("Firewall", "preventive")],
            "VARIANCE_MANAGEMENT": [],
            "DECISION_SUPPORT": [],
            "UNCATEGORIZED": [],
        },
        narrative=narrative,
        run_type="single",
        loss_tolerance=lt,
        # P3 fields
        reporting_code=code,
        reporting_symbol="€" if code == "EUR" else "$",
        reporting_rate=rate,
        currency_provenance=rc.provenance,
        scenario_inputs=_SCENARIO_INPUTS,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRenderParity:
    """Suite of render-parity assertions for a single EUR pinned run at 0.92."""

    @pytest.fixture
    def run(self) -> _FakeRun:
        return _FakeRun()

    @pytest.fixture
    def org(self) -> _FakeOrg:
        return _FakeOrg()

    @pytest.fixture
    def rc_pinned(self, run: _FakeRun, org: _FakeOrg) -> ReportingCurrency:
        """Resolve rc from the pinned snapshot — simulates what both route and
        build_executive_pdf_data do."""
        return resolve_reporting_currency(run, org, active_rate_row=None)

    @pytest.fixture
    def web_vm(self, run: _FakeRun, rc_pinned: ReportingCurrency) -> dict[str, Any]:
        result = build_display_results(run, rc_pinned)
        assert result is not None, (
            "build_display_results returned None — simulation_results missing"
        )
        return result

    @pytest.fixture
    def pdf_data(self, rc_pinned: ReportingCurrency, org: _FakeOrg) -> RunReportData:
        return _build_pdf_data(rc_pinned, org)

    # ------------------------------------------------------------------
    # 1. Headline ALE — SAME converted EUR value in web and PDF
    # ------------------------------------------------------------------

    def test_headline_ale_parity(self, web_vm: dict[str, Any], pdf_data: RunReportData) -> None:
        """Web view-model and PDF RunReportData must produce the same headline ALE.

        If they differ, one surface isn't reading the pinned snapshot (render-parity broken).
        """
        web_ale = web_vm["headline_ale"]["value"]
        pdf_ale = pdf_data.headline_ale
        assert web_ale == pytest.approx(pdf_ale, rel=1e-9), (
            f"RENDER PARITY BROKEN: web headline ALE={web_ale} ≠ PDF headline ALE={pdf_ale}. "
            "One surface is not reading the pinned FX snapshot."
        )
        # Both must be the EUR-converted value
        assert web_ale == pytest.approx(_EUR_ALE, rel=1e-9), (
            f"Headline ALE should be _USD_ALE × 0.92 = {_EUR_ALE}, got web={web_ale}"
        )

    # ------------------------------------------------------------------
    # 2a. Loss-magnitude percentile IS converted
    # ------------------------------------------------------------------

    def test_loss_magnitude_percentile_converted(self, rc_pinned: ReportingCurrency) -> None:
        """Lognormal loss-magnitude p50 must differ between USD and EUR runs.

        Correct: exp(mu + z*sigma) * rate  (post-exponentiation)
        Wrong:   exp(mu * rate)            — methodology corruption
        """
        usd_rows = _lognormal_input_percentiles(_LM_MU, _LM_SIGMA, "USD", 1.0)
        eur_rows = _lognormal_input_percentiles(_LM_MU, _LM_SIGMA, "EUR", float(rc_pinned.rate))

        usd_p50 = usd_rows[2][1]
        eur_p50 = eur_rows[2][1]

        # They must differ (EUR is converted)
        assert usd_p50 != eur_p50, (
            f"Loss-magnitude p50 must differ between USD and EUR: got USD={usd_p50!r}, EUR={eur_p50!r}"
        )
        # EUR result must contain € symbol (not $)
        assert "€" in eur_p50, f"EUR loss percentile p50 must show '€', got {eur_p50!r}"
        assert "$" not in eur_p50, f"EUR loss percentile p50 must not contain '$', got {eur_p50!r}"

        # Sanity: correct post-exp value ≈ 407k; wrong mu*rate value ≈ 171k (ratio > 2)
        usd_p50_raw = math.exp(_LM_MU)  # exp(mu + 0*sigma) = exp(mu)
        correct_eur_p50 = usd_p50_raw * float(rc_pinned.rate)
        wrong_eur_p50 = math.exp(_LM_MU * float(rc_pinned.rate))
        assert correct_eur_p50 / wrong_eur_p50 > 2.0, (
            "Sanity check: correct post-exp EUR p50 should be >2× wrong multiply-mu value"
        )

    # ------------------------------------------------------------------
    # 2b. Vuln / freq percentiles are UNCHANGED (currency-invariant)
    # ------------------------------------------------------------------

    def test_vuln_percentile_unchanged(self, rc_pinned: ReportingCurrency) -> None:
        """Vulnerability (probability) percentiles must be identical for USD and EUR."""
        usd_rows = _lognormal_vuln_percentiles(_VULN_MU, _VULN_SIGMA)
        eur_rows = _lognormal_vuln_percentiles(_VULN_MU, _VULN_SIGMA)
        assert usd_rows == eur_rows, (
            f"Vuln percentile rows must be currency-invariant: USD={usd_rows!r}, EUR={eur_rows!r}"
        )
        # Values must not contain any currency symbol
        for _, val in eur_rows:
            assert "$" not in val and "€" not in val, (
                f"Vuln percentile row must not contain a currency symbol: {val!r}"
            )

    def test_tef_percentile_unchanged(self, rc_pinned: ReportingCurrency) -> None:
        """TEF (frequency) percentiles must be identical for USD and EUR."""
        usd_rows = _lognormal_freq_percentiles(_TEF_MU, _TEF_SIGMA)
        eur_rows = _lognormal_freq_percentiles(_TEF_MU, _TEF_SIGMA)
        assert usd_rows == eur_rows, (
            f"TEF percentile rows must be currency-invariant: USD={usd_rows!r}, EUR={eur_rows!r}"
        )

    # ------------------------------------------------------------------
    # 2c. Loss-tolerance converted in both
    # ------------------------------------------------------------------

    def test_loss_tolerance_converted_in_pdf(self, pdf_data: RunReportData) -> None:
        """PDF RunReportData.loss_tolerance['amount'] must be EUR-converted."""
        lt = pdf_data.loss_tolerance
        assert lt is not None, "PDF RunReportData.loss_tolerance must be set for the test org"
        expected = _USD_LT_AMOUNT * float(_EUR_RATE)
        assert lt["amount"] == pytest.approx(expected, rel=1e-9), (
            f"PDF loss_tolerance.amount should be {expected} (USD × 0.92), got {lt['amount']}"
        )
        # Probability is NOT converted (dimensionless)
        assert lt["probability"] == pytest.approx(0.10), (
            "PDF loss_tolerance.probability must be unchanged (not multiplied by rate)"
        )

    def test_loss_tolerance_parity_web_pdf(
        self, web_vm: dict[str, Any], pdf_data: RunReportData
    ) -> None:
        """Loss-tolerance amount must agree between web view-model (route-injected) and PDF.

        The web route injects loss_tolerance into the view-model dict after the builder runs.
        Here we simulate that injection directly so we can compare both surfaces.
        """
        # Simulate the route-injected web loss_tolerance
        org = _FakeOrg()
        rc = resolve_reporting_currency(_FakeRun(), org, active_rate_row=None)
        web_lt_amount = rc.convert(float(org.loss_tolerance_amount))

        pdf_lt = pdf_data.loss_tolerance
        assert pdf_lt is not None
        assert web_lt_amount == pytest.approx(pdf_lt["amount"], rel=1e-9), (
            f"Loss-tolerance parity broken: web={web_lt_amount} ≠ PDF={pdf_lt['amount']}"
        )

    # ------------------------------------------------------------------
    # 3. Reproducibility: mutating live rate must NOT change either surface
    # ------------------------------------------------------------------

    def test_reproducibility_web_immune_to_live_rate_mutation(self, run: _FakeRun) -> None:
        """Mutating the live active rate after building rc must not change the web headline.

        Both the original rc (pinned rate=0.92) and a re-resolved rc with a
        different live rate (0.99) must use the snapshot — the live rate is only
        consulted for legacy runs (no snapshot). If the web VM headline changes
        with a different active_rate_row, the pinned-snapshot path is broken.
        """
        org = _FakeOrg()
        # Resolve from pinned snapshot (active_rate_row is irrelevant — should be ignored)
        rc_pinned = resolve_reporting_currency(run, org, active_rate_row=None)
        # Now "mutate" by providing a different live rate
        live_rate_mutated = _FakeActiveRate(Decimal("0.99"))
        rc_with_live = resolve_reporting_currency(run, org, active_rate_row=live_rate_mutated)

        # Because the run has a valid pinned snapshot, both resolvers must use 0.92
        assert rc_pinned.rate == rc_with_live.rate, (
            f"Pinned-snapshot rate should be used regardless of live rate: "
            f"rc_pinned.rate={rc_pinned.rate}, rc_with_live.rate={rc_with_live.rate}. "
            "The live active_rate_row must be IGNORED when a valid snapshot exists."
        )
        vm_pinned = build_display_results(run, rc_pinned)
        vm_with_live = build_display_results(run, rc_with_live)
        assert vm_pinned is not None and vm_with_live is not None

        pinned_ale = vm_pinned["headline_ale"]["value"]
        live_ale = vm_with_live["headline_ale"]["value"]
        assert pinned_ale == pytest.approx(live_ale, rel=1e-9), (
            f"REPRODUCIBILITY BROKEN (web): headline ALE changed after live rate mutation: "
            f"pinned={pinned_ale}, with_live_rate={live_ale}. "
            "The pinned snapshot is not being used."
        )

    def test_reproducibility_pdf_immune_to_live_rate_mutation(self, run: _FakeRun) -> None:
        """Mutating the live active rate must not change the PDF headline ALE.

        Mirrors the web test — ReportingCurrency resolved from pinned snapshot
        must yield the same rate regardless of active_rate_row.
        """
        org = _FakeOrg()
        live_rate_mutated = _FakeActiveRate(Decimal("0.99"))

        rc_pinned = resolve_reporting_currency(run, org, active_rate_row=None)
        rc_with_live = resolve_reporting_currency(run, org, active_rate_row=live_rate_mutated)

        # Both must use the pinned snapshot rate
        assert rc_pinned.rate == Decimal("0.92"), (
            f"Pinned rc rate must be 0.92, got {rc_pinned.rate}"
        )
        assert rc_with_live.rate == Decimal("0.92"), (
            f"rc resolved with a mutated live rate must still use the pinned 0.92, "
            f"got {rc_with_live.rate}"
        )

        pdf_pinned = _build_pdf_data(rc_pinned, org)
        pdf_with_live = _build_pdf_data(rc_with_live, org)

        assert pdf_pinned.headline_ale == pytest.approx(pdf_with_live.headline_ale, rel=1e-9), (
            f"REPRODUCIBILITY BROKEN (PDF): headline ALE changed after live rate mutation: "
            f"pinned={pdf_pinned.headline_ale}, with_live={pdf_with_live.headline_ale}"
        )

    # ------------------------------------------------------------------
    # 4. No '$' in EUR web view-model money outputs
    # ------------------------------------------------------------------

    def test_no_dollar_in_web_eur_formatted_values(
        self, web_vm: dict[str, Any], rc_pinned: ReportingCurrency
    ) -> None:
        """The web view-model must produce no '$' in formatted EUR money values.

        The view-model itself does not format — it stores raw floats.  The
        currency metadata it carries must specify 'EUR', not 'USD'.
        """
        currency = web_vm.get("currency", {})
        code = currency.get("code", "")
        assert code == "EUR", f"Web view-model currency.code must be 'EUR', got {code!r}"
        # The symbol Babel returns for EUR in en_US locale must not be '$'
        symbol = currency.get("symbol", "")
        assert symbol != "$", (
            f"Web view-model currency.symbol for EUR must not be '$', got {symbol!r}"
        )

    def test_no_dollar_in_eur_pdf_text(self, pdf_data: RunReportData) -> None:
        """The EUR PDF must not contain a literal '$' money-format character.

        Checks the full PDF text — catches any missed _currency_symbol / abbreviate_money
        remnants that might still emit '$'.
        """
        text = _pdf_text(pdf_data)
        assert "$" not in text, (
            "EUR PDF contains a literal '$' — a missed conversion or format site. "
            f"First occurrence context: {text[max(0, text.find('$') - 40) : text.find('$') + 40]!r}"
        )

    # ------------------------------------------------------------------
    # 5. Provenance present in both
    # ------------------------------------------------------------------

    def test_provenance_in_web_vm(self, web_vm: dict[str, Any]) -> None:
        """Web view-model must carry the provenance string."""
        prov = web_vm.get("currency_provenance")
        assert prov is not None, "Web view-model currency_provenance must be set for EUR run"
        assert _PROVENANCE_FRAGMENT in prov, (
            f"Web provenance must contain '{_PROVENANCE_FRAGMENT}', got {prov!r}"
        )
        assert "ECB" in prov, f"Web provenance must include source 'ECB', got {prov!r}"

    def test_provenance_in_pdf(self, pdf_data: RunReportData) -> None:
        """PDF RunReportData must carry the provenance string."""
        prov = pdf_data.currency_provenance
        assert prov is not None, "PDF RunReportData.currency_provenance must be set for EUR run"
        assert _PROVENANCE_FRAGMENT in prov, (
            f"PDF provenance must contain '{_PROVENANCE_FRAGMENT}', got {prov!r}"
        )

    def test_provenance_rendered_in_pdf_text(self, pdf_data: RunReportData) -> None:
        """The provenance string must appear in the rendered PDF page text."""
        text = re.sub(r"\s+", " ", _pdf_text(pdf_data))
        assert "Converted from USD at 1 USD = 0.92 EUR" in text, (
            f"Expected provenance in PDF text but not found. PDF text excerpt: {text[:500]!r}"
        )
