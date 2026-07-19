# syntax=docker/dockerfile:1.7

# --- Stage 1: build dependencies into a venv -----------------------------------
# Digest-pinned (supply-chain: a mutable tag is the container analog of an
# unpinned action). Dependabot's docker ecosystem keeps this current.
FROM python:3.11-slim@sha256:db3ff2e1800a8581e2c48a27c3995339d47bdf046da21c7627accd3d51053a93 AS builder

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Install uv
RUN pip install --no-cache-dir uv==0.11.11

WORKDIR /app

# Copy dependency manifests first for layer caching
COPY pyproject.toml uv.lock README.md ./

# Copy source trees required by the editable local installs (idraa itself
# and the vendored fair_cam). `uv sync` builds both, so both packages must be
# present before sync runs.
COPY fair_cam ./fair_cam
COPY src ./src
COPY alembic.ini ./alembic.ini
COPY alembic ./alembic

# Install only runtime deps (no --extra dev)
RUN uv sync --frozen --no-dev

# data/ is runtime seed content (read by alembic at upgrade time), not a
# build-time uv-sync input — copy after sync so dep and seed caches don't
# cross-invalidate each other.
COPY data ./data

# FAIR-CAM controls library CSV read at runtime by
# services/controls_importer.py for the "Load FAIR-CAM library" one-click
# import on /controls/import. Lives under docs/ historically; explicit
# COPY needed because the broader `COPY docs` would balloon the image.
# .dockerignore re-includes this specific file inside the otherwise-
# excluded docs/ tree.
COPY docs/reference/fair-cam-controls-library.csv ./docs/reference/fair-cam-controls-library.csv

# #491: rebuild the purged Tailwind sheet from THIS image's templates, so a
# merge-order race between branches (each passing its own per-branch pre-push
# gate, nobody gating the merged tree — GHA is billing-disabled) can never
# ship a stale committed tailwind.css to prod. The committed file + the
# pre-push staleness gate remain the dev workflow; this overwrite is the
# deploy-boundary backstop. idraa.tasks.build_css downloads the standalone
# binary sha256-pinned (fails the image build on mismatch or on a Tailwind
# build error); the ~13MB download is cached across builds via a BuildKit
# cache mount at build_css.BIN_CACHE. Uses the synced venv python directly —
# `uv run` could trigger a re-resolve, and idraa.tasks imports no settings
# (no SESSION_SECRET needed at build time).
COPY tailwind.config.js ./tailwind.config.js
RUN --mount=type=cache,target=/app/.tailwind-bin \
    /app/.venv/bin/python -m idraa.tasks build-css

# --- Stage 2: runtime ---------------------------------------------------------
# Digest-pinned (supply-chain: a mutable tag is the container analog of an
# unpinned action). Dependabot's docker ecosystem keeps this current.
FROM python:3.11-slim@sha256:db3ff2e1800a8581e2c48a27c3995339d47bdf046da21c7627accd3d51053a93 AS runtime

# PYTHONHASHSEED=0 (issue #33): set/frozenset iteration order in the scalar
# compose path (fair_cam group_composition / composition) is hash-seed
# dependent, and float accumulation is not associative — an unpinned seed
# makes the reproducibility-pinned weight_robustness blob differ ~1e-13
# across process launches. Pinning at the interpreter level (must be set
# BEFORE Python starts; os.environ at runtime is too late) makes re-runs
# byte-identical. No displayed value changes.
ENV PYTHONUNBUFFERED=1 \
    PYTHONHASHSEED=0 \
    PATH="/app/.venv/bin:$PATH"

RUN useradd --create-home --shell /bin/bash idraa

WORKDIR /app

COPY --from=builder --chown=idraa:idraa /app /app
COPY --chown=idraa:idraa docker-entrypoint.sh /app/docker-entrypoint.sh

USER idraa

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import httpx; r = httpx.get('http://localhost:8000/healthz'); exit(0 if r.status_code == 200 else 1)"

CMD ["/app/docker-entrypoint.sh"]
