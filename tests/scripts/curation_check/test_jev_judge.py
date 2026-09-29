"""Jev judge: answer conversion, error mapping, pinned endpoint. Fake modules only (no network, no SDK)."""

from __future__ import annotations

import sys
from types import SimpleNamespace
from typing import Any

import pytest
from scripts.curation_check.judge import JudgeError, JudgeFatalError
from scripts.curation_check.judges.jev import JevJudge


class FakeAuthError(Exception):
    pass


class FakeApiError(Exception):
    def __init__(self, message: str, status: int, request_id: str) -> None:
        super().__init__(message)
        self.status = status
        self.request_id = request_id


class FakeClient:
    def __init__(self, raise_: Exception | None = None) -> None:
        self.raise_ = raise_
        self.closed = False

    def system_one(self, state: Any, questions: Any) -> Any:
        if self.raise_:
            raise self.raise_
        return SimpleNamespace(
            choices={"match": SimpleNamespace(probabilities={"A": 0.9, "B": 0.1})},
            nouls={"fn:x": SimpleNamespace(noul=0.42)},
            usage=SimpleNamespace(input_tokens=321),
            model="jev-1.13.0",
        )

    def close(self) -> None:
        self.closed = True


def _judge(client: FakeClient) -> JevJudge:
    return JevJudge(
        client, model="jev-1.13.0", auth_errors=(FakeAuthError,), api_errors=(FakeApiError,)
    )


def test_answers_are_converted_to_plain_floats_and_close_closes() -> None:
    client = FakeClient()
    judge = _judge(client)
    result = judge.ask("k", {"s": 1}, {"match": {}, "fn:x": {}})
    assert result.answers == {"match": {"A": 0.9, "B": 0.1}, "fn:x": 0.42}
    assert result.input_tokens == 321 and result.model == "jev-1.13.0"
    judge.close()
    assert client.closed


def test_auth_errors_stop_the_run_with_the_setup_hint() -> None:  # SC-I3
    with pytest.raises(JudgeFatalError, match="add-generic-password"):
        _judge(FakeClient(FakeAuthError("401 invalid key"))).ask("k", {}, {})


def test_api_errors_fail_only_the_item_and_omit_the_server_text() -> None:  # S-N1
    err = FakeApiError(
        "422 long server message that may echo request content", status=422, request_id="req-9"
    )
    with pytest.raises(JudgeError) as exc:
        _judge(FakeClient(err)).ask("k", {}, {})
    assert str(exc.value) == "FakeApiError status=422 request_id=req-9"


def test_from_sdk_pins_the_endpoint_and_ignores_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # S-B1
    captured: dict[str, Any] = {}

    class FakeTypeSafeError(Exception):
        pass

    def fake_client(**kwargs: Any) -> Any:
        captured["client"] = kwargs
        return FakeClient()

    def fake_http_client(**kwargs: Any) -> Any:
        captured["http"] = kwargs
        return object()

    fake_ts = SimpleNamespace(
        RetryPolicy=lambda **kw: ("retry", kw),
        TypeSafeClient=fake_client,
        TypeSafeError=FakeTypeSafeError,
        TypeSafeAuthenticationError=type("Auth", (FakeTypeSafeError,), {}),
        TypeSafePermissionDeniedError=type("Perm", (FakeTypeSafeError,), {}),
    )
    monkeypatch.setitem(sys.modules, "typesafe_sdk", fake_ts)
    monkeypatch.setitem(sys.modules, "httpx2", SimpleNamespace(Client=fake_http_client))
    monkeypatch.setenv("TYPESAFE_BASE_URL", "http://127.0.0.1:9999")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:8888")
    JevJudge.from_sdk("jev-1.13.0", api_key="k-123")
    assert captured["client"]["base_url"] == "https://api.typesafe.ai"
    assert captured["client"]["api_key"] == "k-123"
    assert captured["client"]["model"] == "jev-1.13.0"
    assert captured["http"]["trust_env"] is False
    assert captured["client"]["retry"] == ("retry", {"max_retries": 2, "api_timeout_error": False})


def test_real_sdk_client_is_pinned_and_env_proof(
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # S-B1: guards SDK upgrades
    pytest.importorskip("typesafe_sdk")  # runs only where the curation extra is installed
    monkeypatch.setenv("TYPESAFE_BASE_URL", "http://127.0.0.1:9999")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:8888")
    judge = JevJudge.from_sdk(
        "jev-1.13.0", api_key="not-a-real-key"
    )  # builds the client; sends nothing
    try:
        sdk = judge._client
        assert sdk._config.base_url == "https://api.typesafe.ai"
        assert sdk._http_client.trust_env is False
        assert [
            t for t in sdk._http_client._mounts.values() if t is not None
        ] == []  # no proxy transports
    finally:
        judge.close()


def test_from_sdk_without_the_sdk_is_fatal_with_install_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "typesafe_sdk", None)  # forces ImportError
    with pytest.raises(JudgeFatalError, match="uv sync --extra curation"):
        JevJudge.from_sdk("jev-1.13.0", api_key="k")
