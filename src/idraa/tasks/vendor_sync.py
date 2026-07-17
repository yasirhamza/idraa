"""Reconcile the vendored front-end bytes to the versions declared in package.json.

``package.json`` is the Dependabot-visible *declaration*; the actual bytes we
ship live under ``static/vendor/``. When a version there changes (a Dependabot
security bump, or a manual upgrade), this task makes the bytes match in one step:

    python -m idraa.tasks vendor-sync

For each file-backed asset it downloads the exact bytes for the declared version
from the official CDN over TLS, rewrites the versioned file (dropping the old
one), updates the ``base.html`` reference, and re-pins ``integrity.json``. The
sha384 pin is the tamper/drift guard for future re-vendors (byte-pin tests read
it); the trust root for a *new* version is the HTTPS fetch from the official
origin, exactly as the original vendoring — the resulting diff is the human
review checkpoint.

Idempotent: run against unchanged versions and it re-verifies the current bytes
and writes nothing new. After it runs, ``test_vendored_version_manifest`` and the
byte-pin tests are green by construction.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import sys
import urllib.request

from idraa.tasks.vendored_assets import (
    BASE_HTML,
    FILE_ASSETS,
    INTEGRITY,
    MANIFEST,
    VendoredAsset,
)


def _sha384(data: bytes) -> str:
    return "sha384-" + base64.b64encode(hashlib.sha384(data).digest()).decode()


def _declared_versions() -> dict[str, str]:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    versions: dict[str, str] = {}
    for group in ("dependencies", "devDependencies"):
        versions.update(manifest.get(group, {}))
    return versions


def _download(url: str) -> bytes:
    print(f"downloading {url}", flush=True)
    try:
        # S310: URL is built from a hardcoded https CDN template + a version read
        # from our own committed package.json — no user input reaches it.
        with urllib.request.urlopen(url, timeout=60) as resp:  # noqa: S310
            data: bytes = resp.read()
    except OSError as exc:
        raise SystemExit(f"failed to download {url} ({exc})") from exc
    if not data:
        raise SystemExit(f"downloaded empty body from {url}")
    return data


def _replace_ref(html: str, asset: VendoredAsset, new_version: str) -> str:
    """Point base.html's reference for this asset at the new version.

    Matches the asset's own ``/static/vendor/<prefix>…<suffix>`` reference (any
    version) and rewrites it, so an unchanged version is a no-op.
    """
    pattern = re.compile(
        r"/static/vendor/" + re.escape(asset.prefix) + r"[^\"'?]*?" + re.escape(asset.suffix)
    )
    new_ref = asset.static_ref(new_version)
    matches = set(pattern.findall(html))
    if not matches:
        raise SystemExit(
            f"no base.html reference found for {asset.npm_name} ({asset.prefix}*{asset.suffix})"
        )
    return pattern.sub(new_ref, html)


def sync() -> int:
    versions = _declared_versions()
    html = BASE_HTML.read_text(encoding="utf-8")
    integrity: dict[str, str] = {}
    changed = False

    for asset in FILE_ASSETS:
        version = versions.get(asset.npm_name)
        if version is None:
            raise SystemExit(f"package.json is missing a version for {asset.npm_name!r}")
        target = asset.path(version)
        data = _download(asset.url(version))
        digest = _sha384(data)

        # Drop any stale-version file(s) for this asset.
        for old in sorted(target.parent.glob(f"{asset.prefix}*{asset.suffix}")):
            if old.name != target.name:
                print(f"removing stale {old.name}", flush=True)
                old.unlink()
                changed = True

        if not target.exists() or target.read_bytes() != data:
            target.write_bytes(data)
            print(f"wrote {target.name} ({len(data)} bytes, {digest})", flush=True)
            changed = True
        else:
            print(f"{target.name} already current ({digest})", flush=True)

        integrity[target.name] = digest
        html = _replace_ref(html, asset, version)

    new_html = html
    if new_html != BASE_HTML.read_text(encoding="utf-8"):
        BASE_HTML.write_text(new_html, encoding="utf-8")
        print("updated base.html references", flush=True)
        changed = True

    integrity_text = json.dumps(integrity, indent=2) + "\n"
    if not INTEGRITY.exists() or INTEGRITY.read_text(encoding="utf-8") != integrity_text:
        INTEGRITY.write_text(integrity_text, encoding="utf-8")
        print(f"updated {INTEGRITY.name}", flush=True)
        changed = True

    if changed:
        print(
            "\nvendor-sync: bytes reconciled to package.json. Review the diff and commit.",
            flush=True,
        )
    else:
        print("\nvendor-sync: already in sync — nothing to do.", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    return sync()


if __name__ == "__main__":
    sys.exit(main())
