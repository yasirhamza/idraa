"""
Core FAIR (Factor Analysis of Information Risk) Calculation Engine

Implements the FAIR methodology for quantitative risk analysis:
- Loss Event Frequency (LEF) = Threat Event Frequency (TEF) × Vulnerability
- Loss Magnitude (LM) = Primary Loss (PL) + Secondary Loss (SL)
- Risk = LEF × LM (computed via Monte Carlo simulation)
"""

import math
import warnings
from dataclasses import dataclass
from enum import Enum
from typing import Any

import numpy as np

warnings.filterwarnings("ignore", category=RuntimeWarning)


class DistributionType(Enum):
    """Supported probability distributions for FAIR modeling"""

    UNIFORM = "uniform"
    NORMAL = "normal"
    LOGNORMAL = "lognormal"
    LOGNORMAL_MIXTURE = "lognormal_mixture"
    BETA = "beta"
    TRIANGULAR = "triangular"
    PERT = "pert"
    POISSON = "poisson"
    EXPONENTIAL = "exponential"


@dataclass
class FAIRDistribution:
    """Represents a probability distribution for FAIR parameters"""

    distribution_type: DistributionType
    # dict[str, Any] (not dict[str, float]): LOGNORMAL_MIXTURE stores a nested
    # shape, ``{"components": [{"mean", "sigma", "weight"}, ...]}`` (issue #27
    # Task 3) -- every other DistributionType still stores flat float params.
    parameters: dict[str, Any]

    def sample(
        self,
        size: int = 1,
        rng: np.random.Generator | None = None,
    ) -> np.ndarray:
        """Generate random samples from the distribution.

        Args:
            size: Number of samples to draw.
            rng: Optional per-call Generator. When None, a fresh
                ``np.random.default_rng()`` is used (non-deterministic but
                never corrupts numpy's global state). Pass an explicit
                Generator (e.g. ``FAIREngine._rng``) for reproducibility.
        """

        # Task #23 (Commit A.1): per-instance RNG instead of np.random.*.
        # When no rng is supplied, fall back to a per-call default_rng so
        # legacy callers (tests, notebooks) still work without mutating
        # numpy's global state.
        if rng is None:
            rng = np.random.default_rng()

        if self.distribution_type == DistributionType.UNIFORM:
            return rng.uniform(self.parameters["low"], self.parameters["high"], size)

        elif self.distribution_type == DistributionType.NORMAL:
            return rng.normal(self.parameters["mean"], self.parameters["std"], size)

        elif self.distribution_type == DistributionType.LOGNORMAL:
            # NB: `mean` and `sigma` here are LOG-space parameters (numpy semantics),
            # not real-space. This differs from `_generate_distribution_samples` in
            # aggregation_engine.py, which takes real-space `mean` and converts via
            # np.log. Both are correct for their respective callers; do not unify
            # without a broader refactor of caller contracts.
            return rng.lognormal(self.parameters["mean"], self.parameters["sigma"], size)

        elif self.distribution_type == DistributionType.LOGNORMAL_MIXTURE:
            # Linear-opinion-pool mixture of lognormal components (issue #27
            # Task 3). Wire shape: parameters["components"] is a list of
            # {"mean" (log-space meanlog), "sigma", "weight"} dicts -- one
            # per pooled SME fit. Mirrors fair_cam.quantile_pooling's
            # LognormMixture one-for-one (no import dependency; the engine
            # layer does not depend on quantile_pooling).
            components = self.parameters["components"]
            n_components = len(components)
            if n_components == 0:
                raise ValueError("lognormal_mixture: parameters['components'] must be non-empty")

            if n_components == 1:
                # DEDICATED single-component branch that bypasses rng.choice
                # entirely (binding amendment). rng.choice ALWAYS advances
                # the generator's state to pick an index -- even with only
                # one possible outcome, it still consumes `size` draws worth
                # of randomness -- so routing a 1-component mixture through
                # rng.choice would desync its sample stream from a plain
                # DistributionType.LOGNORMAL draw at the same seed. This
                # branch keeps that stream byte-identical (pinned by
                # test_single_component_stream_identical_to_plain_lognormal),
                # which is what makes single-SME pooling an exact identity
                # end-to-end, not just at the quantile-math layer.
                c = components[0]
                return rng.lognormal(c["mean"], c["sigma"], size)

            # Multi-component: one rng.choice call to pick the component
            # index per draw (weighted by the pool weights), then a single
            # VECTORIZED rng.lognormal call over the per-draw parameter
            # arrays gathered via that index -- one draw pass, no python
            # loop over components or samples.
            mean_arr = np.array([c["mean"] for c in components], dtype=float)
            sigma_arr = np.array([c["sigma"] for c in components], dtype=float)
            weight_arr = np.array([c["weight"] for c in components], dtype=float)
            idx = rng.choice(n_components, size=size, p=weight_arr)
            return rng.lognormal(mean_arr[idx], sigma_arr[idx], size=size)

        elif self.distribution_type == DistributionType.TRIANGULAR:
            return rng.triangular(
                self.parameters["low"], self.parameters["mode"], self.parameters["high"], size
            )

        elif self.distribution_type == DistributionType.PERT:
            # Explicit float() casts: `parameters` widened to dict[str, Any]
            # for the LOGNORMAL_MIXTURE nested shape (issue #27 Task 3) --
            # without these, `low`/`mode`/`high` infer as Any here and the
            # arithmetic below (`low + beta_samples * (high - low)`) leaks
            # Any into a declared ndarray return (mypy no-any-return).
            low = float(self.parameters["low"])
            mode = float(self.parameters["mode"])
            high = float(self.parameters["high"])

            # A3 fix: mirror the validation in aggregation_engine._generate_pert_samples.
            if low > high:
                raise ValueError(f"pert: low must be <= high (got low={low}, high={high})")
            if low == high:
                return np.full(size, low)
            if mode < low or mode > high:
                raise ValueError(
                    f"pert: mode must be in [low, high] (got low={low}, mode={mode}, high={high})"
                )

            # Vose BetaPERT (γ=4), parameterised IDENTICALLY to pyfair's
            # ``utility/beta_pert.py`` so the native engine is distributionally
            # equivalent to the pyfair oracle it replaces (epic #324 equivalence
            # gate). The earlier simpler form fixed α+β=6 (mean-based), which
            # matched the PERT MEAN but gave a different shape — a ~0.5% median /
            # ~2% ALE-median divergence vs pyfair that the equivalence harness
            # caught (median is shape-sensitive; the mean was already exact).
            #   mean  = (low + γ·mode + high)/(γ+2)         # γ=4 → standard PERT mean
            #   stdev = (high − low)/(γ+2)
            #   α     = ((mean−low)/(high−low)) · ((mean−low)(high−mean)/stdev² − 1)
            #   β     = α · (high − mean)/(mean − low)
            gamma = 4.0
            mean = (low + gamma * mode + high) / (gamma + 2.0)
            stdev = (high - low) / (gamma + 2.0)
            g1 = (mean - low) / (high - low)
            g2 = ((mean - low) * (high - mean)) / (stdev**2)
            alpha = g1 * (g2 - 1.0)
            beta = alpha * (high - mean) / (mean - low)

            beta_samples = rng.beta(alpha, beta, size)
            return low + beta_samples * (high - low)

        elif self.distribution_type == DistributionType.BETA:
            # WARNING: returns raw Beta(α, β) samples in [0, 1]. Suitable for
            # vulnerability-like parameters. DO NOT use for financial quantities —
            # there is no scale parameter. (Phase 2+ should add a scaled-Beta
            # variant or use PERT instead.)
            return rng.beta(self.parameters["alpha"], self.parameters["beta"], size)

        elif self.distribution_type == DistributionType.POISSON:
            return rng.poisson(self.parameters["lambda"], size)

        elif self.distribution_type == DistributionType.EXPONENTIAL:
            return rng.exponential(self.parameters["scale"], size)

        else:
            raise ValueError(f"Unsupported distribution type: {self.distribution_type}")


