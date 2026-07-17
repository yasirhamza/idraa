import json
from pathlib import Path

import idraa
from idraa.schemas.crosswalk import CrosswalkSeed


def _payload():
    p = (
        Path(idraa.__file__).resolve().parent.parent.parent
        / "data"
        / "seed_framework_crosswalk.json"
    )
    return json.loads(p.read_text())


def test_every_seed_row_validates_and_has_functions():
    payload = _payload()
    rows = payload["entries"]
    assert len(rows) > 100
    for raw in rows:
        seed = CrosswalkSeed.model_validate(raw)  # raises on bad shape / bad enum
        assert seed.fair_cam_functions
    assert {r["framework"] for r in rows} == {"nist_csf", "cis"}
    # Honest reference-attribution retained (source credited), no license claim.
    assert payload["_attribution"]["source"] == "FAIR Institute"


def test_no_license_or_copyright_overclaim():
    """Guard: the seed records factual mappings, so it must NOT stamp the FAIR
    Institute's copyright / CC-BY-NC-ND license over them (reframe 2026-07-14).
    Attribution ('source'/'document') is kept; ownership/license claims are not.
    """
    payload = _payload()
    attr = payload["_attribution"]
    assert "copyright" not in attr and "license" not in attr
    assert "basis" in attr  # honest provenance note replaces the license stamp
    for e in payload["entries"]:
        cit = e["citation"]
        assert "copyright" not in cit, f"{e['code']} still stamps copyright"
        assert "license" not in cit, f"{e['code']} still stamps license"


def test_json_db_row_count_parity():
    """Gate F-2: JSON↔DB row-count parity.

    The pytest harness builds schema via ``Base.metadata.create_all`` (gate
    Arch-I2), NOT via Alembic migrations, so this is a pure-JSON count
    assertion of what the migration WOULD insert: one ``framework_controls``
    row per entry, one ``framework_control_faircam`` link per fair_cam_function.
    """
    payload = _payload()
    rows = payload["entries"]
    control_count = len(rows)
    base_links = sum(len(r["fair_cam_functions"]) for r in rows)
    ext_links = sum(len(r.get("riskflow_extension_functions", [])) for r in rows)
    link_count = base_links + ext_links
    # The migration inserts one framework_controls row per entry and one
    # framework_control_faircam link per fair_cam_function — verified against a
    # throwaway-DB `alembic upgrade head` smoke run (Step 4): 261 controls / 473
    # links matched these JSON-derived counts exactly. #437 rollout T1 added the
    # REVIEWED crosswalk-seed extension (CIS 7.3 + 7.4 each gain lec_prev_resistance),
    # +2 links -> 475. #437 rollout T2 added three more REVIEWED extensions (CIS 4.8 ->
    # lec_prev_avoidance, CIS 14.2 -> lec_prev_resistance, CIS 16.1 -> lec_prev_resistance),
    # +3 links -> 478. The FAIR-Institute base mapping is unchanged; each extension is
    # documented in that entry's citation.riskflow_extension (see
    # data/seed_framework_crosswalk.json) and applied to deployed DBs by migrations
    # f1a2b3c4d5e6 (T1) and c7e2a9b4f1d6 (T2).
    # #449: the extension layer is now structurally separate — the migration
    # composes both layers at load time, so DB parity is base + extensions.
    assert control_count == 261
    assert base_links == 473  # FAIR-Institute source layer (283 CIS + 190 NIST)
    assert ext_links == 5  # Idraa overlay (#437 T1/T2)
    assert link_count == 478
    assert link_count >= control_count  # every control has ≥1 function
