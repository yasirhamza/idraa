"""N>=3 iteration preservation for list[ThreatCommunity] -> choices (project policy)."""

from __future__ import annotations

from tests.contracts.helpers import assert_preserves_list_count


class _Row:
    def __init__(self, i: int) -> None:
        self.slug, self.name, self.origin = f"s{i}", f"N{i}", "external" if i % 2 else "internal"


def test_choices_from_rows_preserves_count() -> None:
    from idraa.services.threat_communities import choices_from_rows

    assert_preserves_list_count(choices_from_rows, lambda n: [_Row(i) for i in range(n)], n=3)