@dataclass
class FAIRParameters:
    """Complete FAIR analysis parameters"""

    # Threat Event Frequency (TEF) - events per year
    threat_event_frequency: FAIRDistribution

    # Vulnerability - probability of successful threat action
    vulnerability: FAIRDistribution

    # Primary Loss (PL) - direct financial impact
    primary_loss: FAIRDistribution

    # Secondary Loss (SL) - indirect costs (response, reputation, etc.)
    secondary_loss: FAIRDistribution

    # Optional: Contact frequency for threat community modeling
    contact_frequency: FAIRDistribution | None = None

    # Optional: Action frequency given contact
    action_frequency: FAIRDistribution | None = None

    # Optional: Threat capability vs resistance strength
    threat_capability: FAIRDistribution | None = None
    resistance_strength: FAIRDistribution | None = None

    def scaled(
        self,
        *,
        frequency_multiplier: float,
        magnitude_multiplier: float,
    ) -> "FAIRParameters":
        """Return a new FAIRParameters with TEF / primary_loss / secondary_loss
        scaled by the given multipliers.

        NB: the FAIR-CAM engine path uses `apply_node_multipliers` (4-node, returns
        the sample-level vuln knob), not this 2-multiplier method. `scaled()` is
        retained for the location-scale scaling-property tests
        (`test_fair_parameters_scaling.py`); its removal is tracked alongside the
        other post-pyfair test-only cleanup in #328.

        Frequency multiplier applies to TEF only (vulnerability is left
        untouched — overlay/override semantics scale the event-rate component
        of LEF, not the threat-action-success-probability component).

        Magnitude multiplier applies to primary_loss and secondary_loss
        equally (both contribute to LM additively in the FAIR Monte Carlo,
        so a uniform scale on both is the canonical "scale total loss
        magnitude by k" operation).

        Distribution-shape preservation:
        - TRIANGULAR / PERT / UNIFORM: scale all `low`/`mode`/`high`/`low`/`high`
          parameters by the multiplier.
        - LOGNORMAL: log-space `mean` shifts by `+ln(multiplier)`; `sigma`
          (shape) unchanged. Real-space distribution scales by `multiplier`.
        - NORMAL: `mean *= multiplier`; `std *= multiplier`. Coefficient of
          variation preserved.
        - BETA: only valid for vulnerability (which we don't scale here);
          attempting to scale a BETA primary_loss / secondary_loss raises
          (consistent with FAIREngine's runtime barrier in `calculate_risk`).
        - POISSON / EXPONENTIAL: not currently used for TEF or LM in this
          codebase — raise NotImplementedError as a flag for future work.

        Raises:
            ValueError: multipliers are non-positive or non-finite.
        """
        from copy import deepcopy

        if not math.isfinite(frequency_multiplier) or frequency_multiplier <= 0:
            raise ValueError(
                f"frequency_multiplier must be a positive finite float; "
                f"got {frequency_multiplier!r}"
            )
        if not math.isfinite(magnitude_multiplier) or magnitude_multiplier <= 0:
            raise ValueError(
                f"magnitude_multiplier must be a positive finite float; "
                f"got {magnitude_multiplier!r}"
            )

        return FAIRParameters(
            threat_event_frequency=_scale_distribution(
                self.threat_event_frequency, frequency_multiplier
            ),
            vulnerability=deepcopy(self.vulnerability),  # untouched
            primary_loss=_scale_distribution(self.primary_loss, magnitude_multiplier),
            secondary_loss=_scale_distribution(self.secondary_loss, magnitude_multiplier),
            contact_frequency=deepcopy(self.contact_frequency),
            action_frequency=deepcopy(self.action_frequency),
            threat_capability=deepcopy(self.threat_capability),
            resistance_strength=deepcopy(self.resistance_strength),
        )

    def apply_node_multipliers(
        self, node_multipliers: dict[str, float]
    ) -> "tuple[FAIRParameters, float]":
        """Apply FAIR-CAM 4-node multipliers at the PARAMETER level for TEF /
        primary_loss / secondary_loss, and RETURN the vulnerability multiplier
        as the sample-level knob the caller MUST pass to
        ``FAIREngine.calculate_risk(vulnerability_multiplier=...)``.

        Returns ``(adjusted_params, vulnerability_multiplier)``. Vulnerability is
        deliberately applied at the SAMPLE level (BETA cannot be param-scaled;
        sample-level is exact for any vuln shape) — returning it (rather than
        silently dropping the key) means the split cannot be misused into a
        silent under-control of risk. The currency subtractor is likewise a
        sample-level engine kwarg the caller threads from
        ``group_comp.currency_subtractor_total``.

        A multiplier of exactly 0 (perfect control, ``1 - E*w`` with E=w=1)
        degenerates the node to a point-mass at 0 (mirrors pyfair ``value*0``);
        ``_scale_distribution`` cannot represent 0 (``log(0)``).
        """
        from copy import deepcopy

        def _node(dist: "FAIRDistribution", mult: float) -> "FAIRDistribution":
            if not math.isfinite(mult) or mult < 0:
                raise ValueError(f"node multiplier must be finite and >= 0; got {mult!r}")
            if mult == 0:
                return FAIRDistribution(DistributionType.UNIFORM, {"low": 0.0, "high": 0.0})
            return _scale_distribution(dist, mult)

        vuln_mult = node_multipliers["vulnerability"]
        if not math.isfinite(vuln_mult) or vuln_mult < 0:
            raise ValueError(
                f"vulnerability node multiplier must be finite and >= 0; got {vuln_mult!r}"
            )

        adjusted = FAIRParameters(
            threat_event_frequency=_node(
                self.threat_event_frequency, node_multipliers["threat_event_frequency"]
            ),
            vulnerability=deepcopy(self.vulnerability),  # sample-level, untouched
            primary_loss=_node(self.primary_loss, node_multipliers["primary_loss"]),
            secondary_loss=_node(self.secondary_loss, node_multipliers["secondary_loss"]),
            contact_frequency=deepcopy(self.contact_frequency),
            action_frequency=deepcopy(self.action_frequency),
            threat_capability=deepcopy(self.threat_capability),
            resistance_strength=deepcopy(self.resistance_strength),
        )
        return adjusted, vuln_mult


