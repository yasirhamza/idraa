"""Data-contract iteration test for the entry_meta channel in scenario import (PR ρ rule).

When _validate_rows grew a parallel entry_meta list (Task 7), that list must
remain aligned with forms across all N rows — a future [0]/[-1] optimization
would silently drop per-row currency/rate and assign the wrong metadata.
Build N ≥ 3 rows with DISTINCT (entry_currency, entry_rate) pairs, run the
full parse→validate→apply path, and assert ALL N rows persist the correct
entry_currency and entry_rate.

Mirror tests/contracts/test_scenario_import_iteration.py for the harness shape.
SAR + EUR rates must be seeded so is_selectable_currency returns True for them.
USD needs no rate (is_selectable_currency("USD") is always True).

Task 8 fix round 1: entry_meta grew a THIRD element (threat_community_provenance)
in Task 8. The currency/rate test above alone does not exercise it -- none of
its rows carries a community, so every row resolves to "unassigned" and a
future [0]/[-1]-style regression on just that element would go undetected.
``test_entry_meta_third_element_stays_aligned_per_row`` below pins the SAME
per-row-iteration contract for entry_meta[i][2] directly at the pure
``_validate_rows`` layer (no DB needed for this one).
"""

from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select

from idraa.models.scenario import Scenario
from idraa.services.fx_rates import FxRateService
from idraa.services.scenario_import import _validate_rows, apply_validated_preview, validate_upload

# Threat Agent Library (Task 8): published community slugs assumed available
# to the pure _validate_rows call below (caller-supplied, no DB).
_PUB = {"cybercriminals", "nation_state", "privileged_insider", "hacktivists"}


def _scenario_obj(name: str, currency: str, rate: str) -> dict:
    """Build a minimal JSON scenario object with entry_currency + entry_rate."""
    obj: dict = {
        "name": name,
        "threat_category": "malware",
        "threat_event_frequency": {"distribution": "PERT", "low": 1, "mode": 2, "high": 3},
        "vulnerability": {"distribution": "PERT", "low": 0.1, "mode": 0.2, "high": 0.3},
        "primary_loss": {"distribution": "PERT", "low": 10, "mode": 20, "high": 30},
    }
    if currency:
        obj["entry_currency"] = currency
    if rate:
        obj["entry_rate"] = rate
    return obj


@pytest.mark.asyncio
async def test_entry_meta_all_rows_persist_correct_currency_rate(
    db_session, organization, admin_user
) -> None:
    """N=3 rows with distinct (currency, rate); all N must persist in the DB."""
    fx = FxRateService(db_session)
    await fx.upsert_rate(
        organization.id, "SAR", Decimal("3.75"), dt.date(2026, 6, 15), "SAMA", user_id=None
    )
    await fx.upsert_rate(
        organization.id, "EUR", Decimal("0.92"), dt.date(2026, 6, 15), "ECB", user_id=None
    )
    await db_session.commit()

    rows = [
        _scenario_obj("IterMeta-USD", "USD", ""),  # row 0: USD, no rate
        _scenario_obj("IterMeta-SAR", "SAR", "3.75"),  # row 1: SAR / 3.75
        _scenario_obj("IterMeta-EUR", "EUR", "0.92"),  # row 2: EUR / 0.92
    ]
    data = json.dumps(rows).encode()

    token, _p, _e = await validate_upload(
        db_session,
        org_id=organization.id,
        user_id=admin_user.id,
        data=data,
        filename="s.json",
        content_type="application/json",
    )
    imported, skipped, errors = await apply_validated_preview(
        db_session,
        token=token,
        org_id=organization.id,
        user=admin_user,
    )
    assert errors == [], f"unexpected apply errors: {errors}"
    assert imported == 3, f"expected 3 imported, got {imported}"
    assert skipped == 0, f"expected 0 skipped, got {skipped}"

    # Fetch and assert per-row metadata — all N must survive (iteration guard).
    names = ["IterMeta-USD", "IterMeta-SAR", "IterMeta-EUR"]
    result = await db_session.execute(
        select(Scenario)
        .where(Scenario.organization_id == organization.id, Scenario.name.in_(names))
        .order_by(Scenario.name)
    )
    db_rows = {r.name: r for r in result.scalars().all()}

    assert set(db_rows.keys()) == set(names), f"missing rows: {set(names) - set(db_rows.keys())}"

    usd_row = db_rows["IterMeta-USD"]
    assert usd_row.entry_currency == "USD"
    assert usd_row.entry_rate is None  # blank entry_rate → None

    sar_row = db_rows["IterMeta-SAR"]
    assert sar_row.entry_currency == "SAR"
    assert sar_row.entry_rate == Decimal("3.75000000")

    eur_row = db_rows["IterMeta-EUR"]
    assert eur_row.entry_currency == "EUR"
    assert eur_row.entry_rate == Decimal("0.92000000")


def _row(name: str, **threat_fields: Any) -> dict[str, Any]:
    """Minimal pure-function fd for _validate_rows, overridable threat input."""
    d: dict[str, Any] = {
        "name": name,
        "threat_category": "malware",
        "threat_event_frequency": {"distribution": "PERT", "low": 1, "mode": 2, "high": 3},
        "vulnerability": {"distribution": "PERT", "low": 0.1, "mode": 0.2, "high": 0.3},
        "primary_loss": {"distribution": "PERT", "low": 10, "mode": 20, "high": 30},
    }
    d.update(threat_fields)
    return d


def test_entry_meta_third_element_stays_aligned_per_row() -> None:
    """N=3 rows with DISTINCT threat inputs; entry_meta[i][2] (provenance) and
    forms[i].threat_community must stay aligned with row i through the pure
    _validate_rows pipeline -- a [0]/[-1]-style regression on the third tuple
    element would silently misattribute provenance across rows."""
    rows = [
        (2, _row("EM3-Assigned", threat_community="cybercriminals")),
        (3, _row("EM3-Migrated", threat_actor_type="hacktivists")),
        (4, _row("EM3-Split", threat_actor_type="insider_malicious")),
    ]
    preview, errors, forms, entry_meta, _am = _validate_rows(
        rows, existing_names=set(), published_slugs=_PUB
    )
    assert errors == [], f"unexpected validation errors: {errors}"
    assert [p["action"] for p in preview] == ["create", "create", "create"]

    assert entry_meta[0][2] == "assigned"
    assert entry_meta[1][2] == "migrated"
    assert entry_meta[2][2] == "migrated_split_default"

    assert forms[0] is not None and forms[0].threat_community == "cybercriminals"
    assert forms[1] is not None and forms[1].threat_community == "hacktivists"
    assert forms[2] is not None and forms[2].threat_community == "privileged_insider"
