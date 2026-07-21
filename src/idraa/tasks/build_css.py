"""Build the purged static Tailwind stylesheet with the standalone CLI.

No Node/npm: a single self-contained Tailwind binary. Output is committed;
the pre-push gate rebuilds and byte-compares to block stale CSS.
"""

from __future__ import annotations

import hashlib
import platform
import stat
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
BIN_CACHE = REPO_ROOT / ".tailwind-bin"
CONFIG = REPO_ROOT / "tailwind.config.js"
ENTRY = REPO_ROOT / "src" / "idraa" / "static" / "css" / "_tailwind_entry.css"
OUTPUT = REPO_ROOT / "src" / "idraa" / "static" / "css" / "tailwind.css"

TAILWIND_VERSION = "3.4.17"
_RELEASE_BASE = f"https://github.com/tailwindlabs/tailwindcss/releases/download/v{TAILWIND_VERSION}"

# (system, machine) -> (asset filename, sha256). SHA-256 values are HARD-CODED here from the
# v3.4.17 release's sha256sums.txt, cross-checked at authoring time via independent
# re-download + independent re-hash (`shasum -a 256`). No third-party attestation exists for
# v3.4.17 (the GitHub release `digest` is null; Homebrew/npm ship v4.x, a different artifact),
# so re-fetch+re-hash is the strongest achievable cross-check. Hard-coding breaks build-time
# circularity; the pin is reviewed in the PR diff. DO NOT invent them.
_ASSETS: dict[tuple[str, str], tuple[str, str]] = {
    ("Darwin", "arm64"): (
        "tailwindcss-macos-arm64",
        "a1d0c7985759accca0bf12e51ac1dcbf0f6cf2fffb62e6e0f62d091c477a10a3",
    ),
    ("Darwin", "x86_64"): (
        "tailwindcss-macos-x64",
        "6cbdad74be776c087ffa5e9a057512c54898f9fe8828d3362212dfe32fc933a3",
    ),
    ("Linux", "x86_64"): (
        "tailwindcss-linux-x64",
        "7d24f7fa191d2193b78cd5f5a42a6093e14409521908529f42d80b11fde1f1d4",
    ),
    ("Linux", "aarch64"): (
        "tailwindcss-linux-arm64",
        "69b1378b8133192d7d2feb12a116fa12d035594f58db3eff215879e4ad8cf39b",
    ),
    ("Windows", "AMD64"): (
        "tailwindcss-windows-x64.exe",
        "67f1c5e3f5a03406a7bf5badf5ada09b79f3ae78ec43450c15f7e983068da346",
    ),
}


def _asset() -> tuple[str, str]:
    key = (platform.system(), platform.machine())
    if key not in _ASSETS:
        raise SystemExit(
            f"No pinned Tailwind standalone binary for {key}. "
            f"Add it from {_RELEASE_BASE}/sha256sums.txt"
        )
    return _ASSETS[key]


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def ensure_binary() -> Path:
    name, expected = _asset()
    dest = BIN_CACHE / f"{name}-{TAILWIND_VERSION}"
    if dest.exists():
        if _sha256(dest) == expected:
            return dest
        # A cached binary that fails verification is a tamper/corruption signal — log it
        # (do not vanish it silently) before recovering via re-download.
        print(f"WARNING: cached {dest.name} failed sha256 verification; re-downloading", flush=True)
    BIN_CACHE.mkdir(parents=True, exist_ok=True)
    url = f"{_RELEASE_BASE}/{name}"
    print(f"downloading {url}", flush=True)
    tmp = dest.parent / (dest.name + ".download")  # not with_suffix — that mangles -3.4.17
    try:
        # streamed with a timeout so a stalled fetch cannot hang the pre-push gate
        with urllib.request.urlopen(url, timeout=60) as resp, tmp.open("wb") as fh:  # noqa: S310 — pinned https GitHub release asset
            while chunk := resp.read(1 << 16):
                fh.write(chunk)
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        raise SystemExit(
            f"failed to download the Tailwind binary ({exc}). Run "
            "`python -m idraa.tasks build-css` once while online to prewarm the cache, "
            "or set IDRAA_GATE_SKIP_CSS=1 to skip the CSS gate for this push."
        ) from exc
    actual = _sha256(tmp)
    if actual != expected:
        tmp.unlink(missing_ok=True)
        raise SystemExit(f"sha256 mismatch for {name}: {actual} != {expected}")
    tmp.chmod(tmp.stat().st_mode | stat.S_IXUSR | stat.S_IRUSR | stat.S_IWUSR)
    tmp.replace(dest)
    return dest


VENDOR_DIR = REPO_ROOT / "src" / "idraa" / "static" / "vendor"