def _scale_distribution(dist: "FAIRDistribution", multiplier: float) -> "FAIRDistribution":
    """Scale a FAIRDistribution by a multiplicative factor in real-space.

    Helper for FAIRParameters.scaled. Distribution-shape-aware:
    log-space LOGNORMAL parameters get the additive log-shift; everything
    else gets a simple multiplicative scale on the magnitude parameters.
    """
    # dict[str, Any] (not dict[str, float]): the LOGNORMAL_MIXTURE branch
    # below assigns a nested {"components": [...]} value (issue #27 Task 3).
    new_params: dict[str, Any] = {}
    if (
        dist.distribution_type == DistributionType.TRIANGULAR
        or dist.distribution_type == DistributionType.PERT
    ):
        new_params = {
            "low": dist.parameters["low"] * multiplier,
            "mode": dist.parameters["mode"] * multiplier,
            "high": dist.parameters["high"] * multiplier,
        }
    elif dist.distribution_type == DistributionType.UNIFORM:
        new_params = {
            "low": dist.parameters["low"] * multiplier,
            "high": dist.parameters["high"] * multiplier,
        }
    elif dist.distribution_type == DistributionType.NORMAL:
        new_params = {
            "mean": dist.parameters["mean"] * multiplier,
            "std": dist.parameters["std"] * multiplier,
        }
    elif dist.distribution_type == DistributionType.LOGNORMAL:
        # FAIRDistribution.LOGNORMAL parameters are LOG-space (numpy semantics).
        # Real-space scale by k → log-space mean shifts by +ln(k); sigma unchanged.
        new_params = {
            "mean": dist.parameters["mean"] + math.log(multiplier),
            "sigma": dist.parameters["sigma"],
        }
    elif dist.distribution_type == DistributionType.LOGNORMAL_MIXTURE:
        # Same log-space additive shift as plain LOGNORMAL, applied to
        # EVERY component -- each component's meanlog shifts by +ln(k);
        # sigma and weight are untouched (component identity and pooling
        # weights are shape, not scale). This makes the mixture's overall
        # real-space mean scale by exactly `multiplier` (pinned by
        # test_scale_distribution_shifts_every_component_mean_by_ln_multiplier).
        new_params = {
            "components": [
                {
                    "mean": c["mean"] + math.log(multiplier),
                    "sigma": c["sigma"],
                    "weight": c["weight"],
                }
                for c in dist.parameters["components"]
            ]
        }
    elif dist.distribution_type == DistributionType.BETA:
        raise ValueError(
            "Cannot scale BETA distribution via FAIRParameters.scaled — "
            "BETA is unscaled [0, 1] (vulnerability-only); use PERT or LOGNORMAL "
            "for loss fields and they will scale correctly."
        )
    elif dist.distribution_type in (DistributionType.POISSON, DistributionType.EXPONENTIAL):
        raise NotImplementedError(
            f"Scaling {dist.distribution_type.value} distributions is not "
            f"implemented; the calibration framework targets TRIANGULAR (TEF/vuln) "
            f"and LOGNORMAL/PERT (loss) shapes used by the IRIS-year builders "
            f"in fair_cam.parameters._iris_2025_calibration."
        )
    else:
        raise ValueError(f"Unsupported distribution_type: {dist.distribution_type}")

    return FAIRDistribution(
        distribution_type=dist.distribution_type,
        parameters=new_params,
    )


