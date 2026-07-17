"""Epic B #326 Task 8 + Epic C-i #335 Task 6 + Epic C-iii-a: permanent guard on
seeded lognormal loss nodes.

Guard invariants (as of Epic C-iii-a):

(a) Any lognormal loss node ⇒ loss_tier ∈ {paginated, vendor}.
    A lognormal on an anecdotal/none/absent-tier entry is a methodology error
    (no citation anchor exists to back both σ legs of the derivation).

(b) loss_tier ∈ {anecdotal, none} or absent ⇒ NO lognormal loss node (must be
    PERT). Converse of (a); also catches entries that gained a lognormal node
    without being re-tiered.

(c) Any lognormal node: finite mean + 0 < sigma <= 10 (Epic B validity check,
    unchanged).

(d) BOTH-LEGS citation check, now TIER-SCOPED (Epic C-i restructure,
    plan-gate meth-#1):

    loss_tier == "paginated": both σ legs trace to an IRIS 2025 Figure A3 p95
        citation AND a named p50 primary (NetDiligence / FFIEC / Verizon DBIR /
        IRIS 2025).  Epic C-iii-a broadened the p95 token from the old
        "IRIS 2025 Figure 12" to the two-token form ("IRIS 2025" ∧ "Figure A3")
        to match all 18 re-anchored sector-table entries.

    loss_tier == "vendor": both σ legs trace to the entry's OWN vendor
        citation(s) in source_citations.  Requirement: source_citations is
        non-empty and at least one citation present (the exact vendor-token
        allowlist is a C-iii carryover; see note below).

        C-iii carryover note: the hard-coded paginated p50 tokens
        ("NetDiligence", "FFIEC", "Verizon DBIR", "IRIS 2025") are
        Epic-B/C-iii-a manufacturing/healthcare/etc specific.  C-iii will
        broaden the vendor token set as new cited lognormal entries land (IC3,
        IBM CODB, Advisen, Marsh McLennan, etc.).  At that point, a
        _VENDOR_PRIMARY_TOKENS allowlist will replace the non-empty-cites
        sentinel used here.

Today (Epic B + C-i + C-iii-a) NO lognormal entries are seeded (per-scenario
lognormal curation deferred to C-iii), so this test passes VACUOUSLY.  That is
intentional and permanent: the loop fires the moment any future seed (e.g. Epic
C-iii) lands a lognormal loss node, and rejects it unless both the tier is set
correctly AND both citation legs are present.  The test must therefore tolerate
zero lognormal entries without failing — it does NOT require any lognormal entry
to exist.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

_SEED_FILES = (
    "data/seed_library_entries.json",
    "data/seed_library_entries_extension.json",
)

# Tiers that are allowed to carry a lognormal loss node (both legs cited).
_LOGNORMAL_ALLOWED_TIERS = {"paginated", "vendor"}

# Tiers (or absent loss_tier) that MUST use PERT — no lognormal permitted.
_PERT_ONLY_TIERS = {"anecdotal", "none"}

# ── paginated-tier: named p50 primary tokens ─────────────────────────────────
# One of these must back the p50 leg when loss_tier == "paginated":
#   manufacturing -> IRIS 2025 Figure A3, p. 35 (Epic C-iii-a: pure paginated,
#                    supersedes prior NetDiligence mixed-source anchor)
#   healthcare    -> IRIS 2025 Figure A3, p. 35 (sector median $557K)
#   all other 16  -> IRIS 2025 Figure A3, p. 35 (sector medians from C-iii-a)
# (FFIEC is retained in the allowlist only for forward-compat; FINANCIAL is now
# re-anchored to IRIS 2025 Figure A3 for both legs.)
_P50_PRIMARY_TOKENS = ("NetDiligence", "FFIEC", "Verizon DBIR", "IRIS 2025")


def _load_seed_entries() -> list[dict]:
    entries: list[dict] = []
    for f in _SEED_FILES:
        entries.extend(json.loads(Path(f).read_text(encoding="utf-8")))
    return entries


def test_lognormal_library_seed_tier_and_citation_consistency() -> None:
    """Tier↔citation consistency guard for all seeded lognormal loss nodes.

    Passes vacuously when no lognormal entries are seeded (e.g. today, post
    Epic B + C-i).  Fires on the first C-iii lognormal seed that violates any
    of the four invariants above.
    """
    for entry in _load_seed_entries():
        slug = entry["slug"]
        # Treat absent loss_tier as "anecdotal" (back-compat: pre-C-i seeds
        # have no loss_tier key; the ORM server_default is also "anecdotal").
        loss_tier: str = entry.get("loss_tier") or "anecdotal"

        for node in ("threat_event_frequency", "primary_loss", "secondary_loss"):
            d = entry.get(node)
            if not isinstance(d, dict):
                continue
            if str(d.get("distribution", "")).lower() != "lognormal":
                # (b) non-lognormal nodes: if loss_tier is anecdotal/none/absent
                # this is fine (PERT expected). No assertion needed here — (b) is
                # enforced by checking lognormal ⇒ allowed tier below (its contrapositive).
                continue

            # ── We have a lognormal node: enforce (a), (c), (d) ──────────────

            # (a) lognormal ⇒ loss_tier ∈ {paginated, vendor}
            assert loss_tier in _LOGNORMAL_ALLOWED_TIERS, (
                f"{slug}.{node}: lognormal distribution requires loss_tier in "
                f"{_LOGNORMAL_ALLOWED_TIERS}; got {loss_tier!r}.  "
                "Re-tier the entry or change the distribution to PERT."
            )

            # (c) validity: finite mean, 0 < sigma <= 10.
            assert math.isfinite(d["mean"]), f"{slug}.{node}: non-finite mean"
            assert 0 < d["sigma"] <= 10, f"{slug}.{node}: sigma {d['sigma']} out of (0, 10]"

            cites = " ".join(entry.get("source_citations") or [])

            # (d) TIER-SCOPED both-legs citation check.
            if loss_tier == "paginated":
                # paginated tier: IRIS 2025 Figure A3 (p95 leg) + named industry
                # p50 primary.  MOVE from unconditional to paginated-only
                # (Epic C-i restructure) so that vendor-tier lognormals in
                # C-iii are not wrongly rejected here.
                # Epic C-iii-a: replaced the old "IRIS 2025 Figure 12" single-
                # token check with the punctuation-robust two-token form so all
                # 18 re-anchored sector-table entries pass without exact-string
                # coupling to "Figure 12" vs "Figure A3".
                assert "IRIS 2025" in cites and "Figure A3" in cites, (
                    f"{slug}.{node}: paginated-tier p95 leg not traced to "
                    f"'IRIS 2025 … Figure A3' (found cites: {cites!r})"
                )
                assert any(tok in cites for tok in _P50_PRIMARY_TOKENS), (
                    f"{slug}.{node}: paginated-tier p50 leg not traced to a "
                    f"named primary (one of {_P50_PRIMARY_TOKENS}); "
                    f"found cites: {cites!r}"
                )

            elif loss_tier == "vendor":
                # vendor tier: both σ legs must trace to the entry's OWN
                # vendor citation(s).  For now we assert source_citations is
                # non-empty (a necessary condition).  The per-vendor token
                # allowlist (_VENDOR_PRIMARY_TOKENS) is a C-iii carryover —
                # see module docstring.
                assert entry.get("source_citations"), (
                    f"{slug}.{node}: vendor-tier lognormal requires at least "
                    "one vendor citation in source_citations"
                )

    # (b) contrapositive: entries with anecdotal/none/absent loss_tier must
    # carry NO lognormal node.
    for entry in _load_seed_entries():
        slug = entry["slug"]
        loss_tier = entry.get("loss_tier") or "anecdotal"
        if loss_tier in _PERT_ONLY_TIERS:
            for node in ("threat_event_frequency", "primary_loss", "secondary_loss"):
                d = entry.get(node)
                if not isinstance(d, dict):
                    continue
                dist = str(d.get("distribution", "")).lower()
                assert dist != "lognormal", (
                    f"{slug}.{node}: loss_tier={loss_tier!r} entries must use "
                    "PERT, not lognormal.  Either upgrade the loss_tier or "
                    "revert the distribution."
                )

    # NOTE: this test deliberately does NOT assert that any lognormal entry
    # exists.  No lognormal entries are seeded today (post Epic B + C-i) —
    # per-scenario lognormal curation is deferred to Epic C-iii.  The guard
    # remains permanently armed: it rejects any future lognormal seed that
    # violates tier↔citation consistency.


def test_loss_form_profile_consistency() -> None:
    """Epic D-i (#497 §7): when an entry carries a loss_form_profile, it is
    well-formed and consistent with its stored loss nodes.  Tolerant of empty
    profiles (D-i seeds none; D-iii populates).  Fires on the first populated
    entry."""
    for entry in _load_seed_entries():
        profile = entry.get("loss_form_profile") or []
        if not profile:
            continue  # D-i state -- nothing to check yet
        # (form, kind) is set-like -- no duplicates (security NTH; makes the
        # DTO max_length=12 unreachable by duplicate padding).
        seen_form_kind = {(f["form"], f["kind"]) for f in profile}
        assert len(seen_form_kind) == len(profile), (
            f"{entry['slug']}: duplicate (form, kind) in loss_form_profile"
        )
        for form in profile:
            assert form["kind"] in ("primary", "secondary")
            assert form["composition_role"] in (
                "dominant",
                "contributing",
                "provenance_only",
            )
            # Epic D-iii Amendment A1: envelope-share forms are analyst-judged
            # (vulnerability-grade) with NO per-form citation — the cited anchor is
            # the sector envelope on the entry's source_citations/loss_tier, not the
            # share. (Supersedes the D-i per-form citation+verified requirement.)
            # A share-bearing form carries a `share` in (0,1]; a beyond-envelope form
            # (BEC/IP own distribution) has share=None + its own cited magnitude_basis.
            if form.get("share") is not None:
                assert 0.0 < form["share"] <= 1.0, (
                    f"{entry['slug']}: share out of (0,1] for {form['form']}"
                )
        # A1 coherence bound: Σ(shares of in-envelope forms) ≤ 1 per entry.
        share_sum = sum(f["share"] for f in profile if f.get("share") is not None)
        assert share_sum <= 1.0 + 1e-9, (
            f"{entry['slug']}: Σ(shares)={share_sum:.3f} exceeds 1 (A1 coherence bound)"
        )
        # if the entry has a lognormal loss node, it must have >=1 matching-side form
        for side, kind in (("primary_loss", "primary"), ("secondary_loss", "secondary")):
            node = entry.get(side) or {}
            if node.get("distribution") == "lognormal":
                assert any(f["kind"] == kind for f in profile), (
                    f"{entry['slug']}: lognormal {side} without a {kind} loss form"
                )
