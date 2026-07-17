"""Schema + tier-honesty guard for data/loss_form_source_corpus.json (Epic D-ii-a)."""

from __future__ import annotations

import json
from pathlib import Path

_FORMS = {"productivity", "response", "replacement", "fines", "competitive_advantage", "reputation"}
_TIERS = {"paginated", "vendor", "none"}
# gap-report mis-cited seed citations — FORBIDDEN as corpus/attestation sources
# (docs/analysis/2026-07-05-attack-library-gap-report.md "Data-quality byproduct"; meth M2)
_FORBIDDEN_CITES = {
    "FBI PSA I-091019-PSA",  # agri-coop-bec-fraud — claim not in that PSA
    "DOJ 15-1433",  # crop-science-ip-exfiltration — resolves to unrelated case
    "CISA AA22-186A",  # education-research-ip-exfiltration — unconfirmable
    "PREPA/AMI",  # energy-billing-system-tamper — physical, not network
}


def _rows() -> list[dict]:
    return json.loads(Path("data/loss_form_source_corpus.json").read_text(encoding="utf-8"))


def test_every_row_has_required_shape() -> None:
    for r in _rows():
        assert r["form"] in _FORMS, f"bad form {r.get('form')!r}"
        assert r["loss_tier"] in _TIERS
        assert isinstance(r["carries"], str) and r["carries"]
        assert isinstance(r["blended_headline"], bool)  # meth M1
        if r["loss_tier"] == "none":
            # honest gap row: no source/locator/sectors_covered (they may be
            # null/empty) — the shape test must not force a fabricated source or
            # locator onto a documented form-gap (A1/M4/M2).
            continue
        assert isinstance(r["source"], str) and r["source"]
        assert isinstance(r["locator"], str) and r["locator"]
        assert isinstance(r["sectors_covered"], list) and r["sectors_covered"]


def test_every_form_is_represented() -> None:
    # Every form must appear as EITHER a real (paginated/vendor) source OR an
    # explicit loss_tier:"none" gap row — so no form is silently omitted. It does
    # NOT force a real source per form: competitive_advantage/reputation legitimately
    # have no clean repo source and honestly escalate to D-ii-b via a none row
    # (meth M2 — fail-and-escalate, never fake). The none rows ARE the D-ii-b
    # loss-magnitude worklist for form-level anchors.
    seen = {r["form"] for r in _rows()}
    assert seen >= _FORMS, (
        f"forms not represented at all (need a source or a none gap-row): {_FORMS - seen}"
    )


def test_paginated_rows_have_a_page_or_figure_locator() -> None:
    for r in _rows():
        if r["loss_tier"] == "paginated":
            loc = r["locator"].lower()
            assert any(t in loc for t in ("p.", "page", "fig", "table", "exhibit")), (
                f"{r['source']}: paginated tier needs a figure/table/page locator, got {r['locator']!r}"
            )


def test_vendor_rows_have_permalink_and_accessed() -> None:
    # symmetric tier-2 guard (meth NTH): a vendor row needs a permalink locator
    # + an accessed date, so it can't pass as tier-2 on a vague name alone.
    for r in _rows():
        if r["loss_tier"] == "vendor":
            assert r["locator"].startswith("http"), (
                f"{r['source']}: vendor tier needs a permalink locator, got {r['locator']!r}"
            )
            assert r.get("accessed"), f"{r['source']}: vendor tier needs an accessed date"


def test_blended_headline_rows_name_a_single_form_slice() -> None:
    # meth M1: a source whose headline blends >1 form (IRIS sector pair, CODB
    # total-cost-of-breach / lost-business) MUST name the single-form slice it is
    # being catalogued for in `single_form_slice`, so D-ii-b cannot pull the same
    # blended number into two different forms and let D-iii double-count it in
    # Fenton-Wilkinson.
    for r in _rows():
        if r["blended_headline"]:
            assert r.get("single_form_slice"), (
                f"{r['source']} ({r['form']}): blended-headline source must name the "
                "single-form slice used (not the blended total)"
            )


def test_no_forbidden_citation_in_corpus() -> None:
    # meth M2: the four gap-report mis-cited citations may never be laundered
    # into the corpus. competitive_advantage with no clean source must fail-and-
    # escalate honestly, not back-fill from a known-bad cite. NOTE: _FORBIDDEN_CITES
    # tokens MUST stay in their canonical seed form (a reformatted docket would
    # slip this substring match — meth N2).
    for r in _rows():
        blob = f"{r.get('source', '')} {r.get('locator', '')} {r.get('secondary_url', '')}"
        for bad in _FORBIDDEN_CITES:
            assert bad not in blob, f"{r['source']}: uses forbidden mis-cited source {bad!r}"


def test_multi_form_source_slices_are_distinct() -> None:
    # meth N1: if the SAME source backs >1 form, each row must name a
    # single_form_slice AND the slices must DIFFER — so the same blended number
    # can't be laundered under two forms with cosmetically-different labels
    # (closes the M1 mislabel-identical hole at the corpus layer, not just D-ii-b).
    by_source: dict[str, list[dict]] = {}
    for r in _rows():
        if r["loss_tier"] == "none":
            continue
        by_source.setdefault(r["source"], []).append(r)
    for source, rows in by_source.items():
        if len(rows) < 2:
            continue
        slices = [r.get("single_form_slice") for r in rows]
        assert all(slices), (
            f"{source}: backs multiple forms, each row must name a single_form_slice"
        )
        assert len(set(slices)) == len(slices), (
            f"{source}: multiple forms share an identical single_form_slice — blended double-pull risk"
        )
