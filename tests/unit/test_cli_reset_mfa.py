from __future__ import annotations

import pytest

import idraa.__main__ as cli


def test_cli_requires_command() -> None:
    with pytest.raises(SystemExit):
        cli.main([])


def test_cli_dispatches_reset_mfa(monkeypatch: pytest.MonkeyPatch) -> None:
    called: dict[str, str] = {}

    async def _fake(email: str) -> int:
        called["email"] = email
        return 0

    monkeypatch.setattr(cli, "_reset_mfa", _fake)
    assert cli.main(["auth", "reset-mfa", "user@example.com"]) == 0
    assert called["email"] == "user@example.com"


def test_cli_unknown_subcommand_exits() -> None:
    with pytest.raises(SystemExit):
        cli.main(["auth", "frobnicate"])
