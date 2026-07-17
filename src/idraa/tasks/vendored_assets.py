"""Single source of truth for the self-hosted, byte-vendored front-end assets.

Shared by:
- ``tests/unit/test_vendored_version_manifest.py`` — the sync gate that keeps
  ``package.json`` (the Dependabot-visible declaration) honest against the
  actual bytes we ship.
- ``idraa.tasks.vendor_sync`` — the one-command re-vendor task that reconciles
  the shipped bytes to the versions declared in ``package.json`` after a
  Dependabot bump / manual upgrade.

Each ``VendoredAsset`` with a file maps an npm package (the name the dependency
graph tracks for CVEs) to (a) the versioned filename we commit, (b) the official
CDN URL its exact bytes come from, and (c) the ``base.html`` reference. The URL
templates are self-tested: ``vendor_sync`` at the *current* declared versions
must reproduce the committed bytes (their pinned sha384 in ``integrity.json``).

Tailwind is declared too (``tailwindcss`` devDependency) but has no vendored
file — it is the build tool whose version lives in
``build_css.TAILWIND_VERSION``; the sync gate checks that instead.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
MANIFEST = REPO_ROOT / "package.json"
VENDOR_DIR = REPO_ROOT / "src" / "idraa" / "static" / "vendor"
INTEGRITY = VENDOR_DIR / "integrity.json"
BASE_HTML = REPO_ROOT / "src" / "idraa" / "templates" / "base.html"


@dataclass(frozen=True)
class VendoredAsset:
    """A third-party front-end lib we self-host as committed bytes."""

    npm_name: str
    """package.json key and the name the GitHub dependency graph tracks."""
    dep_group: str
    """"dependencies" or "devDependencies" — where it sits in package.json."""
    prefix: str
    """Filename stem before the version, e.g. "htmx-" -> htmx-1.9.12.min.js."""
    suffix: str
    """Filename tail after the version, e.g. ".min.js" / ".min.css"."""
    url_template: str
    """Official CDN URL with a ``{version}`` placeholder — the exact-bytes source."""

    def filename(self, version: str) -> str:
        return f"{self.prefix}{version}{self.suffix}"

    def path(self, version: str) -> Path:
        return VENDOR_DIR / self.filename(version)

    def static_ref(self, version: str) -> str:
        """The ``/static/vendor/...`` path as referenced in base.html."""
        return f"/static/vendor/{self.filename(version)}"

    def url(self, version: str) -> str:
        return self.url_template.format(version=version)


# Assets we vendor as actual files under static/vendor/. Order is display-only.
FILE_ASSETS: tuple[VendoredAsset, ...] = (
    VendoredAsset(
        npm_name="htmx.org",
        dep_group="dependencies",
        prefix="htmx-",
        suffix=".min.js",
        url_template="https://unpkg.com/htmx.org@{version}/dist/htmx.min.js",
    ),
    VendoredAsset(
        npm_name="alpinejs",
        dep_group="dependencies",
        prefix="alpinejs-",
        suffix=".min.js",
        url_template="https://unpkg.com/alpinejs@{version}/dist/cdn.min.js",
    ),
    VendoredAsset(
        npm_name="daisyui",
        dep_group="dependencies",
        prefix="daisyui-",
        suffix=".min.css",
        url_template="https://cdn.jsdelivr.net/npm/daisyui@{version}/dist/full.min.css",
    ),
)

# The build-tool package that has no vendored file; its version is owned by
# build_css.TAILWIND_VERSION. Kept as a bare (npm_name, dep_group) so the sync
# gate can assert package.json agrees with the binary the build actually fetches.
TOOL_ASSET_NPM_NAME = "tailwindcss"
TOOL_ASSET_DEP_GROUP = "devDependencies"
