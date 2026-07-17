"""Integration tests for /analyses/new form + POST + legacy redirects (PR xi F8)."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from html.parser import HTMLParser
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.enums import EntityStatus, ScenarioType, ThreatCategory
from idraa.models.scenario import Scenario
from tests.conftest import csrf_post


def _seed_scenario_for_org(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    created_by: uuid.UUID | None = None,
    name: str = "analyses-test-scenario",
) -> Scenario:
    """Build a minimal schema-valid Scenario for the given org.

    Caller must ``await db.commit()`` after calling this.
    """
    s = Scenario(
        organization_id=org_id,
        name=name,
        scenario_type=ScenarioType.CUSTOM,
        threat_category=ThreatCategory.RANSOMWARE,
        threat_event_frequency={"distribution": "PERT", "low": 0.1, "mode": 0.5, "high": 2.0},
        vulnerability={"distribution": "PERT", "low": 0.2, "mode": 0.4, "high": 0.6},
        primary_loss={
            "distribution": "PERT",
            "low": 50_000,
            "mode": 250_000,
            "high": 2_000_000,
        },
        status=EntityStatus.ACTIVE,
        created_by=created_by,
    )
    db.add(s)
    return s


# ---- GET /analyses/new -----------------------------------------------


@pytest.mark.asyncio
async def test_get_analyses_new_renders_for_analyst(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """Analyst can GET /analyses/new — form renders with expected heading."""
    client, org_id = authed_analyst
    # Seed a scenario so the form has something to list
    _seed_scenario_for_org(db_session, org_id=org_id, name="s1")
    await db_session.commit()

    response = await client.get("/analyses/new")
    assert response.status_code == 200
    assert "New analysis" in response.text


@pytest.mark.asyncio
async def test_analyses_new_renders_readonly_controls_panel(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """Issue #89: the controls section is a read-only info panel — no
    editable checkboxes, no override Alpine state."""
    client, org_id = authed_analyst
    _seed_scenario_for_org(db_session, org_id=org_id, name="s1")
    await db_session.commit()

    response = await client.get("/analyses/new")
    assert response.status_code == 200
    body = response.text
    # Read-only panel sentinel text.
    assert "Each scenario uses the controls configured on its own scenario record" in body
    # Override Alpine state is GONE (no editable checklist for controls).
    assert "applyDefaults()" not in body
    assert "effectivelyEmptyDefaults" not in body
    assert "selectedControlIds" not in body
    assert "unionMitigatingArray" not in body
    # The new data map is embedded.
    assert "nameById" in body
    assert "scenarioToMitigating" in body


@pytest.mark.asyncio
async def test_analyses_new_escapes_xss_in_scenario_name(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """Issue #257: free-text scenario names are embedded in the Alpine x-data
    nameById map. A name containing an attribute-breakout payload must be
    HTML-escaped (the x-data attr is single-quoted) so it cannot break out of
    the attribute and execute as markup."""
    client, org_id = authed_analyst
    payload = "'><img src=x onerror=alert(document.cookie)>"
    _seed_scenario_for_org(db_session, org_id=org_id, name=payload)
    await db_session.commit()

    response = await client.get("/analyses/new")
    assert response.status_code == 200
    body = response.text

    # The raw breakout payload must NOT appear verbatim in the rendered HTML.
    assert "'><img src=x onerror=alert(document.cookie)>" not in body
    # No raw <img onerror=...> markup at all.
    assert "<img src=x onerror=" not in body
    # The dangerous characters must be escaped (Jinja |tojson emits < etc.,
    # and/or HTML-attribute escaping turns ' into &#39;). Confirm the name still
    # round-trips as escaped text, not raw markup.
    assert ("\\u003c" in body) or ("&lt;" in body) or ("\\u0027" in body and "&#39;" in body)


class _FormXDataExtractor(HTMLParser):
    """Pull the x-data attribute value off the first <form>. The stdlib parser
    honours attribute quoting exactly as a browser would, so if x-data is
    double-quoted while its |tojson value contains a raw ``"``, the value is
    truncated at that ``"`` — which this test detects."""

    def __init__(self) -> None:
        super().__init__()
        self.x_data: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "form" and self.x_data is None:
            for k, v in attrs:
                if k == "x-data":
                    self.x_data = v or ""


