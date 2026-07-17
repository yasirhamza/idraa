"""F3: breadcrumb_for(request) walks request.url.path into (label, href|None) tuples."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from idraa.utils.breadcrumbs import breadcrumb_for


def _req(path: str) -> SimpleNamespace:
    return SimpleNamespace(url=SimpleNamespace(path=path))


@pytest.mark.parametrize(
    "path,expected",
    [
        ("/", [("Home", None)]),
        ("/controls", [("Home", "/"), ("Controls", None)]),
        (
            "/controls/maintenance",
            [("Home", "/"), ("Controls", "/controls"), ("Maintenance", None)],
        ),
        ("/scenarios", [("Home", "/"), ("Scenarios", None)]),
        ("/analyses/new", [("Home", "/"), ("Analyses", "/analyses"), ("New", None)]),
        ("/users", [("Home", "/"), ("Users", None)]),
    ],
)
def test_breadcrumb_for_known_paths(path: str, expected: list[tuple[str, str | None]]) -> None:
    result = breadcrumb_for(_req(path))
    assert result == expected


def test_breadcrumb_for_uuid_segment_renders_detail_label() -> None:
    result = breadcrumb_for(_req("/controls/0a91b2c3-d4e5-4f67-8901-23456789abcd/edit"))
    assert result == [
        ("Home", "/"),
        ("Controls", "/controls"),
        ("Detail", "/controls/0a91b2c3-d4e5-4f67-8901-23456789abcd"),
        ("Edit", None),
    ]


def test_breadcrumb_for_unknown_path_falls_back_to_titlecased() -> None:
    result = breadcrumb_for(_req("/foo/bar-baz"))
    assert result == [("Home", "/"), ("Foo", "/foo"), ("Bar Baz", None)]
