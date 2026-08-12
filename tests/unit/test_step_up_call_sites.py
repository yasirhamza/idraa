import pathlib
import re

# 43 refactored route WIRINGS. 37 after #107 (routes/settings.py's POST
# /settings/security is the 33rd; GET /runs/{run_id}/samples.csv.gz (#109)
# is the 34th; #107 gated the three pre-category exports — GET
# /scenarios/{id}/export the 35th, GET /runs/{id}/control-matrix.csv the
# 36th, and GET /library/entries/{id}/export the 37th, the last surfaced
# by the #107 review pass); the B2 hardening added 6 more — POST
# /organization (38th), POST /fx-rates (39th), and the four admin-only
# SME-directory mutations new/edit/archive/unarchive (40th–43rd). Count
# only `Depends(require_step_up(` call sites and EXCLUDE deps.py (which
# holds the `def require_step_up(` definition AND a docstring example
# `Depends(require_step_up(...))` — neither is a wiring). This tripwire
# fails loudly if an auth decorator is silently dropped. The COMPLETENESS
# companion (a new admin route missing the gate) lives in
# tests/unit/test_step_up_completeness.py.
_EXPECTED_STEP_UP_WIRINGS = 43

# Built dynamically (not a literal) so this guard itself never contains the
# retired token — a raw repo-wide grep for it (the feature's final acceptance
# criterion) must return zero, including inside this very file.
_RETIRED = "require_recent" + "_auth"


def test_step_up_wiring_count():
    files = [p for p in pathlib.Path("src/idraa/routes").glob("*.py") if p.name != "deps.py"]
    n = sum(len(re.findall(r"Depends\(require_step_up\(", p.read_text())) for p in files)
    assert n == _EXPECTED_STEP_UP_WIRINGS, (
        f"expected {_EXPECTED_STEP_UP_WIRINGS} wirings, found {n} — a decorator was added/dropped"
    )
    # The retired pre-category dependency must be fully gone (routes AND its
    # docstrings in deps.py/errors.py):
    assert not any(_RETIRED in p.read_text() for p in pathlib.Path("src/idraa").rglob("*.py"))