@pytest.mark.asyncio
async def test_analyses_new_x_data_attribute_is_well_formed(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """Issue #257 follow-up (reviewer BLOCKER): ``| tojson`` emits raw double
    quotes (e.g. ``["id"]``), so the Alpine ``x-data`` attribute MUST be
    single-quoted — a double-quoted attribute is terminated by the first
    embedded ``"``, breaking Alpine and opening an attribute-injection surface.
    A scenario name containing a double quote must not break the attribute."""
    client, org_id = authed_analyst
    _seed_scenario_for_org(db_session, org_id=org_id, name='quote"in"name')
    await db_session.commit()

    response = await client.get("/analyses/new")
    assert response.status_code == 200
    body = response.text

    # Must use the single-quoted form (tojson double-quotes are only safe there).
    assert "x-data='{" in body, "x-data must be single-quoted (issue #257 follow-up)"

    # The parsed attribute value must survive intact — not truncated at a tojson
    # double-quote. If double-quoted, HTMLParser would cut it short and these fail.
    extractor = _FormXDataExtractor()
    extractor.feed(body)
    assert extractor.x_data is not None, "no <form> x-data found"
    assert "nameById" in extractor.x_data, "x-data truncated before nameById (broken quoting)"
    assert extractor.x_data.rstrip().endswith("}"), "x-data not terminated by '}' (truncated)"


@pytest.mark.asyncio
async def test_analyses_new_has_select_all_control(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """The scenario picker offers a Select all / Clear control wired to the
    Alpine selectedScenarios state via an allScenarioIds list."""
    client, org_id = authed_analyst
    _seed_scenario_for_org(db_session, org_id=org_id, name="s1")
    _seed_scenario_for_org(db_session, org_id=org_id, name="s2")
    await db_session.commit()

    response = await client.get("/analyses/new")
    assert response.status_code == 200
    body = response.text
    assert "allScenarioIds" in body
    assert "Select all" in body
    assert "selectedScenarios = [...allScenarioIds]" in body
    assert "selectedScenarios = []" in body


@pytest.mark.asyncio
async def test_analyses_new_has_high_fidelity_cost_warning(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """Task 11: an inline Alpine-driven cost warning appears once the entered
    mc_iterations reaches the high-fidelity threshold (250k). Per the
    combobox/x-data convention (PR #205), the threshold is inlined into
    x-data — not read from a window global — so it survives an HTMX swap."""
    client, org_id = authed_analyst
    _seed_scenario_for_org(db_session, org_id=org_id, name="s1")
    await db_session.commit()

    response = await client.get("/analyses/new")
    assert response.status_code == 200
    body = response.text
    assert "highFidelityThreshold: 250000" in body
    assert "mcIterations" in body
    assert 'x-model.number="mcIterations"' in body
    assert 'x-show="mcIterations >= highFidelityThreshold"' in body
    assert "High-fidelity run" in body


@pytest.mark.asyncio
async def test_get_analyses_new_rejects_reviewer(
    authed_reviewer: tuple[AsyncClient, uuid.UUID],
) -> None:
    """Reviewer is rejected with 403 (RBAC gate via require_role)."""
    client, _ = authed_reviewer
    response = await client.get("/analyses/new")
    assert response.status_code == 403


# ---- POST /analyses ---------------------------------------------------


@pytest.mark.asyncio
async def test_post_analyses_with_one_scenario_creates_single(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
    wire_executor_to_test_db: None,
) -> None:
    """1 scenario -> SINGLE run; response is 204 with HX-Redirect to /runs/{id}."""
    client, org_id = authed_analyst
    scenario = _seed_scenario_for_org(db_session, org_id=org_id, name="single-s")
    await db_session.commit()

    response = await csrf_post(
        client,
        "/analyses",
        {
            "scenario_ids": str(scenario.id),
            "mc_iterations": "1000",
        },
    )
    assert response.status_code == 204
    assert response.headers.get("HX-Redirect", "").startswith("/runs/")


@pytest.mark.asyncio
async def test_post_analyses_with_two_scenarios_creates_aggregate(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
    wire_executor_to_test_db: None,
) -> None:
    """2+ scenarios -> AGGREGATE run; response is 204 with HX-Redirect to /runs/{id}."""
    client, org_id = authed_analyst
    s1 = _seed_scenario_for_org(db_session, org_id=org_id, name="agg-s1")
    s2 = _seed_scenario_for_org(db_session, org_id=org_id, name="agg-s2")
    await db_session.commit()

    # Bootstrap CSRF cookie then inject _csrf into multi-value form data.
    # httpx requires a sequence of (key, value) 2-tuples passed as ``data``
    # to encode a multipart form with repeated keys; the deprecation warning
    # about raw bytes vs form fields is suppressed by using the dict form for
    # single-value keys.  For repeated keys we use a URL-encoded body directly.
    bootstrap = await client.get("/setup")
    assert bootstrap.status_code in (200, 303)
    token = client.cookies.get("csrf_token")
    assert token

    from urllib.parse import urlencode

    body = urlencode(
        [
            ("scenario_ids", str(s1.id)),
            ("scenario_ids", str(s2.id)),
            ("mc_iterations", "1000"),
            ("_csrf", token),
        ]
    )
    response = await client.post(
        "/analyses",
        content=body,
        headers={"content-type": "application/x-www-form-urlencoded"},
    )
    assert response.status_code == 204
    assert response.headers.get("HX-Redirect", "").startswith("/runs/")


@pytest.mark.asyncio
async def test_post_analyses_rejects_empty_scenario_ids(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
) -> None:
    """Missing scenario_ids returns 422 (required Form field)."""
    client, _ = authed_analyst
    response = await csrf_post(
        client,
        "/analyses",
        {"mc_iterations": "1000"},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_post_analyses_rejects_cross_org_scenario(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    seed_scenario_factory: Callable[..., Any],
    db_session: AsyncSession,
) -> None:
    """Cross-org scenario_id (belongs to seed_organization, not analyst's org) -> 404."""
    client, _ = authed_analyst
    # seed_scenario_factory seeds into seed_organization, which is a DIFFERENT org
    # from the authed_analyst's org — so this is a cross-org submission.
    s_other_org = await seed_scenario_factory(name="cross-org-scenario")

    response = await csrf_post(
        client,
        "/analyses",
        {
            "scenario_ids": str(s_other_org.id),
            "mc_iterations": "1000",
        },
    )
    # Service raises ScenarioNotFoundError for cross-org -> 404
    assert response.status_code == 404


# ---- Legacy redirect + adapter ---------------------------------------


@pytest.mark.asyncio
async def test_legacy_get_scenarios_run_new_redirects_303(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """Legacy GET /scenarios/{id}/run/new -> 303 redirect to /analyses/new?prefill."""
    client, org_id = authed_analyst
    scenario = _seed_scenario_for_org(db_session, org_id=org_id, name="redirect-s")
    await db_session.commit()

    response = await client.get(
        f"/scenarios/{scenario.id}/run/new",
        follow_redirects=False,
    )
    assert response.status_code == 303
    location = response.headers.get("location", "")
    assert "prefill_scenario_id" in location
    assert str(scenario.id) in location


@pytest.mark.asyncio
async def test_legacy_get_run_new_rejects_reviewer(
    authed_reviewer: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """Legacy GET /scenarios/{id}/run/new no longer checks RBAC (it's a 303 redirect
    with no auth check — the new /analyses/new form enforces RBAC). The redirect
    itself returns 303 regardless of role."""
    client, org_id = authed_reviewer
    scenario = _seed_scenario_for_org(db_session, org_id=org_id, name="reviewer-redirect-s")
    await db_session.commit()

    response = await client.get(
        f"/scenarios/{scenario.id}/run/new",
        follow_redirects=False,
    )
    # 303 redirect — RBAC is now on /analyses/new, not the redirect handler
    assert response.status_code == 303


@pytest.mark.asyncio
async def test_legacy_post_scenarios_run_via_adapter(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
    wire_executor_to_test_db: None,
) -> None:
    """Legacy POST /scenarios/{id}/run adapter calls create_and_dispatch([scenario_id])."""
    client, org_id = authed_analyst
    scenario = _seed_scenario_for_org(db_session, org_id=org_id, name="adapter-s")
    await db_session.commit()

    response = await csrf_post(
        client,
        f"/scenarios/{scenario.id}/run",
        {"mc_iterations": "1000"},
    )
    assert response.status_code == 204
    assert response.headers.get("HX-Redirect", "").startswith("/runs/")


@pytest.mark.asyncio
async def test_legacy_post_scenarios_run_reviewer_403(
    authed_reviewer: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """Legacy POST /scenarios/{id}/run adapter rejects reviewer with 403."""
    client, org_id = authed_reviewer
    scenario = _seed_scenario_for_org(db_session, org_id=org_id, name="reviewer-run-s")
    await db_session.commit()

    response = await csrf_post(
        client,
        f"/scenarios/{scenario.id}/run",
        {"mc_iterations": "1000"},
    )
    assert response.status_code == 403
