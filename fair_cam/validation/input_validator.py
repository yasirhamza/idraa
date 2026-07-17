"""
Comprehensive Input Validation System for FAIR-CAM
Validates user inputs across all forms and interfaces with helpful error messages
"""

import re
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Any

import pandas as pd


class ValidationSeverity(Enum):
    """Validation issue severity levels"""

    ERROR = "error"  # Prevents processing
    WARNING = "warning"  # Allows processing but flags issue
    INFO = "info"  # Informational guidance
    SUCCESS = "success"  # Validation passed


@dataclass
class ValidationResult:
    """Result of a validation check"""

    field_name: str
    severity: ValidationSeverity
    message: str
    suggested_value: Any | None = None
    help_text: str | None = None


@dataclass
class ValidationSummary:
    """Summary of all validation results"""

    is_valid: bool
    results: list[ValidationResult]
    error_count: int
    warning_count: int
    info_count: int

    @property
    def has_errors(self) -> bool:
        return self.error_count > 0

    @property
    def has_warnings(self) -> bool:
        return self.warning_count > 0


class FAIRCAMValidator:
    """Comprehensive validation system for FAIR-CAM inputs"""

    def __init__(self) -> None:
        # Validation rules and patterns
        self.control_id_pattern = re.compile(r"^[A-Z0-9]+-[A-Z0-9]+-[0-9]{3}$")
        self.valid_domains = ["loss_event", "variance_management", "decision_support"]
        self.valid_control_types = [
            "technical",
            "administrative",
            "physical",
            "preventive",
            "detective",
            "corrective",
        ]
        self.valid_risk_ratings = ["Critical", "High", "Medium", "Low", "Very Low"]

        # Reasonable value ranges
        self.value_ranges = {
            "control_strength": (0.0, 1.0),
            "control_reliability": (0.0, 1.0),
            "control_coverage": (0.0, 1.0),
            "annual_cost": (0, 100_000_000),
            "initial_cost": (0, 50_000_000),
            "tef_value": (0, 1000),
            "loss_magnitude": (0, 1_000_000_000),
            "probability": (0.0, 1.0),
            "percentage": (0.0, 100.0),
        }

    def validate_control_data(self, control_data: dict[str, Any]) -> ValidationSummary:
        """Validate control configuration data"""
        results = []

        # Control ID validation
        results.extend(self._validate_control_id(control_data.get("control_id", "")))

        # Control name validation
        results.extend(self._validate_control_name(control_data.get("name", "")))

        # Domain validation
        results.extend(self._validate_domain(control_data.get("domain", "")))

        # Control type validation
        results.extend(self._validate_control_type(control_data.get("control_type", "")))

        # Effectiveness values validation
        results.extend(self._validate_effectiveness_values(control_data))

        # Cost validation
        results.extend(self._validate_cost_data(control_data.get("cost_model", {})))

        # Compliance mappings validation
        results.extend(self._validate_compliance_mappings(control_data))

        return self._create_summary(results)

    def validate_risk_parameters(self, risk_data: dict[str, Any]) -> ValidationSummary:
        """Validate risk parameter inputs"""
        results = []

        # Threat Event Frequency validation
        results.extend(self._validate_tef_parameters(risk_data.get("threat_event_frequency", {})))

        # Primary Loss validation
        results.extend(
            self._validate_loss_parameters(risk_data.get("primary_loss", {}), "Primary Loss")
        )

        # Secondary Loss validation — optional field; skip entirely when absent
        # or None so callers that omit secondary_loss do not receive a spurious
        # ERROR for a missing-but-optional field.
        secondary_loss_data = risk_data.get("secondary_loss")
        if secondary_loss_data is not None:
            results.extend(self._validate_loss_parameters(secondary_loss_data, "Secondary Loss"))

        # Distribution parameters validation
        results.extend(self._validate_distribution_parameters(risk_data))

        return self._create_summary(results)

    def validate_scenario_data(self, scenario_data: dict[str, Any]) -> ValidationSummary:
        """Validate threat scenario data"""
        results = []

        # Basic scenario information
        results.extend(self._validate_scenario_basic_info(scenario_data))

        # Risk assessment values
        results.extend(self._validate_risk_assessment(scenario_data))

        # Organizational context
        results.extend(self._validate_organizational_context(scenario_data))

        # Timeline and metadata
        results.extend(self._validate_scenario_metadata(scenario_data))

        return self._create_summary(results)

    def validate_excel_import_data(self, df: pd.DataFrame, sheet_type: str) -> ValidationSummary:
        """Validate Excel import data"""
        results = []

        if sheet_type == "controls":
            results.extend(self._validate_excel_controls_sheet(df))
        elif sheet_type == "risk_parameters":
            results.extend(self._validate_excel_risk_sheet(df))
        elif sheet_type == "scenarios":
            results.extend(self._validate_excel_scenarios_sheet(df))

        return self._create_summary(results)

    def validate_streamlit_form_data(
        self, form_data: dict[str, Any], form_type: str
    ) -> ValidationSummary:
        """Validate Streamlit form inputs"""
        results = []

        if form_type == "quick_start":
            results.extend(self._validate_quick_start_form(form_data))
        elif form_type == "control_wizard":
            results.extend(self._validate_control_wizard_form(form_data))
        elif form_type == "scenario_manager":
            results.extend(self._validate_scenario_manager_form(form_data))

        return self._create_summary(results)

    # Private validation methods
    def _validate_control_id(self, control_id: str) -> list[ValidationResult]:
        """Validate control ID format"""
        results = []

        if not control_id or not control_id.strip():
            results.append(
                ValidationResult(
                    field_name="control_id",
                    severity=ValidationSeverity.ERROR,
                    message="Control ID is required",
                    help_text="Use format: XXX-YYY-000 (e.g., MFG-NET-001)",
                )
            )
        elif not self.control_id_pattern.match(control_id):
            results.append(
                ValidationResult(
                    field_name="control_id",
                    severity=ValidationSeverity.ERROR,
                    message="Invalid Control ID format",
                    suggested_value=self._suggest_control_id_format(control_id),
                    help_text="Use format: PREFIX-CATEGORY-NUMBER (e.g., MFG-NET-001)",
                )
            )
        else:
            results.append(
                ValidationResult(
                    field_name="control_id",
                    severity=ValidationSeverity.SUCCESS,
                    message="Control ID format is valid",
                )
            )

        return results

    def _validate_control_name(self, name: str) -> list[ValidationResult]:
        """Validate control name"""
        results = []

        if not name or not name.strip():
            results.append(
                ValidationResult(
                    field_name="name",
                    severity=ValidationSeverity.ERROR,
                    message="Control name is required",
                    help_text="Provide a descriptive name for the control",
                )
            )
        elif len(name.strip()) < 5:
            results.append(
                ValidationResult(
                    field_name="name",
                    severity=ValidationSeverity.WARNING,
                    message="Control name is very short",
                    help_text="Consider a more descriptive name (5+ characters)",
                )
            )
        elif len(name) > 100:
            results.append(
                ValidationResult(
                    field_name="name",
                    severity=ValidationSeverity.WARNING,
                    message="Control name is very long",
                    suggested_value=name[:97] + "...",
                    help_text="Consider shortening to under 100 characters",
                )
            )
        else:
            results.append(
                ValidationResult(
                    field_name="name",
                    severity=ValidationSeverity.SUCCESS,
                    message="Control name is valid",
                )
            )

        return results

    def _validate_domain(self, domain: str) -> list[ValidationResult]:
        """Validate control domain"""
        results = []

        if not domain:
            results.append(
                ValidationResult(
                    field_name="domain",
                    severity=ValidationSeverity.ERROR,
                    message="Control domain is required",
                    help_text=f"Choose from: {', '.join(self.valid_domains)}",
                )
            )
        elif domain.lower() not in self.valid_domains:
            closest_match = self._find_closest_match(domain.lower(), self.valid_domains)
            results.append(
                ValidationResult(
                    field_name="domain",
                    severity=ValidationSeverity.ERROR,
                    message=f"Invalid control domain: {domain}",
                    suggested_value=closest_match,
                    help_text=f"Valid domains: {', '.join(self.valid_domains)}",
                )
            )
        else:
            results.append(
                ValidationResult(
                    field_name="domain",
                    severity=ValidationSeverity.SUCCESS,
                    message="Control domain is valid",
                )
            )

        return results

    def _validate_control_type(self, control_type: str) -> list[ValidationResult]:
        """Validate control type"""
        results = []

        if not control_type:
            results.append(
                ValidationResult(
                    field_name="control_type",
                    severity=ValidationSeverity.ERROR,
                    message="Control type is required",
                    help_text=f"Choose from: {', '.join(self.valid_control_types)}",
                )
            )
        elif control_type.lower() not in self.valid_control_types:
            closest_match = self._find_closest_match(control_type.lower(), self.valid_control_types)
            results.append(
                ValidationResult(
                    field_name="control_type",
                    severity=ValidationSeverity.ERROR,
                    message=f"Invalid control type: {control_type}",
                    suggested_value=closest_match,
                    help_text=f"Valid types: {', '.join(self.valid_control_types)}",
                )
            )
        else:
            results.append(
                ValidationResult(
                    field_name="control_type",
                    severity=ValidationSeverity.SUCCESS,
                    message="Control type is valid",
                )
            )

        return results

    def _validate_effectiveness_values(
        self, control_data: dict[str, Any]
    ) -> list[ValidationResult]:
        """Validate control effectiveness values"""
        results = []

        effectiveness_fields = ["control_strength", "control_reliability", "control_coverage"]

        for field in effectiveness_fields:
            value = control_data.get(field)
            if value is None:
                results.append(
                    ValidationResult(
                        field_name=field,
                        severity=ValidationSeverity.ERROR,
                        message=f"{field.replace('_', ' ').title()} is required",
                        help_text="Value should be between 0.0 and 1.0",
                    )
                )
            elif not isinstance(value, (int, float)):
                results.append(
                    ValidationResult(
                        field_name=field,
                        severity=ValidationSeverity.ERROR,
                        message=f"{field.replace('_', ' ').title()} must be a number",
                        help_text="Enter a decimal value between 0.0 and 1.0",
                    )
                )
            elif not (0.0 <= value <= 1.0):
                results.append(
                    ValidationResult(
                        field_name=field,
                        severity=ValidationSeverity.ERROR,
                        message=f"{field.replace('_', ' ').title()} must be between 0.0 and 1.0",
                        suggested_value=max(0.0, min(1.0, value)),
                        help_text="1.0 = 100% effective, 0.0 = 0% effective",
                    )
                )
            elif value < 0.1:
                results.append(
                    ValidationResult(
                        field_name=field,
                        severity=ValidationSeverity.WARNING,
                        message=f"{field.replace('_', ' ').title()} is very low ({value:.1%})",
                        help_text="Consider if this control provides meaningful risk reduction",
                    )
                )
            else:
                results.append(
                    ValidationResult(
                        field_name=field,
                        severity=ValidationSeverity.SUCCESS,
                        message=f"{field.replace('_', ' ').title()} is valid",
                    )
                )

        return results

    def _validate_cost_data(self, cost_data: dict[str, Any]) -> list[ValidationResult]:
        """Validate cost model data"""
        results = []

        cost_fields = {
            "initial_cost": "Initial Cost",
            "annual_operating_cost": "Annual Operating Cost",
            "maintenance_cost": "Maintenance Cost",
        }

        for field, display_name in cost_fields.items():
            value = cost_data.get(field)
            if value is None:
                results.append(
                    ValidationResult(
                        field_name=field,
                        severity=ValidationSeverity.WARNING,
                        message=f"{display_name} not specified",
                        help_text="Consider providing cost information for ROI analysis",
                    )
                )
            elif not isinstance(value, (int, float)) or value < 0:
                results.append(
                    ValidationResult(
                        field_name=field,
                        severity=ValidationSeverity.ERROR,
                        message=f"{display_name} must be a positive number",
                        help_text="Enter cost in dollars (e.g., 100000 for $100,000)",
                    )
                )
            elif value > self.value_ranges.get(field, (0, 100_000_000))[1]:
                max_val = self.value_ranges.get(field, (0, 100_000_000))[1]
                results.append(
                    ValidationResult(
                        field_name=field,
                        severity=ValidationSeverity.WARNING,
                        message=f"{display_name} seems unusually high (${value:,.0f})",
                        help_text=f"Typical range is under ${max_val:,.0f}",
                    )
                )
            else:
                results.append(
                    ValidationResult(
                        field_name=field,
                        severity=ValidationSeverity.SUCCESS,
                        message=f"{display_name} is valid",
                    )
                )

        return results

    def _validate_compliance_mappings(self, control_data: dict[str, Any]) -> list[ValidationResult]:
        """Validate compliance framework mappings"""
        results = []

        mapping_fields = {
            "nist_mappings": "NIST Framework Mappings",
            "cis_mappings": "CIS Controls Mappings",
            "iso27001_mappings": "ISO 27001 Mappings",
        }

        for field, display_name in mapping_fields.items():
            mappings = control_data.get(field, [])
            if mappings and len(mappings) > 0:
                # Validate mapping format
                invalid_mappings = []
                for mapping in mappings:
                    if not isinstance(mapping, str) or len(mapping.strip()) < 2:
                        invalid_mappings.append(mapping)

                if invalid_mappings:
                    results.append(
                        ValidationResult(
                            field_name=field,
                            severity=ValidationSeverity.WARNING,
                            message=f"Invalid {display_name}: {invalid_mappings}",
                            help_text="Mappings should be valid framework identifiers",
                        )
                    )
                else:
                    results.append(
                        ValidationResult(
                            field_name=field,
                            severity=ValidationSeverity.SUCCESS,
                            message=f"{display_name} format is valid",
                        )
                    )
            else:
                results.append(
                    ValidationResult(
                        field_name=field,
                        severity=ValidationSeverity.INFO,
                        message=f"{display_name} not provided",
                        help_text="Consider mapping to compliance frameworks for audit purposes",
                    )
                )

        return results

    def _validate_tef_parameters(self, tef_data: dict[str, Any]) -> list[ValidationResult]:
        """Validate Threat Event Frequency parameters"""
        results = []

        if not tef_data:
            results.append(
                ValidationResult(
                    field_name="threat_event_frequency",
                    severity=ValidationSeverity.ERROR,
                    message="Threat Event Frequency parameters are required",
                    help_text="Provide low, mode, and high estimates",
                )
            )
            return results

        # Check for PERT distribution parameters
        if "low" in tef_data and "mode" in tef_data and "high" in tef_data:
            low, mode, high = tef_data["low"], tef_data["mode"], tef_data["high"]

            # Validate individual values
            for param, value in [("low", low), ("mode", mode), ("high", high)]:
                if not isinstance(value, (int, float)) or value < 0:
                    results.append(
                        ValidationResult(
                            field_name=f"tef_{param}",
                            severity=ValidationSeverity.ERROR,
                            message=f"TEF {param} must be a positive number",
                            help_text="Enter annual frequency (e.g., 2.5 for 2.5 events per year)",
                        )
                    )

            # Validate ordering
            if (
                isinstance(low, (int, float))
                and isinstance(mode, (int, float))
                and isinstance(high, (int, float))
            ):
                if not (low <= mode <= high):
                    results.append(
                        ValidationResult(
                            field_name="threat_event_frequency",
                            severity=ValidationSeverity.ERROR,
                            message="TEF parameters must be ordered: low ≤ mode ≤ high",
                            suggested_value={
                                "low": min(low, mode, high),
                                "mode": sorted([low, mode, high])[1],
                                "high": max(low, mode, high),
                            },
                            help_text="Ensure low estimate ≤ most likely ≤ high estimate",
                        )
                    )

                # Check reasonableness
                if high > 365:
                    results.append(
                        ValidationResult(
                            field_name="tef_high",
                            severity=ValidationSeverity.WARNING,
                            message=f"TEF high value ({high}) exceeds daily occurrence",
                            help_text="Consider if this threat could realistically occur more than daily",
                        )
                    )

        return results

    def _validate_loss_parameters(
        self, loss_data: dict[str, Any], loss_type: str
    ) -> list[ValidationResult]:
        """Validate loss magnitude parameters"""
        results = []

        if not loss_data:
            results.append(
                ValidationResult(
                    field_name=f"{loss_type.lower().replace(' ', '_')}_loss",
                    severity=ValidationSeverity.ERROR,
                    message=f"{loss_type} parameters are required",
                    help_text="Provide loss magnitude estimates",
                )
            )
            return results

        # Check for PERT distribution parameters
        if "low" in loss_data and "mode" in loss_data and "high" in loss_data:
            low, mode, high = loss_data["low"], loss_data["mode"], loss_data["high"]

            # Validate individual values
            for param, value in [("low", low), ("mode", mode), ("high", high)]:
                if not isinstance(value, (int, float)) or value < 0:
                    results.append(
                        ValidationResult(
                            field_name=f"{loss_type.lower()}_{param}",
                            severity=ValidationSeverity.ERROR,
                            message=f"{loss_type} {param} must be a positive number",
                            help_text="Enter dollar amount (e.g., 100000 for $100,000)",
                        )
                    )

            # Validate ordering
            if (
                isinstance(low, (int, float))
                and isinstance(mode, (int, float))
                and isinstance(high, (int, float))
            ):
                if not (low <= mode <= high):
                    results.append(
                        ValidationResult(
                            field_name=f"{loss_type.lower().replace(' ', '_')}_loss",
                            severity=ValidationSeverity.ERROR,
                            message=f"{loss_type} parameters must be ordered: low ≤ mode ≤ high",
                            suggested_value={
                                "low": min(low, mode, high),
                                "mode": sorted([low, mode, high])[1],
                                "high": max(low, mode, high),
                            },
                            help_text="Ensure low estimate ≤ most likely ≤ high estimate",
                        )
                    )

                # Check reasonableness for primary loss
                if loss_type == "Primary Loss" and high > 1_000_000_000:
                    results.append(
                        ValidationResult(
                            field_name="primary_loss_high",
                            severity=ValidationSeverity.WARNING,
                            message=f"Primary loss high value (${high:,.0f}) is extremely large",
                            help_text="Verify this represents realistic maximum loss scenario",
                        )
                    )

                # Check secondary vs primary relationship
                if loss_type == "Secondary Loss" and mode > 10_000_000:
                    results.append(
                        ValidationResult(
                            field_name="secondary_loss_mode",
                            severity=ValidationSeverity.WARNING,
                            message=f"Secondary loss (${mode:,.0f}) seems high",
                            help_text="Secondary losses are typically smaller than primary losses",
                        )
                    )

        return results

    def _validate_distribution_parameters(
        self, risk_data: dict[str, Any]
    ) -> list[ValidationResult]:
        """Validate distribution-specific parameters"""
        results = []

        # This would validate specific distribution parameters
        # (normal, lognormal, etc.) based on selected distribution type
        distribution_type = risk_data.get("distribution_type", "pert")

        if distribution_type not in ["pert", "normal", "lognormal", "uniform", "poisson", "pareto"]:
            results.append(
                ValidationResult(
                    field_name="distribution_type",
                    severity=ValidationSeverity.ERROR,
                    message=f"Unsupported distribution type: {distribution_type}",
                    help_text="Use: pert, normal, lognormal, uniform, poisson, or pareto",
                )
            )

        return results

    def _validate_scenario_basic_info(
        self, scenario_data: dict[str, Any]
    ) -> list[ValidationResult]:
        """Validate basic scenario information"""
        results = []

        # Scenario name
        name = scenario_data.get("name", "")
        if not name or not name.strip():
            results.append(
                ValidationResult(
                    field_name="name",
                    severity=ValidationSeverity.ERROR,
                    message="Scenario name is required",
                    help_text="Provide a descriptive name for the threat scenario",
                )
            )

        # Description
        description = scenario_data.get("description", "")
        if not description or len(description.strip()) < 10:
            results.append(
                ValidationResult(
                    field_name="description",
                    severity=ValidationSeverity.WARNING,
                    message="Scenario description is missing or too brief",
                    help_text="Provide a detailed description of the threat scenario",
                )
            )

        return results

    def _validate_risk_assessment(self, scenario_data: dict[str, Any]) -> list[ValidationResult]:
        """Validate risk assessment values"""
        results = []

        risk_rating = scenario_data.get("risk_rating", "")
        if risk_rating not in self.valid_risk_ratings:
            results.append(
                ValidationResult(
                    field_name="risk_rating",
                    severity=ValidationSeverity.ERROR,
                    message=f"Invalid risk rating: {risk_rating}",
                    help_text=f"Use one of: {', '.join(self.valid_risk_ratings)}",
                )
            )

        return results

    def _validate_organizational_context(
        self, scenario_data: dict[str, Any]
    ) -> list[ValidationResult]:
        """Validate organizational context"""
        results = []

        org_sizes = scenario_data.get("applicable_org_sizes", [])
        if not org_sizes:
            results.append(
                ValidationResult(
                    field_name="applicable_org_sizes",
                    severity=ValidationSeverity.WARNING,
                    message="No organization sizes specified",
                    help_text="Specify which organization sizes this scenario applies to",
                )
            )

        return results

    def _validate_scenario_metadata(self, scenario_data: dict[str, Any]) -> list[ValidationResult]:
        """Validate scenario metadata"""
        results = []

        # Created by
        created_by = scenario_data.get("created_by", "")
        if not created_by:
            results.append(
                ValidationResult(
                    field_name="created_by",
                    severity=ValidationSeverity.INFO,
                    message="Creator not specified",
                    help_text="Consider adding creator information for audit trail",
                )
            )

        # Review date
        review_date = scenario_data.get("review_date")
        if review_date and isinstance(review_date, (str, date)):
            try:
                if isinstance(review_date, str):
                    review_date = datetime.strptime(review_date, "%Y-%m-%d").date()

                if review_date < date.today():
                    results.append(
                        ValidationResult(
                            field_name="review_date",
                            severity=ValidationSeverity.WARNING,
                            message="Review date is in the past",
                            suggested_value=date.today(),
                            help_text="Consider updating review date to ensure current relevance",
                        )
                    )
            except ValueError:
                results.append(
                    ValidationResult(
                        field_name="review_date",
                        severity=ValidationSeverity.ERROR,
                        message="Invalid review date format",
                        help_text="Use YYYY-MM-DD format",
                    )
                )

        return results

    def _validate_excel_controls_sheet(self, df: pd.DataFrame) -> list[ValidationResult]:
        """Validate Excel controls sheet"""
        results = []

        required_columns = ["Control Name", "Domain", "Control Type"]
        missing_columns = [col for col in required_columns if col not in df.columns]

        if missing_columns:
            results.append(
                ValidationResult(
                    field_name="excel_structure",
                    severity=ValidationSeverity.ERROR,
                    message=f"Missing required columns: {missing_columns}",
                    help_text="Ensure Excel sheet has all required control columns",
                )
            )

        # Validate data completeness and values
        for idx, row in df.iterrows():
            # Check control name
            control_name = row.get("Control Name", "")
            if pd.isna(control_name) or str(control_name).strip() == "":
                results.append(
                    ValidationResult(
                        field_name=f"row_{idx + 2}_control_name",
                        severity=ValidationSeverity.ERROR,
                        message=f"Row {idx + 2}: Missing control name",
                        help_text="Control name is required for each row",
                    )
                )

            # Check domain
            domain = row.get("Domain", "")
            if not pd.isna(domain) and str(domain).lower() not in self.valid_domains:
                results.append(
                    ValidationResult(
                        field_name=f"row_{idx + 2}_domain",
                        severity=ValidationSeverity.ERROR,
                        message=f"Row {idx + 2}: Invalid domain '{domain}'",
                        help_text=f"Valid domains: {', '.join(self.valid_domains)}",
                    )
                )

            # Check control type
            control_type = row.get("Control Type", "")
            if not pd.isna(control_type) and str(control_type).strip() == "":
                results.append(
                    ValidationResult(
                        field_name=f"row_{idx + 2}_control_type",
                        severity=ValidationSeverity.ERROR,
                        message=f"Row {idx + 2}: Missing control type",
                        help_text="Control type is required for each row",
                    )
                )

        return results

    def _validate_excel_risk_sheet(self, df: pd.DataFrame) -> list[ValidationResult]:
        """Validate Excel risk parameters sheet"""
        results = []

        required_params = [
            "TEF Low",
            "TEF Mode",
            "TEF High",
            "Primary Loss Low",
            "Primary Loss Mode",
            "Primary Loss High",
        ]

        if "Parameter" in df.columns and "Value" in df.columns:
            available_params = df["Parameter"].tolist()
            missing_params = [param for param in required_params if param not in available_params]

            if missing_params:
                results.append(
                    ValidationResult(
                        field_name="risk_parameters",
                        severity=ValidationSeverity.WARNING,
                        message=f"Missing risk parameters: {missing_params}",
                        help_text="Consider providing all FAIR risk parameters",
                    )
                )

        return results

    def _validate_excel_scenarios_sheet(self, df: pd.DataFrame) -> list[ValidationResult]:
        """Validate Excel scenarios sheet"""
        results = []

        required_columns = ["Scenario Name", "Description", "Risk Rating"]
        missing_columns = [col for col in required_columns if col not in df.columns]

        if missing_columns:
            results.append(
                ValidationResult(
                    field_name="scenario_structure",
                    severity=ValidationSeverity.ERROR,
                    message=f"Missing scenario columns: {missing_columns}",
                    help_text="Ensure scenario sheet has required columns",
                )
            )

        return results

    def _validate_quick_start_form(self, form_data: dict[str, Any]) -> list[ValidationResult]:
        """Validate Quick Start Wizard form"""
        results = []

        # Scenario selection
        scenario = form_data.get("scenario")
        if not scenario:
            results.append(
                ValidationResult(
                    field_name="scenario",
                    severity=ValidationSeverity.ERROR,
                    message="Threat scenario selection is required",
                    help_text="Select a threat scenario to analyze",
                )
            )

        # Organization size
        org_size = form_data.get("org_size")
        if not org_size:
            results.append(
                ValidationResult(
                    field_name="org_size",
                    severity=ValidationSeverity.ERROR,
                    message="Organization size is required",
                    help_text="Select your organization size for appropriate scaling",
                )
            )

        # Budget constraint
        budget = form_data.get("budget_constraint")
        if budget is not None and (not isinstance(budget, (int, float)) or budget <= 0):
            results.append(
                ValidationResult(
                    field_name="budget_constraint",
                    severity=ValidationSeverity.ERROR,
                    message="Budget constraint must be a positive number",
                    help_text="Enter annual security budget in dollars",
                )
            )

        return results

    def _validate_control_wizard_form(self, form_data: dict[str, Any]) -> list[ValidationResult]:
        """Validate Control Wizard form"""
        results = []

        # Use existing control validation logic
        results.extend(self.validate_control_data(form_data).results)

        return results

    def _validate_scenario_manager_form(self, form_data: dict[str, Any]) -> list[ValidationResult]:
        """Validate Scenario Manager form"""
        results = []

        # Use existing scenario validation logic
        results.extend(self.validate_scenario_data(form_data).results)

        return results

    # Helper methods
    def _suggest_control_id_format(self, control_id: str) -> str:
        """Suggest properly formatted control ID"""
        # Simple suggestion logic
        parts = control_id.upper().replace("_", "-").split("-")
        if len(parts) >= 2:
            prefix = parts[0][:3] if len(parts[0]) > 3 else parts[0]
            category = parts[1][:3] if len(parts[1]) > 3 else parts[1]
            number = "001"
            return f"{prefix}-{category}-{number}"
        return "MFG-CTL-001"

    def _find_closest_match(self, value: str, valid_options: list[str]) -> str:
        """Find closest matching option"""
        # Simple similarity matching
        from difflib import get_close_matches

        matches = get_close_matches(value, valid_options, n=1, cutoff=0.3)
        return matches[0] if matches else valid_options[0]

    def _create_summary(self, results: list[ValidationResult]) -> ValidationSummary:
        """Create validation summary from results"""
        error_count = sum(1 for r in results if r.severity == ValidationSeverity.ERROR)
        warning_count = sum(1 for r in results if r.severity == ValidationSeverity.WARNING)
        info_count = sum(1 for r in results if r.severity == ValidationSeverity.INFO)

        is_valid = error_count == 0

        return ValidationSummary(
            is_valid=is_valid,
            results=results,
            error_count=error_count,
            warning_count=warning_count,
            info_count=info_count,
        )


# Global validator instance
fair_cam_validator = FAIRCAMValidator()
