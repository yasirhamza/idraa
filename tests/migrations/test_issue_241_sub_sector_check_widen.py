"""Issue #241 -- ``organizations.industry_sub_sector`` CHECK widened to 39 values.

``922b63358719`` installed a named CHECK (``industry_sub_sector_enum``) over the
original 8 ``IndustrySubSector`` members. ``3e0c10ecedb3`` widens it to the full
39-member enum so an organisation can be saved with any library sub-sector.
Upgrade writes no data; downgrade narrows back to 8 and must refuse -- naming the
offending rows -- while any organisation holds one of the 31 added members.

Mirrors the pytest-alembic fixture pattern of ``test_audit_action_widen.py``:
drives ``command.upgrade`` / ``command.downgrade`` against an isolated SQLite
file, then inspects the resulting schema through the sync engine.
"""

from __future__ import annotations

import re
import uuid

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import Engine

from idraa.models.enums import IndustrySubSector

_CONSTRAINT = "industry_sub_sector_enum"
_PREV_HEAD = "e3a7c5b1d9f2"
_THIS_REV = "3e0c10ecedb3"
_ORIGINAL_8 = frozenset(
    {
        "oil_and_gas",
        "electric_utility",
        "water_utility",
        "pipeline",
        "chemical_manufacturing",
        "nuclear",
        "process_manufacturing",
        "other",
    }
)
_ALL_39 = frozenset(m.value for m in IndustrySubSector)


def _organizations_ddl(engine: Engine) -> str:
    """Return the stored ``CREATE TABLE`` statement of ``organizations``."""
    with engine.connect() as conn:
        ddl: str = conn.execute(
            sa.text("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'organizations'")
        ).scalar_one()
    return ddl


def _check_values(engine: Engine) -> list[str]:
    """Return the value list of the ``industry_sub_sector_enum`` CHECK, from the DDL."""
    ddl = _organizations_ddl(engine)
    matches = re.findall(
        rf"CONSTRAINT {_CONSTRAINT} CHECK \(industry_sub_sector IN \(([^)]*)\)\)", ddl
    )
    assert len(matches) == 1, f"expected exactly one {_CONSTRAINT} CHECK, DDL was:\n{ddl}"
    return re.findall(r"'([^']*)'", matches[0])


def _insert_org(engine: Engine, sub_sector: str | None) -> str:
    # ``uuid4().hex``: the ORM stores CHAR(32) without hyphens, so a hyphenated
    # raw-text id would be silently unreachable from the ORM (raw-seed UUID foot-gun).
    org_id = uuid.uuid4().hex
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO organizations "
                "(id, created_at, updated_at, name, organization_size, "
                "industry_type, industry_sub_sector, security_maturity, risk_appetite, "
                "preferred_currency, preferred_language, "
                "geographic_regions, compliance_requirements, "
                "regulatory_environment, technology_stack, "
                "has_cyber_insurance) VALUES "
                "(:id, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 'Test Org', "
                "'large', 'healthcare', :sub, 'defined', 'moderate', "
                "'USD', 'en', '[]', '[]', '[]', '[]', 0)"
            ),
            {"id": org_id, "sub": sub_sector},
        )
    return org_id


def _sub_sector_of(engine: Engine, org_id: str) -> str | None:
    with engine.connect() as conn:
        return conn.execute(
            sa.text("SELECT industry_sub_sector FROM organizations WHERE id = :id"),
            {"id": org_id},
        ).scalar_one()


def test_upgrade_widens_the_named_check_to_the_full_enum(
    alembic_config: Config, alembic_engine: Engine
) -> None:
    command.upgrade(alembic_config, "head")

    values = _check_values(alembic_engine)
    assert len(values) == len(set(values)) == 39
    # Parity with the live enum: adding an IndustrySubSector member without a
    # widening migration fails here, not on a user's first save.
    assert set(values) == _ALL_39
    # The sibling CHECK on the same table survives the rebuild.
    assert "CONSTRAINT industrytype CHECK" in _organizations_ddl(alembic_engine)


def test_new_member_persists_and_every_member_is_insertable(
    alembic_config: Config, alembic_engine: Engine
) -> None:
    command.upgrade(alembic_config, "head")

    hospital_id = _insert_org(alembic_engine, "hospital")
    assert _sub_sector_of(alembic_engine, hospital_id) == "hospital"

    # Every enum member, original and added, passes the widened CHECK; NULL still does.
    for member in sorted(_ALL_39):
        org_id = _insert_org(alembic_engine, member)
        assert _sub_sector_of(alembic_engine, org_id) == member
    assert _sub_sector_of(alembic_engine, _insert_org(alembic_engine, None)) is None


def test_unknown_value_is_still_rejected(alembic_config: Config, alembic_engine: Engine) -> None:
    command.upgrade(alembic_config, "head")

    with pytest.raises(sa.exc.IntegrityError):
        _insert_org(alembic_engine, "not_a_sector")


def test_downgrade_is_blocked_while_an_added_member_is_in_use(
    alembic_config: Config, alembic_engine: Engine
) -> None:
    command.upgrade(alembic_config, "head")
    keep_id = _insert_org(alembic_engine, "oil_and_gas")
    offender_id = _insert_org(alembic_engine, "hospital")

    with pytest.raises(RuntimeError) as excinfo:
        command.downgrade(alembic_config, _PREV_HEAD)

    message = str(excinfo.value)
    assert "Downgrade BLOCKED" in message
    assert offender_id in message
    assert "'hospital'" in message
    # The in-range row is not an offender.
    assert keep_id not in message

    # Nothing changed: still at this revision, CHECK still 39-wide, both rows intact.
    with alembic_engine.connect() as conn:
        version: str = conn.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one()
    assert version == _THIS_REV
    assert set(_check_values(alembic_engine)) == _ALL_39
    assert _sub_sector_of(alembic_engine, offender_id) == "hospital"
    assert _sub_sector_of(alembic_engine, keep_id) == "oil_and_gas"


def test_downgrade_succeeds_once_no_added_member_remains(
    alembic_config: Config, alembic_engine: Engine
) -> None:
    command.upgrade(alembic_config, "head")
    keep_id = _insert_org(alembic_engine, "oil_and_gas")
    null_id = _insert_org(alembic_engine, None)
    offender_id = _insert_org(alembic_engine, "hospital")

    with alembic_engine.begin() as conn:
        conn.execute(sa.text("DELETE FROM organizations WHERE id = :id"), {"id": offender_id})

    command.downgrade(alembic_config, _PREV_HEAD)

    # Back to the 8-value CHECK under the same name; surviving rows untouched.
    assert set(_check_values(alembic_engine)) == _ORIGINAL_8
    assert _sub_sector_of(alembic_engine, keep_id) == "oil_and_gas"
    assert _sub_sector_of(alembic_engine, null_id) is None
    with pytest.raises(sa.exc.IntegrityError):
        _insert_org(alembic_engine, "hospital")

    # And the migration is re-appliable after a downgrade.
    command.upgrade(alembic_config, "head")
    assert set(_check_values(alembic_engine)) == _ALL_39
    assert _sub_sector_of(alembic_engine, keep_id) == "oil_and_gas"
