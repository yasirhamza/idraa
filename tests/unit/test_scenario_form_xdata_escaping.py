"""B3 regression: the scenario-form Alpine ``x-data`` distribution selectors
must JSON-encode the reflected value, never interpolate it raw into the
attribute.

The raw shape ``x-data="{ dist: '{{ form.pl_dist ... }}' }"`` let a reflected
POST value break out of the single-quoted JS string and execute — self-XSS,
proven in headless Chromium against the app's own vendored Alpine during the
STRIDE audit (autoescape is no defense: the browser HTML-decodes the attribute
before Alpine evaluates it as a JS expression). The fix mirrors the money-field
idiom in the SAME file: a single-quoted attribute + ``| tojson`` (which escapes
``'``/``<``/``>``/``&``). This guard fails loudly if any of the loss selectors
(pl/sl) regresses to the raw shape.
"""

from __future__ import annotations

import pathlib
import re

# Scope note: this guard covers the `dist:` x-data selectors specifically. The
# `@input` (Alpine x-on) handlers in the same file (dispatch_fieldset / field_key)
# use the same raw-single-quote idiom but interpolate server-FIXED macro literals
# ("pl"/"sl", a hardcoded loop tuple), never request-derived data — so they are
# not a live XSS surface and are intentionally out of this guard's scope. If
# either ever becomes request-derived, widen this guard.
_FORM = pathlib.Path("src/idraa/templates/scenarios/form.html")


def test_no_raw_dist_xdata_interpolation():
    src = _FORM.read_text()
    # The exact vulnerable shape: a double-quoted x-data attribute whose JS
    # value is a single-quoted string holding a raw {{ ... }} substitution.
    vulnerable = re.findall(r"""x-data="\{ dist: '\{\{""", src)
    assert not vulnerable, (
        f"{len(vulnerable)} scenario-form dist x-data selector(s) interpolate a "
        "reflected value raw into the attribute — use a single-quoted attribute "
        "+ | tojson, exactly like the money fields in this file."
    )


def test_all_dist_selectors_use_tojson():
    src = _FORM.read_text()
    # pl/sl selectors (TEF is PERT-only, #318: no selector), each a
    # single-quoted x-data routed through tojson.
    safe = re.findall(r"x-data='\{ dist:[^']*\|\s*tojson[^']*\}'", src)
    assert len(safe) == 2, (
        f"expected 2 tojson-guarded dist x-data selectors (pl/sl), found {len(safe)}"
    )


def test_tef_mixture_warning_says_pert_not_lognormal() -> None:
    """#318: saving a pooled TEF from this form stores a PERT, so the TEF call of
    the pooled-estimate warning must take the PERT wording (the loss calls keep
    the lognormal wording)."""
    src = _FORM.read_text()
    tef_calls = re.findall(r"mixture_replace_warning\(form\.tef_from_mixture,[^)]*\)", src)
    assert len(tef_calls) == 1
    assert "as_pert=true" in tef_calls[0]
    loss_calls = re.findall(r"mixture_replace_warning\(form\.(?:pl|sl)_from_mixture,[^)]*\)", src)
    assert loss_calls, "the loss nodes still warn"
    assert all("as_pert" not in call for call in loss_calls)
    assert "with a PERT whose low and high are the shown values" in src
