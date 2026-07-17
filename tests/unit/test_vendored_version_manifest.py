"""Keep package.json (the Dependabot-visible declaration) honest against the
bytes we actually ship.

``package.json`` exists so GitHub's dependency graph / Dependabot track the
self-hosted front-end libs for CVEs. But Dependabot's bump PR only edits
package.json — it cannot re-download the vendored ``.min.js`` / ``.min.css`` or
touch base.html. This gate makes that drift fail loudly: the declared version
must equal the version in the actual vendored filename, its base.html reference,
and its integrity pin (and, for the build tool, ``build_css.TAILWIND_VERSION``).

So a Dependabot bump turns the next local ``git push`` red until reconciled with
``vendor-sync``. NOTE this is a LOCAL pre-push guard only — there is no branch
protection / CI status check, so a manifest-only bump merged via the GitHub UI
would NOT be blocked here. It also means closing a Dependabot alert (which the
manifest bump does) is not the same as removing the vulnerable code: the app
serves the committed vendored bytes, so real remediation is ``vendor-sync`` +
redeploy, not the bump alone.
"""

from __future__ import annotations

import json
import re

import pytest

from idraa.tasks.build_css import TAILWIND_VERSION
from idraa.tasks.vendored_assets import (
    BASE_HTML,
    FILE_ASSETS,
    INTEGRITY,
    MANIFEST,
    TOOL_ASSET_DEP_GROUP,
    TOOL_ASSET_NPM_NAME,
    VENDOR_DIR,
    VendoredAsset,
)

_MANIFEST = json.loads(MANIFEST.read_text(encoding="utf-8"))
_BASE_HTML = BASE_HTML.read_text(encoding="utf-8")
_INTEGRITY = json.loads(INTEGRITY.read_text(encoding="utf-8"))


def _declared(npm_name: str, group: str) -> str | None:
    version: str | None = _MANIFEST.get(group, {}).get(npm_name)
    return version


@pytest.mark.parametrize("asset", FILE_ASSETS, ids=lambda a: a.npm_name)
def test_declared_version_matches_shipped_bytes(asset: VendoredAsset) -> None:
    version = _declared(asset.npm_name, asset.dep_group)
    assert version is not None, f"package.json[{asset.dep_group}] is missing {asset.npm_name!r}"

    # Exactly one vendored file for this asset, and it is the declared version.
    present = sorted(VENDOR_DIR.glob(f"{asset.prefix}*{asset.suffix}"))
    assert len(present) == 1, (
        f"expected exactly one {asset.prefix}*{asset.suffix} in {VENDOR_DIR}, found "
        f"{[p.name for p in present]} — run `python -m idraa.tasks vendor-sync`"
    )
    assert present[0].name == asset.filename(version), (
        f"{asset.npm_name}: package.json says {version} but the vendored file is "
        f"{present[0].name} — reconcile with `python -m idraa.tasks vendor-sync`"
    )

    # base.html references this asset exactly once, at the declared version —
    # a leftover stale-version ref must fail here, not just 404 at runtime.
    refs = re.findall(
        r"/static/vendor/" + re.escape(asset.prefix) + r"[^\"'?]*?" + re.escape(asset.suffix),
        _BASE_HTML,
    )
    assert refs == [asset.static_ref(version)], (
        f"base.html should reference {asset.static_ref(version)} exactly once and no other "
        f"version of {asset.npm_name}; found {refs} — run `python -m idraa.tasks vendor-sync`"
    )

    # The declared file is pinned in integrity.json.
    assert asset.filename(version) in _INTEGRITY, (
        f"{asset.filename(version)} has no integrity.json pin"
    )


def test_tailwind_declaration_matches_build_tool() -> None:
    declared = _declared(TOOL_ASSET_NPM_NAME, TOOL_ASSET_DEP_GROUP)
    assert declared == TAILWIND_VERSION, (
        f"package.json {TOOL_ASSET_NPM_NAME}=={declared} but build_css.TAILWIND_VERSION"
        f"=={TAILWIND_VERSION} — keep the declaration and the build binary in lockstep"
    )


def test_manifest_declares_no_untracked_packages() -> None:
    """Every package.json dependency is a known vendored asset — no stray decl."""
    declared = set(_MANIFEST.get("dependencies", {})) | set(_MANIFEST.get("devDependencies", {}))
    known = {a.npm_name for a in FILE_ASSETS} | {TOOL_ASSET_NPM_NAME}
    assert declared == known, (
        f"package.json declares {declared} but the registry knows {known}; "
        "add the asset to vendored_assets.py or remove it from package.json"
    )


def test_integrity_pins_have_no_orphans() -> None:
    """Every integrity.json pin corresponds to a present vendored file."""
    for filename in _INTEGRITY:
        assert (VENDOR_DIR / filename).exists(), (
            f"integrity.json pins {filename} but it is not in {VENDOR_DIR}"
        )
