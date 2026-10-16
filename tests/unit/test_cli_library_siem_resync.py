from __future__ import annotations

import pytest

import idraa.__main__ as cli


def test_cli_dispatches_library_siem_resync_dry(monkeypatch: pytest.MonkeyPatch) -> None:
    called: dict[str, object] = {}

    async def _fake(*, apply: bool, database_url: str | None) -> int:
        called["apply"], called["database_url"] = apply, database_url
        return 0

    monkeypatch.setattr(cli, "_siem_resync", _fake)
    assert cli.main(["library", "siem-resync"]) == 0
    assert called == {"apply": False, "database_url": None}


def test_cli_dispatches_library_siem_resync_apply_with_url(monkeypatch: pytest.MonkeyPatch) -> None:
    called: dict[str, object] = {}

    async def _fake(*, apply: bool, database_url: str | None) -> int:
        called["apply"], called["database_url"] = apply, database_url
        return 2

    monkeypatch.setattr(cli, "_siem_resync", _fake)
    rc = cli.main(
        ["library", "siem-resync", "--apply", "--database-url", "sqlite+aiosqlite:////tmp/x.db"]
    )
    assert rc == 2
    assert called == {"apply": True, "database_url": "sqlite+aiosqlite:////tmp/x.db"}


def test_cli_unknown_library_subcommand_exits() -> None:
    with pytest.raises(SystemExit):
        cli.main(["library", "frobnicate"])


def test_cli_auth_reset_mfa_still_dispatches(monkeypatch: pytest.MonkeyPatch) -> None:
    called: dict[str, str] = {}

    async def _fake(email: str) -> int:
        called["email"] = email
        return 0

    monkeypatch.setattr(cli, "_reset_mfa", _fake)
    assert cli.main(["auth", "reset-mfa", "user@example.com"]) == 0
    assert called["email"] == "user@example.com"
