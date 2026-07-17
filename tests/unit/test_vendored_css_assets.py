"""Vendored CSS must be present and byte-integrity-pinned so a future re-vendor
cannot silently swap it. Pins live in ``static/vendor/integrity.json`` (the
single place ``vendor-sync`` maintains); sibling of ``test_vendored_js_assets``.
"""

import base64
import hashlib
import json

from idraa.tasks.vendored_assets import INTEGRITY, VENDOR_DIR

_PINS = json.loads(INTEGRITY.read_text(encoding="utf-8"))
_CSS_PINS = {name: sha for name, sha in _PINS.items() if name.endswith(".css")}


def test_vendored_css_present_and_integrity_pinned() -> None:
    assert _CSS_PINS, "no .css pins found in integrity.json"
    for filename, expected_sha384 in _CSS_PINS.items():
        path = VENDOR_DIR / filename
        assert path.exists(), f"vendored CSS missing: {filename}"
        digest = base64.b64encode(hashlib.sha384(path.read_bytes()).digest()).decode()
        assert f"sha384-{digest}" == expected_sha384, (
            f"{filename} bytes drifted from the pinned build — if this is a deliberate "
            "re-vendor, run `python -m idraa.tasks vendor-sync`."
        )
