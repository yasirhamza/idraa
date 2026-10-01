"""Every mapper is in exactly one of ORG_SCOPED_MODELS / NON_ORG_COLUMN_MODELS (lint tripwire)."""

from __future__ import annotations

from scripts.lint_org_scoped_lookups import NON_ORG_COLUMN_MODELS, ORG_SCOPED_MODELS

import idraa.models  # noqa: F401  registers every mapper
from idraa.db import Base


def test_org_scoped_allowlists_match_schema() -> None:
    names = {m.class_.__name__: m.class_ for m in Base.registry.mappers}
    with_org = {n for n, c in names.items() if "organization_id" in c.__table__.columns}
    without_org = set(names) - with_org
    assert with_org == ORG_SCOPED_MODELS, with_org ^ ORG_SCOPED_MODELS
    assert without_org == NON_ORG_COLUMN_MODELS, without_org ^ NON_ORG_COLUMN_MODELS
