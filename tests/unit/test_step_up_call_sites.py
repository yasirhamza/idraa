import pathlib
import re

# 32 refactored route WIRINGS after Task 4. NOTE: Task 6 adds settings.py with
# its own require_step_up(ADMIN) POST gate -> bump this to 33 when Task 6 lands
# (flagged in Task 6 Step 3.6). Count only `Depends(require_step_up(` call sites
# and EXCLUDE deps.py (which holds the `def require_step_up(` definition AND a
# docstring example `Depends(require_step_up(...))` — neither is a wiring). This
# tripwire fails loudly if an auth decorator is silently dropped.
_EXPECTED_STEP_UP_WIRINGS = 32  # Task 6 -> 33

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
