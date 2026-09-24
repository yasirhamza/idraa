"""Advisory C4 guard: audit ``changes=`` payloads never carry a raw email.

``services/audit.redact_email`` states the invariant; two user-create sites
(routes/users.py, routes/setup.py) broke it. Any ``changes={...}`` literal
with an email-named key must route the value through ``redact_email``.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "idraa"


def _calls_redact(node: ast.AST) -> bool:
    """Value is a ``redact_email(...)`` call, or a name bound from one
    (``email_redacted = redact_email(user.email)`` in services/users.py)."""
    return any(
        (
            isinstance(n, ast.Call)
            and (getattr(n.func, "id", None) or getattr(n.func, "attr", None)) == "redact_email"
        )
        or (isinstance(n, ast.Name) and "redact" in n.id)
        for n in ast.walk(node)
    )


def test_no_raw_email_in_audit_changes() -> None:
    offenders: list[str] = []
    checked = 0
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for call in ast.walk(tree):
            if not isinstance(call, ast.Call):
                continue
            for kw in call.keywords:
                if kw.arg != "changes" or not isinstance(kw.value, ast.Dict):
                    continue
                for key, value in zip(kw.value.keys, kw.value.values, strict=True):
                    if isinstance(key, ast.Constant) and "email" in str(key.value).lower():
                        checked += 1
                        if not _calls_redact(value):
                            offenders.append(f"{path.relative_to(SRC)}:{call.lineno}")
    assert checked >= 3, "guard found too few email-keyed payloads; it may be vacuous"
    assert not offenders, f"raw email in audit changes= payload: {offenders}"
