"""Read-only scientific rules over detached CSV snapshots; no database or file IO."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import math
from typing import Literal, TypeAlias

from antarctic_sea_ice_dashboard.services.dataset_metadata import is_valid_observation_month
from antarctic_sea_ice_dashboard.services.variables import VARIABLE_DEFINITIONS


RULESET_VERSION = "csv-science-1.0.0"
RAW_VALUE_LIMIT = 512
RuleStatus = Literal["passed", "failed", "blocked", "not_applicable"]
Outcome = Literal["passed", "failed", "blocked"]
Severity = Literal["error", "warning", "info"]
JSONValue: TypeAlias = str | int | float | bool | None | list["JSONValue"] | dict[str, "JSONValue"]


@dataclass(frozen=True)
class SnapshotRow:
    row_id: int
    row_position: int
    cell_values_json: str


@dataclass(frozen=True)
class DatasetSnapshot:
    dataset_id: int
    identity_key: str
    data_revision: int
    column_names_json: str
    declared_row_count: int
    variable_key: str | None
    observation_month: str | None
    unit: str | None
    grid_key: str | None
    rows: tuple[SnapshotRow, ...]


@dataclass(frozen=True)
class ValidationProfile:
    """Server-selected policy; callers cannot submit custom scientific rules."""

    key: str
    version: str
    variable_key: str | None
    required_fields: tuple[str, ...]
    units: tuple[str, ...]
    longitude_convention: str = "[-180, 180], no normalisation"
    missing_policy: str = "Empty cells and all nonfinite numbers fail; no finite fill codes are assumed."


def select_validation_profile(variable_key: str | None) -> ValidationProfile:
    """Select v10 only for its explicit declaration; other variables stay blocked."""
    definition = VARIABLE_DEFINITIONS.get(variable_key or "")
    if definition is not None:
        return ValidationProfile(
            definition.profile_key, definition.profile_version, definition.variable_key,
            ("Latitude", "Longitude", definition.value_field), definition.accepted_units,
            missing_policy=definition.missing_policy,
        )
    return ValidationProfile("generic-csv", "1.0.0", None, ("Latitude", "Longitude"), ())


@dataclass(frozen=True)
class ReferenceGrid:
    """Validated retained coordinates, or an explicit dependency failure."""

    identity: dict[str, JSONValue]
    coordinates: tuple[tuple[float, float], ...] = ()
    dependency_code: str | None = None
    dependency_message: str | None = None
    grid_cell_ids: tuple[int, ...] = ()


@dataclass(frozen=True)
class RuleDefinition:
    rule_code: str
    required: bool
    scope: str
    severity: Severity
    source: str
    configuration_version: str = RULESET_VERSION


RULE_DEFINITIONS = (
    RuleDefinition("CSV_STRUCTURE", True, "all records", "error", "Application ordered-string CSV contract; wind_pipeline/constants.py:CSV_FIELDS"),
    RuleDefinition("RECORD_COVERAGE", True, "whole snapshot", "error", "Application row-count and complete-scan contract"),
    RuleDefinition("VARIABLE_PROFILE", True, "declared variable", "error", "wind_pipeline/native.py scientific_notes; sources.py v10 selection"),
    RuleDefinition("VARIABLE_UNIT", True, "declared variable", "error", "wind_pipeline/sources.py accepted wind units"),
    RuleDefinition("FINITE_NUMBERS", True, "required numeric cells", "error", "wind_pipeline/plotting.py:target_array finite-value requirement"),
    RuleDefinition("COORDINATE_RANGE", True, "geographic coordinate pairs", "error", "Geographic degree limits; signed longitude convention in wind_pipeline/netcdf.py"),
    RuleDefinition("UNIQUE_COORDINATES", True, "valid coordinate pairs", "error", "wind_pipeline/plotting.py:target_array exact duplicate pairs; no tolerance"),
    RuleDefinition("OBSERVATION_MONTH", True, "declared month", "error", "Application Gregorian YYYY-MM declaration; not original acquisition verification"),
    RuleDefinition("MONTH_CONSISTENCY", False, "optional observation_month CSV column", "error", "Application v1 optional column contract; exact YYYY-MM comparison"),
    RuleDefinition("DECLARED_GRID", True, "declared retained grid", "error", "dataset_metadata.py grid registry; wind_pipeline/plotting.py:target_array exact coordinates and order"),
)


@dataclass
class RuleResult:
    definition: RuleDefinition
    status: RuleStatus = "passed"
    reason: str = "All applicable checks completed."
    evaluated_row_count: int = 0
    issue_count: int = 0


@dataclass(frozen=True)
class ValidationIssue:
    issue_code: str
    rule_code: str
    severity: Severity
    snapshot_row_id: int | None
    snapshot_row_position: int | None
    field: str | None
    raw_value: str | None
    raw_value_truncated: bool
    message: str


@dataclass(frozen=True)
class ValidationReport:
    outcome: Outcome
    scanned_row_count: int
    summary: dict[str, JSONValue]
    rule_results: tuple[RuleResult, ...]
    issues: tuple[ValidationIssue, ...]


@dataclass
class _Report:
    rules: dict[str, RuleResult] = field(default_factory=lambda: {
        definition.rule_code: RuleResult(definition) for definition in RULE_DEFINITIONS
    })
    issues: list[ValidationIssue] = field(default_factory=list)

    def issue(
        self, rule: str, code: str, message: str, *, row: SnapshotRow | None = None,
        field_name: str | None = None, raw: str | None = None,
        status: RuleStatus = "failed", severity: Severity = "error",
    ) -> None:
        result = self.rules[rule]
        # A failure takes precedence over a blocked prerequisite for other rows.
        if result.status != "failed":
            result.status = status
            result.reason = message
        result.issue_count += 1
        self.issues.append(ValidationIssue(
            code, rule, severity, row.row_id if row else None,
            row.row_position if row else None, field_name,
            raw[:RAW_VALUE_LIMIT] if raw is not None else None,
            raw is not None and len(raw) > RAW_VALUE_LIMIT, message,
        ))

    def block(self, rule: str, reason: str) -> None:
        if self.rules[rule].status == "passed":
            self.rules[rule].status = "blocked"
            self.rules[rule].reason = reason


def _string_array(raw: str) -> list[str] | None:
    try:
        value = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(value, list) or any(not isinstance(cell, str) for cell in value):
        return None
    return value


def validate_csv_snapshot(
    snapshot: DatasetSnapshot, profile: ValidationProfile, reference: ReferenceGrid,
) -> ValidationReport:
    """Inspect every snapshot row without changing strings, order or declarations.

    The orchestrator supplies a bounded, consistent snapshot and trusted profile.
    Data failures are returned as rules/issues. Unexpected exceptions propagate
    to the orchestrator as execution errors, never scientific failures.
    """
    report = _Report()
    headers = _string_array(snapshot.column_names_json)
    header_valid = bool(headers) and all(name.strip() for name in headers or [])
    if headers is not None and len({name.strip() for name in headers}) != len(headers):
        header_valid = False
    if not header_valid:
        report.issue("CSV_STRUCTURE", "INVALID_COLUMN_STRUCTURE", "Headers must be a nonempty ordered array of unique, nonempty strings.")
    mapping: dict[str, int] = {}
    for name in profile.required_fields:
        candidates = [index for index, header in enumerate(headers or []) if header == name]
        if len(candidates) == 1 and header_valid:
            mapping[name] = candidates[0]
        elif len(candidates) > 1:
            report.issue("CSV_STRUCTURE", "COLUMN_MAPPING_AMBIGUOUS", "Multiple exact columns match a required field.", field_name=name)
        else:
            report.issue("CSV_STRUCTURE", "REQUIRED_COLUMN_MISSING", "The required exact column is unavailable; no fuzzy aliases are inferred.", field_name=name)

    if not snapshot.rows:
        report.issue("RECORD_COVERAGE", "EMPTY_DATASET", "An empty dataset cannot pass scientific checks.")
    if len(snapshot.rows) != snapshot.declared_row_count:
        report.issue("RECORD_COVERAGE", "RECORD_COUNT_MISMATCH", "The scanned record count differs from the snapshot's stored record count.")
    report.rules["RECORD_COVERAGE"].evaluated_row_count = len(snapshot.rows)

    if profile.variable_key is None:
        report.issue("VARIABLE_PROFILE", "VALIDATION_PROFILE_UNSUPPORTED", "No complete scientific profile is defined for this variable; concentration scale and fill codes are not inferred.", status="blocked")
        report.block("VARIABLE_UNIT", "The variable's scientific unit policy is unavailable.")
    elif snapshot.unit is None:
        report.issue("VARIABLE_UNIT", "UNIT_REQUIRED", "Declare the variable's measurement unit.", status="blocked", field_name="unit")
    elif snapshot.unit not in profile.units:
        report.issue("VARIABLE_UNIT", "UNIT_MISMATCH", "The declared unit is not one of the profile's explicit equivalent wind units.", field_name="unit", raw=snapshot.unit)

    month_valid = is_valid_observation_month(snapshot.observation_month)
    if not month_valid:
        report.issue("OBSERVATION_MONTH", "OBSERVATION_MONTH_INVALID", "Declare a valid Gregorian observation month in YYYY-MM format.", field_name="observation_month", raw=snapshot.observation_month, status="blocked" if snapshot.observation_month is None else "failed")
    month_columns = [i for i, name in enumerate(headers or []) if name == "observation_month"]
    month_index = month_columns[0] if len(month_columns) == 1 and header_valid else None
    month_evidence = "user_declaration_only"
    if month_index is None:
        report.rules["MONTH_CONSISTENCY"].status = "not_applicable"
        report.rules["MONTH_CONSISTENCY"].reason = "No unambiguous observation_month column or trusted original source timestamp is available."
        report.issues.append(ValidationIssue("MONTH_UNVERIFIED", "MONTH_CONSISTENCY", "warning", None, None, "observation_month", None, False, "Month validity checks the declaration only; original acquisition time is unverified."))
        report.rules["MONTH_CONSISTENCY"].issue_count += 1
    elif not month_valid:
        report.block("MONTH_CONSISTENCY", "A valid declared month is needed for comparison.")
    else:
        month_evidence = "user_declaration_and_csv_column"

    missing_fields = len(mapping) != len(profile.required_fields)
    if missing_fields:
        report.block("FINITE_NUMBERS", "Required numeric columns are unavailable or ambiguous.")
    coordinate_columns = all(name in mapping for name in ("Latitude", "Longitude"))
    if not coordinate_columns:
        for rule in ("COORDINATE_RANGE", "UNIQUE_COORDINATES", "DECLARED_GRID"):
            report.block(rule, "Unambiguous coordinate columns are required.")

    valid_pairs: list[tuple[float, float] | None] = []
    seen: dict[tuple[float, float], int] = {}
    wind_values: list[float] = []
    missing_values = invalid_values = unavailable_values = 0
    for row in snapshot.rows:
        report.rules["CSV_STRUCTURE"].evaluated_row_count += 1
        cells = _string_array(row.cell_values_json)
        if cells is None or headers is None or len(cells) != len(headers):
            report.issue("CSV_STRUCTURE", "RECORD_FIELD_COUNT_MISMATCH", "The record must contain one string per saved header.", row=row)
            for rule in ("FINITE_NUMBERS", "COORDINATE_RANGE", "UNIQUE_COORDINATES", "DECLARED_GRID"):
                report.block(rule, "Some records have invalid structure; dependent checks could not inspect those records.")
            if month_index is not None:
                report.block("MONTH_CONSISTENCY", "Some records could not be compared because their structure is invalid.")
            valid_pairs.append(None)
            unavailable_values += int(profile.variable_key == "v10")
            continue
        numbers: dict[str, float] = {}
        for name, index in mapping.items():
            raw = cells[index]
            value: float | None = None
            if not raw.strip():
                code = "REQUIRED_VALUE_MISSING"
                missing_values += int(name == "v_wind")
            else:
                try:
                    value = float(raw)
                    code = "NONFINITE_NUMERIC_VALUE" if not math.isfinite(value) else ""
                except (ValueError, OverflowError):
                    code = "INVALID_NUMERIC_VALUE"
                invalid_values += int(name == "v_wind" and bool(code))
            if code:
                report.issue("FINITE_NUMBERS", code, "A required numeric cell must be present, parseable and finite.", row=row, field_name=name, raw=raw)
            elif value is not None:
                numbers[name] = value
        report.rules["FINITE_NUMBERS"].evaluated_row_count += 1
        if "v_wind" in numbers:
            # Signed northward wind is not speed magnitude; no clipping or abs().
            wind_values.append(numbers["v_wind"])
        elif profile.variable_key == "v10" and "v_wind" not in mapping:
            unavailable_values += 1

        pair: tuple[float, float] | None = None
        if "Latitude" in numbers and "Longitude" in numbers:
            latitude, longitude = numbers["Latitude"], numbers["Longitude"]
            report.rules["COORDINATE_RANGE"].evaluated_row_count += 1
            if not -90 <= latitude <= 90:
                report.issue("COORDINATE_RANGE", "LATITUDE_OUT_OF_RANGE", "Latitude must be between -90 and 90 degrees inclusive.", row=row, field_name="Latitude", raw=cells[mapping["Latitude"]])
            if not -180 <= longitude <= 180:
                report.issue("COORDINATE_RANGE", "LONGITUDE_OUT_OF_RANGE", "Longitude must use the profile's signed [-180, 180] degree convention.", row=row, field_name="Longitude", raw=cells[mapping["Longitude"]])
            if -90 <= latitude <= 90 and -180 <= longitude <= 180:
                pair = (latitude, longitude)
        else:
            report.block("COORDINATE_RANGE", "Some coordinate values could not be parsed; their ranges were not checked.")
        valid_pairs.append(pair)
        if pair is None:
            for rule in ("UNIQUE_COORDINATES", "DECLARED_GRID"):
                report.block(rule, "Some coordinates are unavailable or invalid; dependent comparisons were skipped.")
        else:
            report.rules["UNIQUE_COORDINATES"].evaluated_row_count += 1
            if pair in seen:
                report.issue("UNIQUE_COORDINATES", "DUPLICATE_COORDINATE", f"The exact numeric coordinate pair repeats snapshot record position {seen[pair]} (zero-based); no rounding is applied.", row=row)
            else:
                seen[pair] = row.row_position
        if month_index is not None and month_valid:
            report.rules["MONTH_CONSISTENCY"].evaluated_row_count += 1
            raw_month = cells[month_index]
            if not is_valid_observation_month(raw_month) or raw_month != snapshot.observation_month:
                report.issue("MONTH_CONSISTENCY", "OBSERVATION_MONTH_MISMATCH", "The record month must be valid YYYY-MM and equal the declared observation month; this does not establish original acquisition time.", row=row, field_name="observation_month", raw=raw_month)

    if reference.dependency_code is not None:
        report.issue("DECLARED_GRID", reference.dependency_code, reference.dependency_message or "The reference grid dependency is unavailable.", status="blocked")
    else:
        if len(snapshot.rows) != len(reference.coordinates):
            report.issue("DECLARED_GRID", "GRID_CELL_COUNT_MISMATCH", "The number of records differs from the identified retained-cell mask.")
        for index, (row, pair) in enumerate(zip(snapshot.rows, valid_pairs)):
            if pair is None:
                continue
            report.rules["DECLARED_GRID"].evaluated_row_count += 1
            if index >= len(reference.coordinates) or pair != reference.coordinates[index]:
                report.issue("DECLARED_GRID", "GRID_COORDINATE_ORDER_MISMATCH", "Coordinates must exactly match the identified retained grid in canonical row order; no row remapping or tolerance merging is applied.", row=row)

    if not snapshot.rows:
        for rule in ("FINITE_NUMBERS", "COORDINATE_RANGE", "UNIQUE_COORDINATES", "DECLARED_GRID"):
            report.block(rule, "No records were available to execute this rule.")
    rules = tuple(report.rules.values())
    outcome: Outcome = "passed"
    if any(rule.status == "failed" for rule in rules):
        outcome = "failed"
    elif any(rule.definition.required and rule.status != "passed" for rule in rules):
        outcome = "blocked"
    summary: dict[str, JSONValue] = {
        "total_row_count": len(snapshot.rows),
        "declared_row_count": snapshot.declared_row_count,
        "value_field": "v_wind" if profile.variable_key == "v10" else None,
        "valid_numeric_count": len(wind_values),
        "missing_numeric_count": missing_values,
        "invalid_numeric_count": invalid_values,
        "unavailable_numeric_count": unavailable_values,
        "minimum": min(wind_values) if wind_values else None,
        "maximum": max(wind_values) if wind_values else None,
        "statistics_basis": "Finite parseable v_wind samples only; no area weighting or regional mask. Other columns do not affect this numeric count.",
        "month_evidence": month_evidence,
        "acquisition_time_verified": False,
        "longitude_convention": profile.longitude_convention,
        "missing_value_policy": profile.missing_policy,
        "issue_count": len(report.issues),
        "warning_count": sum(issue.severity == "warning" for issue in report.issues),
        "issues_truncated": False,
        "missing_dependencies": [
            {"rule_code": rule.definition.rule_code, "reason": rule.reason}
            for rule in rules if rule.status == "blocked"
        ],
    }
    return ValidationReport(outcome, len(snapshot.rows), summary, rules, tuple(report.issues))
