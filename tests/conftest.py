"""Global pytest fixtures — async DB + FastAPI AsyncClient.

This module also hosts shared test data-seeding callables —
``seed_overlay``, ``seed_override``, ``seed_org_user`` — promoted out
of inline ``_seed_*`` helpers in individual test files (E3.e Step 17,
Phase 1.3 Scenarios CRUD plan). Each returns a callable rather than
performing the seed at fixture-resolution time so a single test can
seed multiple rows with different parameters. The helpers include
the NOT NULL columns (P7/P8 paranoid-review fixes — overlay
``display_name`` on definition + ``tag``/``display_name`` on revision;
override ``industry``/``revenue_tier`` on revision) so any test
acquiring them lands a schema-valid row.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from pathlib import Path
from typing import Any as _Any

# Set ENVIRONMENT=test BEFORE importing idraa so module-level app
# creation (``idraa.app`` imports run ``create_app()``) sees the test
# environment and the tightened session_secret guard accepts the default
# placeholder. Task 1.1.0.a tightened the guard: only `test` accepts the
# default; `dev` and `prod` now require an explicit non-default secret.
os.environ.setdefault("ENVIRONMENT", "test")

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from idraa import config, db
from idraa.app import create_app
from idraa.db import Base, get_engine
from idraa.models.organization import Organization
from idraa.models.risk_analysis_run import RiskAnalysisRun, RunStatus, RunType
from idraa.models.user import User


async def _create_schema(engine: AsyncEngine) -> None:
    """Create all tables on the given engine.

    Single entry point so that when Alembic migrations replace
    ``Base.metadata.create_all`` in a later milestone, only this helper
    changes instead of every fixture that needs a schema.
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


@pytest_asyncio.fixture
async def db_url(tmp_path: Path) -> str:
    """Per-test SQLite file URL.

    ``Path.as_posix()`` keeps the URL cross-platform: Windows paths would
    otherwise contain backslashes that break SQLAlchemy's SQLite URL parser.
    """
    db_file = tmp_path / f"test-{uuid.uuid4().hex}.db"
    return f"sqlite+aiosqlite:///{db_file.as_posix()}"


@pytest_asyncio.fixture
async def db_session(db_url: str) -> AsyncIterator[AsyncSession]:
    """Per-test AsyncSession bound to an isolated SQLite DB with schema created.

    SQLite foreign-key enforcement (RESTRICT / CASCADE) requires
    ``PRAGMA foreign_keys = ON`` per connection.  We reuse the production
    ``_install_sqlite_pragmas`` (which also sets WAL / synchronous /
    busy_timeout) so the test DB obeys the same per-connection PRAGMAs as the
    real ``get_engine()`` path — matching what Postgres enforces by default in
    prod and keeping a single source of truth for the PRAGMA set.
    """
    from idraa.db import _install_sqlite_pragmas, strict_json_dumps

    # json_serializer mirrors get_engine() (#327): non-finite floats must
    # fail at flush in tests exactly as they do in prod.
    engine = create_async_engine(db_url, future=True, json_serializer=strict_json_dumps)
    _install_sqlite_pragmas(engine)

    try:
        await _create_schema(engine)
        sm = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with sm() as session:
            yield session
    finally:
        # Dispose even if the test raised, to avoid leaked aiosqlite connections
        # pinning the tmp SQLite file (notably on Windows).
        await engine.dispose()


@pytest_asyncio.fixture
async def organization(db_session: AsyncSession) -> Organization:
    """A bare Organization row, no users seeded.

    Used by tests that need an org id but not a logged-in client (e.g. the
    overlay seed-migration tests in C3, and PR γ tests for C4/C5/C6 will
    reuse this same fixture).
    """
    from tests.factories import create_org

    return await create_org(db_session)


@pytest_asyncio.fixture
async def admin_user(db_session: AsyncSession, organization: Organization) -> User:
    """An ADMIN user belonging to ``organization``.

    Mirrors the ``authed_admin`` client fixture's user creation but yields
    just the User row — used by service-layer tests (e.g. C6 importer)
    that exercise audit-trail userid threading without going through the
    full HTTP login dance.
    """
    from idraa.models.enums import UserRole
    from tests.factories import create_user

    return await create_user(
        db_session,
        organization,
        email="admin@test.local",
        role=UserRole.ADMIN,
    )


@pytest_asyncio.fixture
async def seeded_critical_infrastructure_overlay(db_session, organization):
    """Returns the OverlayDefinition row for ``critical_infrastructure``,
    seeding it via the shared callable if not already present.

    B6 fix: tests build schema via Base.metadata.create_all and never
    invoke Alembic data migrations. This fixture invokes the same
    ``seed_starter_overlays_for_org`` callable the migration uses, so the
    fixture works under both production deploys (where Alembic runs) AND
    test runs (where it doesn't).
    """
    from sqlalchemy import select

    from idraa.models.overlay import OverlayDefinition
    from idraa.services.overlays import seed_starter_overlays_for_org

    await seed_starter_overlays_for_org(
        db_session,
        organization_id=organization.id,
    )

    result = await db_session.execute(
        select(OverlayDefinition).where(
            OverlayDefinition.organization_id == organization.id,
            OverlayDefinition.tag == "critical_infrastructure",
        )
    )
    od = result.scalar_one_or_none()
    if od is None:
        pytest.fail(
            "seed_starter_overlays_for_org failed to create the "
            "critical_infrastructure overlay; check STARTER_OVERLAYS + "
            "STARTER_OVERLAY_PROVENANCE in fair_cam.parameters.overlays."
        )
    return od


