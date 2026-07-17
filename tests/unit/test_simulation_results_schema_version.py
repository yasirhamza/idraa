"""simulation_results schema-version stamp (whole-project-eval hygiene item).

``run.simulation_results`` payloads persisted with no version marker make
future shape changes (renamed/removed/resemantified keys) silently divergent
across historical rows. New runs now carry ``schema_version`` (stamped at
the run_executor persist site, AFTER split_simulation_payload so the
split/merge helpers stay pure and the backfill round-trip stays lossless).
Legacy rows lack the key and read back as version 0.
"""

from __future__ import annotations

from idraa.services.simulation_payload import (
    SIMULATION_RESULTS_SCHEMA_VERSION,
    results_schema_version,
    split_simulation_payload,
)


def test_current_schema_version_is_one() -> None:
    assert SIMULATION_RESULTS_SCHEMA_VERSION == 1


def test_results_schema_version_reads_stamp() -> None:
    assert results_schema_version({"schema_version": 1, "residual_risk": {}}) == 1


def test_legacy_rows_without_stamp_are_version_zero() -> None:
    assert results_schema_version({"residual_risk": {}}) == 0


def test_split_does_not_stamp() -> None:
    """split/merge stay PURE — the stamp happens at the persist site only,
    so the run_samples backfill round-trip (merge(split(p)) == p) holds."""
    payload = {"residual_risk": {"simulation_results": [1.0, 2.0]}}
    summary, _arrays = split_simulation_payload(payload)
    assert "schema_version" not in summary
