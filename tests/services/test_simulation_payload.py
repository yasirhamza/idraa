from idraa.services.simulation_payload import (
    SAMPLE_ARRAY_KEY,
    merge_simulation_payload,
    split_simulation_payload,
)


def _single():
    return {
        "headline_ale": 7.0,
        "base_risk": {"annualized_loss_expectancy": 2.0, SAMPLE_ARRAY_KEY: [1.0, 2.0, 3.0]},
        "residual_risk": {"var_95": 1.0, SAMPLE_ARRAY_KEY: [4.0, 5.0, 6.0]},
        "loss_exceedance_curve": [{"loss": 1, "probability": 0.5}],
    }


def _aggregate():
    return {
        "aggregate_with_controls": {"mean": 5.0, SAMPLE_ARRAY_KEY: [10.0, 11.0]},
        "aggregate_without_controls": {"mean": 9.0, SAMPLE_ARRAY_KEY: [20.0, 21.0]},
        "per_scenario": [
            {
                "base_risk": {"annualized_loss_expectancy": float(i), SAMPLE_ARRAY_KEY: [i, i + 1]},
                "residual_risk": {"var_95": float(i), SAMPLE_ARRAY_KEY: [i + 2, i + 3]},
            }
            for i in range(3)
        ],
    }


def _no_array_at_any_depth(obj):
    if isinstance(obj, dict):
        if SAMPLE_ARRAY_KEY in obj:
            return False
        return all(_no_array_at_any_depth(v) for v in obj.values())
    if isinstance(obj, list):
        return all(_no_array_at_any_depth(v) for v in obj)
    return True


def test_single_round_trip():
    p = _single()
    s, a = split_simulation_payload(p)
    assert _no_array_at_any_depth(s)
    assert merge_simulation_payload(s, a) == p


def test_aggregate_recurses_into_per_scenario_list():
    p = _aggregate()
    s, a = split_simulation_payload(p)
    assert _no_array_at_any_depth(s)
    assert len(a) == 8  # 2 aggregate + 3*2 per-scenario
    assert merge_simulation_payload(s, a) == p


def test_nondestructive_copy_mode_leaves_input_intact():
    p = _aggregate()
    split_simulation_payload(p)
    assert p["per_scenario"][0]["base_risk"][SAMPLE_ARRAY_KEY] == [0, 1]


def test_move_mode_mutates_input_for_write_path():
    p = _aggregate()
    _s, a = split_simulation_payload(p, copy=False)
    assert SAMPLE_ARRAY_KEY not in p["per_scenario"][0]["base_risk"]
    assert len(a) == 8


def test_summary_keeps_non_array_fields():
    s, _ = split_simulation_payload(_single())
    assert s["headline_ale"] == 7.0
    assert s["base_risk"]["annualized_loss_expectancy"] == 2.0
    assert s["loss_exceedance_curve"] == [{"loss": 1, "probability": 0.5}]


def test_merge_preserves_schema_version_stamp():
    """NTH-1 (review): a stamped summary (as persisted by run_executor)
    round-trips through merge_simulation_payload with the stamp intact —
    future-proofing for a samples-download path that reconstructs full
    payloads."""
    summary = {
        "schema_version": 1,
        "residual_risk": {"annualized_loss_expectancy": 1.0},
    }
    arrays = {"residual_risk": [0.5, 1.5]}
    merged = merge_simulation_payload(summary, arrays)
    assert merged["schema_version"] == 1
    assert merged["residual_risk"]["simulation_results"] == [0.5, 1.5]