class FAIREngine:
    """Core FAIR risk calculation engine with Monte Carlo simulation"""

    def __init__(
        self,
        iterations: int = 10000,
        random_seed: int | np.random.SeedSequence | None = None,
    ):
        """Initialize FAIR engine

        Args:
            iterations: Number of Monte Carlo iterations
            random_seed: Random seed for reproducibility (None → fresh entropy).
                Accepts an ``int`` (legacy), a ``numpy.random.SeedSequence``
                (enables round-trip reproducibility via a persisted spawn_key),
                or ``None``.  Passed directly to ``numpy.random.default_rng``,
                which accepts all three forms.

        Note:
            Not thread-safe across threads sharing one instance — instantiate
            per request. The per-instance ``_rng`` avoids corruption between
            concurrent engines but a single Generator is itself not safe for
            concurrent draws.
        """
        self.iterations = iterations
        # Task #23 (Commit A.1): per-instance Generator instead of
        # np.random.seed() so concurrent engine instances (e.g. FastAPI
        # request handlers) don't stomp on each other's sample streams via
        # numpy's global state. default_rng(None) seeds from fresh entropy
        # — same behavior as the legacy no-seed path. Unlike the legacy
        # `if random_seed:` check, default_rng(0) correctly honors a zero
        # seed rather than silently treating it as "no seed".
        self._rng = np.random.default_rng(random_seed)

    def calculate_risk(
        self,
        parameters: FAIRParameters,
        *,
        secondary_loss_subtractor: float = 0.0,
        vulnerability_multiplier: float = 1.0,
    ) -> dict[str, Any]:
        """Calculate risk using full FAIR methodology

        Args:
            parameters: Complete FAIR analysis parameters
            secondary_loss_subtractor: Currency amount subtracted from each
                secondary-loss sample BEFORE flooring at zero. Applied at the
                sample level because lognormal loss is not closed under
                subtraction — doing this at the parameter level would be wrong.
                Must be finite and >= 0. The reduction is modeled against the
                Secondary Loss node only; Primary Loss is untouched.
            vulnerability_multiplier: Multiplier applied to each vulnerability
                sample, then clipped to [0, 1]. A value of 0 (perfect control)
                yields a point-mass-at-0 LEF and is explicitly allowed.
                Must be finite and >= 0.

        Returns:
            Dictionary containing risk statistics and distributions
        """

        # --- Sample-level knob validation ---
        # secondary_loss_subtractor: finite and >= 0 (negative subtractor would
        # ADD to SL, which is nonsensical semantically and never produced by
        # the node-multiplier pipeline).
        if not math.isfinite(secondary_loss_subtractor) or secondary_loss_subtractor < 0:
            raise ValueError(
                f"secondary_loss_subtractor must be finite and >= 0; "
                f"got {secondary_loss_subtractor!r}"
            )
        # vulnerability_multiplier: finite and >= 0.
        # NB: 0 is allowed — a perfect control composes to a 0 node multiplier;
        # clip(vuln * 0, 0, 1) == 0 is the correct point-mass-at-0 vulnerability.
        if not math.isfinite(vulnerability_multiplier) or vulnerability_multiplier < 0:
            raise ValueError(
                f"vulnerability_multiplier must be finite and >= 0; "
                f"got {vulnerability_multiplier!r}"
            )

        # Task #22 (BETA runtime barrier): FAIRDistribution.sample() returns
        # raw Beta(α, β) samples in [0, 1] with NO scale — suitable for
        # vulnerability-like parameters but nonsensical for financial loss
        # magnitudes (primary / secondary loss). Reject before simulation
        # starts so downstream callers see a clear error instead of a
        # silently-wrong ALE distribution peaking at sub-dollar values.
        #
        # NB: the barrier is intentionally scoped to the two loss fields
        # only. TEF (dimensionless event counts) and vulnerability (a
        # probability in [0,1], where BETA is the *correct* modeling
        # choice) stay unbarred. A future maintainer who "fixes" the
        # asymmetry by extending this loop to TEF or vulnerability would
        # regress legitimate usage — the hazard here is a silent scale
        # mismatch on money-denominated fields, not BETA itself.
        for field_name in ("primary_loss", "secondary_loss"):
            dist = getattr(parameters, field_name)
            if dist.distribution_type == DistributionType.BETA:
                raise ValueError(
                    f"{field_name}: BETA distribution is unsuitable for loss "
                    "fields (unscaled [0,1] output); use PERT or LOGNORMAL"
                )

        # Generate Monte Carlo samples (sample-level knobs threaded through)
        samples = self._generate_samples(
            parameters,
            secondary_loss_subtractor=secondary_loss_subtractor,
            vulnerability_multiplier=vulnerability_multiplier,
        )

        # Calculate Loss Event Frequency (LEF)
        lef = samples["tef"] * samples["vulnerability"]

        # Calculate Loss Magnitude (LM)
        loss_magnitude = samples["primary_loss"] + samples["secondary_loss"]

        # Calculate Risk (Annual Loss Expectancy)
        risk = lef * loss_magnitude

        # Finite-output guard (Sec-B1 / #307 class): the native engine samples
        # lognormal natively; an unbounded tail (huge sigma) or lef*lm overflow
        # can produce inf/nan that would be durably stored as JSON `Infinity`/
        # `NaN` and corrupt the distribution. Refuse, so execute_run flips the
        # run to FAILED rather than persisting a corrupt result.
        if not np.all(np.isfinite(risk)):
            n_bad = int((~np.isfinite(risk)).sum())
            raise ValueError(
                f"FAIREngine produced {n_bad} non-finite risk samples "
                f"(inf/nan from unbounded lognormal tail or lef*lm overflow); "
                f"refusing to emit a corrupt distribution"
            )

        # Calculate statistics
        results = {
            "risk_distribution": risk,
            "lef_distribution": lef,
            "loss_magnitude_distribution": loss_magnitude,
            # Risk statistics
            "ale_mean": np.mean(risk),
            "ale_median": np.median(risk),
            "ale_std": np.std(risk),
            "ale_min": np.min(risk),
            "ale_max": np.max(risk),
            # Percentiles for VaR analysis
            "ale_p10": np.percentile(risk, 10),
            "ale_p25": np.percentile(risk, 25),
            "ale_p75": np.percentile(risk, 75),
            "ale_p90": np.percentile(risk, 90),
            "ale_p95": np.percentile(risk, 95),
            "ale_p99": np.percentile(risk, 99),
            # Component statistics
            "lef_mean": np.mean(lef),
            "lef_median": np.median(lef),
            "loss_magnitude_mean": np.mean(loss_magnitude),
            "loss_magnitude_median": np.median(loss_magnitude),
            # Model parameters
            "iterations": self.iterations,
            "parameters_used": parameters,
        }

        return results

    def _generate_samples(
        self,
        parameters: FAIRParameters,
        *,
        secondary_loss_subtractor: float = 0.0,
        vulnerability_multiplier: float = 1.0,
    ) -> dict[str, np.ndarray]:
        """Generate Monte Carlo samples for all parameters.

        Args:
            parameters: FAIR analysis parameters.
            secondary_loss_subtractor: Currency amount subtracted from each
                secondary-loss sample before flooring at 0. Lognormal loss is
                not closed under subtraction — this MUST be applied at the
                sample level, not at the parameter level.
            vulnerability_multiplier: Multiplier applied to each vulnerability
                sample, then clipped to [0, 1]. 0 is allowed (perfect control).
        """

        # Task #23 (Commit A.1): pass the per-instance Generator to every
        # sample() call so draws use this engine's stream, not numpy global.
        samples = {
            "tef": parameters.threat_event_frequency.sample(self.iterations, rng=self._rng),
            "vulnerability": parameters.vulnerability.sample(self.iterations, rng=self._rng),
            "primary_loss": parameters.primary_loss.sample(self.iterations, rng=self._rng),
            "secondary_loss": parameters.secondary_loss.sample(self.iterations, rng=self._rng),
        }

        # Ensure non-negative values for primary loss
        samples["primary_loss"] = np.maximum(samples["primary_loss"], 0)

        # Sample-level currency subtractor: max(0, SL - c).
        # Lognormal SL is not closed under subtraction, so this CANNOT be done
        # at the parameter level. Floor the raw SL at 0 first (defensive), then
        # subtract and floor again.
        samples["secondary_loss"] = np.maximum(
            0.0, np.maximum(samples["secondary_loss"], 0.0) - secondary_loss_subtractor
        )

        # Sample-level vulnerability multiplier then clip to [0, 1].
        # 0 (perfect control) is legitimate — clip(vuln*0, 0, 1) == 0.
        samples["vulnerability"] = np.clip(
            samples["vulnerability"] * vulnerability_multiplier, 0, 1
        )

        # Ensure TEF is non-negative
        samples["tef"] = np.maximum(samples["tef"], 0)

        return samples

    def generate_loss_exceedance_curve(
        self, risk_results: dict[str, Any]
    ) -> tuple[np.ndarray, np.ndarray]:
        """Generate loss exceedance curve for insurance analysis

        Returns:
            Tuple of (loss_values, exceedance_probabilities)
        """
        risk_distribution = risk_results["risk_distribution"]

        # Sort risk values
        sorted_risks = np.sort(risk_distribution)

        # Calculate exceedance probabilities
        n = len(sorted_risks)
        exceedance_probs = np.arange(n, 0, -1) / n

        return sorted_risks, exceedance_probs

    def calculate_var_and_cvar(
        self, risk_results: dict[str, Any], confidence_level: float = 0.95
    ) -> dict[str, float]:
        """Calculate Value at Risk (VaR) and Conditional VaR (CVaR)

        Args:
            risk_results: Results from calculate_risk()
            confidence_level: Confidence level for VaR calculation (default 95%)

        Returns:
            Dictionary with VaR and CVaR values
        """
        risk_distribution = risk_results["risk_distribution"]

        # Calculate VaR (Value at Risk)
        var_percentile = confidence_level * 100
        var = np.percentile(risk_distribution, var_percentile)

        # Calculate CVaR (Conditional Value at Risk / Expected Shortfall)
        # Average of losses exceeding VaR
        excess_losses = risk_distribution[risk_distribution >= var]
        cvar = np.mean(excess_losses) if len(excess_losses) > 0 else var

        return {
            "var": var,
            "cvar": cvar,
            "confidence_level": confidence_level,
            "var_percentile": var_percentile,
        }
