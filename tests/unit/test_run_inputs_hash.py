"""run_inputs_hash: SHA-256 of stable JSON over reproducibility-relevant
scenario inputs. Order-invariant for control_ids.

PR π: hash payload narrowed to {distributions, control_ids, mc_iterations}.
Calibration-runtime fields (iris_calibration_year, industry, revenue_tier,
calibration_override_pin, overlay_pins) are no longer in the hash; tests
that asserted hash-changes against those fields were removed. A regression
test (test_inputs_hash_unchanged_when_dropped_fields_change) locks the
new minimal payload by attaching deceased fields to the input object and
asserting the hash is unchanged.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

from idraa.services.run_inputs_hash import build_inputs_hash


def _make_scenario(
    *,
    tef: dict[str, object] | None = None,
    vuln: dict[str, object] | None = None,
    primary: dict[str, object] | None = None,
    secondary: dict[str, object] | None = None,
) -> SimpleNamespace:
    """Build a Scenario-shaped SimpleNamespace for hash testing.

    We don't need a real Scenario row — build_inputs_hash only reads
    attributes by name. SimpleNamespace is faster than DB fixtures.
    """
    return SimpleNamespace(
        threat_event_frequency=tef or {"low": 1, "mode": 5, "high": 10},
        vulnerability=vuln or {"low": 0.1, "mode": 0.3, "high": 0.5},
        primary_loss=primary or {"low": 1e5, "mode": 5e5, "high": 1e6},
        secondary_loss=secondary or {"low": 5e4, "mode": 2e5, "high": 5e5},
    )


def test_hash_is_64_char_hex() -> None:
    s = _make_scenario()
    h = build_inputs_hash(s, control_ids=[], mc_iterations=10000)
    assert len(h) == 64
    assert all(c in "0123456789abcdef" for c in h)


def test_hash_stable_for_same_inputs() -> None:
    s1 = _make_scenario()
    s2 = _make_scenario()
    h1 = build_inputs_hash(s1, control_ids=[], mc_iterations=10000)
    h2 = build_inputs_hash(s2, control_ids=[], mc_iterations=10000)
    assert h1 == h2


def test_hash_changes_with_mc_iterations() -> None:
    s = _make_scenario()
    h1 = build_inputs_hash(s, control_ids=[], mc_iterations=10000)
    h2 = build_inputs_hash(s, control_ids=[], mc_iterations=50000)
    assert h1 != h2


def test_hash_invariant_to_control_id_ordering() -> None:
    s = _make_scenario()
    cid_a = uuid.UUID("11111111-1111-1111-1111-111111111111")
    cid_b = uuid.UUID("22222222-2222-2222-2222-222222222222")
    h_forward = build_inputs_hash(s, control_ids=[cid_a, cid_b], mc_iterations=10000)
    h_reverse = build_inputs_hash(s, control_ids=[cid_b, cid_a], mc_iterations=10000)
    assert h_forward == h_reverse


def test_hash_changes_with_control_id_set() -> None:
    s = _make_scenario()
    cid_a = uuid.UUID("11111111-1111-1111-1111-111111111111")
    cid_b = uuid.UUID("22222222-2222-2222-2222-222222222222")
    h_one = build_inputs_hash(s, control_ids=[cid_a], mc_iterations=10000)
    h_two = build_inputs_hash(s, control_ids=[cid_a, cid_b], mc_iterations=10000)
    assert h_one != h_two


def test_hash_changes_with_fair_distributions() -> None:
    s1 = _make_scenario(tef={"low": 1, "mode": 5, "high": 10})
    s2 = _make_scenario(tef={"low": 1, "mode": 6, "high": 10})
    h1 = build_inputs_hash(s1, control_ids=[], mc_iterations=10000)
    h2 = build_inputs_hash(s2, control_ids=[], mc_iterations=10000)
    assert h1 != h2


def test_inputs_hash_unchanged_when_dropped_fields_change() -> None:
    """The new hash MUST ignore deceased fields even when present on the object.

    Setting iris_calibration_year/industry/etc. with weird values on the
    SimpleNamespace must not change the hash, since they're not in the
    new payload. This locks the regression: if a future maintainer re-adds
    one of these to the hash payload, this test goes red.
    """
    base = SimpleNamespace(
        threat_event_frequency={"distribution": "pert", "low": 1, "mode": 2, "high": 3},
        vulnerability={"distribution": "pert", "low": 0.1, "mode": 0.2, "high": 0.3},
        primary_loss={"distribution": "pert", "low": 100, "mode": 200, "high": 300},
        secondary_loss=None,
    )
    expected = build_inputs_hash(base, [], 1000)

    polluted = SimpleNamespace(
        threat_event_frequency=base.threat_event_frequency,
        vulnerability=base.vulnerability,
        primary_loss=base.primary_loss,
        secondary_loss=None,
        # Deceased fields below — attached deliberately:
        iris_calibration_year=9999,
        industry="weird-industry-that-doesnt-exist",
        revenue_tier="space_exploration",
        calibration_override_pin={"override_id": str(uuid.uuid4()), "version": 42},
        overlay_pins=[{"tag": "x", "version": 1}],
    )
    actual = build_inputs_hash(polluted, [], 1000)
    assert actual == expected
