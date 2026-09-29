"""The running app must never depend on the dev-only curation checker or its vendor SDK."""

from __future__ import annotations

import pathlib
import re

_FORBIDDEN = re.compile(r"\b(typesafe_sdk|httpx2|curation_check)\b")


def test_product_never_imports_the_curation_checker() -> None:
    root = pathlib.Path(__file__).resolve().parent.parent.parent
    offenders = [
        str(p.relative_to(root))
        for p in (root / "src" / "idraa").rglob("*.py")
        if _FORBIDDEN.search(p.read_text(encoding="utf-8"))
    ]
    assert offenders == [], f"src/idraa references the dev-only curation checker: {offenders}"
