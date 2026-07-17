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


def build(output: Path = OUTPUT) -> int:
    binary = ensure_binary()
    cmd = [str(binary), "-c", str(CONFIG), "-i", str(ENTRY), "-o", str(output), "--minify"]
    print(f"> {' '.join(cmd)}", flush=True)
    return subprocess.run(cmd, check=False, cwd=REPO_ROOT).returncode  # noqa: S603


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
