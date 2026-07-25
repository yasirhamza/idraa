"""#sigma-recalibration (PR1 Task 2): re-author the library's within-scenario
loss dispersion from the mis-applied cross-firm envelope sigma onto
WITHIN_SCENARIO_SIGMA_DEFAULT = 1.7 (172 fields total: 18 catastrophic
lognormal + 154 capped PERT, of which 5 are vendor mean-anchored).

Anchor-held rule (docs/reference/within-scenario-sigma-calibration.md §8):
  - 18 catastrophic lognormal fields: mu unchanged (authored median), sigma -> 1.7,
    EXCEPT the two _ANCHOR_OVERRIDES fields (destructive-wiper-nationstate PL/SL),
    whose median is re-anchored per the calibration reference's decision-table
    adjustment (d) -- see that override dict's docstring below.
  - 149 capped non-vendor fields: mu = ln(sqrt(low*high)) held (authored median);
    low/mode/high re-emitted at sigma=1.7, mode == low.
  - 5 capped vendor PL fields (loss_tier == "vendor"): mu = ln(123005.0) - 1.7**2/2
    (mean-anchored to the IC3 2025 BEC per-complaint mean, D11') -- NOT the
    held-median formula; low/mode/high re-emitted at sigma=1.7, mode == low.

Provenance strings (three distinct fail-loud operations -- see Step 2 in the
plan; a single regex is both unsatisfiable across the vendor set and
self-defeating against the guard):
  1. 45 non-vendor `calibration_anchor.loss_anchor` tokens matching
     `sigma=(\\d+\\.\\d+)` -> replaced with the within-scenario-default sentence.
  2. 2 vendor `loss_anchor` tokens (manufacturing-billing-fraud,
     professional-payroll-bec) -> bespoke mean-preserving wording (the generic
     envelope-supersession template would be FALSE for these).
  3. 5 vendor `loss_form_profile[*].magnitude_basis` narratives matching
     `sigma borrowed from the \\w+ envelope` -> replaced with the
     within-scenario-default clause.
  Plus exactly 1 further amendment (not one of the three counted operations):
     the wiper's replaced loss_anchor string gets an appended re-anchoring
     note, since its median no longer derives from the transportation
     envelope (Task-0 override consequence; see _ANCHOR_OVERRIDES).

Extension-JSON rev handling (plan Step 2b, spec item -- verified benign, not
assumed): there is no boot-time seed upsert (seeding is Alembic-only), and all
11 catastrophic entries already carry `loss_shape: "catastrophic"` in both
JSONs and prod, so this builder needs no rev bump and no loss_shape re-assert
-- it only PINS loss_shape presence on the 11 (already true) so the earlier
"capped-on-boot" foot-gun note can never be re-litigated as forgotten.

Emits ensure_ascii=True (the seed builder default; ensure_ascii=False here
would churn every unicode char in the diff, per the TEF/PERT builder
precedents).

One-shot against the pre-recalibration seed state: re-running against
already-converted seeds dies on the old-value guard (no field will match one
of the 11 superseded envelope sigmas any more).

Run: uv run python scripts/build_sigma_recalibration.py
"""

from __future__ import annotations

import json
import math
import re
import sys
from pathlib import Path

_PROJ = Path(__file__).resolve().parent.parent
for p in (_PROJ / "src",):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))
if str(_PROJ) not in sys.path:
    sys.path.insert(0, str(_PROJ))

from tests._loss_shape_helpers import CATASTROPHIC_SLUGS  # noqa: E402

_SEED = _PROJ / "data" / "seed_library_entries.json"
_EXT = _PROJ / "data" / "seed_library_entries_extension.json"

_Z95 = 1.6448536269514722
_SIGMA_TARGET = 1.7

# The 2 vendor entries whose loss_anchor carries an envelope sigma= token; their
# replacement wording is the IC3 mean-anchor form, not the envelope-supersession
# template (see the calibration reference, section 8 / plan Task 2 op 2).
_VENDOR_ANCHOR_SLUGS = frozenset({"manufacturing-billing-fraud", "professional-payroll-bec"})
_IC3_MEAN = 123005.0

_VENDOR_SLUGS = frozenset(
    {
        "agri-coop-bec-fraud",
        "bec-fraud-financial",
        "manufacturing-billing-fraud",
        "professional-payroll-bec",
        "telecom-sim-swap-fraud",
    }
)

