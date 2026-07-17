"""Entry point for ``python -m idraa.tasks``."""

from __future__ import annotations

import sys

from idraa.tasks.runner import main

if __name__ == "__main__":
    sys.exit(main())
