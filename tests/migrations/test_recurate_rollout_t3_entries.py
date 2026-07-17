"""Migration pinning: #437 rollout tranche 3 re-curation (a3f7c1e9b2d4).

Tranche-3 is the INVERSE of tranches 1-2: it REMOVES mis-authored / non-faithful
channels rather than adding scoring ones. This pins, for each of the five touched
control-library entries, that:

- the removed non-faithful channels are ABSENT after the migration, AND
- the genuine scoring channel each entry KEEPS is still PRESENT (guard: no entry
  may lose a legitimate score — DRE/DTE keep resistance, NAC keeps avoidance, UAC
  keeps resistance, CRQM keeps its DSC triad).

Faithfulness rationale is captured per removal in ``_meta.claim_drops`` and in the
migration docstring; the crosswalk gate (test_control_library_seed.py) tightens under
removals so it stays green by construction. Runs the full chain through ``head`` so
the test exercises the real migration stack.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import Engine

from idraa.services.control_library_scoring import classify_entry

_LATEST_ASSIGNMENTS = (
    "SELECT a.sub_function, a.id, e.version "
    "FROM control_library_entry_assignments a "
    "JOIN control_library_entries e "
    "  ON e.id = a.library_entry_id AND e.version = a.library_entry_version "
    "WHERE e.slug = :slug"
)

# slug -> sub-functions the tranche must REMOVE (absent after re-curation).
_REMOVED = {
    "data-at-rest-encryption": {
        "vmc_id_control_monitoring",
        "vmc_corr_implementation",
        "dsc_prev_defined_expectations",
    },
    "data-in-transit-encryption": {"vmc_id_control_monitoring", "vmc_corr_implementation"},
    "network-access-control": {"vmc_prev_reduce_variance_prob"},
    "user-access-control": {"vmc_prev_reduce_variance_prob"},
    "cyber-risk-quantification-management": {"vmc_corr_implementation"},
}

# slug -> the genuine channel(s) that MUST survive (guards against a score regression:
# each entry keeps its scoring channel; CRQM keeps its genuine decision-support triad
# even though it stays a non-scoring residual by design).
_KEPT = {
    "data-at-rest-encryption": {"lec_prev_resistance", "lec_resp_loss_reduction"},
    "data-in-transit-encryption": {"lec_prev_resistance"},
    "network-access-control": {"lec_prev_avoidance"},
    "user-access-control": {
        "lec_prev_resistance",
        "vmc_id_control_monitoring",
        "vmc_corr_implementation",
        "dsc_prev_defined_expectations",
    },
    "cyber-risk-quantification-management": {
        "dsc_prev_sa_reporting",
        "dsc_prev_sa_analysis",
        "dsc_prev_defined_expectations",
    },
}


def _rows(engine: Engine, slug: str) -> list:
    with engine.connect() as conn:
        return conn.execute(sa.text(_LATEST_ASSIGNMENTS), {"slug": slug}).all()


def test_t3_entries_shed_non_faithful_channels(
    alembic_config: Config, alembic_engine: Engine
) -> None:
    """Each tranche-3 entry sheds its non-faithful / double-scoring channel(s) AND
    retains the genuine channel(s) it keeps (guards against a score regression)."""
    command.upgrade(alembic_config, "head")
    for slug, removed in _REMOVED.items():
        rows = _rows(alembic_engine, slug)
        assert rows, f"{slug}: no assignments joined to latest version — version-join broken"
        subs = {r[0] for r in rows}
        leaked = removed & subs
        assert not leaked, f"{slug}: non-faithful channel(s) {leaked} were NOT removed"
        kept = _KEPT[slug]
        missing = kept - subs
        assert not missing, f"{slug}: genuine kept channel(s) {missing} accidentally dropped"


def test_t3_versions_bumped(alembic_config: Config, alembic_engine: Engine) -> None:
    """Each re-curated entry's version is bumped above 1 (the field #438 keys on)."""
    command.upgrade(alembic_config, "head")
    with alembic_engine.connect() as conn:
        for slug in _REMOVED:
            ver = conn.execute(
                sa.text("SELECT MAX(version) FROM control_library_entries WHERE slug = :slug"),
                {"slug": slug},
            ).scalar_one()
            assert ver is not None and ver >= 2, f"{slug} version not bumped: {ver!r}"


def test_t3_assignment_ids_no_hyphen(alembic_config: Config, alembic_engine: Engine) -> None:
    """Re-inserted assignment ids are 32-char no-hyphen hex (raw-text-seed foot-gun)."""
    command.upgrade(alembic_config, "head")
    for slug in _REMOVED:
        rows = _rows(alembic_engine, slug)
        bad = [r[1] for r in rows if len(r[1]) != 32 or "-" in r[1]]
        assert not bad, f"{slug}: re-curated id(s) not 32-char no-hyphen hex: {bad!r}"


_FULL_ASSIGNMENTS = (
    "SELECT a.sub_function, a.capability_default, a.coverage_default, "
    "       a.reliability_default "
    "FROM control_library_entry_assignments a "
    "JOIN control_library_entries e "
    "  ON e.id = a.library_entry_id AND e.version = a.library_entry_version "
    "WHERE e.slug = :slug"
)

# slug -> expected 3-way classification after tranche-3 re-curation.
_EXPECTED_CLASS = {
    "data-at-rest-encryption": "scoring",
    "data-in-transit-encryption": "scoring",
    "network-access-control": "scoring",
    "user-access-control": "scoring",
    "cyber-risk-quantification-management": "non-scoring-residual",
}


def test_t3_triage_classification_unchanged(alembic_config: Config, alembic_engine: Engine) -> None:
    """Tranche-3 entries classify correctly after channel removals.

    scoring:              DRE, DTE, NAC, UAC — each keeps a genuine LEC channel that
                          produces v(S) > 0 despite shedding the non-faithful VMC/DSC.
    non-scoring-residual: CRQM — keeps its genuine DSC decision-support triad (≥2
                          assignments) but those DSC channels yield v(S) = 0.

    Guards "no score lost / CRQM stays residual" as an automated regression: if a
    future migration drops a genuine kept channel, the entry tips to non-scoring-
    residual or under-authored and this test catches it.
    """
    command.upgrade(alembic_config, "head")
    for slug, expected_class in _EXPECTED_CLASS.items():
        with alembic_engine.connect() as conn:
            rows = conn.execute(sa.text(_FULL_ASSIGNMENTS), {"slug": slug}).all()
        assert rows, f"{slug}: no assignments after migration — version-join broken"
        entry = {
            "assignments": [
                {
                    "sub_function": r[0],
                    "capability_default": r[1],
                    "coverage_default": r[2],
                    "reliability_default": r[3],
                }
                for r in rows
            ]
        }
        actual = classify_entry(entry)
        assert actual == expected_class, (
            f"{slug}: expected {expected_class!r}, got {actual!r}. "
            f"Assignments present: {[a['sub_function'] for a in entry['assignments']]}"
        )
