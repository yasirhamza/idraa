import inspect

import idraa.services.run_executor as rex


def test_primary_mc_calls_are_offloaded():
    # Guard against regressing back onto the event loop: the SINGLE and AGGREGATE
    # primary engine calls must be wrapped in asyncio.to_thread.
    # execute_run itself is a thin registry-bookkeeping wrapper (#211 Phase 2);
    # the actual engine calls live in _execute_run_body.
    src = inspect.getsource(rex._execute_run_body)
    normalized = src.replace(" ", "").replace("\n", "")
    assert "asyncio.to_thread(calculator.calculate_control_enhanced_risk" in normalized
    assert "asyncio.to_thread(calculator.calculate_aggregate_enhanced_risk" in normalized