async def csrf_post(
    client: AsyncClient,
    url: str,
    data: dict[str, str],
    *,
    bootstrap_url: str = "/setup",
    **kwargs: _Any,
) -> _Any:
    """POST helper that does the double-submit CSRF dance.

    1. GET ``bootstrap_url`` so CSRFMiddleware issues the cookie (defaults to
       ``/setup``, which is always allowlisted by setup_guard — works pre- and
       post-bootstrap).
    2. Read the ``csrf_token`` cookie from the jar.
    3. POST to ``url`` with ``_csrf`` injected into the form data.

    Tasks 1.1.6 / 1.1.8 / 1.1.9 will each want the same GET-then-POST-with-CSRF
    helper. Extracted here so those tasks don't reinvent subtly different
    variants.
    """
    get = await client.get(bootstrap_url)
    assert get.status_code in (200, 303), (
        f"CSRF bootstrap GET {bootstrap_url} returned {get.status_code}"
    )
    token = client.cookies.get("csrf_token")
    assert token, f"csrf_token cookie was not set by GET {bootstrap_url}"
    return await client.post(url, data={**data, "_csrf": token}, **kwargs)


@pytest_asyncio.fixture
async def client(db_url: str, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncClient]:
    """AsyncClient wired to a FastAPI app whose DB URL is the per-test SQLite file.

    Tests that request both ``client`` and ``db_session`` in the same test function
    end up with two independent engines pointing at the same SQLite file. That is
    intentional — each fixture owns its lifecycle — and SQLite handles concurrent
    connections. Do not "optimize" by making them share an engine without
    accounting for cross-teardown ordering.
    """
    monkeypatch.setenv("DATABASE_URL", db_url)
    # Force settings + engine singletons to re-read the monkeypatched DATABASE_URL.
    config.reset_for_tests()

    engine: AsyncEngine | None = None
    try:
        app = create_app()
        engine = get_engine()
        await _create_schema(engine)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c
    finally:
        # Dispose even on test failure so the next test starts with fresh singletons.
        if engine is not None:
            await engine.dispose()
        db.reset_for_tests()
        config.reset_for_tests()


@pytest_asyncio.fixture
async def authed_admin(
    client: AsyncClient, db_session: AsyncSession
) -> tuple[AsyncClient, uuid.UUID]:
    """Return a client logged in as an admin of a seeded org, plus the org id.

    Imports are intentionally inline: ``tests/factories`` imports from
    ``idraa.services.auth``, and hoisting those to module scope would pull
    heavy modules into every test-collection pass (including unit tests that
    never touch auth). Keep them here.
    """
    from idraa.services.auth import SESSION_COOKIE
    from tests.factories import create_org, create_user, login_client_as

    org = await create_org(db_session)
    user = await create_user(db_session, org)
    cookie = await login_client_as(db_session, user)
    client.cookies.set(SESSION_COOKIE, cookie)
    return client, org.id


@pytest_asyncio.fixture
async def authed_analyst(
    client: AsyncClient, db_session: AsyncSession
) -> tuple[AsyncClient, uuid.UUID]:
    """Return a client logged in as an ANALYST of a seeded org, plus the org id.

    Parallel to :func:`authed_admin`; used by RBAC negative tests that
    need a non-admin authenticated session to assert the route's
    ``require_role(UserRole.ADMIN)`` dependency rejects with 403 (NOT
    401, which would mean unauthenticated). Inline imports for the same
    reason as :func:`authed_admin`.
    """
    from idraa.models.enums import UserRole
    from idraa.services.auth import SESSION_COOKIE
    from tests.factories import create_org, create_user, login_client_as

    org = await create_org(db_session)
    user = await create_user(db_session, org, email="analyst@test.local", role=UserRole.ANALYST)
    cookie = await login_client_as(db_session, user)
    client.cookies.set(SESSION_COOKIE, cookie)
    return client, org.id


@pytest_asyncio.fixture
async def authed_reviewer(
    client: AsyncClient, db_session: AsyncSession
) -> tuple[AsyncClient, uuid.UUID]:
    """Return a client logged in as a REVIEWER of a seeded org, plus the org id.

    Parallel to :func:`authed_analyst`; used by Phase 1.3 P12 RBAC
    negative tests that assert routes gated on ``require_role(ANALYST,
    ADMIN)`` reject reviewer with 403. Spec §6.6 names reviewer as
    "view-only on master data + scenarios" — so the read endpoints
    (list / detail) must succeed, but write endpoints (edit form,
    update POST, delete POST) must reject with 403.
    """
    from idraa.models.enums import UserRole
    from idraa.services.auth import SESSION_COOKIE
    from tests.factories import create_org, create_user, login_client_as

    org = await create_org(db_session)
    user = await create_user(db_session, org, email="reviewer@test.local", role=UserRole.REVIEWER)
    cookie = await login_client_as(db_session, user)
    client.cookies.set(SESSION_COOKIE, cookie)
    return client, org.id


