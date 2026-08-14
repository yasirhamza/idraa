"""Schemathesis ``before_call`` hook: inject a valid CSRF token into bodies.

Loaded via the ``SCHEMATHESIS_HOOKS=security.dast.hooks`` env var (the
orchestrator sets it before invoking ``schemathesis run``). The session
cookie itself travels as a static ``-H "Cookie: ..."`` header set by the
orchestrator — this hook only handles the double-submit ``_csrf`` field,
which Schemathesis would otherwise fuzz like any other body field and which
would then fail CSRF verification on every state-changing request before the
handler logic is ever exercised.

``DAST_CSRF`` is read **inside** the hook body, not at module import time —
importing this module (e.g. the harness's own smoke-import check) must not
require the env var to already be set; only actually firing the hook (once a
real fuzzing run is underway, with the value set by the orchestrator after
login) does.

**Signature note (deviates from the design doc's illustrative 2-arg
sketch):** schemathesis 4.24.3's hook registration validates strictly by
parameter *count* against the ``before_call`` spec
(``schemathesis/hook_specs.py``), which declares three parameters —
``(context, case, kwargs)``. A 2-parameter ``def before_call(context,
case):`` raises ``TypeError: Hook 'before_call' takes 3 arguments but 2 is
defined`` at decoration time (verified against the exact pinned
``schemathesis~=4.24`` install — see the Task 4 report for the reproduction
transcript). The third parameter is accepted-but-unused here; ``case.body``
mutation is the same shape the design doc describes.
"""

from __future__ import annotations

import os
from typing import Any

import schemathesis
from schemathesis import Case, HookContext

_MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


@schemathesis.hook
def before_call(context: HookContext, case: Case[Any], kwargs: dict[str, Any]) -> None:
    # `context` and `kwargs` are unused — the third parameter exists only to
    # match the registered `before_call` hook spec's arity (see module
    # docstring); ruff's ARG (unused-argument) rule isn't enabled repo-wide,
    # so no suppression comment is needed for either.
    if case.method in _MUTATING_METHODS and isinstance(case.body, dict):
        case.body["_csrf"] = os.environ["DAST_CSRF"]
