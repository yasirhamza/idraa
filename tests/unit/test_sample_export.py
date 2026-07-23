"""Unit tests for services/sample_export.py (#109)."""

from __future__ import annotations

import gzip
import uuid
from typing import Any

import numpy as np
import pytest

from idraa.services.sample_export import (
    build_export_columns,
    build_preamble,
    iter_csv_gz,
)

S1, S2, S3 = (uuid.uuid4() for _ in range(3))


def _agg_summary() -> dict[str, Any]:
    return {
        "per_scenario": [
            {"scenario_id": str(S1), "scenario_name": "Ransomware"},
            {"scenario_id": str(S2), "scenario_name": "=EVIL,name\nwith newline"},
            {"scenario_id": str(S3), "scenario_name": "OT outage"},
        ]
    }


def _agg_arrays(n: int = 5) -> dict[str, np.ndarray]:
    a = lambda seed: np.arange(n, dtype=np.float32) + seed  # noqa: E731
    return {
        "aggregate_with_controls": a(100),
        "aggregate_without_controls": a(200),
        "per_scenario/0/base_risk": a(0),
        "per_scenario/0/residual_risk": a(10),
        "per_scenario/1/base_risk": a(1),
        "per_scenario/1/residual_risk": a(11),
        "per_scenario/2/base_risk": a(2),
        "per_scenario/2/residual_risk": a(12),
    }


def test_columns_single_run_order() -> None:
    arrays = {
        "residual_risk": np.ones(3, dtype=np.float32),
        "base_risk": np.zeros(3, dtype=np.float32),
    }
    cols, legend = build_export_columns({}, arrays)
    assert [h for h, _ in cols] == ["base_risk", "residual_risk"]
    assert legend == []


def test_columns_aggregate_maps_index_to_scenario_id_n3() -> None:
    # Adapter-iteration discipline: N >= 3, all preserved, correctly keyed.
    cols, legend = build_export_columns(_agg_summary(), _agg_arrays())
    headers = [h for h, _ in cols]
    assert headers == [
        "aggregate_without_controls",
        "aggregate_with_controls",
        f"scenario_{S1.hex}_base_risk",
        f"scenario_{S1.hex}_residual_risk",
        f"scenario_{S2.hex}_base_risk",
        f"scenario_{S2.hex}_residual_risk",
        f"scenario_{S3.hex}_base_risk",
        f"scenario_{S3.hex}_residual_risk",
    ]
    by_header = dict(cols)
    # Values follow the header: scenario 2's base column is per_scenario/1/base_risk.
    np.testing.assert_array_equal(
        by_header[f"scenario_{S2.hex}_base_risk"],
        np.arange(5, dtype=np.float32) + 1,
    )
    assert len(legend) == 3


def test_columns_positional_fallback_when_summary_entry_missing() -> None:
    summary = {"per_scenario": [{"scenario_id": str(S1), "scenario_name": "ok"}]}
    arrays = {
        "per_scenario/0/base_risk": np.ones(2, dtype=np.float32),
        "per_scenario/0/residual_risk": np.ones(2, dtype=np.float32),
        "per_scenario/1/base_risk": np.ones(2, dtype=np.float32),
        "per_scenario/1/residual_risk": np.ones(2, dtype=np.float32),
    }
    headers = [h for h, _ in build_export_columns(summary, arrays)[0]]
    assert f"scenario_{S1.hex}_base_risk" in headers
    assert "per_scenario_1_base_risk" in headers
    assert "per_scenario_1_residual_risk" in headers


def test_columns_length_mismatch_raises() -> None:
    arrays = {
        "base_risk": np.ones(3, dtype=np.float32),
        "residual_risk": np.ones(4, dtype=np.float32),
    }
    with pytest.raises(ValueError):
        build_export_columns({}, arrays)


def test_columns_unknown_path_fails_loudly() -> None:
    # SWE2-I1 tripwire: a future array path must force an exporter update,
    # never silently vanish from a completeness-claiming export.
    arrays = {
        "base_risk": np.ones(3, dtype=np.float32),
        "per_scenario/0/tail_risk": np.ones(3, dtype=np.float32),
    }
    with pytest.raises(ValueError, match="unrecognised sample array paths"):
        build_export_columns({}, arrays)


def test_columns_aliased_index_paths_rejected() -> None:
    # Sec3-N1: 'per_scenario/007/...' aliases index 7 after int() — a crafted
    # row must fail loudly, never silently overwrite a scenario's column.
    arrays = {
        "per_scenario/7/base_risk": np.ones(2, dtype=np.float32),
        "per_scenario/007/base_risk": np.zeros(2, dtype=np.float32),
    }
    with pytest.raises(ValueError, match="duplicate per-scenario array path"):
        build_export_columns({}, arrays)