# ---- shared seed helpers (promoted from inline ``_seed_*`` per E3.e) ----
#
# These fixtures return *callables* rather than seeded objects: a single
# test often needs to seed multiple rows with different parameters
# (e.g. two overlays, two overrides), which a per-test fixture can't
# express. The callables share session lifecycle with the test's
# ``db_session`` argument, which is why they require it to be passed
# in on each invocation rather than captured at fixture-resolution time.


@pytest_asyncio.fixture
async def seed_org_user() -> Callable[..., Awaitable[_Any]]:
    """Return a callable: ``await seed_org_user(db, role=UserRole.ANALYST)``.

    Yields ``(Organization, User)``. Defaults match the unit-test
    common case (one MANUFACTURING org + one ANALYST user). Distinct
    from the ``organization`` + ``admin_user`` fixtures in this file:
    those auto-seed a single (org, admin) pair at fixture-resolution
    time; this callable lets a test seed *multiple* (org, user) pairs
    in one body — needed by the IDOR-isolation tests in
    ``test_scenario_service.py``.
    """
    from idraa.models.enums import (
        IndustryType,
        OrganizationSize,
        UserRole,
    )
    from idraa.models.organization import Organization
    from idraa.models.user import User

    async def _impl(
        db: _Any,  # AsyncSession; lazy-typed to avoid runtime import dance
        *,
        org_name: str = "A",
        industry: IndustryType = IndustryType.MANUFACTURING,
        size: OrganizationSize = OrganizationSize.MEDIUM,
        email: str = "a@example.com",
        role: UserRole = UserRole.ANALYST,
    ) -> tuple[_Any, _Any]:
        org = Organization(name=org_name, industry_type=industry, organization_size=size)
        db.add(org)
        await db.flush()
        user = User(
            organization_id=org.id,
            email=email,
            password_hash="x",
            full_name=email.split("@")[0],
            role=role,
        )
        db.add(user)
        await db.flush()
        return org, user

    return _impl


# PR pi removed the ``seed_override`` fixture (CalibrationOverride model
# was deleted in F14 alongside the calibration runtime).


@pytest_asyncio.fixture
async def seed_organization(db_session: AsyncSession) -> Organization:
    """A seeded Organization row used by Phase 1.4 unit fixtures.

    Phase 1.4+ tests should use this seed_* family (seed_organization / seed_user /
    seed_scenario_factory / seed_control_factory). The older ``organization`` /
    ``admin_user`` family from earlier phases is still in use by Phase 1.1-1.3 tests
    and remains valid; both produce equivalent rows. Don't mix-and-match within a
    single test.
    """
    from tests.factories import create_org

    return await create_org(db_session)


@pytest_asyncio.fixture
async def seed_user(db_session: AsyncSession, seed_organization: Organization) -> User:
    """An ANALYST user in ``seed_organization``, used by Phase 1.4 unit fixtures."""
    from idraa.models.enums import UserRole
    from tests.factories import create_user

    return await create_user(
        db_session,
        seed_organization,
        email="analyst-seed@test.local",
        role=UserRole.ANALYST,
    )


@pytest_asyncio.fixture
async def seed_scenario_factory(
    db_session: AsyncSession,
    seed_organization: Organization,
    seed_user: User,
) -> Callable[..., Awaitable[_Any]]:
    """Factory fixture: ``await seed_scenario_factory(name="...", **overrides)``.

    Creates a minimal schema-valid Scenario in ``seed_organization``.
    Any field accepted by the Scenario ORM constructor can be passed as
    a keyword argument; the factory fills safe defaults for all required
    NOT NULL columns.

    Route-test fixtures that authenticate as a user in a DIFFERENT
    organization (e.g. ``authed_analyst``) can pass ``organization_id``
    and ``created_by`` to scope the seeded row outside ``seed_organization``
    (issue #102).
    """
    from idraa.models.enums import EntityStatus, ScenarioType, ThreatCategory
    from idraa.models.scenario import Scenario

    async def _factory(
        *,
        name: str,
        status: EntityStatus = EntityStatus.ACTIVE,
        organization_id: uuid.UUID | None = None,
        created_by: uuid.UUID | None = None,
        **kwargs: _Any,
    ) -> Scenario:
        scenario = Scenario(
            organization_id=organization_id
            if organization_id is not None
            else seed_organization.id,
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
            status=status,
            created_by=created_by if created_by is not None else seed_user.id,
            **kwargs,
        )
        db_session.add(scenario)
        await db_session.commit()
        return scenario

    return _factory


@pytest_asyncio.fixture
async def seed_scenario_with_no_controls(
    seed_scenario_factory: Callable[..., Awaitable[_Any]],
) -> _Any:
    """A single committed Scenario row with no controls attached."""
    return await seed_scenario_factory(name="phase-1-4-test-scenario")