# UAT 2026-07-21 (wizard catastrophic toggle): @tailwindcss/forms ships GLOBAL
# `[type=checkbox],[type=radio]` resets (appearance:none, 1rem square). They tie
# DaisyUI's `.toggle`/`.checkbox`/`.radio` on specificity, and tailwind.css
# loads AFTER the vendored DaisyUI sheet — so the reset won every tie and
# flattened all DaisyUI form controls into unstyled 16px squares. Fix: extract
# the DaisyUI control rules from the vendored sheet and append them to the END
# of the built output, where same-specificity+later restores them. Extraction
# (not a hand copy) keeps the block in sync across Dependabot DaisyUI bumps.
_CONTROL_SELECTOR_TOKENS = (".toggle", ".checkbox", ".radio")
_RESTORE_MARKER = "/*! daisyui-controls-restore (build_css) */"


def _iter_css_rules(css: str) -> list[tuple[str, str]]:
    """Yield (selector_or_atrule_header, full_rule_text) for top-level blocks,
    recursing one level into grouping at-rules (@media/@supports). Brace-
    balance parser — sufficient for minified vendor CSS (no comments after
    the jsDelivr header is stripped)."""
    rules: list[tuple[str, str]] = []
    i, n = 0, len(css)
    while i < n:
        brace = css.find("{", i)
        if brace == -1:
            break
        header = css[i:brace].strip()
        depth, j = 1, brace + 1
        while j < n and depth:
            if css[j] == "{":
                depth += 1
            elif css[j] == "}":
                depth -= 1
            j += 1
        body = css[brace + 1 : j - 1]
        if header.startswith("@") and "{" in body:
            for inner_header, inner_rule in _iter_css_rules(body):
                rules.append((inner_header, f"{header}{{{inner_rule}}}"))
        else:
            rules.append((header, css[i:j]))
        i = j
    return rules


def _extract_control_rules() -> str:
    """Pull every DaisyUI rule styling .toggle/.checkbox/.radio from the
    vendored sheet, in vendor order."""
    import re

    sheets = sorted(VENDOR_DIR.glob("daisyui-*.min.css"))
    if len(sheets) != 1:
        # Exactly one vendored sheet, ever: lexical sort is NOT semver
        # (4.9 > 4.12), so choosing among several could desync the restore
        # block from the sheet base.html actually loads (W-3).
        raise SystemExit(
            f"build-css: expected exactly one vendored daisyui-*.min.css, found {len(sheets)}"
        )
    css = sheets[0].read_text(encoding="utf-8")
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    keep: list[str] = []
    for header, rule in _iter_css_rules(css):
        if header.startswith("@") and not rule.startswith("@"):
            continue
        selector = header
        if any(tok in selector for tok in _CONTROL_SELECTOR_TOKENS):
            keep.append(rule)
    if not keep:
        raise SystemExit("build-css: daisyui control extraction found 0 rules")
    return "".join(keep)


def build(output: Path = OUTPUT) -> int:
    binary = ensure_binary()
    cmd = [str(binary), "-c", str(CONFIG), "-i", str(ENTRY), "-o", str(output), "--minify"]
    print(f"> {' '.join(cmd)}", flush=True)
    rc = subprocess.run(cmd, check=False, cwd=REPO_ROOT).returncode  # noqa: S603
    if rc == 0:
        # NOTE (W-5): appending at EOF also places these rules AFTER the
        # utilities layer, so `class="toggle w-10"`-style sizing utilities on
        # controls would lose to the restored base rules. No template does
        # that today (only mt-* margins co-occur); prefer DaisyUI's own size
        # variants (toggle-sm etc.) on controls.
        restore = _extract_control_rules()
        with output.open("a", encoding="utf-8") as f:
            f.write(f"\n{_RESTORE_MARKER}{restore}")
        print(f"build-css: appended daisyui-controls-restore ({len(restore)} bytes)", flush=True)
    return rc


def _normalize(data: bytes) -> bytes:
    # newline-normalize so a CRLF-emitting platform binary cannot spuriously fail the gate
    return data.replace(b"\r\n", b"\n")


def check() -> int:
    """Build to a temp file and compare (newline-normalized) to the committed OUTPUT.
    NEVER mutates the working tree. Returns 0 if in sync, 1 if stale (or build failed)."""
    with tempfile.NamedTemporaryFile(suffix=".css", delete=False) as tf:
        tmp = Path(tf.name)
    try:
        if build(output=tmp) != 0:
            print("build-css --check: build FAILED", flush=True)
            return 1
        fresh = _normalize(tmp.read_bytes())
    finally:
        tmp.unlink(missing_ok=True)
    committed = _normalize(OUTPUT.read_bytes()) if OUTPUT.exists() else b""
    if fresh != committed:
        print(
            "build-css --check: tailwind.css is STALE. "
            "Run `python -m idraa.tasks build-css` and commit the result.",
            flush=True,
        )
        return 1
    print("build-css --check: tailwind.css is up to date", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "--check":
        return check()
    return build()


if __name__ == "__main__":
    sys.exit(main())
