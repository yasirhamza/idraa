#!/usr/bin/env python3
"""Pre-commit guard: block commits to the PRIVATE riskflow archive.

All work is committed and tracked on the PUBLIC repo ``yasirhamza/idraa``
(``~/projects/Idraa``). The private ``yasirhamza/riskflow`` archive receives
ONLY the sanctioned sensitive security issues, and only with an explicit
override.

Rationale: on 2026-07-22 an entire feature (strong-auth MFA + passkeys, 13
commits) was mistakenly built in a checkout whose ``origin`` was the private
riskflow remote — its directory was named ``RiskFlow`` and its CLAUDE.md said
Idraa, so the only distinguishing signal was the remote, which went unchecked.
Nothing was pushed, so it was recoverable, but it wasted significant work.
This guard makes that mistake un-committable by default.

See CLAUDE.md "Canonical repository". Escape hatch for a sanctioned commit to
one of the private sensitive security issues: ``IDRAA_ALLOW_PRIVATE_COMMIT=1``.
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
            "\nBLOCKED: this checkout's origin is the PRIVATE riskflow archive:\n"
            f"  {url}\n\n"
            "All work is committed + tracked on the PUBLIC repo\n"
            "  https://github.com/yasirhamza/idraa   (~/projects/Idraa)\n\n"
            "Only the sanctioned sensitive security issues may land in riskflow.\n"
            "If this is genuinely one of them, set IDRAA_ALLOW_PRIVATE_COMMIT=1.\n"
            "Otherwise: cd ~/projects/Idraa and commit there.\n\n"
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
