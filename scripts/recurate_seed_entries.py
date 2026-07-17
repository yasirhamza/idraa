# ruff: noqa: RUF001,RUF002,RUF003
"""INPUTS FROZEN AT C-iii-a — do not extend for C-iii-b or later content; the script maps only `keep` archetypes and must SKIP entries without an archetype mapping.

Dev-only: re-curate all 44 seed library entries per Epic C-iii-a rules 1-8.

Implements the conversion rules from
  docs/superpowers/plans/2026-06-10-epic-c-iii-a-existing-content-recuration.md
  Task 3 — "Conversion rules (the script implements EXACTLY these)".

Run from the repo root only:
  python scripts/recurate_seed_entries.py

Relative ``data/…`` paths are intentional — same convention as
``scripts/audit_library_vulnerability.py``.

IDEMPOTENCY GUARD: for each entry, if ``primary_loss.distribution`` is already
``"lognormal"`` the primary/secondary loss conversion step is SKIPPED (only
``loss_tier`` / ``calibration_anchor`` provenance keys are refreshed). This
guards against double-conversion corrupting rule-3's ``existing_secondary_mode``
lookup (which must read a PERT ``mode``, never a lognormal ``mean``).

INPUTS (all committed artifacts — no runtime discovery):
  data/loss_anchor_tables.json   — 82 verified anchor rows (C-ii-b output)
  data/target_archetypes.json    — 82 rows, 44 with keep_or_new="keep"
  data/seed_library_entries.json         — 31 base seed entries (rewritten in-place)
  data/seed_library_entries_extension.json — 13 extension entries (rewritten in-place)

OUTPUT FORMAT (pinned): json.dumps(data, indent=2, ensure_ascii=False) + "\\n"

RULES IMPLEMENTED:
  1. Map existing_slug → archetype → anchor row in loss_anchor_tables.json.
  2. Schema precondition: calibration_anchor extended with loss_anchor/vuln_posture
     (Step 1 commit landed this; rules 3–8 populate these provenance fields).
  3. quantile_pair anchor → lognormal conversion:
       primary: {distribution: lognormal, mean: ln(p50), sigma: ln(p95/p50)/Z_0_95}
       secondary: same sigma, mean = ln(p50 × existing_secondary_mode/existing_primary_mode)
       loss_tier = "paginated"; citations replaced with anchor's, serialized as
       "{source}, {locator} (accessed {accessed})".
  4. multiplier_over_baseline anchor (verified) → lognormal:
       baseline lookup: collect quantile_pair rows for baseline_sector,
       assert all share same (p50, p95); derive baseline_sigma.
       primary: {distribution: lognormal, mean: ln(baseline_p50 × multiplier), sigma: baseline_sigma}
       loss_tier = "vendor".
  5. none anchor → values UNTOUCHED; loss_tier = "anecdotal".
  6. vuln_posture: every entry gets calibration_anchor.vuln_posture.
  7. credential-stuffing-consumer-portal: TEF reinterpreted to campaign-level
     {low:1, mode:5, high:20}; vuln raised to {0.10, 0.30, 0.60};
     canonical_fair_gap rewritten to campaign framing; rule 3 applies for loss.
  8. bec-fraud-financial: vuln raised to {0.05, 0.20, 0.45} (control-naive).
  9. generative-ai-prompt-injection (#114): stays PERT/anecdotal; tier recorded.

SECURITY CARRY-FORWARD (S-I2):
  Citations written by this script are plain-text strings stored in
  source_citations[]. No href rendering exists or is added here.
  The https-only URL-scheme allowlist obligation (S-I1) is now IMPLEMENTED
  (2026-06-12): gate is `idraa.formatting.linkify_https` (explicit
  `urlsplit` scheme == "https" + non-empty netloc check), applied in
  `templates/library/entry_detail.html`; regression tests in
  `tests/unit/test_formatting_linkify.py` +
  `tests/integration/test_library_routes.py`. Issue #349 closed.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

Z_0_95 = 1.6448536269514722  # scipy.stats.norm.ppf(0.95) — pinned literal

VULN_POSTURE_NOTE = "inherent (control-naive) per fair-cam-methodology 'Vulnerability anchor'"

# Slugs with special-case semantic re-curation (rule 6/7)
_CREDENTIAL_STUFFING_SLUG = "credential-stuffing-consumer-portal"
_BEC_FRAUD_SLUG = "bec-fraud-financial"


def _load_json(path: str) -> object:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _dump_json(data: object, path: str) -> None:
    Path(path).write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _serialize_citation(cite: dict) -> str:
    """Serialize one citation object to the canonical string form.

    Exact format: "{source}, {locator} (accessed {accessed})"
    This guarantees the combined source_citations string contains both the
    "IRIS 2025" and "Figure A3" guard tokens (Task-1-updated guard checks both).
    """
    return f"{cite['source']}, {cite['locator']} (accessed {cite['accessed']})"


def _build_anchor_lookup(anchor_rows: list[dict]) -> dict[str, dict]:
    return {row["archetype"]: row for row in anchor_rows}


def _baseline_pair_for_sector(anchor_rows: list[dict], baseline_sector: str) -> tuple[float, float]:
    """Return (p50, p95) baseline pair for the given sector from quantile_pair rows.

    Rule 4 algorithm: collect quantile_pair rows where sector == baseline_sector;
    ASSERT all share the same (p50, p95); return that pair.
    Hard-fails if no rows found or if rows disagree on the pair.
    """
    qp_rows = [
        r
        for r in anchor_rows
        if r["anchor_type"] == "quantile_pair" and r["sector"] == baseline_sector
    ]
    if not qp_rows:
        raise AssertionError(f"No quantile_pair rows found for baseline_sector={baseline_sector!r}")
    p50s = {r["p50"] for r in qp_rows}
    p95s = {r["p95"] for r in qp_rows}
    if len(p50s) != 1 or len(p95s) != 1:
        raise AssertionError(
            f"quantile_pair rows for sector={baseline_sector!r} disagree on "
            f"(p50, p95): got p50s={sorted(p50s)}, p95s={sorted(p95s)}"
        )
    return (float(p50s.pop()), float(p95s.pop()))


def _apply_credential_stuffing_special_case(entry: dict, *, already_lognormal: bool) -> None:
    """Rule 7: reinterpret credential-stuffing from per-attempt to campaign-level model.

    Method:
    - TEF: {low:1, mode:5, high:20} (campaign/year, inherent posture, Akamai-grounded)
    - Vuln: {low:0.10, mode:0.30, high:0.60} (campaign-level material-incident rate)
    - canonical_fair_gap: rewritten to campaign-level framing
    - Loss: rule 3 applies normally via the Retail quantile_pair anchor, BUT with
      secondary R overridden to campaign-level (see _override_secondary_R below).

    Rationale (methodology-gated, plan-gate round 1):
    Old TEF {5, 50, 500} × vuln {0.001, 0.005, 0.02} ≈ {0.005, 0.25, 10} loss events/yr
    at per-attempt semantics. With Retail anchor p50=$746K: incoherent scale.
    Campaign reinterpretation: TEF = campaigns/year for a major consumer portal
    at inherent posture, consistent with Akamai State-of-the-Internet campaign-
    frequency reporting. Loss semantics = per-material-incident via Retail anchor.
    #113 closes on TEF coherence grounds.

    Secondary R override rationale (T3M-B1 fix):
    The pre-curation PERT secondary mode=$100K / primary mode=$500 gave a per-ATO ratio
    R=200 (per-ATO semantics: $100K regulatory/fraud exposure per $500 account loss).
    After rule 7 reinterpreted primary loss to campaign-level (Retail anchor p50=$746K),
    that per-ATO R=200 is semantically inapplicable — it would yield median secondary
    ≈$149M per campaign, which has no citation support at campaign scale.
    Analyst-judged campaign-level R=1.0: secondary losses (regulatory notification,
    per-record fines, class-action exposure, churn) are same order of magnitude as
    direct remediation at campaign level. R=1.0 matches the closest library analogue
    data-breach-notification-regulatory-tail (R=1.0); no real-incident citation justifies
    larger R at campaign scale. The override is injected here so rule 3 reads it before
    computing R from PERT modes.

    Idempotency: the ``_override_secondary_R`` sentinel is only injected when the entry
    has not yet been converted (``already_lognormal=False``).  On a re-run the entry is
    already lognormal so the injection is skipped — the loss block never runs and there
    is nothing to read or clean up.
    """
    # analyst-judged campaign-level; per-ATO R=200 inapplicable after reinterpretation.
    # Guard: only inject when conversion is still pending — idempotency on re-run.
    if not already_lognormal:
        entry["_override_secondary_R"] = 1.0

    entry["threat_event_frequency"] = {
        "distribution": "PERT",
        "low": 1,
        "mode": 5,
        "high": 20,
    }
    entry["vulnerability"] = {
        "distribution": "PERT",
        "low": 0.10,
        "mode": 0.30,
        "high": 0.60,
    }
    entry["canonical_fair_gap"] = (
        "FAIR does not natively distinguish a credential-stuffing campaign from a discrete "
        "unauthorized-access incident; the campaign-level model used here treats each "
        "credential-stuffing campaign as a threat event (TEF in campaigns/year) that, if "
        "it achieves material account takeover, produces a per-incident loss event (via the "
        "Retail sector loss anchor). This framing aligns with Akamai's campaign-frequency "
        "reporting and avoids the prior per-attempt model where the extremely-low per-attempt "
        "vulnerability collapsed the loss event frequency to near-zero — incoherent with the "
        "Retail per-incident anchor magnitude. The model is inherent-posture (control-naive): "
        "controls (rate-limiting, credential monitoring, MFA) reduce either the campaign "
        "success rate (vuln) or the incident frequency (TEF), but are not embedded in the "
        "seed values."
    )


def _apply_bec_fraud_vuln(entry: dict) -> None:
    """Rule 8 (bec-fraud-financial): raise vuln to control-naive inherent level.

    Old vuln mode=0.08 was curated at controlled/typical posture (with email controls
    in place). Under the canonical inherent frame: financial staff without email
    security controls (DMARC enforcement, anti-phishing training, multi-approver
    payment processes) have higher raw susceptibility to a well-crafted BEC. The
    new values {0.05, 0.20, 0.45} reflect analyst judgment for an inherent posture
    in a financial-services context. Methodology-gated (plan-gate round 1).
    """
    entry["vulnerability"] = {
        "distribution": "PERT",
        "low": 0.05,
        "mode": 0.20,
        "high": 0.45,
    }


def _convert_entry(
    entry: dict,
    archetype_row: dict,
    anchor_row: dict,
    anchor_rows: list[dict],
) -> None:
    """Apply conversion rules 1–8 in-place to a single seed entry.

    The idempotency guard: if primary_loss.distribution is already "lognormal",
    skip the loss conversion (only refresh tier and calibration_anchor provenance).
    """
    slug = entry["slug"]
    anchor_type = anchor_row["anchor_type"]
    already_lognormal = (
        str(entry.get("primary_loss", {}).get("distribution", "")).lower() == "lognormal"
    )

    # ── Rule 6: vuln_posture for every entry (applied unconditionally) ──────
    ca = dict(entry.get("calibration_anchor") or {})
    ca["vuln_posture"] = VULN_POSTURE_NOTE

    # ── Rule 7: credential-stuffing special case ─────────────────────────────
    if slug == _CREDENTIAL_STUFFING_SLUG:
        _apply_credential_stuffing_special_case(entry, already_lognormal=already_lognormal)

    # ── Rule 8 (rule 6 extension): bec-fraud-financial vuln re-curation ──────
    if slug == _BEC_FRAUD_SLUG:
        _apply_bec_fraud_vuln(entry)

    # ── Rules 3–5: loss distribution + tier by anchor type ──────────────────
    if anchor_type == "quantile_pair":
        p50 = float(anchor_row["p50"])
        p95 = float(anchor_row["p95"])
        sigma = math.log(p95 / p50) / Z_0_95
        entry["loss_tier"] = "paginated"

        R = None  # noqa: N806 — R is the conventional name for secondary/primary ratio per plan
        _r_was_overridden = False
        if not already_lognormal:
            # Check for analyst override BEFORE computing R from PERT modes; override takes priority.
            _r_override = entry.pop("_override_secondary_R", None)

            # Capture existing secondary/primary PERT ratio BEFORE overwriting PL
            existing_pl = entry.get("primary_loss") or {}
            existing_sl = entry.get("secondary_loss")
            existing_primary_mode = float(existing_pl.get("mode", 0))
            if existing_sl and existing_primary_mode:
                existing_secondary_mode = float(existing_sl.get("mode", 0))
                R = existing_secondary_mode / existing_primary_mode  # noqa: N806

            # Override takes priority over mode-ratio computation
            if _r_override is not None:
                R = _r_override  # noqa: N806
                _r_was_overridden = True

            # Primary loss: lognormal with mean=ln(p50), sigma=ln(p95/p50)/Z_0_95
            entry["primary_loss"] = {
                "distribution": "lognormal",
                "mean": math.log(p50),
                "sigma": sigma,
            }

            # Secondary loss: same sigma, location scaled by curated ratio R
            if existing_sl is not None and R is not None:
                mean_secondary = math.log(p50 * R)
                entry["secondary_loss"] = {
                    "distribution": "lognormal",
                    "mean": mean_secondary,
                    "sigma": sigma,
                }

        # Citation replacement: anchor citations serialized as "{source}, {locator} (accessed {accessed})"
        # Non-loss citations appended untouched (superseded per-scenario loss ref moves to note).
        anchor_cites = anchor_row.get("citations") or []
        cite_strs = [_serialize_citation(c) for c in anchor_cites]
        entry["source_citations"] = cite_strs

        # loss_anchor note: on idempotent re-run, R is None (conversion already done).
        # For the rule-7 override case, the existing loss_anchor already records the
        # correct provenance ("rule 7 override"); we must NOT overwrite it with the
        # generic "idempotent refresh" text — that would silently erase the override
        # rationale.  Guard: skip regenerating the note when it already contains the
        # override token.  ``ca`` already holds the existing loss_anchor value (loaded
        # at the top of _convert_entry), so the final ``entry["calibration_anchor"] = ca``
        # write will preserve it as-is.
        sector = anchor_row.get("sector", "unknown")
        _existing_loss_anchor = ca.get("loss_anchor", "")
        _preserve_existing_note = (
            already_lognormal
            and entry.get("secondary_loss") is not None
            and "rule 7 override" in _existing_loss_anchor
        )
        if _preserve_existing_note:
            # Re-run on an already-converted rule-7 entry: the existing note is correct;
            # leave ca["loss_anchor"] untouched (idempotency for the override provenance).
            pass
        elif R is not None and _r_was_overridden:
            ca["loss_anchor"] = (
                f"IRIS 2025 Figure A3 p.35 {sector} pair (p50=${p50:,.0f}, p95=${p95:,.0f}); "
                f"supersedes prior per-scenario analyst-judged PERT anchor. "
                f"sigma={sigma:.10f}. "
                f"secondary sigma inherited from primary; location scaled by analyst-judged "
                f"campaign-level secondary/primary ratio R={R:.10f} "
                f"(rule 7 override: pre-curation per-ATO R=200 semantically inapplicable "
                f"after campaign reinterpretation)"
            )
        elif R is not None:
            ca["loss_anchor"] = (
                f"IRIS 2025 Figure A3 p.35 {sector} pair (p50=${p50:,.0f}, p95=${p95:,.0f}); "
                f"supersedes prior per-scenario analyst-judged PERT anchor. "
                f"sigma={sigma:.10f}. "
                f"secondary sigma inherited from primary; location scaled by curated secondary/primary ratio R={R:.10f}"
            )
        elif entry.get("secondary_loss") is not None:
            # Idempotent path: entry already lognormal with secondary loss, R not set
            # (conversion was done on a prior run).  Back-compute R from the stored
            # secondary mean so the note records the same spec-format provenance as
            # the first-run path, making the output byte-identical on every re-run.
            _sl_mean = entry["secondary_loss"]["mean"]
            _back_R = math.exp(_sl_mean) / p50  # noqa: N806
            ca["loss_anchor"] = (
                f"IRIS 2025 Figure A3 p.35 {sector} pair (p50=${p50:,.0f}, p95=${p95:,.0f}); "
                f"supersedes prior per-scenario analyst-judged PERT anchor. "
                f"sigma={sigma:.10f}. "
                f"secondary sigma inherited from primary; location scaled by curated secondary/primary ratio R={_back_R:.10f}"
            )
        else:
            ca["loss_anchor"] = (
                f"IRIS 2025 Figure A3 p.35 {sector} pair (p50=${p50:,.0f}, p95=${p95:,.0f}); "
                f"supersedes prior per-scenario analyst-judged PERT anchor. "
                f"sigma={sigma:.10f}. "
                f"no secondary loss"
            )

    elif anchor_type == "multiplier_over_baseline":
        # Rule 4: verified multiplier_over_baseline — look up baseline pair
        baseline_sector = anchor_row["baseline_sector"]
        multiplier = float(anchor_row["multiplier"])
        baseline_p50, baseline_p95 = _baseline_pair_for_sector(anchor_rows, baseline_sector)
        baseline_sigma = math.log(baseline_p95 / baseline_p50) / Z_0_95
        entry["loss_tier"] = "vendor"

        if not already_lognormal:
            derived_p50 = baseline_p50 * multiplier
            entry["primary_loss"] = {
                "distribution": "lognormal",
                "mean": math.log(derived_p50),
                "sigma": baseline_sigma,
            }
            # Secondary loss: rule 4 specifies only the primary conversion.
            # Keep existing secondary distribution (PERT) — not mentioned in rule 4.

        anchor_cites = anchor_row.get("citations") or []
        cite_strs = [_serialize_citation(c) for c in anchor_cites]
        entry["source_citations"] = cite_strs
        ca["loss_anchor"] = (
            f"Baseline: {baseline_sector} quantile_pair (p50=${baseline_p50:,.0f}, "
            f"p95=${baseline_p95:,.0f}, sigma={baseline_sigma:.10f}) × multiplier={multiplier}. "
            f"Derived p50=${baseline_p50 * multiplier:,.0f}; "
            f"supersedes prior per-scenario analyst-judged PERT anchor."
        )

    elif anchor_type == "none":
        # Rule 5: no anchor — values untouched, tier = anecdotal
        entry["loss_tier"] = "anecdotal"
        ca["loss_anchor"] = "no citeable anchor (C-ii-b sweep) — analyst-judged PERT retained"

    # Belt-and-braces: ensure the private sentinel never leaks into serialized JSON.
    # Normally it is popped inside the `if not already_lognormal:` block above;
    # this unconditional pop is a safety net for any future code path that might
    # set the key without consuming it.
    entry.pop("_override_secondary_R", None)

    # Write back calibration_anchor with provenance keys
    entry["calibration_anchor"] = ca


def recurate_all(
    base_path: str = "data/seed_library_entries.json",
    extension_path: str = "data/seed_library_entries_extension.json",
    anchors_path: str = "data/loss_anchor_tables.json",
    archetypes_path: str = "data/target_archetypes.json",
) -> tuple[list[dict], list[dict]]:
    """Apply re-curation rules to both seed files and return (base, extension).

    Modifies the loaded data in-place; caller is responsible for writing back.
    """
    base_entries: list[dict] = list(_load_json(base_path))  # type: ignore[arg-type]
    ext_entries: list[dict] = list(_load_json(extension_path))  # type: ignore[arg-type]
    anchor_rows: list[dict] = list(_load_json(anchors_path))  # type: ignore[arg-type]
    archetype_rows: list[dict] = list(_load_json(archetypes_path))  # type: ignore[arg-type]

    anchor_lookup = _build_anchor_lookup(anchor_rows)
    archetype_by_slug = {
        row["existing_slug"]: row for row in archetype_rows if row.get("keep_or_new") == "keep"
    }

    for entry in base_entries + ext_entries:
        slug = entry["slug"]
        archetype_row = archetype_by_slug.get(slug)
        if archetype_row is None:
            # Not a keep entry — skip (new entries handled in C-iii-b)
            continue
        arch = archetype_row["archetype"]
        anchor_row = anchor_lookup.get(arch)
        if anchor_row is None:
            raise AssertionError(f"No anchor row found for archetype={arch!r} (slug={slug!r})")
        _convert_entry(entry, archetype_row, anchor_row, anchor_rows)

    return base_entries, ext_entries


def main() -> None:  # pragma: no cover
    base, ext = recurate_all()
    _dump_json(base, "data/seed_library_entries.json")
    _dump_json(ext, "data/seed_library_entries_extension.json")
    print(f"Re-curated {len(base)} base + {len(ext)} extension entries.")
    print("data/seed_library_entries.json written.")
    print("data/seed_library_entries_extension.json written.")


if __name__ == "__main__":  # pragma: no cover
    main()
