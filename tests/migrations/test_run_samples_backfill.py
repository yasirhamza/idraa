"""Guard test for the run_samples backfill migration (#297).

Property the migration's inlined frozen split/merge relies on: the live helper
split is idempotent (re-splitting a stripped summary moves nothing) and the
merge is lossless (merge(split(payload)) == payload). The migration carries a
FROZEN copy of this logic; this test pins the property against the live helper
so a future helper change that breaks the property is caught at the source.

Fixtures carry ``np.ndarray`` (not plain lists) for the sample arrays — the
real production shape since Task 3 dropped ``.tolist()`` in
``_fair_risk_to_dict``. Comparing dicts holding ndarrays with a bare ``==``
raises "ambiguous truth value", so array-bearing assertions go through the
binary codec's ``decode(encode(...))`` round-trip or ``np.array_equal``.
"""

import numpy as np

from idraa.services.sample_codec import decode_sample_arrays, encode_sample_arrays
from idraa.services.simulation_payload import (
    SAMPLE_ARRAY_KEY,
    merge_simulation_payload,
    split_simulation_payload,
)


def test_backfill_property_lossless_and_idempotent_on_aggregate_shape() -> None:
    payload = {
        "aggregate_with_controls": {"mean": 5.0, SAMPLE_ARRAY_KEY: np.array([1.0] * 100)},
        "per_scenario": [
            {
                "base_risk": {"ale": float(i), SAMPLE_ARRAY_KEY: np.array([float(i)] * 50)},
                "residual_risk": {"v": float(i), SAMPLE_ARRAY_KEY: np.array([float(i)] * 50)},
            }
            for i in range(3)
        ],
    }
    summary, arrays = split_simulation_payload(payload)
    s2, a2 = split_simulation_payload(summary)
    # Idempotent: re-splitting a stripped summary moves nothing. ``summary``
    # itself carries no arrays at this point, so a plain ``==`` is safe here.
    assert a2 == {} and s2 == summary

    # Lossless, part 1: every split-out array round-trips through the codec.
    decoded = decode_sample_arrays(encode_sample_arrays(arrays))
    assert set(decoded) == set(arrays)
    for path, arr in arrays.items():
        assert decoded[path] == arr.tolist(), f"codec round-trip mismatch for {path}"

    # Lossless, part 2: merge reassembles the original payload. Compare
    # structurally (array-aware) instead of one blanket ``==`` over the whole
    # nested dict.
    merged = merge_simulation_payload(summary, arrays)
    assert merged["aggregate_with_controls"]["mean"] == payload["aggregate_with_controls"]["mean"]
    assert np.array_equal(
        merged["aggregate_with_controls"][SAMPLE_ARRAY_KEY],
        payload["aggregate_with_controls"][SAMPLE_ARRAY_KEY],
    )
    for i in range(3):
        for kind, key in (("base_risk", "ale"), ("residual_risk", "v")):
            assert merged["per_scenario"][i][kind][key] == payload["per_scenario"][i][kind][key]
            assert np.array_equal(
                merged["per_scenario"][i][kind][SAMPLE_ARRAY_KEY],
                payload["per_scenario"][i][kind][SAMPLE_ARRAY_KEY],
            )