# Task-0 carryover, transcribed verbatim from the anchor-review decision table
# (docs/reference/within-scenario-sigma-calibration.md, adjustment (d)): the
# wiper's held median contradicted its own founding rationale (the fat tail it
# exists to model), so its event median is re-anchored to the IRIS 2025
# ransomware type-conditional p50_2024 = $3,200,000, split by the entry's
# existing PL:SL share (416.5:49). Builder converts each to
# mean = round(ln(median), 10); sigma stays 1.7 (same as every other field).
_ANCHOR_OVERRIDES: dict[tuple[str, str], float] = {
    ("destructive-wiper-nationstate", "primary_loss"): 2_863_158.0,
    ("destructive-wiper-nationstate", "secondary_loss"): 336_842.0,
}

# The 11 superseded envelope sigma values (data/loss_form_envelopes.json,
# sigma = ln(p95/p50)/z_0.95) that every catastrophic sigma / capped implied
# sigma must currently equal -- otherwise something unknown touched the field.
_KNOWN_ENVELOPE_SIGMAS = (
    1.8377081683,
    1.9088254741,
    1.9345562423,
    1.9602032156,
    2.2723417799,
    2.3399310679,
    2.4924358626,
    2.6945564938,
    2.8196794735,
    3.2026303573,
    3.4721527617,
)


def _die(msg: str) -> None:
    raise SystemExit(f"build_sigma_recalibration: {msg}")


def _matches_known_envelope_sigma(sigma: float) -> bool:
    return any(math.isclose(sigma, k, abs_tol=1e-6) for k in _KNOWN_ENVELOPE_SIGMAS)


def _emit_capped(mu: float) -> dict:
    low = round(math.exp(mu - _Z95 * _SIGMA_TARGET), 10)
    high = round(math.exp(mu + _Z95 * _SIGMA_TARGET), 10)
    if not (0 < low < high):
        _die(f"bad bounds low={low} high={high} for mu={mu}")
    return {"distribution": "PERT", "low": low, "mode": low, "high": high}


