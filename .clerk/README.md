# .clerk

Project-owned inputs of superpowers-clerk (not yet published), the review clerk Idraa adopted on 2026-10-03. The clerk reads these and `clerk.toml` from the **base commit** of a review, never from the worktree under review; the pre-push pre-flight hands it the working-tree copy of the tracked citations manifest.

- `manifests/citations.json` — generated; one entry per cited range of every backticked `file:line` citation in `docs/security/threat-model.md` and `docs/reference/fair-departures-register.md` (outside fenced blocks), with the anchor line's text. After editing a citation in either document: `uv run python scripts/clerk_manifests.py --write`. If code moved under an unchanged citation, fix the citation in the document first; `--write` refuses to re-anchor it unless you pass `--accept-drift <file>:<line>`. A citation starts on its anchor line (the first line of the range with at least 12 characters). `--check` is what the tests run; `--verbose` lists anchors whose text repeats in their file.
- `manifests/surfaces.json` — hand-curated fragments that must read the same, with the same meaning, on two or more surfaces. Add one when a PR changes a number or label shown in two places.
- `manifests/lanes.json` — the four reviewer lanes (`methodology`, `spec-compliance`, `architect`, `security`), their path globs and `lane_semantic` subjects.

Idraa takes no Python dependency on the clerk; see `tests/arch/test_clerk_isolation.py`.

## Merging branches that both re-cite

Never hand-merge `manifests/citations.json`: it is generated, and a merge of two generated files proves nothing. When two branches both touch the governed documents or the cited code:

1. Resolve the governed documents (`docs/security/threat-model.md`, `docs/reference/fair-departures-register.md`) first. Then run `uv run python scripts/clerk_manifests.py --check` even when git merged the documents cleanly: identical line-number edits on both sides collapse into one and leave the merged code off by one.
2. Re-cite each reported site against the merged code, run `uv run python scripts/clerk_manifests.py --write`, then `--check` again (it must print fresh).
3. `main`'s branch protection is not strict, so two green PRs can merge into a red freshness test on `main`. Fix it forward: re-cite and `--write`, in the next PR.

Measured cost: about 40–45% of recent `main` commits would have shifted at least one cited anchor (hot files: `app.py`, `routes/scenarios.py`, `config.py`, `run_executor.py`, `routes/runs.py`, `services/auth.py`, `routes/auth.py`).