@pytest_asyncio.fixture
async def scenario_factory(
    db_session: AsyncSession,
    seed_organization: Organization,
    seed_user: User,
) -> Callable[..., Awaitable[_Any]]:
    """Factory fixture: ``await scenario_factory(**overrides)`` -> Scenario.

    Shared across ``tests/models/``, ``tests/services/`` and
    ``tests/routes/`` (issue #475 T1/T7/T8/T11/T12/T13/T14). Unlike
    ``seed_scenario_factory`` (which commits — used by pre-existing
    route-style fixtures), this factory only **flushes**: SC3-I1 —
    service/model tests flush; any test that seeds rows and then drives a
    route (HTTP client) must ``await db_session.commit()`` itself after
    calling this factory, since the app client runs on a SEPARATE engine
    from ``db_session`` and would not see an uncommitted write.

    Defaults to ``seed_organization`` — Arch2-I4: route-test modules whose
    authenticated client owns a DIFFERENT org (e.g. ``authed_analyst``,
    ``analyst_client``) must pass ``organization_id=`` (and typically
    ``created_by=``) explicitly, mirroring
    ``tests/routes/test_scenario_detail_recommendations.py``'s seed
    fixtures, or the scenario lands in the wrong org and route lookups
    404.

    SC-I2 defensive re-add: a mid-test ``db_session.rollback()`` (e.g. after
    provoking an IntegrityError to assert a UNIQUE constraint) discards
    EVERY pending change in the session's single flat transaction — not just
    rows the test just added, but also the ``seed_organization`` /
    ``seed_user`` rows this factory defaults to, since those were only
    ``flush()``-ed (never committed) by their own fixtures. A subsequent
    factory call would otherwise hit a FOREIGN KEY failure inserting a
    Scenario against an organization_id/created_by that rollback just
    erased. Re-``add()``-ing them here is a no-op when they're already
    persistent (the common case) and re-persists them with their ORIGINAL
    ids when rollback expunged them.
    """
    from idraa.models.enums import EntityStatus, ScenarioType, ThreatCategory
    from idraa.models.scenario import Scenario

    async def _factory(
        *,
        name: str = "attack-crosswalk-test-scenario",
        status: EntityStatus = EntityStatus.ACTIVE,
        organization_id: uuid.UUID | None = None,
        created_by: uuid.UUID | None = None,
        **kwargs: _Any,
    ) -> Scenario:
        if organization_id is None and seed_organization not in db_session:
            db_session.add(seed_organization)
        if created_by is None and seed_user not in db_session:
            db_session.add(seed_user)
        scenario = Scenario(
            organization_id=organization_id
            if organization_id is not None
            else seed_organization.id,
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
            status=status,
            created_by=created_by if created_by is not None else seed_user.id,
            **kwargs,
        )
        db_session.add(scenario)
        await db_session.flush()
        return scenario

    return _factory


@pytest_asyncio.fixture
async def seed_control_factory(
    db_session: AsyncSession,
    seed_organization: Organization,
    seed_user: User,
) -> Callable[..., Awaitable[_Any]]:
    """Factory fixture: ``await seed_control_factory(name="...", **overrides)``.

    Creates a minimal schema-valid Control + one ControlFunctionAssignment in
    ``seed_organization``. The old ``function``/``control_strength``/
    ``control_reliability``/``control_coverage`` kwargs were removed in PR iota;
    pass ``capability_value``/``coverage``/``reliability`` instead.

    Route-test fixtures that authenticate as a user in a DIFFERENT
    organization (e.g. ``authed_analyst``) can pass ``organization_id``
    and ``created_by`` to scope the seeded row outside ``seed_organization``
    (issue #102).
    """
    from datetime import UTC, datetime
    from decimal import Decimal

    from idraa.models.control import Control
    from idraa.models.control_function_assignment import ControlFunctionAssignment
    from idraa.models.enums import ControlDomain, ControlType, EntityStatus, FairCamSubFunction

    # Default sub_function per domain: LE→LEC_PREV_RESISTANCE,
    # VM→VMC_PREV_REDUCE_VARIANCE_PROB, DS→DSC_PREV_DEFINED_EXPECTATIONS.
    _default_sub_fn: dict[ControlDomain, FairCamSubFunction] = {
        ControlDomain.LOSS_EVENT: FairCamSubFunction.LEC_PREV_RESISTANCE,
        ControlDomain.VARIANCE_MANAGEMENT: FairCamSubFunction.VMC_PREV_REDUCE_VARIANCE_PROB,
        ControlDomain.DECISION_SUPPORT: FairCamSubFunction.DSC_PREV_DEFINED_EXPECTATIONS,
    }

    async def _factory(
        *,
        name: str,
        capability_value: float = 0.7,
        coverage: float = 0.8,
        reliability: float = 0.85,
        domain: ControlDomain = ControlDomain.VARIANCE_MANAGEMENT,
        type_: ControlType = ControlType.TECHNICAL,
        sub_function: FairCamSubFunction | None = None,
        organization_id: uuid.UUID | None = None,
        created_by: uuid.UUID | None = None,
    ) -> Control:
        _org_id = organization_id if organization_id is not None else seed_organization.id
        _created_by = created_by if created_by is not None else seed_user.id
        ctrl = Control(
            id=uuid.uuid4(),
            organization_id=_org_id,
            name=name,
            type=type_,
            annual_cost=Decimal("0"),
            nist_csf_functions=[],
            iso_27001_domains=[],
            compliance_mappings={},
            skill_requirements=[],
            technology_dependencies=[],
            applicable_industries=[],
            applicable_org_sizes=[],
            status=EntityStatus.ACTIVE,
            version="1.0",
            created_by=_created_by,
        )
        db_session.add(ctrl)
        await db_session.flush()  # populate ctrl.id

        _sub_fn = sub_function or _default_sub_fn[domain]
        asgn = ControlFunctionAssignment(
            control_id=ctrl.id,
            organization_id=_org_id,
            sub_function=_sub_fn,
            capability_value=capability_value,
            coverage=coverage,
            reliability=reliability,
            confirmed_by_user_at=datetime.now(UTC),
        )
        db_session.add(asgn)

        # Slice 2 (#439) fixture repair: the domain default for VARIANCE_MANAGEMENT
        # / DECISION_SUPPORT is a meta-only (VMC/DSC) channel, which post-Slice-2-D1
        # correctly scores $0 standalone reduction (direct meta targets retired; meta
        # value now flows via the kappa reliability coupling, which requires a
        # co-present LEC channel to have anything to uplift). Most prod controls
        # carry an LEC channel, so a bare `seed_control_factory(name="X")` call with
        # no explicit sub_function should too, or callers relying on "this control
        # reduces risk on its own" silently get a $0-reduction control. Only applies
        # when the CALLER did not request a specific sub_function/domain — an
        # explicit `sub_function=` override (used throughout the suite for
        # domain-specific scenarios) is left untouched.
        if sub_function is None and domain in (
            ControlDomain.VARIANCE_MANAGEMENT,
            ControlDomain.DECISION_SUPPORT,
        ):
            db_session.add(
                ControlFunctionAssignment(
                    control_id=ctrl.id,
                    organization_id=_org_id,
                    sub_function=FairCamSubFunction.LEC_PREV_RESISTANCE,
                    capability_value=0.7,
                    coverage=0.8,
                    reliability=0.8,
                    confirmed_by_user_at=datetime.now(UTC),
                )
            )

        await db_session.commit()
        return ctrl

    return _factory


