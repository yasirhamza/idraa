from __future__ import annotations

from sqlalchemy import inspect

from idraa.models.scenario import Scenario


def test_scenario_has_entry_currency_columns() -> None:
    cols = {c.key for c in inspect(Scenario).columns}
    assert "entry_currency" in cols
    assert "entry_rate" in cols


def test_entry_currency_column_defaults() -> None:
    c = inspect(Scenario).columns
    assert c["entry_currency"].default.arg == "USD"
    assert c["entry_rate"].nullable is True
