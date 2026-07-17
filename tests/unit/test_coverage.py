from idraa.services.coverage import coverage


def test_coverage_ratio_missing_and_present():
    r = coverage(reference=["a", "b", "c", "d"], covered=["b", "d", "z"])
    assert r.reference_count == 4
    assert r.covered_count == 2  # only b,d intersect the reference; z ignored
    assert r.ratio == 0.5
    assert sorted(r.missing) == ["a", "c"]  # in reference, not covered — the actionable gap
    assert sorted(r.present) == ["b", "d"]


def test_coverage_empty_reference_is_zero_not_div0():
    r = coverage(reference=[], covered=["x"])
    assert r.reference_count == 0 and r.covered_count == 0 and r.ratio == 0.0
    assert r.missing == [] and r.present == []


def test_coverage_dedups_and_is_order_stable():
    r = coverage(reference=["a", "a", "b"], covered=["a", "a"])
    assert r.reference_count == 2 and r.covered_count == 1 and r.ratio == 0.5
