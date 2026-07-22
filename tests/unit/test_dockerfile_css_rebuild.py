"""#491 structural guard: the Docker builder stage rebuilds tailwind.css from
THIS image's templates, so a merge-order race between branches can never ship
a stale committed sheet to prod (the per-branch pre-push gate and CI's per-PR
merge-ref check each only see one branch merged with main-at-that-time, so a
merge-order race between two branches can still slip a stale sheet through).

The committed tailwind.css + the pre-push staleness gate remain the DEV
workflow; the Dockerfile RUN is the deploy-boundary backstop. These are
text-level assertions (no docker daemon in the local gate) — the real build is
exercised by every Fly remote deploy, which fails loudly if the RUN breaks.
"""

from __future__ import annotations

import re
from pathlib import Path

_DOCKERFILE = (Path(__file__).parent.parent.parent / "Dockerfile").read_text(encoding="utf-8")


def test_builder_stage_rebuilds_css_after_copying_src() -> None:
    # The config must be in the build context for the rebuild.
    copy_config = _DOCKERFILE.find("COPY tailwind.config.js")
    assert copy_config != -1, "Dockerfile must COPY tailwind.config.js for the CSS rebuild"

    # Line-continued RUN: `RUN --mount=... \` + `    <venv python> -m idraa.tasks build-css`.
    run_build = re.search(
        r"RUN --mount=type=cache[^\n]*\\\n\s*/app/\.venv/bin/python -m idraa\.tasks build-css",
        _DOCKERFILE,
    )
    assert run_build is not None, (
        "Dockerfile must RUN `... -m idraa.tasks build-css` in the builder "
        "stage (#491 deploy-boundary CSS rebuild)"
    )

    # Ordering: the rebuild must run AFTER the source tree (templates + entry
    # css) is copied, else it would purge against nothing.
    copy_src = _DOCKERFILE.find("COPY src ./src")
    assert copy_src != -1
    assert run_build.start() > copy_src, "build-css RUN must come after COPY src"

    # And within the BUILDER stage (before the runtime FROM), so the output
    # rides the existing `COPY --from=builder /app /app`. Match on the stage
    # name only (not the full FROM line) so this survives base-image digest
    # bumps (#555 digest-pin; Dependabot keeps the sha256 current).
    runtime_from = _DOCKERFILE.find("AS runtime")
    assert runtime_from != -1
    assert run_build.start() < runtime_from, "build-css RUN must be in the builder stage"


def test_css_rebuild_uses_cache_mount_for_binary() -> None:
    """The sha256-pinned Tailwind binary download (~13MB) is cached across
    builds via a BuildKit cache mount targeting build_css.BIN_CACHE (/app/.tailwind-bin)."""
    assert "--mount=type=cache,target=/app/.tailwind-bin" in _DOCKERFILE
