"""Sec-1 R1 mass-assignment defense (Sec-1 PR1 refactor).

Per-model minimum-valid-kwargs dispatch so the unknown-field rejection is
what's actually under test for every model — including WizardStep3Submit,
which has no `name` field (the prior `model_cls(name="X", organization_id=...)`
call would TypeError before exercising the extra="forbid" guard).
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from pydantic import BaseModel

from idraa.schemas.sme import SMECreate, SMERequest, SMEUpdate
from idraa.schemas.wizard_step3 import WizardStep3Submit

REQUEST_MODELS: list[type[BaseModel]] = [SMECreate, SMERequest, SMEUpdate, WizardStep3Submit]
FORBIDDEN_FIELDS = [
    "organization_id",
    "created_by",
    "is_system_owned",
    "created_via",
    "archived_at",
    "archived_by",
]

_MIN_VALID_KWARGS: dict[type[BaseModel], dict[str, Any]] = {
    SMECreate: {"name": "X"},
    SMERequest: {"name": "X"},
    SMEUpdate: {"name": "X"},
    WizardStep3Submit: {
        "tef": {"rows": [{"sme_id": str(uuid4()), "low": 1, "high": 2}]},
        "vuln": {"rows": [{"sme_id": str(uuid4()), "low": 0.1, "high": 0.5}]},
        "pl": {"rows": [{"sme_id": str(uuid4()), "low": 1000, "high": 10000}]},
        "sl": {"rows": []},
        "version_token": 0,
    },
}


@pytest.mark.parametrize("model_cls", REQUEST_MODELS)
@pytest.mark.parametrize("forbidden", FORBIDDEN_FIELDS)
def test_model_has_no_forbidden_field(model_cls: type[BaseModel], forbidden: str) -> None:
    assert forbidden not in model_cls.model_fields, (
        f"{model_cls.__name__} must not declare {forbidden!r}"
    )


@pytest.mark.parametrize("model_cls", REQUEST_MODELS)
def test_model_rejects_unknown_field(model_cls: type[BaseModel]) -> None:
    # Pydantic v2 (verified 2.13) emits "Extra inputs are not permitted"
    # in the message body with the stable error-type code "extra_forbidden"
    # in the same string. Narrowed from the prior 3-alternative regex per
    # code-quality NICE #10 -- the older "Extra fields" Pydantic-v1 phrasing
    # is unreachable on this project and the loose pattern hid the actual
    # wording we depend on.
    base_kwargs = dict(_MIN_VALID_KWARGS[model_cls])
    base_kwargs["organization_id"] = str(uuid4())
    with pytest.raises(Exception, match=r"extra_forbidden"):
        model_cls(**base_kwargs)