@pytest_asyncio.fixture
async def seed_organization_factory(
    db_session: AsyncSession,
) -> Callable[..., Awaitable[_Any]]:
    """Factory fixture: ``await seed_organization_factory(name="...")``.

    Phase 1.4+ tests use this to create multiple Organizations in a single
    test (e.g., cross-org IDOR or sub-sector × no-sub-sector comparisons).
    Mirrors the column-set of ``seed_organization`` (industry_type +
    organization_size required by Organization NOT NULL constraints).

    Accepts an optional ``industry_sub_sector`` kwarg (F9 addition) so that
    sub-sector overlay integration tests can create orgs with different
    sub-sector values without duplicating boilerplate.
    """
    from idraa.models.enums import IndustrySubSector, IndustryType, OrganizationSize
    from idraa.models.organization import Organization

    async def _factory(
        *,
        name: str,
        industry_sub_sector: IndustrySubSector | None = None,
    ) -> Organization:
        org = Organization(
            name=name,
            industry_type=IndustryType.MANUFACTURING,
            organization_size=OrganizationSize.MEDIUM,
            industry_sub_sector=industry_sub_sector,
        )
        db_session.add(org)
        await db_session.commit()
        return org

    return _factory


@pytest_asyncio.fixture
async def wire_executor_to_test_db(
    db_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[None]:
    """Wire execute_run's _get_sessionmaker() to the per-test SQLite DB.

    execute_run opens its own session via idraa.db._get_sessionmaker(),
    which is built from get_settings().database_url. The db_session fixture
    creates its own engine directly from db_url but does NOT set DATABASE_URL
    in the environment, so by default execute_run would open a DIFFERENT DB.

    This fixture sets DATABASE_URL + resets both singletons (config + db)
    so that the next call to _get_sessionmaker() picks up the test DB.

    Teardown also resets the cached singletons so subsequent tests get a
    fresh sessionmaker pointing at their own DB.
    """
    monkeypatch.setenv("DATABASE_URL", db_url)
    config.reset_for_tests()
    db.reset_for_tests()

    yield  # tests run here

    # Teardown: clear cached singletons so the next test starts fresh.
    config.reset_for_tests()
    db.reset_for_tests()


@pytest_asyncio.fixture
async def seed_scenario_with_controls(
    db_session: AsyncSession,
    seed_scenario_factory: Callable[..., Awaitable[_Any]],
    seed_control_factory: Callable[..., Awaitable[_Any]],
) -> _Any:
    """Scenario with 2 mitigating controls + valid IRIS calibration anchors.

    Used by Phase 1.4 executor tests as the canonical happy-path subject.
    Industry and revenue_tier are now sourced from the seed_organization.
    """
    from idraa.models.scenario_control import ScenarioControl

    scenario = await seed_scenario_factory(
        name="phase-1-4-with-controls",
    )
    c1 = await seed_control_factory(name="Firewall")
    c2 = await seed_control_factory(name="EDR")
    db_session.add(ScenarioControl(scenario_id=scenario.id, control_id=c1.id))
    db_session.add(ScenarioControl(scenario_id=scenario.id, control_id=c2.id))
    await db_session.commit()
    await db_session.refresh(scenario)
    return scenario


@pytest_asyncio.fixture
async def seed_run_factory(
    db_session: AsyncSession,
    seed_scenario_with_controls: _Any,
    seed_user: User,
    seed_organization: Organization,
) -> Callable[..., Awaitable[_Any]]:
    """Factory fixture: ``await seed_run_factory(status=RunStatus.QUEUED, **overrides)``.

    Creates a minimal schema-valid RiskAnalysisRun. Defaults to
    ``seed_organization`` when no org override is passed; callers that need
    an analyst-org run pass ``organization=<Organization>`` (object form) OR
    ``organization_id=<uuid>`` (id form), but not both — passing both raises
    ValueError. PR nu adds simulation_results / controls_snapshot /
    completed_at kwargs for chart-rendering tests.
    """
    import hashlib

    from idraa.models.risk_analysis_run import RiskAnalysisRun, RunStatus, RunType

    async def _factory(
        *,
        scenario: _Any = None,
        status: RunStatus = RunStatus.QUEUED,
        mc_iterations: int = 1000,
        organization: _Any = None,
        organization_id: uuid.UUID | None = None,
        simulation_results: dict[str, _Any] | None = None,
        controls_snapshot: list[dict[str, _Any]] | None = None,
        completed_at: _Any = None,
        random_seed: int | None = None,
    ) -> RiskAnalysisRun:
        _scenario = scenario or seed_scenario_with_controls
        # Mutually-exclusive: passing both is almost always a test typo.
        if organization is not None and organization_id is not None:
            raise ValueError(
                "seed_run_factory: pass exactly one of organization=<obj> or "
                "organization_id=<uuid>; got both."
            )
        if organization is not None:
            _org_id = organization.id
        elif organization_id is not None:
            _org_id = organization_id
        else:
            _org_id = seed_organization.id
        run = RiskAnalysisRun(
            id=uuid.uuid4(),
            organization_id=_org_id,
            scenario_id=_scenario.id,
            mc_iterations=mc_iterations,
            inputs_hash=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
            controls_snapshot=controls_snapshot if controls_snapshot is not None else [],
            control_ids_used=[],
            status=status,
            run_type=RunType.SINGLE,
            created_by=seed_user.id,
            simulation_results=simulation_results,
            completed_at=completed_at,
            random_seed=random_seed,
        )
        db_session.add(run)
        await db_session.commit()
        await db_session.refresh(run)
        return run

    return _factory


@pytest_asyncio.fixture
async def seed_completed_run(
    db_session: AsyncSession,
    seed_scenario_with_controls: _Any,
    seed_user: User,
    seed_organization: Organization,
    wire_executor_to_test_db: None,
) -> _Any:
    """A run in COMPLETED state with persisted simulation_results.

    Uses ``RunService.create_and_dispatch`` with ``mc_iterations_override=200``
    (below the 1000-iteration sync threshold) so the executor runs inline
    without a background task — the returned run is already COMPLETED with
    ``simulation_results`` populated. ``wire_executor_to_test_db`` wires the
    executor's ``_get_sessionmaker()`` to the per-test SQLite DB.

    Depends on ``seed_scenario_with_controls`` (industry=tech, 2 controls,
    valid IRIS 2025 calibration anchors) as the canonical happy-path subject.
    """
    from fastapi import BackgroundTasks

    from idraa.services.runs import RunService

    bg = BackgroundTasks()
    service = RunService(db_session)
    run = await service.create_and_dispatch(
        organization_id=seed_organization.id,
        scenario_ids=[seed_scenario_with_controls.id],
        mc_iterations_override=200,
        created_by=seed_user.id,
        background_tasks=bg,
    )
    # mc_iterations=200 < _SYNC_THRESHOLD=1000 → execute_run ran inline;
    # bg.tasks is empty and run is already COMPLETED. No additional steps needed.
    await db_session.refresh(run)
    return run


@pytest_asyncio.fixture
async def authed_other_org_analyst(
    client: AsyncClient, db_session: AsyncSession
) -> tuple[AsyncClient, uuid.UUID]:
    """Return a client logged in as an ANALYST of a SECOND org (not seed_organization).

    Used exclusively by IDOR tests in test_run_routes_idor.py (and future
    cross-org guard tests). The analyst has valid credentials but belongs to
    a different organization than the runs seeded by seed_completed_run /
    seed_run_factory, so org-scoped lookups must return 404.
    """
    from idraa.models.enums import UserRole
    from idraa.services.auth import SESSION_COOKIE
    from tests.factories import create_org, create_user, login_client_as

    other_org = await create_org(db_session, name="Other Org IDOR")
    other_user = await create_user(
        db_session, other_org, email="other-analyst@test.local", role=UserRole.ANALYST
    )
    cookie = await login_client_as(db_session, other_user)
    client.cookies.set(SESSION_COOKIE, cookie)
    return client, other_org.id


@pytest_asyncio.fixture
async def admin_client(authed_admin: tuple[AsyncClient, uuid.UUID]) -> AsyncClient:
    """Flat AsyncClient for an authed admin (drop the org-id tuple second slot).
    Phase 1.5a tests prefer the flat shape so they don't have to unpack
    ``client, _ = authed_admin``. Use ``admin_client`` for HTTP assertions;
    use ``authed_admin`` directly when the test also needs the org id.
    """
    client, _org_id = authed_admin
    return client


@pytest_asyncio.fixture
async def analyst_client(authed_analyst: tuple[AsyncClient, uuid.UUID]) -> AsyncClient:
    """Flat AsyncClient for an authed analyst. See ``admin_client`` rationale."""
    client, _org_id = authed_analyst
    return client


@pytest_asyncio.fixture
async def reviewer_client(authed_reviewer: tuple[AsyncClient, uuid.UUID]) -> AsyncClient:
    """Flat AsyncClient for an authed reviewer. See ``admin_client`` rationale."""
    client, _org_id = authed_reviewer
    return client


@pytest_asyncio.fixture
async def authed_viewer(
    client: AsyncClient, db_session: AsyncSession
) -> tuple[AsyncClient, uuid.UUID]:
    """Return a client logged in as a VIEWER of a seeded org, plus the org id.
    F0 addition: Phase 1.5a wizard RBAC tests (F18) and library RBAC tests
    (F13) need viewer-role assertions. Mirrors authed_admin / authed_analyst
    / authed_reviewer one-for-one.
    """
    from idraa.models.enums import UserRole
    from idraa.services.auth import SESSION_COOKIE
    from tests.factories import create_org, create_user, login_client_as

    org = await create_org(db_session)
    user = await create_user(db_session, org, email="viewer@test.local", role=UserRole.VIEWER)
    cookie = await login_client_as(db_session, user)
    client.cookies.set(SESSION_COOKIE, cookie)
    return client, org.id


@pytest_asyncio.fixture
async def viewer_client(authed_viewer: tuple[AsyncClient, uuid.UUID]) -> AsyncClient:
    """Flat AsyncClient for an authed viewer. See ``admin_client`` rationale."""
    client, _org_id = authed_viewer
    return client


@pytest_asyncio.fixture
async def anonymous_client(client: AsyncClient) -> AsyncClient:
    """An unlogged-in AsyncClient — no SESSION_COOKIE set. RBAC negative tests
    use this to assert routes redirect to /login (303) or 401 for JSON callers."""
    return client


@pytest_asyncio.fixture
async def client_styleguide_on(
    monkeypatch: pytest.MonkeyPatch,
    authed_admin: tuple[AsyncClient, uuid.UUID],
) -> AsyncIterator[tuple[AsyncClient, uuid.UUID]]:
    """AsyncClient (admin) with Settings.dev_styleguide_enabled flipped on.

    Plan-gate Arch-7: the route is mounted unconditionally; the in-handler
    404 gate reads get_settings().dev_styleguide_enabled at request time.
    We reset the Settings singleton so the new env var takes effect.
    """
    monkeypatch.setenv("DEV_STYLEGUIDE_ENABLED", "true")
    config.reset_for_tests()
    yield authed_admin
    config.reset_for_tests()


@pytest_asyncio.fixture
async def seed_library_entry(db_session: AsyncSession) -> _Any:
    """Pre-seeded published library entry for override + service tests.

    F3 / F7 / F8 / F9 / F22 use this fixture; the ``_new_entry`` helper
    in test_scenario_library_entry_model.py is a more flexible builder
    for tests that need multiple variants.
    """
    import uuid as _uuid

    from idraa.models.enums import AssetClass, ThreatActorType, ThreatCategory
    from idraa.models.scenario_library import ScenarioLibraryEntry

    entry = ScenarioLibraryEntry(
        id=_uuid.uuid4(),
        version=1,
        slug="test-entry-fixture",
        name="Test Library Entry",
        status="published",
        threat_event_type=ThreatCategory.RANSOMWARE,
        threat_actor_type=ThreatActorType.CYBERCRIMINALS,
        asset_class=AssetClass.SYSTEMS,
        tags=[],
        description="A fixture entry for unit tests.",
        canonical_fair_gap="Test fixture; not a real gap.",
        source_citations=[],
        threat_event_frequency={"distribution": "PERT", "low": 1.0, "mode": 4.0, "high": 12.0},
        vulnerability={"distribution": "PERT", "low": 0.05, "mode": 0.20, "high": 0.50},
        primary_loss={
            "distribution": "PERT",
            "low": 100_000.0,
            "mode": 750_000.0,
            "high": 5_000_000.0,
        },
        suggested_control_ids=[],
    )
    db_session.add(entry)
    await db_session.commit()
    await db_session.refresh(entry)
    return entry


@pytest_asyncio.fixture
async def seed_library_entries_factory(
    db_session: AsyncSession,
) -> Callable[..., Awaitable[list[_Any]]]:
    """Bulk-create N library entries for pagination tests.

    Each entry gets a unique name + slug suffix; all are status=published.
    Mirrors seed_library_entry's required-field set.
    """
    import uuid as _uuid

    from idraa.models.enums import AssetClass, ThreatActorType, ThreatCategory
    from idraa.models.scenario_library import ScenarioLibraryEntry

    async def _factory(count: int) -> list[_Any]:
        entries: list[_Any] = []
        for i in range(count):
            entry = ScenarioLibraryEntry(
                id=_uuid.uuid4(),
                version=1,
                slug=f"factory-entry-{i:03d}",
                name=f"Library Entry {i:03d}",
                status="published",
                threat_event_type=ThreatCategory.RANSOMWARE,
                threat_actor_type=ThreatActorType.CYBERCRIMINALS,
                asset_class=AssetClass.SYSTEMS,
                tags=[],
                description=f"Factory entry {i:03d} for pagination tests.",
                canonical_fair_gap=f"Factory gap {i:03d}.",
                source_citations=[],
                threat_event_frequency={
                    "distribution": "PERT",
                    "low": 1.0,
                    "mode": 4.0,
                    "high": 12.0,
                },
                vulnerability={"distribution": "PERT", "low": 0.05, "mode": 0.20, "high": 0.50},
                primary_loss={
                    "distribution": "PERT",
                    "low": 100_000.0,
                    "mode": 750_000.0,
                    "high": 5_000_000.0,
                },
                suggested_control_ids=[],
            )
            db_session.add(entry)
            entries.append(entry)
        await db_session.commit()
        return entries

    return _factory


@pytest_asyncio.fixture
async def seed_overlay() -> Callable[..., Awaitable[_Any]]:
    """Return a callable: ``await seed_overlay(db, org_id=..., tag=...)``.

    Seeds an OverlayDefinition + matching v1 revision.
    P2: column is ``version`` (not ``current_version``).
    P7: ``OverlayDefinition.display_name`` is NOT NULL, and
    ``OverlayDefinitionRevision.tag``/``display_name`` are NOT NULL.
    """
    from idraa.models.overlay import OverlayDefinition, OverlayDefinitionRevision

    async def _impl(
        db: _Any,
        *,
        org_id: uuid.UUID,
        tag: str,
        version: int = 1,
    ) -> _Any:
        od = OverlayDefinition(
            organization_id=org_id,
            tag=tag,
            display_name=f"Overlay {tag}",
            version=version,
            frequency_multiplier=1.40,
            magnitude_multiplier=2.00,
            methodology="Critical-infrastructure overlay; twenty-char min met.",
            sources=["nist_csf_2024"],
        )
        db.add(od)
        await db.flush()
        rev = OverlayDefinitionRevision(
            overlay_definition_id=od.id,
            version=version,
            tag=tag,
            display_name=f"Overlay {tag}",
            frequency_multiplier=od.frequency_multiplier,
            magnitude_multiplier=od.magnitude_multiplier,
            methodology=od.methodology,
            sources=list(od.sources),
            methodology_change_reason="Initial overlay",
        )
        db.add(rev)
        await db.flush()
        return od

    return _impl


@pytest_asyncio.fixture
async def seed_aggregate_run_factory(
    db_session: AsyncSession,
    seed_organization: Organization,
    seed_user: User,
    seed_scenario_factory: _Any,
    seed_control_factory: _Any,
    wire_executor_to_test_db: None,  # required for execute_run() in tests
) -> Callable[..., Awaitable[RiskAnalysisRun]]:
    """AGGREGATE-typed RiskAnalysisRun with N scenarios + N controls in seed_organization."""

    async def _factory(
        n_scenarios: int = 2,
        n_controls: int = 1,
        n_simulations: int = 5000,
    ) -> RiskAnalysisRun:
        scenarios = [
            await seed_scenario_factory(name=f"agg_scenario_{i}") for i in range(n_scenarios)
        ]
        controls = [await seed_control_factory(name=f"agg_control_{i}") for i in range(n_controls)]
        from idraa.services.run_inputs_hash import build_aggregate_inputs_hash

        run = RiskAnalysisRun(
            id=uuid.uuid4(),
            organization_id=seed_organization.id,
            run_type=RunType.AGGREGATE,
            scenario_id=None,
            aggregate_scenario_ids=sorted(str(s.id) for s in scenarios),
            control_ids_used=sorted(str(c.id) for c in controls),
            mc_iterations=n_simulations,
            inputs_hash=build_aggregate_inputs_hash(
                scenarios=scenarios,
                control_ids=[c.id for c in controls],
                mc_iterations=n_simulations,
            ),
            controls_snapshot=[],
            created_by=seed_user.id,
            status=RunStatus.QUEUED,
        )
        db_session.add(run)
        await db_session.commit()
        return run

    return _factory


@pytest.fixture(autouse=True)
def _clear_active_run_registry() -> Iterator[None]:
    """#211 Phase 2 review NTH: the active-run registry is module-level
    mutable state — clear it after every test so a test that registers an id
    and dies before its own cleanup can never sweep-exempt rows in later
    tests. execute_run's finally already guarantees this on normal paths;
    this is the bulletproofing layer."""
    yield
    from idraa.services.run_reaper import _ACTIVE_RUNS

    _ACTIVE_RUNS.clear()
