import pytest

from idraa.app import templates


@pytest.mark.parametrize(
    "status,css",
    [
        ("ok", "border-l-[var(--color-status-success)]"),
        ("near", "border-l-[var(--color-status-warning)]"),
        ("bad", "border-l-[var(--color-status-critical)]"),
    ],
)
def test_kpi_card_status_accent(status, css):
    tpl = templates.env.from_string(
        "{% from 'macros/kpi_card.html' import kpi_card %}"
        "{{ kpi_card('L', 0.5, format='percent', status=status) }}"
    )
    assert css in tpl.render(status=status)
