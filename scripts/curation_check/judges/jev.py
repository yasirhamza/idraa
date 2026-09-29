"""Jev (TypeSafe System One) judge.

The key arrives as an argument from the CLI, which read it from stdin (scripts/curation-check).
The endpoint is pinned and the HTTP client ignores proxy/cert environment variables, so no
stray environment can redirect the key. Never print or log the client, the key or headers."""

from __future__ import annotations

import time
from typing import Any

from scripts.curation_check.config import JEV_BASE_URL, JEV_TIMEOUT_S
from scripts.curation_check.criteria import Question
from scripts.curation_check.judge import JudgeError, JudgeFatalError, JudgeResult

SETUP_HINT = (
    "check the key: on macOS run `security add-generic-password -s idraa-typesafe-key -a typesafe -U -w` "
    "in your own terminal (or the service named by CURATION_KEYCHAIN_SERVICE); elsewhere set TYPESAFE_API_KEY"
)


def _short(e: BaseException) -> str:
    """Error type, HTTP status and request id only: server text can echo request content."""
    parts = [type(e).__name__]
    status = getattr(e, "status", None)
    request_id = getattr(e, "request_id", None)
    if status is not None:
        parts.append(f"status={status}")
    if request_id:
        parts.append(f"request_id={request_id}")
    return " ".join(parts)


class JevJudge:
    name = "jev"

    def __init__(
        self,
        client: Any,
        *,
        model: str,
        auth_errors: tuple[type[BaseException], ...],
        api_errors: tuple[type[BaseException], ...],
    ) -> None:
        self._client = client
        self.model = model
        self._auth_errors = auth_errors
        self._api_errors = api_errors

    @classmethod
    def from_sdk(cls, model: str, *, api_key: str) -> JevJudge:
        try:
            import httpx2
            import typesafe_sdk as ts
        except ImportError as e:
            raise JudgeFatalError(
                "typesafe-sdk is not installed: run `uv sync --extra curation`"
            ) from e
        try:
            client = ts.TypeSafeClient(
                api_key=api_key,
                model=model,
                base_url=JEV_BASE_URL,
                http_client=httpx2.Client(trust_env=False, timeout=JEV_TIMEOUT_S),
                # explicit, so an SDK upgrade cannot silently change cost; a timed-out request is never re-sent
                retry=ts.RetryPolicy(max_retries=2, api_timeout_error=False),
            )
        except ts.TypeSafeError as e:
            raise JudgeFatalError(
                f"TypeSafe client setup failed ({type(e).__name__}); {SETUP_HINT}"
            ) from e
        return cls(
            client,
            model=model,
            auth_errors=(ts.TypeSafeAuthenticationError, ts.TypeSafePermissionDeniedError),
            api_errors=(ts.TypeSafeError,),
        )

    def close(self) -> None:
        close = getattr(self._client, "close", None)
        if callable(close):
            close()

    def ask(
        self, item_key: str, state: dict[str, Any], questions: dict[str, Question]
    ) -> JudgeResult:
        start = time.perf_counter()
        try:
            resp = self._client.system_one(state, questions)
        except self._auth_errors as e:
            raise JudgeFatalError(
                f"{type(e).__name__}: the API rejected the key; {SETUP_HINT}"
            ) from e
        except self._api_errors as e:
            raise JudgeError(_short(e)) from e
        answers: dict[str, Any] = {
            qid: {k: float(v) for k, v in a.probabilities.items()}
            for qid, a in resp.choices.items()
        }
        answers.update({qid: float(a.noul) for qid, a in resp.nouls.items()})
        usage = getattr(resp, "usage", None)
        return JudgeResult(
            answers=answers,
            input_tokens=getattr(usage, "input_tokens", None),
            model=(str(m)[:40] if (m := getattr(resp, "model", None)) is not None else None),
            ms=(time.perf_counter() - start) * 1000,
        )