def test_columns_unicode_digit_path_rejected() -> None:
    # Sec3-N1: \d would match Unicode digits; [0-9] must not. The Arabic-digit
    # path falls through to the unknown-path tripwire instead of aliasing.
    arrays = {"per_scenario/٣/base_risk": np.ones(2, dtype=np.float32)}
    with pytest.raises(ValueError, match="unrecognised sample array paths"):
        build_export_columns({}, arrays)


def test_columns_trailing_newline_path_rejected() -> None:
    # FBSec-N1: $ also matches before a trailing \n; \Z must not. A crafted
    # path with a trailing newline falls to the unknown-path tripwire.
    arrays = {"per_scenario/0/base_risk\n": np.ones(2, dtype=np.float32)}
    with pytest.raises(ValueError, match="unrecognised sample array paths"):
        build_export_columns({}, arrays)


def test_legend_flattens_triggers_separators_and_newlines() -> None:
    _, legend = build_export_columns(_agg_summary(), _agg_arrays())
    evil = next(line for line in legend if S2.hex in line)
    assert "\n" not in evil and "\r" not in evil
    # Allowlist flatten: the name segment keeps letters but loses '=', ',',
    # and every other separator/trigger character entirely.
    name_seg = evil.split(" name=", 1)[1]
    assert "EVIL" in name_seg and "with newline" in name_seg
    for ch in ("=", ",", ";", '"'):
        assert ch not in name_seg


def test_legend_separator_formula_bypass_blocked() -> None:
    # Sec-B2/Sec2-B1 bypass regression: name does NOT start with a trigger,
    # but carries ',=' AND ';=' mid-string — a surviving separator would
    # open a fresh Excel cell (',' in en locales, ';' in most European
    # locales) whose content starts with '=' — a live formula.
    sid = uuid.uuid4()
    summary = {
        "per_scenario": [
            {
                "scenario_id": str(sid),
                "scenario_name": 'Backup,=HYPERLINK("http://evil/","x");=CMD()',
            }
        ]
    }
    arrays = {
        "per_scenario/0/base_risk": np.ones(2, dtype=np.float32),
        "per_scenario/0/residual_risk": np.ones(2, dtype=np.float32),
    }
    _, legend = build_export_columns(summary, arrays)
    assert len(legend) == 1
    for seq in (",=", ";=", ",+", ";+", ",-", ";-", ",@", ";@"):
        assert seq not in legend[0]
    assert '"' not in legend[0].split(" name=", 1)[1]


def test_iter_csv_gz_round_trips_float32_exactly() -> None:
    arrays = {
        "base_risk": np.array([0.1, 123456.789, 3.4e38], dtype=np.float32),
        "residual_risk": np.array([7.25, 0.0, 1e-30], dtype=np.float32),
    }
    cols, _ = build_export_columns({}, arrays)
    body = b"".join(iter_csv_gz(["hello"], cols, chunk_rows=2))
    text = gzip.decompress(body).decode("utf-8")
    lines = [ln for ln in text.split("\r\n") if ln and not ln.startswith("#")]
    assert lines[0] == "iteration,base_risk,residual_risk"
    for i, line in enumerate(lines[1:]):
        it, b, r = line.split(",")
        assert int(it) == i
        assert np.float32(b) == arrays["base_risk"][i]
        assert np.float32(r) == arrays["residual_risk"][i]


def test_iter_csv_gz_preamble_lines_are_comments() -> None:
    cols, _ = build_export_columns({}, {"base_risk": np.ones(1, dtype=np.float32)})
    text = gzip.decompress(b"".join(iter_csv_gz(["run_id: x", "# already"], cols))).decode()
    assert text.startswith("# run_id: x\r\n# already\r\n")


def test_build_preamble_contains_provenance_keys() -> None:
    import types

    run = types.SimpleNamespace(
        id=S1,
        run_type=types.SimpleNamespace(value="aggregate"),
        mc_iterations=1000,
        random_seed=42,
        inputs_hash="ab" * 32,
    )
    lines = build_preamble(
        run=run,  # type: ignore[arg-type]
        derived_seed_keys={str(S2): 0},
        app_version="1.2.3",
        legend_lines=["column x: scenario_id=y name=z"],
    )
    joined = "\n".join(lines)
    for key in (
        "schema: samples-export/1",
        f"run_id: {S1}",
        "run_type: aggregate",
        "mc_iterations: 1000",
        "random_seed: 42",
        f"inputs_hash: {'ab' * 32}",
        "derived_seed_keys: ",
        "app_version: 1.2.3",
        "generated_at: ",
        "float32",
        "iteration index is 0-based",
        "column x: scenario_id=y name=z",
    ):
        assert key in joined, key
