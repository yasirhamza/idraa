"""Start the checker without trusting the working directory (run by scripts/curation-check).

The wrapper runs `python -I _launch.py ROOT ...`. Isolated mode puts neither the working directory nor this
file's folder on sys.path, so a stray module at the repo root (json.py, httpx2.py, ...) cannot shadow the
standard library or the SDK in the process that holds the key. ROOT is appended after site-packages, and
the `scripts` package is refused unless it is the one under ROOT."""

import runpy
import sys
from pathlib import Path

root = Path(sys.argv.pop(1)).resolve()
sys.path.append(str(root))
import scripts  # noqa: E402

if Path(scripts.__file__).resolve().parent != root / "scripts":
    sys.exit(
        f"curation-check: refusing the 'scripts' package at {scripts.__file__}; expected {root / 'scripts'}"
    )
sys.argv[0] = "curation-check"
runpy.run_module("scripts.curation_check", run_name="__main__", alter_sys=True)
