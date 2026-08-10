"""Step-up COMPLETENESS guard (B2).

``test_step_up_call_sites.py`` tripwires a REGRESSION in the *count* of
``require_step_up`` wirings — but a brand-new admin route that simply never
wires the gate is invisible to a count (the total only has to stay put). This
guard closes that hole: it introspects the live app and asserts that EVERY
admin-only state-changing route (``POST``/``PUT``/``PATCH``/``DELETE`` gated by
``require_role(ADMIN)`` alone) either carries a ``require_step_up`` dependency
OR is named in an explicit exemption allowlist. A new admin route therefore
fails this test until someone consciously decides: gate it, or allowlist it
with a rationale.

The allowlist is the routine reference-data-authoring / multi-step-import
surface, where a per-request re-auth would be disruptive with little security
benefit. Sensitive config (org profile, FX rates, security settings, SME
directory) and every destructive / credential / user-management route are
gated instead.

Introspection couples to two internal shapes and asserts a non-vacuous floor
so it can never pass by silently finding nothing:

* the app's ``_IncludedRouter`` wrapper (descend via ``original_router``), and
* the closure qualnames of ``require_role`` / ``require_step_up``.
"""

from __future__ import annotations

from fastapi.routing import APIRoute

from idraa.app import app
from idraa.models.enums import UserRole

_STATE_CHANGING = {"POST", "PUT", "PATCH", "DELETE"}

# Admin-only state-changing routes intentionally WITHOUT step-up: routine
# reference-data authoring and the multi-step import wizards. None of these
# feeds a stale-session-exploitable org-wide computation the way /organization
# and /fx-rates do, and gating each step of an import wizard would be
# disruptive for negligible benefit. A NEW admin route lands here only with a
# deliberate rationale — never by default.
_STEP_UP_EXEMPT: frozenset[str] = frozenset(
    {
        "/controls/import",
        "/library/import",
        "/library/import/confirm",
        "/library/overrides",
        "/library/overrides/{override_id}",
        "/overlays",
        "/overlays/import",
        "/overlays/import/confirm",
        "/overlays/{overlay_id}/edit",
        "/qualitative-bands",
        "/qualitative-bands/{band_id}",
        "/register-import",
        "/register-import/{token}/apply-profile",
        "/register-import/{token}/bind",
        "/register-import/{token}/columns",
        "/register-import/{token}/convert",
        "/register-import/{token}/sheet",
        "/scenarios/import",
        "/scenarios/import/confirm",
    }
)

# Non-vacuous floor. If the route-walk or closure inspection ever silently
# breaks (router internals change, closure shape changes), enumeration must
# FAIL rather than pass by finding nothing. The real count is ~37; this floor
# sits comfortably below it.
_MIN_ADMIN_STATE_CHANGING = 20


def _flatten(routes):
    for r in routes:
        if isinstance(r, APIRoute):
            yield r
        orig = getattr(r, "original_router", None)
        if orig is not None and getattr(orig, "routes", None):
            yield from _flatten(orig.routes)
        sub = getattr(r, "routes", None)
        if sub:
            yield from _flatten(sub)


def _iter_deps(dep):
    yield dep
    for sub in dep.dependencies:
        yield from _iter_deps(sub)


def _require_role_roles(route):
    """The roles tuple from this route's ``require_role`` gate, or ``None``."""
    for d in _iter_deps(route.dependant):
        call = d.call
        if "require_role.<locals>._checker" in getattr(call, "__qualname__", ""):
            for cell in call.__closure__ or []:
                try:
                    val = cell.cell_contents
                except ValueError:
                    continue
                if isinstance(val, tuple) and val and all(isinstance(x, UserRole) for x in val):
                    return val
    return None


def _has_step_up(route):
    return any(
        "require_step_up.<locals>._dep" in getattr(d.call, "__qualname__", "")
        for d in _iter_deps(route.dependant)
    )


def _admin_only_state_changing():
    out = []
    for route in _flatten(app.routes):
        if not isinstance(route, APIRoute):
            continue
        if not ((route.methods or set()) & _STATE_CHANGING):
            continue
        roles = _require_role_roles(route)
        if roles is not None and set(roles) == {UserRole.ADMIN}:
            out.append(route)
    return out


def test_every_admin_state_changing_route_is_gated_or_allowlisted():
    routes = _admin_only_state_changing()
    # Non-vacuous: the enumeration actually saw the admin surface.
    assert len(routes) >= _MIN_ADMIN_STATE_CHANGING, (
        f"introspection found only {len(routes)} admin-only state-changing "
        "routes — below the floor; the route-walk or closure inspection likely "
        "broke and this guard would otherwise pass vacuously."
    )
    ungated = sorted(
        r.path for r in routes if not _has_step_up(r) and r.path not in _STEP_UP_EXEMPT
    )
    assert not ungated, (
        "admin-only state-changing route(s) missing require_step_up and not in "
        f"_STEP_UP_EXEMPT: {ungated}. Either wire "
        "Depends(require_step_up(StepUpCategory.ADMIN)) on the route or add its "
        "path to the allowlist with a rationale."
    )


def test_step_up_exempt_allowlist_has_no_stale_entries():
    """Every allowlisted path must still be a live admin-only state-changing
    route. Catches renames / deletions / role-changes so the allowlist can't
    rot into paths that exempt nothing (or mask a route that later needs a
    gate)."""
    live = {r.path for r in _admin_only_state_changing()}
    stale = sorted(_STEP_UP_EXEMPT - live)
    assert not stale, (
        "_STEP_UP_EXEMPT entries no longer match a live admin-only "
        f"state-changing route (renamed / deleted / re-scoped?): {stale}"
    )