def _load(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    if not (_SIGMA_TARGET > _Z95):
        _die(f"precondition failed: _SIGMA_TARGET {_SIGMA_TARGET} must exceed z_0.95 {_Z95}")

    base = _load(_SEED)
    ext = _load(_EXT)
    entries = base + ext
    by_slug = {e["slug"]: e for e in entries}

    # --- Pins before writing ------------------------------------------------
    cat_seen: set[str] = set()
    vendor_seen: set[str] = set()
    n_catastrophic_fields = 0
    n_capped_fields = 0
    for e in entries:
        slug = e["slug"]
        is_cat = e.get("loss_shape") == "catastrophic"
        if is_cat:
            cat_seen.add(slug)
        if e.get("loss_tier") == "vendor":
            vendor_seen.add(slug)
        for f in ("primary_loss", "secondary_loss"):
            d = e.get(f) or {}
            dist = d.get("distribution")
            if dist == "lognormal":
                n_catastrophic_fields += 1
                if not is_cat:
                    _die(f"{slug}.{f}: lognormal but loss_shape != catastrophic")
            elif dist == "PERT":
                n_capped_fields += 1
                if is_cat:
                    _die(f"{slug}.{f}: PERT but loss_shape == catastrophic")

    if n_catastrophic_fields != 18:
        _die(f"expected 18 catastrophic lognormal fields, got {n_catastrophic_fields}")
    if n_capped_fields != 154:
        _die(f"expected 154 capped PERT fields, got {n_capped_fields}")
    if vendor_seen != _VENDOR_SLUGS:
        _die(f"vendor slug set mismatch: {sorted(vendor_seen)} != {sorted(_VENDOR_SLUGS)}")
    if not cat_seen >= CATASTROPHIC_SLUGS:
        _die(
            f"CATASTROPHIC_SLUGS not all present in seeds: missing {CATASTROPHIC_SLUGS - cat_seen}"
        )
    for slug in CATASTROPHIC_SLUGS:
        if by_slug[slug].get("loss_shape") != "catastrophic":
            _die(f"{slug}: CATASTROPHIC_SLUGS member missing loss_shape=='catastrophic'")

    # --- Old-value guards: every touched field must sit on a known envelope sigma
    for e in entries:
        slug = e["slug"]
        for f in ("primary_loss", "secondary_loss"):
            d = e.get(f) or {}
            dist = d.get("distribution")
            if dist == "lognormal":
                if not _matches_known_envelope_sigma(float(d["sigma"])):
                    _die(f"{slug}.{f}: sigma {d['sigma']} not a known envelope value -- abort")
            elif dist == "PERT":
                implied = math.log(d["high"] / d["low"]) / (2 * _Z95)
                if not _matches_known_envelope_sigma(implied):
                    _die(f"{slug}.{f}: implied sigma {implied} not a known envelope value -- abort")

    # --- Transform -----------------------------------------------------------
    for e in entries:
        slug = e["slug"]
        is_vendor = slug in _VENDOR_SLUGS
        for f in ("primary_loss", "secondary_loss"):
            d = e.get(f)
            if not d:
                continue
            dist = d.get("distribution")
            if dist == "lognormal":
                override = _ANCHOR_OVERRIDES.get((slug, f))
                new_mean = round(math.log(override), 10) if override is not None else d["mean"]
                e[f] = {"distribution": "lognormal", "mean": new_mean, "sigma": _SIGMA_TARGET}
            elif dist == "PERT":
                if is_vendor and f == "primary_loss":
                    mu = math.log(_IC3_MEAN) - _SIGMA_TARGET**2 / 2
                else:
                    mu = math.log(math.sqrt(d["low"] * d["high"]))
                e[f] = _emit_capped(mu)

    # --- Provenance strings ---------------------------------------------------
    envelope_tok = re.compile(r"sigma=(\d+\.\d+)")
    narrative_tok = re.compile(r"sigma borrowed from the \w+ envelope")

    n_envelope_replaced = 0
    n_vendor_anchor_replaced = 0
    n_narrative_replaced = 0
    n_wiper_amendment = 0

    for e in entries:
        slug = e["slug"]
        anchor = e.get("calibration_anchor") or {}
        loss_anchor = str(anchor.get("loss_anchor", ""))
        changed = False

        old_match = envelope_tok.search(loss_anchor)
        if old_match:
            old_value = old_match.group(1)
            if slug in _VENDOR_ANCHOR_SLUGS:
                new_text = (
                    f"sigma=1.7 (within-scenario default; mean-preserved against "
                    f"IC3 $123,005 — prior envelope dispersion {old_value})"
                )
                loss_anchor = envelope_tok.sub(new_text, loss_anchor, count=1)
                n_vendor_anchor_replaced += 1
            else:
                new_text = (
                    f"sigma=1.7 (within-scenario default; prior envelope dispersion "
                    f"{old_value} — see docs/reference/within-scenario-sigma-calibration.md)"
                )
                loss_anchor = envelope_tok.sub(new_text, loss_anchor, count=1)
                n_envelope_replaced += 1
            changed = True

        # Wiper amendment: a DISTINCT, always-applied operation (not gated on the
        # sigma= regex above) -- the wiper's current loss_anchor carries no literal
        # "sigma=" token (verified against the actual seed data; the trouble-watch
        # note's "one of the 45" framing does not hold literally in this data), so
        # this note is appended to whatever the string already is, once, per the
        # Task-0 carryover instruction. Fail-loud count is independent of n_envelope_replaced.
        if slug == "destructive-wiper-nationstate":
            loss_anchor += (
                " — medians re-anchored to IRIS 2025 Fig 15 ransomware p50 $3.2M "
                "(event, PL:SL share-split; see the calibration reference, "
                "adjustment d)"
            )
            n_wiper_amendment += 1
            changed = True

        if changed:
            anchor["loss_anchor"] = loss_anchor
            e["calibration_anchor"] = anchor

        for lf in e.get("loss_form_profile") or []:
            basis = str(lf.get("magnitude_basis", ""))
            if narrative_tok.search(basis):
                lf["magnitude_basis"] = narrative_tok.sub(
                    "sigma = the within-scenario default 1.7 (mean preserved exactly; "
                    "see docs/reference/within-scenario-sigma-calibration.md)",
                    basis,
                )
                n_narrative_replaced += 1

    if n_envelope_replaced != 45:
        _die(f"expected 45 non-vendor envelope loss_anchor replacements, got {n_envelope_replaced}")
    if n_vendor_anchor_replaced != 2:
        _die(f"expected 2 vendor loss_anchor replacements, got {n_vendor_anchor_replaced}")
    if n_narrative_replaced != 5:
        _die(
            f"expected 5 vendor magnitude_basis narrative replacements, got {n_narrative_replaced}"
        )
    if n_wiper_amendment != 1:
        _die(f"expected exactly 1 wiper provenance amendment, got {n_wiper_amendment}")

    # --- Write back ------------------------------------------------------------
    for group, path in ((base, _SEED), (ext, _EXT)):
        path.write_text(json.dumps(group, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")

    print(
        f"re-authored {n_catastrophic_fields}/{n_capped_fields - 5}/5 fields "
        f"(catastrophic/capped-non-vendor/capped-vendor); "
        f"strings {n_envelope_replaced}/{n_vendor_anchor_replaced}/{n_narrative_replaced} "
        f"(envelope/vendor-anchor/narrative) + {n_wiper_amendment} wiper amendment"
    )


if __name__ == "__main__":
    main()
