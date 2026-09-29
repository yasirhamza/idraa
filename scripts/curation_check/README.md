# Curation checker

Dev-only tool that gives every library curation campaign ranked review queues:

| Check | Question | A flag means |
|---|---|---|
| Scenario label audit | Does each scenario's description support its threat event, asset class and threat actor labels? | The curated label scores low (the queue ranks by 1 − the curated label's score; the judge's top choice may still agree) |
| Control function audit | Does each control's description support its FAIR-CAM function labels? | Two sub-queues: a function looks *missing* (8 rows) or a label looks *wrong* (7 rows) |
| Scenario overlap | Which other library scenario describes the same risk (same threat, asset, method and effect)? | Merge the pair, or sharpen the descriptions |
| Coverage gaps | Which library scenario covers each threat in an intake list? | Nothing covers it |

**Flags are candidates for review, not verdicts.**
- Scores only order the queues; they are not calibrated probabilities.
- The tool never edits seed data and never blocks CI.
- Deliberately dropped control claims (seed `_meta.claim_drops`) are suppressed, not re-flagged every campaign.

## Run

```bash
scripts/curation-check all --campaign epic-f                          # start of campaign
scripts/curation-check all --campaign epic-f --intake ~/intake.jsonl  # with a gap intake list (keep it outside the repo)
scripts/curation-check all --campaign epic-f --compare-to curation-runs/<start-folder>   # end of campaign (another day)
scripts/curation-check tally                                          # hit rates across committed reports
```

**Setup:**
- macOS, one time, in your own terminal: `security add-generic-password -s idraa-typesafe-key -a typesafe -U -w`.
- An existing item named `jev-eval-typesafe-key` (from the Jev trial) is also accepted: run with `CURATION_KEYCHAIN_SERVICE=jev-eval-typesafe-key`.
- Elsewhere (Linux), set `TYPESAFE_API_KEY` for that one command only, typed in your own terminal with a leading
  space (` TYPESAFE_API_KEY=... scripts/curation-check ...`; `HISTCONTROL=ignorespace` then keeps it out of shell
  history, for that one command only); never export it in a shell an agent inherits.
- Install the SDK with `uv sync --extra dev --extra curation`. A later plain `uv sync --extra dev` removes the
  extra; the wrapper then stops with exit 3 until you sync with `--extra curation` again.

The wrapper runs in a clean environment and passes the key on stdin. Proxy, base-URL and Python path variables in your shell have no effect. Run it directly as `scripts/curation-check ...`; `bash scripts/curation-check` is refused because it would skip the wrapper's protected mode.

**Output:** each run writes `curation-runs/<date>-<campaign>/`, and refuses to overwrite an existing report unless you pass `--force`:
- `report.md`: the queues, with a Disposition column.
- `run.json`: the commit, seed and criteria hashes, pinned model, token counts, errored items and queue keys.
- `responses.jsonl`: the raw answers. Re-score offline with `python -m scripts.curation_check all --judge replay --replay <path> --out <new folder>`.

## Dispositions

- In `report.md`, set every row's **Disposition** to `accepted`, `rejected` or `deferred`, with a one-line **Reason**.
- Accepted flags become seed changes through the normal campaign process: research, adversarial verification, methodology gate.
- When every row is dispositioned, commit exactly `report.md`, `run.json` and `responses.jsonl` under `docs/curation/<date>-<campaign>/` with the campaign PR. `scripts/lint_tracked_paths.py` refuses anything else there (a pre-commit hook; CI does not run it).
- An intake item's id, source and a title of at most 80 characters are published in the report.
- **Never commit an intake file.** It may contain licensed text.

## Tuning rules (applied by `tally`)

- `tally` pools every committed report per model, check and queue length (`--top`). Each subject counts once across all campaigns: the latest decision wins, and a later blank never erases an earlier decision. A rejected flag that re-queues every campaign is therefore not counted again each time.
- No rule fires until a check has at least 10 decided rows (accepted + rejected). Deferred rows are shown, not counted.
- **Raise `--top`** when the 90% Wilson interval for the lowest-ranked third's hit rate lies wholly above 50%. The lowest third is `max(1, floor(n/3))` rows of each queue.
- **Shrink `--top` or retire the check** when the 90% interval for its overall hit rate lies wholly below 20%.
- Hit rates are never pooled across models (`run.json` → `model`).

## Limits

- **Wording:** the option wording lives in `data/curation/criteria.json` and follows `docs/reference/scenario-labelling-conventions.md`. Changes need methodology review.
- **Pinning:** the judge model and endpoint are pinned in `config.py` (`jev-1.13.0`, `https://api.typesafe.ai`).
- **Library size:** the overlap and gap checks put every library description into one question. Above about 200 entries they need a two-stage question (spec §4.5, not built yet). `run.json` warns as token use approaches the limit.
- **Supply chain:** the `curation` extra is outside `sca_gate`, like `dev`. Dependabot and dependency-review cover it (`docs/supply-chain.md`).
- **Trusted venv:** `.venv` runs in the process that holds the key (its `.pth` files and site-packages). The wrapper refuses uncommitted changes to `pyproject.toml` and `uv.lock`, but it does not verify the installed packages; sync the venv only from the committed lock.
