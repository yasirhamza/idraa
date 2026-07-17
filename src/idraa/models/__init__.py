"""SQLAlchemy ORM model exports.

Imported by Alembic ``env.py`` for autogenerate and by ``conftest.py`` so
that ``Base.metadata.create_all()`` sees every table before the test
schema is materialised. Adding a new model means adding it here — if
Alembic can't see it, the migration won't cover it.
"""

from __future__ import annotations

from idraa.models.attack import (
    AttackTactic,
    AttackTechnique,
    ScenarioAttackMapping,
    ScenarioLibraryEntryAttackMapping,
)
from idraa.models.audit_log import AuditLog
from idraa.models.control import Control
from idraa.models.control_function_assignment import ControlFunctionAssignment
from idraa.models.control_library import (
    ControlLibraryEntry,
    ControlLibraryEntryAssignment,
)
from idraa.models.csv_import_preview import CSVImportPreview
from idraa.models.enums import EntityStatus, ScenarioFieldset, UserRole
from idraa.models.framework_crosswalk import (
    FrameworkControl,
    FrameworkControlFairCam,
)
from idraa.models.fx_rate import FxRate
from idraa.models.mixins import IdMixin, OrgMixin, TimestampMixin
from idraa.models.organization import Organization
from idraa.models.overlay import OverlayDefinition, OverlayDefinitionRevision
from idraa.models.risk_analysis_run import RiskAnalysisRun, RunStatus, RunType
from idraa.models.run_samples import RunSamples
from idraa.models.scenario import Scenario
from idraa.models.scenario_control import ScenarioControl
from idraa.models.scenario_library import (
    ScenarioLibraryEntry,
    ScenarioLibraryOverride,
)
from idraa.models.scenario_sme_estimate import ScenarioSMEEstimate
from idraa.models.session import AuthSession
from idraa.models.sme import SubjectMatterExpert
from idraa.models.system_state import SystemState
from idraa.models.user import User
from idraa.models.wizard_draft import WizardDraft

__all__ = [
    "AttackTactic",
    "AttackTechnique",
    "AuditLog",
    "AuthSession",
    "CSVImportPreview",
    "Control",
    "ControlFunctionAssignment",
    "ControlLibraryEntry",
    "ControlLibraryEntryAssignment",
    "EntityStatus",
    "FrameworkControl",
    "FrameworkControlFairCam",
    "FxRate",
    "IdMixin",
    "OrgMixin",
    "Organization",
    "OverlayDefinition",
    "OverlayDefinitionRevision",
    "RiskAnalysisRun",
    "RunSamples",
    "RunStatus",
    "RunType",
    "Scenario",
    "ScenarioAttackMapping",
    "ScenarioControl",
    "ScenarioFieldset",
    "ScenarioLibraryEntry",
    "ScenarioLibraryEntryAttackMapping",
    "ScenarioLibraryOverride",
    "ScenarioSMEEstimate",
    "SubjectMatterExpert",
    "SystemState",
    "TimestampMixin",
    "User",
    "UserRole",
    "WizardDraft",
]
