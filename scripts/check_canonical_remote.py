#!/usr/bin/env python3
"""Pre-commit guard: block commits whose origin is not the canonical public repo.

All work is committed and tracked on the PUBLIC repo
``github.com/yasirhamza/idraa``. A private archive remote receives ONLY the
sanctioned sensitive security issues, and only with an explicit override.

Escape hatch for a sanctioned commit to one of the private sensitive security
issues: ``IDRAA_ALLOW_PRIVATE_COMMIT=1``.
"""

from __future__ import annotations

import os
import subprocess
import sys


def _origin_url() -> str:
    try:
        return subprocess.check_output(
            ["git", "remote", "get-url", "origin"], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except Exception:
        return ""


def main() -> int:
    url = _origin_url()
    # Private archive if origin names riskflow but not idraa (case-insensitive).
    low = url.lower()
    if "riskflow" in low and "idraa" not in low:
        if os.environ.get("IDRAA_ALLOW_PRIVATE_COMMIT") == "1":
            return 0
        sys.stderr.write(
            "\nBLOCKED: this checkout's origin is not the canonical public repo\n"
            "  (github.com/yasirhamza/idraa):\n"
            f"  {url}\n\n"
            "Only the sanctioned sensitive security issues may land elsewhere.\n"
            "If this is genuinely one of them, set IDRAA_ALLOW_PRIVATE_COMMIT=1.\n"
            "Otherwise: switch to a checkout whose origin is the canonical repo.\n\n"
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
