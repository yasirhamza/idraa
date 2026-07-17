"""Contract: the simulation-payload split preserves ALL sample series.

Regression guard for the #294/#297 M-1 bug where the recursive split missed
per-scenario arrays for AGGREGATE-shaped payloads — silently dropping sample
series so the full-distribution / CSV-export paths rendered incomplete data.

The split must:
  - lift every ``SAMPLE_ARRAY_KEY`` array out of every known risk container
    (the two aggregate containers + each per-scenario base/residual entry), and
  - round-trip back to a byte-identical payload via ``merge_simulation_payload``.

Fixtures carry ``np.ndarray`` (not plain lists) for the sample arrays — the
real production shape since Task 3 dropped ``.tolist()`` in
``_fair_risk_to_dict``. Comparing dicts holding ndarrays with a bare ``==``
raises "ambiguous truth value" (numpy refuses to collapse an elementwise
comparison to a single bool), so array-bearing assertions go through either
the binary codec's ``decode(encode(...))`` round-trip or ``np.array_equal``
rather than a blanket dict ``==``.
"""

from __future__ import annotations

import numpy as np

from idraa.services.sample_codec import decode_sample_arrays, encode_sample_arrays
from idraa.services.simulation_payload import (
    SAMPLE_ARRAY_KEY,
    merge_simulation_payload,
    split_simulation_payload,
)


def test_aggregate_split_preserves_all_series() -> None:
    payload = {
        "aggregate_with_controls": {"m": 1, SAMPLE_ARRAY_KEY: np.array([1.0, 2.0])},
        "aggregate_without_controls": {"m": 2, SAMPLE_ARRAY_KEY: np.array([3.0, 4.0])},
        "per_scenario": [
            {
                "base_risk": {"v": i, SAMPLE_ARRAY_KEY: np.array([float(i)])},
                "residual_risk": {"v": i, SAMPLE_ARRAY_KEY: np.array([float(i)])},
            }
            for i in range(3)
        ],
    }
    summary, arrays = split_simulation_payload(payload)
    # 2 aggregate containers + 3 per-scenario * 2 (base + residual) = 8 series.
    assert len(arrays) == 8, f"split dropped sample series: {sorted(arrays)}"
    # Summary must no longer carry any sample arrays (they moved to ``arrays``).
    assert SAMPLE_ARRAY_KEY not in summary["aggregate_with_controls"]
    assert SAMPLE_ARRAY_KEY not in summary["aggregate_without_controls"]
    for entry in summary["per_scenario"]:
        assert SAMPLE_ARRAY_KEY not in entry["base_risk"]
        assert SAMPLE_ARRAY_KEY not in entry["residual_risk"]

    # Every split-out array round-trips through the binary codec unchanged.
    decoded = decode_sample_arrays(encode_sample_arrays(arrays))
    assert set(decoded) == set(arrays)
    for path, arr in arrays.items():
        assert decoded[path] == arr.tolist(), f"codec round-trip mismatch for {path}"

    # merge_simulation_payload must still reassemble the ORIGINAL payload
    # shape byte-for-byte; compare structurally (array-aware) instead of one
    # blanket ``==`` over the whole nested dict.
    merged = merge_simulation_payload(summary, arrays)
    assert merged["aggregate_with_controls"]["m"] == payload["aggregate_with_controls"]["m"]
    assert np.array_equal(
        merged["aggregate_with_controls"][SAMPLE_ARRAY_KEY],
        payload["aggregate_with_controls"][SAMPLE_ARRAY_KEY],
    )
    assert merged["aggregate_without_controls"]["m"] == payload["aggregate_without_controls"]["m"]
    assert np.array_equal(
        merged["aggregate_without_controls"][SAMPLE_ARRAY_KEY],
        payload["aggregate_without_controls"][SAMPLE_ARRAY_KEY],
    )
    for i in range(3):
        for kind in ("base_risk", "residual_risk"):
            assert merged["per_scenario"][i][kind]["v"] == payload["per_scenario"][i][kind]["v"]
            assert np.array_equal(
                merged["per_scenario"][i][kind][SAMPLE_ARRAY_KEY],
                payload["per_scenario"][i][kind][SAMPLE_ARRAY_KEY],
            )
