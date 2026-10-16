"""Real alembic-built-schema smoke for scripts/sweep_issue_199_prod_rows.py (the unit fixture
is hand-rolled DDL; this guards the column names against the migrated schema)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "sweep_issue_199_prod_rows.py"
_spec = importlib.util.spec_from_file_location("sweep_issue_199_prod_rows_realschema", _SCRIPT)
assert _spec is not None and _spec.loader is not None
mod = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = mod  # @dataclass + `from __future__ import annotations` (A4-I3)
_spec.loader.exec_module(mod)


def test_sweep_against_alembic_built_head_schema(alembic_config: Config) -> None:
    command.upgrade(alembic_config, "head")
    url = alembic_config.get_main_option("sqlalchemy.url")
    assert url is not None
    db_file = Path(url.removeprefix("sqlite+aiosqlite:///"))
    result = mod.sweep(db_file)
    assert result.entry_state == "post-bump"  # the fresh seed already holds 0.3 at head
    assert result.counts["siem_entry_present"] == 1
    assert {k: v for k, v in result.counts.items() if k != "siem_entry_present"} == {
        k: 0 for k in result.counts if k != "siem_entry_present"
    }
