"""Application configuration loaded from environment + .env."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from idraa.errors import RetentionConfigError

_DEFAULT_SECRET = "change-me-in-production"  # noqa: S105 — literal placeholder, guarded below
_PROD_MIN_SECRET_LEN = 32


class Settings(BaseSettings):
    """Global application settings. Override via environment or .env."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "Idraa"
    version: str = "0.0.0"
    environment: Literal["dev", "test", "prod"] = "dev"
    database_url: str = Field(
        default="sqlite+aiosqlite:///./idraa.db",
        description="SQLAlchemy DSN. Postgres in prod; SQLite in dev.",
    )
    session_secret: str = Field(
        default=_DEFAULT_SECRET,
        min_length=16,
    )
    list_page_size: int = Field(default=50, ge=1, le=500)
    # Centralised pagination size for list views (scenarios, library entries,
    # library overrides). Avoids scattering magic-number 50s across routes.

    mc_iterations_default: int = Field(default=10_000, ge=100, le=10_000_000)
    # UAT 2026-05-21 (issue #212): default Monte Carlo iteration count for the
    # /analyses form. Dropped from 100k to 10k after a 100k AGGREGATE OOM-killed
    # the 512MB Fly VM. Operators can still raise it manually up to
    # `mc_iterations_max`. Production deployments with larger VMs should raise
    # this via the MC_ITERATIONS_DEFAULT env var.
    mc_iterations_max: int = Field(default=1_000_000, ge=100, le=10_000_000)
    # UAT 2026-05-21 (issue #212): server-side cap on mc_iterations enforced at
    # /analyses POST. Operator request above this returns 422 with a friendly
    # error. Production deployments with larger VMs should raise this via the
    # MC_ITERATIONS_MAX env var.
    #
    # Raised 100_000 -> 1_000_000 (2026-07-06, high-fidelity-tail-mc PR2,
    # Task 11): opt-in high-fidelity tail runs for a stable deep-tail ES (and
    # its 95% MC interval) at N up to 1M. mc_iterations_default stays at
    # 10_000 — high-N is opt-in, never the default. This raise is gated on
    # BOTH of: (1) the Task 8 benchmark confirming the memory envelope
    # (M=30 AGGREGATE scenarios at N=1M ~700MB peak RSS, comfortably inside
    # the 2048MB shared-cpu-1x VM — see fly.toml / the Production deploy
    # section of CLAUDE.md); (2) PR1's hardening already merged (binary
    # sample codec, streaming encode, event-loop offload, the min-free-disk
    # guard, and the startup VACUUM) — without PR1, a 1M-iteration run would
    # regress the storage/memory/runtime pathologies PR1 exists to close.

    # Verification workbook (Phase 2a): scenario cap bounds the aggregate in-Excel
    # MC — at most K = verification_workbook_max_scenarios scenarios get an in-Excel
    # LET block; the remainder are listed summary-only.
    verification_workbook_max_scenarios: int = Field(
        default=15, ge=1, le=100, alias="VERIFICATION_WORKBOOK_MAX_SCENARIOS"
    )

    # Verification workbook (spill redesign): per-run N ceiling for the in-Excel
    # LET Monte Carlo, and the ΣN ceiling across an aggregate run's scenarios.
    # These replaced the old explicit-row caps (verification_workbook_max_rows /
    # _max_scenarios "rows" naming was misleading once the LET generates its draws
    # internally; the old verification_workbook_max_rows field + the explicit-row
    # code path it fed were removed in Task 7). Both are env-overridable like
    # MC_ITERATIONS_*.
    verification_workbook_max_n: int = Field(
        default=50_000, ge=100, le=100_000, alias="VERIFICATION_WORKBOOK_MAX_N"
    )
    # Per-run N ceiling: the in-Excel LET draws N = min(run.mc_iterations,
    # verification_workbook_max_n) so the workbook recalcs responsively. N rows
    # demonstrate convergence; exact App agreement is within sampling error.
    verification_workbook_aggregate_total_max: int = Field(
        default=150_000,
        ge=1_000,
        le=500_000,
        alias="VERIFICATION_WORKBOOK_AGGREGATE_TOTAL_MAX",
    )
    # ΣN ceiling across an aggregate run's reconstructible scenarios. Per-scenario
    # N starts at min(mc_iterations, verification_workbook_max_n), then is scaled
    # DOWN proportionally so the total Σ N across scenarios <= this cap, bounding
    # the in-Excel recompute cost for many-scenario aggregates. The le bound MUST
    # admit the 150_000 default (do NOT copy the old field's le=100_000).

    run_orphan_threshold_seconds: int = Field(
        default=300, ge=60, alias="RUN_ORPHAN_THRESHOLD_SECONDS"
    )
    # Operational watchdog timeout (issue #211), NOT a FAIR calibration
    # constant — it carries no distributional / half-life semantics. On app
    # startup the orphaned-run reaper flips RUNNING / stale QUEUED rows older
    # than this many seconds to FAILED, because a process boot (Fly
    # auto-restart) means any pre-boot worker is dead. The threshold only
    # guards clock skew + runs that flipped RUNNING seconds before boot.
    # Default 300s sits comfortably above observed run wall-clock; note a 100k
    # AGGREGATE on shared-cpu-1x can run for minutes, so an operator tuning
    # this DOWN must keep it above the legitimate max-run duration implied by
    # mc_iterations_max. Tune via RUN_ORPHAN_THRESHOLD_SECONDS.
    # (#211 Phase 2: the PERIODIC sweep additionally exempts rows owned by a
    # live in-process task via the active-run registry, so a slow-but-alive
    # run is never false-killed even at an aggressive threshold; the
    # threshold still guards the QUEUED→RUNNING dispatch gap.)

    run_reaper_interval_seconds: int = Field(
        default=300, ge=0, le=86_400, alias="RUN_REAPER_INTERVAL_SECONDS"
    )
    # #211 Phase 2: cadence of the in-process periodic orphan sweep
    # (services/run_reaper.py::periodic_reaper_loop, spawned from the app
    # lifespan). 0 disables the loop (boot sweep still runs). Operational
    # knob, not a calibration constant. Also the cadence of the wizard-draft
    # TTL sweep (drafts-surfaced spec §4) — see wizard_draft_ttl_days below.

    wizard_draft_ttl_days: int = Field(
        default=30,
        ge=0,
        description=(
            "Delete wizard drafts idle longer than this many days "
            "(drafts-surfaced spec §4). 0 disables the sweep."
        ),
    )
    # The periodic sweep rides the run-reaper loop —
    # RUN_REAPER_INTERVAL_SECONDS=0 disables it (boot sweep still runs).

    export_rate_limit_count: int = Field(
        default=30, ge=0, le=10_000, alias="EXPORT_RATE_LIMIT_COUNT"
    )
    # #357: max bulk-export downloads per user (per org for anonymous system
    # paths) within the sliding window below. Enforced at the log_bulk_export
    # choke point every export endpoint already funnels through, counting the
    # audit rows those exports write — no separate limiter state, so the cap
    # is correct across processes/restarts by construction. 0 disables.
    # Operational knob, not a calibration constant.

    export_rate_limit_window_seconds: int = Field(
        default=300, ge=1, le=86_400, alias="EXPORT_RATE_LIMIT_WINDOW_SECONDS"
    )
    # #357: sliding-window size for export_rate_limit_count.

    audit_log_watermark_rows: int = Field(default=250_000, ge=0, alias="AUDIT_LOG_WATERMARK_ROWS")
    # #357: emit a WARNING log when audit_log row count reaches this
    # watermark (checked on export writes only — the bloat vector the cap
    # bounds). Alert-only; retention policy is the operator's call. 0
    # disables. Sized well below the point where the 3GB Fly volume or
    # query latency would hurt (audit rows are small; run_samples is the
    # historical disk hog, see fly.toml RETENTION_SAMPLE_PURGE_DAYS).

    dev_styleguide_enabled: bool = False
    """Mounts /_dev/styleguide as functional when True. Off in prod; turn on locally for design QA."""

    max_smes_per_fieldset: int = Field(default=20, alias="MAX_SMES_PER_FIELDSET")
    # Sec-4 PR1 cap consumed by WizardStep3Submit fieldset validators
    # (FieldsetRows / VulnFieldsetRows). Bounds the number of SME-estimate rows
    # an analyst can submit per fieldset (tef/vuln/pl/sl), defending the
    # finalize pipeline's per-fit Nelder-Mead loop from unbounded fan-out.
    # Landed early (T8) alongside the schema that reads it; T9 adds the
    # remaining wizard runtime Settings keys.

    quantile_fit_maxiter: int = Field(default=200, alias="QUANTILE_FIT_MAXITER")
    # Per-fit Nelder-Mead iteration cap consumed by
    # services.wizard_finalize.process_sme_estimates via fit_lognorm_trunc /
    # fit_norm_trunc maxiter= kwarg. Landed early (T5) alongside the finalize
    # pipeline that reads it; T9 keeps the remaining wizard runtime Settings.
    quantile_fit_wall_clock_ms: int = Field(default=500, alias="QUANTILE_FIT_WALL_CLOCK_MS")
    # Per-fit cooperative scipy.optimize wall-clock budget (DeadlineCallback).
    # Used by services.wizard_finalize.process_sme_estimates.
    finalize_wall_clock_ms: int = Field(default=5000, alias="FINALIZE_WALL_CLOCK_MS")
    # Aggregate wall-clock budget across all fieldsets in a single finalize
    # call (Sec-12 R3). Checked interleaved inside the per-fit loop so a
    # single bad fieldset cannot bust the budget by 4x before raising
    # (Spec-10/Arch-11 PR1). Consumed by
    # services.wizard_finalize.process_sme_estimates.

    retention_sample_purge_days: int = Field(default=0, ge=0)
    # Age (days) after which a COMPLETED/FAILED/CANCELLED run's heavy
    # run_samples row is purged (keeps the run + summary). 0 = disabled
    # (default). Operational retention only — NOT a FAIR calibration value.
    retention_run_delete_days: int = Field(default=0, ge=0)
    # Age (days) after which an eligible run is fully deleted (cascade drops
    # run_samples). 0 = disabled (default). Must strictly exceed
    # retention_sample_purge_days when both enabled (validate_retention).
    retention_sweep_interval_hours: int = Field(default=6, ge=1)
    # How often the background retention sweep runs. Consumed by the trigger
    # (separate task #297), not by sweep_retention itself.
    retention_sweep_batch_limit: int = Field(default=200, ge=1)
    # Per-phase cap on rows touched in a single sweep pass — bounds the
    # transaction size + audit fan-out on a long-neglected DB.
    retention_vacuum_enabled: bool = Field(default=False)
    # Startup-only VACUUM (Task 5, Arch-B1): reclaims disk after the purge
    # phase frees run_samples rows. SQLite-only, AUTOCOMMIT connection (VACUUM
    # cannot run inside a transaction). NEVER runs on the request-path sweep —
    # only the startup one-shot passes vacuum=True to sweep_retention.
    # NOTE: deliberately no ``alias=`` here (unlike some sibling settings
    # elsewhere in this file) — model_config has no ``populate_by_name=True``,
    # so a field with an explicit alias can ONLY be set via that alias string
    # in the constructor; passing the python attribute name as a kwarg is
    # silently swallowed by ``extra="ignore"`` and the default wins with no
    # error. ``case_sensitive=False`` already gives case-insensitive env-var
    # matching (RETENTION_VACUUM_ENABLED -> retention_vacuum_enabled) without
    # needing an alias, matching the other retention_* fields above.
    retention_vacuum_min_free_bytes: int = Field(default=50_000_000, ge=0)
    # Minimum reclaimable free space (freelist_count * page_size) that must
    # already exist in the SQLite file before a startup VACUUM is worth its
    # full-file rewrite (default 50 MB). Gating on ACTUAL free pages — not on
    # THIS pass's purge count — is deliberate (ARCH-I1): VACUUM reclaims ALL
    # free pages whenever it runs, and the aged rows are usually purged by the
    # opportunistic boot sweep that runs BEFORE the startup vacuum sweep, so a
    # this-pass-purge-count gate would skip VACUUM on exactly the boots where
    # space was just freed. No alias — matches the retention_* convention
    # (case-insensitive env matching, no populate_by_name).

    min_free_disk_bytes: int = Field(default=300_000_000, ge=0)
    # Sec-I2 (2026-06-29 outage class): services.runs.create_and_dispatch
    # rejects new dispatches with RunValidationError when the SQLite DB
    # volume's free space drops below this threshold (~300 MB default), so a
    # burst of high-N/high-M runs cannot refill the 3 GB volume between
    # 14-day retention purges. Operational guard, not a FAIR calibration
    # constant. Deliberately no ``alias=`` here (see
    # retention_vacuum_enabled's note above) — case-insensitive env matching
    # already binds MIN_FREE_DISK_BYTES without needing one, and an alias
    # would silently break constructor-kwarg binding in tests since
    # model_config has no ``populate_by_name=True``.

    high_fidelity_iterations_threshold: int = Field(default=250_000, ge=1_000)
    # Issue #508 (PR2 final-gate Sec-I): a run at or above this iteration count
    # is "high-fidelity" — it peaks ~700 MB RSS at M=30/1M (vs ~70 MB at 100k),
    # so concurrent such runs are capped (below). The form's high-fidelity
    # cost-warning trigger (templates/analyses/new.html ``highFidelityThreshold``)
    # is templated from THIS value (routes/runs.py), so the UI warning and the
    # server cap can never desync — even under an env override.
    # No ``alias=`` (same rationale as min_free_disk_bytes).
    max_concurrent_high_fidelity_runs: int = Field(default=2, ge=1)
    # Issue #508: max simultaneous IN-FLIGHT (RUNNING + QUEUED) high-fidelity
    # runs. Raising mc_iterations_max to 1M scaled per-run peak RSS ~10x, so
    # unbounded concurrent high-N dispatch could OOM the 4 GB VM (headroom fell
    # from ~50 to ~4-5 concurrent max-N runs). 2 x ~700 MB = ~1.4 GB, well under
    # the VM; a 3rd is rejected at dispatch. Counted GLOBALLY (not org-scoped) —
    # the VM RAM is shared across orgs. Env: MAX_CONCURRENT_HIGH_FIDELITY_RUNS.

    # Weight-robustness ensemble (issue #419) — logit-normal band sampler.
    # These are draw-budget / sampler-shape knobs, not FAIR calibration constants.
    weight_ensemble_draws: int = Field(default=256, ge=1, le=4096)
    # K target: desired number of logit-normal ensemble draws per scenario pair.
    weight_ensemble_min_draws: int = Field(default=32, ge=2, le=512)
    # below this → insufficient_budget fallback (band-endpoints only, Arch-B1/Perf-I1)
    # Sized so K_target fits small/medium portfolios; large portfolios degrade and,
    # below K_min, fall back to band-endpoints. NOT tied to the 2M single-pass CAP
    # (that would collapse K to ~1). Arch-B-Budget1: the Sec-N5 model_validator below
    # checks bootability against a REPRESENTATIVE SMALL pass cost
    # (REPRESENTATIVE_PASS_EVALS, e.g. one n=12 scenario = 4096 evals), NOT
    # MAX_ATTRIBUTION_TOTAL_EVALS — so the default (10M) is bootable
    # (32 * 4096 = 131k << 10M) while large portfolios honestly degrade.
    weight_ensemble_eval_budget: int = Field(default=10_000_000, ge=0)
    # Sec-N5/Arch-B-Budget1: bootable when budget >= min_draws * REPRESENTATIVE_PASS_EVALS.
    # Optional opt-in throttle for the ENSEMBLE's per-draw Shapley permutation count
    # (>EXACT_MAX_N scenarios). Default None => FULL Maleki precision (no sample
    # compromise). The ensemble is fast at full precision because the κ-invariant
    # precompose_parts result (ComposedParts) is cached per subset across draws
    # (run_executor _build_weight_robustness); the per-draw work is only the cheap
    # finalize_composition(κ) + weight application (~6.6µs per finalize), so cutting
    # permutations is NOT needed for speed; this knob exists only as an
    # emergency throttle. When set (16..5000) it reduces per-draw perms; that trades
    # a small conservative location offset vs the full-precision canonical_value for
    # speed (sound under fixed-seed common random numbers — the noise cancels in the
    # band WIDTH, not the absolute location). Pinned in the band for reproducibility.
    weight_ensemble_shapley_permutations: int | None = Field(
        default=None, ge=16, le=5000, alias="WEIGHT_ENSEMBLE_SHAPLEY_PERMUTATIONS"
    )
    weight_band_logit_sigma: float = Field(
        default=0.6,
        ge=0.0,
        le=5.0,
        # logit-space perturbation width; 0.0 => identity (Test-N1, Meth-B5)
    )
    weight_rank_stable_threshold: float = Field(default=0.90, ge=0.5, le=1.0)
    weight_rank_indistinguishable_threshold: float = Field(default=0.10, ge=0.0, le=0.5)

    # --- Strong auth / MFA (2026-07-22 design) ---
    # Config-driven so the software stays self-hostable — never hardcode an
    # operator's domains into WebAuthn. Defaults are the OWNER deployment.
    webauthn_rp_id: str = "idraa.fly.dev"
    webauthn_rp_name: str = "Idraa"
    # Single registrable domain (plan-gate): one RP-ID can't span idraa.fly.dev +
    # idraa.app. A second passkey domain needs its own RP-ID or Related Origin Requests.
    webauthn_origins: str = "https://idraa.fly.dev"
    auth_mfa_policy: Literal["required", "optional"] = "required"
    totp_issuer: str = "Idraa"
    mfa_encryption_key: str | None = None
    # Minimal login throttle — idraa#81 slice pulled into P1 at plan-gate (B1):
    # the reworked login must not ship a rate-limit-free 6-digit second factor.
    auth_max_failed_logins: int = Field(default=5, ge=0)  # 0 disables lockout
    auth_lockout_seconds: int = Field(default=900, ge=0)

    # Step-up ("sudo mode") freshness window — P2. Sensitive actions require a
    # re-auth within this many seconds. 0 disables step-up entirely (mirrors
    # auth_max_failed_logins' 0-disables convention).
    auth_step_up_max_age_seconds: int = Field(default=600, ge=0)

    @property
    def webauthn_origin_list(self) -> list[str]:
        """WEBAUTHN_ORIGINS parsed: comma-split, trimmed, blanks dropped."""
        return [o.strip() for o in self.webauthn_origins.split(",") if o.strip()]

    @model_validator(mode="after")
    def _ensemble_budget_bootable(self) -> Settings:
        """Sec-N5/Arch-B-Budget1: budget must cover at least min_draws representative passes.

        REPRESENTATIVE_PASS_EVALS = 4096 (one n=12 exact-Shapley scenario). The
        check is against this SMALL representative cost, NOT MAX_ATTRIBUTION_TOTAL_EVALS
        (which would collapse K to ~1 and defeat the ensemble). The default (10M) is
        bootable: 32 * 4096 = 131_072 << 10_000_000.
        """
        representative_pass_evals = 4096
        floor = self.weight_ensemble_min_draws * representative_pass_evals
        if self.weight_ensemble_eval_budget < floor:
            raise ValueError(
                f"weight_ensemble_eval_budget {self.weight_ensemble_eval_budget} "
                f"< {floor} (weight_ensemble_min_draws={self.weight_ensemble_min_draws} "
                f"x representative_pass_evals={representative_pass_evals}); "
                "raise weight_ensemble_eval_budget or lower weight_ensemble_min_draws"
            )
        return self

    def validate_retention(self) -> None:
        """Fail-fast on internally-inconsistent retention windows (#297).

        When BOTH phases are enabled, the auto-delete window must strictly
        exceed the sample-purge window — otherwise auto-delete would fire on
        or before the purge window and the purge phase is dead config. Either
        phase disabled (0) is always valid (independent toggles). Raised at
        startup BEFORE the reaper's try/except so a misconfiguration crashes
        boot rather than silently mis-retaining."""
        d, p = self.retention_run_delete_days, self.retention_sample_purge_days
        if d and p and d <= p:
            raise RetentionConfigError(
                f"retention_run_delete_days ({d}) must exceed retention_sample_purge_days ({p})"
            )

    @model_validator(mode="after")
    def _check_secret_hardening(self) -> Settings:
        """Refuse to boot with weak/default secrets outside the test harness.

        Threat model: an operator deploys with ``ENVIRONMENT`` unset — it
        defaults to ``dev`` — and also forgets ``SESSION_SECRET``. The
        checked-in placeholder then signs real cookies in production, and
        anyone who can read the repo can forge them.

        Mitigation: the ONLY environment that accepts the default secret
        is ``test``, which legitimately uses disposable fixtures. Both
        ``dev`` and ``prod`` require the operator to set a non-default
        ``SESSION_SECRET`` via ``.env`` (dev: 16+ chars, prod: 32+). That
        is a one-time cost for dev contributors and closes the blind spot
        above.

        Actionable error: message names ``SESSION_SECRET`` so the operator
        knows exactly which env var to set, and points at ``.env`` as the
        local-dev path.
        """
        if self.environment == "test":
            # Test harness owns its own env — disposable defaults are fine.
            return self
        if self.session_secret == _DEFAULT_SECRET:
            raise ValueError(
                "Refusing to boot with the default SESSION_SECRET in "
                f"environment={self.environment!r}. Set the SESSION_SECRET "
                "environment variable (or add it to your .env file) to a "
                "random value — generate one with "
                "`python -c 'import secrets; print(secrets.token_urlsafe(48))'`."
            )
        if self.environment == "prod" and len(self.session_secret) < _PROD_MIN_SECRET_LEN:
            raise ValueError(
                f"SESSION_SECRET must be at least {_PROD_MIN_SECRET_LEN} characters "
                f"in environment={self.environment!r} (got {len(self.session_secret)}). "
                "Regenerate the SESSION_SECRET environment variable."
            )
        return self

    @model_validator(mode="after")
    def _check_webauthn_hardening(self) -> Settings:
        """Refuse to boot in prod with an unusable WebAuthn RP-ID / origins.

        A placeholder RP-ID or origins that don't cover it silently breaks
        passkeys for the deployment. dev/test accept the defaults.
        """
        if self.environment != "prod":
            return self
        if not self.webauthn_rp_id.strip():
            raise ValueError(
                "WEBAUTHN_RP_ID must be set to the deployment's domain in "
                f"environment={self.environment!r} (e.g. app.example.com)."
            )
        origins = self.webauthn_origin_list
        if not origins:
            raise ValueError(
                "WEBAUTHN_ORIGINS must list at least one https:// origin in "
                f"environment={self.environment!r} "
                "(e.g. https://app.example.com,https://example.com)."
            )
        rp = self.webauthn_rp_id
        for origin in origins:
            if not origin.startswith("https://"):
                raise ValueError(f"WEBAUTHN_ORIGINS entry {origin!r} must be https://")
            host = origin.removeprefix("https://").split("/", 1)[0].split(":", 1)[0]
            if not (host == rp or host.endswith("." + rp)):
                raise ValueError(
                    f"WEBAUTHN_ORIGINS entry {origin!r} (host {host!r}) is not "
                    f"WEBAUTHN_RP_ID {rp!r} nor a subdomain of it — WebAuthn "
                    "requires every origin's host to equal or be under the RP-ID."
                )
        return self


_cached: Settings | None = None


def get_settings() -> Settings:
    """Return the process-wide Settings singleton."""
    global _cached
    if _cached is None:
        _cached = Settings()
    return _cached


def reset_for_tests() -> None:
    """Drop the cached Settings singleton. Tests only — do not call from prod code.

    Lets tests monkeypatch environment variables and re-read them on the next
    ``get_settings()`` call. Update this function alongside any change to the
    singleton implementation.
    """
    global _cached
    _cached = None
