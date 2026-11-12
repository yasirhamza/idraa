"""Every EXPORTS-step-up route needs the export role, or an explicit exemption (#315).

Read-only roles (viewer, reviewer) read on screen; taking the data away as a
file needs analyst or admin. The walk is over the live route table, so a new
route behind EXPORTS step-up cannot ship open to every role unnoticed: it must
either carry a ``require_role`` allowlist inside ``EXPORT_ROLES`` or be added
to ``_OPEN_TO_EVERY_ROLE`` with a reason.

Limit: the walk finds export routes by their EXPORTS step-up. A download route
that carries no EXPORTS step-up is outside what it sees.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Any

from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute

from idraa.app import app
from idraa.models.enums import StepUpCategory, UserRole
from idraa.routes.deps import EXPORT_ROLES, can_export

# EXPORTS-gated routes deliberately open to every authenticated role.
#   - the run PDF is the report a read-only reader exists to take away
#     (owner decision 2026-11-11, #315).
_OPEN_TO_EVERY_ROLE: frozenset[tuple[str, str]] = frozenset({("GET", "/reports/run/{run_id}")})

# The live count: 13 role-gated routes and the PDF (the users export sits under
# ADMIN step-up, not EXPORTS). Pinned exactly, so a route that silently leaves
# the walk (closure inspection breaking, or EXPORTS step-up dropped from an
# export) fails here, and a new export changes the number on purpose.
_EXPORT_ROUTE_COUNT = 14


def _flatten(routes: Iterable[Any]) -> Iterator[APIRoute]:
    for r in routes:
        if isinstance(r, APIRoute):
            yield r
        orig = getattr(r, "original_router", None)
        if orig is not None and getattr(orig, "routes", None):
            yield from _flatten(orig.routes)
        sub = getattr(r, "routes", None)
        if sub:
            yield from _flatten(sub)


def _iter_deps(dep: Dependant) -> Iterator[Dependant]:
    yield dep
    for sub in dep.dependencies:
        yield from _iter_deps(sub)


def _closure_values(call: object) -> Iterator[Any]:
    for cell in getattr(call, "__closure__", None) or []:
        try:
            yield cell.cell_contents
        except ValueError:
            continue


def _step_up_categories(route: APIRoute) -> set[StepUpCategory]:
    out: set[StepUpCategory] = set()
    for d in _iter_deps(route.dependant):
        if "require_step_up.<locals>._dep" in getattr(d.call, "__qualname__", ""):
            out.update(v for v in _closure_values(d.call) if isinstance(v, StepUpCategory))
    return out


def _role_allowlist(route: APIRoute) -> tuple[UserRole, ...] | None:
    for d in _iter_deps(route.dependant):
        if "require_role.<locals>._checker" in getattr(d.call, "__qualname__", ""):
            for val in _closure_values(d.call):
                if isinstance(val, tuple) and val and all(isinstance(x, UserRole) for x in val):
                    return val
    return None


def _export_routes() -> dict[tuple[str, str], APIRoute]:
    """EXPORTS-step-up routes keyed by (method, path), one key per method."""
    return {
        (method, r.path): r
        for r in _flatten(app.routes)
        if StepUpCategory.EXPORTS in _step_up_categories(r)
        for method in sorted(r.methods or ())
    }


def test_export_roles_are_analyst_and_admin() -> None:
    assert set(EXPORT_ROLES) == {UserRole.ANALYST, UserRole.ADMIN}


def test_walk_finds_exactly_the_export_routes() -> None:
    assert len(_export_routes()) == _EXPORT_ROUTE_COUNT


def test_every_export_route_requires_the_export_role_or_is_exempt() -> None:
    open_routes = []
    for key, route in sorted(_export_routes().items()):
        if key in _OPEN_TO_EVERY_ROLE:
            continue
        roles = _role_allowlist(route)
        if roles is None or not set(roles) <= set(EXPORT_ROLES):
            open_routes.append((key, None if roles is None else sorted(r.value for r in roles)))
    assert open_routes == [], (
        "EXPORTS-gated routes open beyond analyst/admin; gate them with "
        f"require_export_role or add to _OPEN_TO_EVERY_ROLE with a reason: {open_routes}"
    )


def test_exemptions_are_live_and_really_open() -> None:
    """An exemption that names no route, or names a gated one, is stale."""
    routes = _export_routes()
    for key in _OPEN_TO_EVERY_ROLE:
        assert key in routes, f"stale exemption: {key}"
        assert _role_allowlist(routes[key]) is None, f"{key} is role-gated; drop the exemption"


def test_can_export_matches_the_role_set() -> None:
    class _U:
        def __init__(self, role: UserRole) -> None:
            self.role = role

    assert can_export(None) is False
    for role in UserRole:
        assert can_export(_U(role)) is (role in EXPORT_ROLES)
