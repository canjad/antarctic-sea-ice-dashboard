"""Synchronous, revision-bound quality checks and bounded report reads."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
import json
import logging
import re
from typing import Literal, cast
from uuid import uuid4

import reflex as rx
from sqlalchemy import func, insert, select
from sqlalchemy.orm import Session

from antarctic_sea_ice_dashboard.models import QualityCheckRun, QualityIssue, UploadedDataset, UploadedDatasetRow
from antarctic_sea_ice_dashboard.services.csv_import import MAX_ROWS
from antarctic_sea_ice_dashboard.services.csv_validation import (
    DatasetSnapshot, JSONValue, Outcome, RULE_DEFINITIONS, RULESET_VERSION, Severity, SnapshotRow,
    ValidationProfile, ValidationReport, select_validation_profile, validate_csv_snapshot,
)
from antarctic_sea_ice_dashboard.services.dataset_metadata import DatasetMetadata
from antarctic_sea_ice_dashboard.services.uploaded_datasets import DatasetConflict, claim_uploaded_dataset
from antarctic_sea_ice_dashboard.services.validation_reference import load_reference_grid


logger = logging.getLogger(__name__)
LEASE_DURATION = timedelta(minutes=30)
MAX_SNAPSHOT_BYTES = 64 * 1024 * 1024
MAX_ISSUE_PAGE_SIZE = 200
QualityStatus = Literal["not_checked", "running", "passed", "failed", "blocked", "error", "stale"]
RunStatus = Literal["running", "completed", "error"]


class QualityServiceError(ValueError):
    """Stable caller-visible code and safe message; never a driver exception."""

    def __init__(self, code: str, message: str, *, check_run_id: str | None = None):
        super().__init__(message)
        self.code = code
        self.check_run_id = check_run_id


@dataclass(frozen=True)
class QualityCheckResult:
    check_run_id: str
    dataset_id: int
    dataset_identity_key: str
    checked_data_revision: int
    current_data_revision: int
    attempt_number: int
    ruleset_version: str
    profile_key: str
    profile_version: str
    reference_identity: dict[str, JSONValue]
    run_status: RunStatus
    outcome: Outcome | None
    is_current: bool
    quality_status: QualityStatus
    stale_reasons: tuple[str, ...]
    started_at: str
    completed_at: str | None
    lease_expires_at: str
    scanned_row_count: int
    snapshot_row_count: int
    issue_count: int
    warning_count: int
    report_complete: bool
    issues_truncated: bool
    summary: dict[str, JSONValue]
    rule_results: list[dict[str, JSONValue]]
    error_code: str | None


@dataclass(frozen=True)
class QualitySummary:
    dataset_id: int
    dataset_identity_key: str
    current_data_revision: int
    quality_status: QualityStatus
    selected_check: QualityCheckResult | None


@dataclass(frozen=True)
class QualityIssueItem:
    issue_number: int
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
class QualityIssuePage:
    check_run_id: str
    offset: int
    limit: int
    total: int
    total_issue_count: int
    report_complete: bool
    issues_truncated: bool
    items: tuple[QualityIssueItem, ...]


@dataclass(frozen=True)
class QualityCheckListItem:
    check_run_id: str
    attempt_number: int
    checked_data_revision: int
    run_status: str
    outcome: str | None
    started_at: str


@dataclass(frozen=True)
class QualityCheckPage:
    total: int
    items: tuple[QualityCheckListItem, ...]


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":"))


def _utc(value: datetime) -> datetime:
    # SQLite returns naive timestamps from timezone=True columns; they store UTC.
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _positive_integer(value: int, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 < value <= 2**63 - 1:
        raise QualityServiceError("INVALID_INPUT", f"{label} must be a positive signed 64-bit integer.")


def _target_input(dataset_id: int, identity_key: str) -> None:
    _positive_integer(dataset_id, "Dataset ID")
    if not isinstance(identity_key, str) or not identity_key or len(identity_key) > 128:
        raise QualityServiceError("IDENTITY_REQUIRED", "Supply the dataset identity key read from the catalogue; complete Phase 1 migration if it is absent.")


def _run_input(check_run_id: str) -> None:
    if not isinstance(check_run_id, str) or re.fullmatch(r"[0-9a-f]{32}", check_run_id) is None:
        raise QualityServiceError("INVALID_INPUT", "Check run ID must be a saved 32-character hexadecimal identity.")


def _lock(session: Session, dataset_id: int, identity_key: str, revision: int | None = None) -> UploadedDataset:
    if session.get_bind().dialect.name not in {"sqlite", "postgresql"}:
        raise QualityServiceError("DATABASE_UNSUPPORTED", "Quality snapshots require the documented PostgreSQL or SQLite transaction strategy.")
    try:
        return claim_uploaded_dataset(
            session, dataset_id, revision, identity_key,
            allow_any_revision=revision is None,
        )
    except DatasetConflict as error:
        current = session.get(UploadedDataset, dataset_id, populate_existing=True)
        if current is None or current.identity_key != identity_key:
            raise QualityServiceError("DATASET_NOT_FOUND", "The dataset incarnation was deleted or replaced. Refresh the catalogue.") from error
        raise QualityServiceError("REVISION_CONFLICT", "The dataset revision changed. Refresh and explicitly retry the check.") from error


def _metadata(dataset: UploadedDataset) -> DatasetMetadata:
    return DatasetMetadata(dataset.variable_key, dataset.observation_month, dataset.unit, dataset.grid_key)


def _recover_expired(session: Session, identity_key: str, now: datetime) -> None:
    active = session.scalar(select(QualityCheckRun).where(QualityCheckRun.active_dataset_key == identity_key))
    if active is not None and _utc(active.lease_expires_at) <= now:
        active.run_status = "error"
        active.error_code = "RUN_INTERRUPTED"
        active.completed_at = now
        active.active_dataset_key = None
        # Flush releases the unique active key before accepting another attempt.
        session.flush()


def _safe_system_error(check_run_id: str | None = None) -> QualityServiceError:
    return QualityServiceError("SYSTEM_ERROR", "The quality operation could not be completed. Read its status before explicitly retrying.", check_run_id=check_run_id)


def _read_declaration(dataset_id: int, revision: int, identity_key: str) -> DatasetMetadata:
    with rx.session() as session:
        dataset = session.get(UploadedDataset, dataset_id)
        if dataset is None or dataset.identity_key != identity_key:
            raise QualityServiceError("DATASET_NOT_FOUND", "The dataset incarnation no longer exists.")
        if dataset.data_revision != revision:
            raise QualityServiceError("REVISION_CONFLICT", "Refresh the dataset revision before starting a check.")
        return _metadata(dataset)


def _start(
    dataset_id: int, revision: int, identity_key: str, metadata: DatasetMetadata,
    profile: ValidationProfile, reference_identity: dict[str, JSONValue],
) -> tuple[str, DatasetSnapshot]:
    with rx.session() as session, session.begin():
        dataset = _lock(session, dataset_id, identity_key, revision)
        if _metadata(dataset) != metadata:
            raise QualityServiceError("REVISION_CONFLICT", "Scientific declarations changed without the expected revision. Refresh the dataset.")
        now = datetime.now(UTC)
        _recover_expired(session, identity_key, now)
        active = session.scalar(select(QualityCheckRun.id).where(QualityCheckRun.active_dataset_key == identity_key))
        if active is not None:
            raise QualityServiceError("CHECK_ALREADY_RUNNING", "A check is already running for this dataset. Read that run's status.", check_run_id=active)
        actual_count = session.scalar(select(func.count()).select_from(UploadedDatasetRow).where(UploadedDatasetRow.dataset_id == dataset_id)) or 0
        if actual_count > MAX_ROWS:
            raise QualityServiceError("SNAPSHOT_LIMIT_EXCEEDED", "The dataset exceeds the supported 100,000-record snapshot limit; no partial check was started.")
        rows: list[SnapshotRow] = []
        size = len(dataset.column_names_json.encode("utf-8"))
        if size > MAX_SNAPSHOT_BYTES:
            raise QualityServiceError("SNAPSHOT_LIMIT_EXCEEDED", "Snapshot headers exceed the 64 MiB stored-JSON limit.")
        records = session.execute(select(
            UploadedDatasetRow.id, UploadedDatasetRow.row_position, UploadedDatasetRow.cell_values_json,
        ).where(UploadedDatasetRow.dataset_id == dataset_id).order_by(UploadedDatasetRow.row_position, UploadedDatasetRow.id).execution_options(yield_per=1000))
        for row_id, position, values_json in records:
            size += len(values_json.encode("utf-8"))
            if size > MAX_SNAPSHOT_BYTES:
                raise QualityServiceError("SNAPSHOT_LIMIT_EXCEEDED", "The editable snapshot exceeds 64 MiB of stored JSON; no partial check was started.")
            rows.append(SnapshotRow(row_id, position, values_json))
        if len(rows) != actual_count:
            raise QualityServiceError("SNAPSHOT_INCOMPLETE", "The complete record read did not match the locked snapshot count; no check was started.")
        snapshot = DatasetSnapshot(
            dataset_id, identity_key, revision, dataset.column_names_json, dataset.row_count,
            metadata.variable_key, metadata.observation_month, metadata.unit, metadata.grid_key, tuple(rows),
        )
        attempt = (session.scalar(select(func.max(QualityCheckRun.attempt_number)).where(QualityCheckRun.dataset_identity_key == identity_key)) or 0) + 1
        run_id = uuid4().hex
        session.add(QualityCheckRun(
            id=run_id, dataset_id=dataset_id, dataset_identity_key=identity_key,
            checked_data_revision=revision, attempt_number=attempt,
            ruleset_version=RULESET_VERSION, profile_key=profile.key, profile_version=profile.version,
            reference_identity_json=_json(reference_identity), active_dataset_key=identity_key,
            started_at=now, lease_expires_at=now + LEASE_DURATION, completed_at=None,
            run_status="running", outcome=None, snapshot_row_count=dataset.row_count,
            summary_json=_json({"snapshot_metadata": asdict(metadata)}),
        ))
    return run_id, snapshot


def _complete(
    run_id: str, snapshot: DatasetSnapshot, report: ValidationReport,
    completion_reference_identity: dict[str, JSONValue],
) -> None:
    # Serialisation errors occur before saving any report rows. No NaN/Infinity
    # or raw ORM values may enter the persisted JSON contract.
    summary = dict(report.summary)
    summary["snapshot_metadata"] = {
        "variable_key": snapshot.variable_key,
        "observation_month": snapshot.observation_month,
        "unit": snapshot.unit,
        "grid_key": snapshot.grid_key,
    }
    rules_json = _json([asdict(rule) for rule in report.rule_results])
    with rx.session() as session, session.begin():
        dataset = _lock(session, snapshot.dataset_id, snapshot.identity_key)
        now = datetime.now(UTC)
        _recover_expired(session, snapshot.identity_key, now)
        run = session.get(QualityCheckRun, run_id, populate_existing=True)
        if run is None:
            raise QualityServiceError("CHECK_NOT_FOUND", "The check report was deleted.")
        if run.run_status != "running" or run.active_dataset_key != snapshot.identity_key:
            # A recovered/expired worker never saves a report over its successor.
            return
        current_profile = select_validation_profile(dataset.variable_key)
        summary["completion_observation"] = {
            "data_revision_matched": dataset.data_revision == run.checked_data_revision,
            "rule_identity_matched": (run.ruleset_version, run.profile_key, run.profile_version) == (RULESET_VERSION, current_profile.key, current_profile.version),
            "reference_identity_matched": (
                dataset.grid_key == snapshot.grid_key
                and json.loads(run.reference_identity_json) == completion_reference_identity
            ),
        }
        summary_json = _json(summary)
        for start in range(0, len(report.issues), 1000):
            session.execute(insert(QualityIssue.__table__), [
                {
                    "check_run_id": run_id, "issue_number": start + index + 1,
                    **asdict(issue), "raw_value_truncated": int(issue.raw_value_truncated),
                }
                for index, issue in enumerate(report.issues[start:start + 1000])
            ])
        run.scanned_row_count = report.scanned_row_count
        run.issue_count = len(report.issues)
        run.warning_count = sum(issue.severity == "warning" for issue in report.issues)
        run.summary_json = summary_json
        run.rule_results_json = rules_json
        run.outcome = report.outcome
        run.report_complete = 1
        run.completed_at = now
        run.run_status = "completed"
        run.active_dataset_key = None
        # Issues, counts, summary and the completion marker commit together.


def _record_error(run_id: str, dataset_id: int, identity_key: str) -> None:
    with rx.session() as session, session.begin():
        _lock(session, dataset_id, identity_key)
        run = session.get(QualityCheckRun, run_id)
        if run is not None and run.run_status == "running" and run.active_dataset_key == identity_key:
            run.run_status = "error"
            run.completed_at = datetime.now(UTC)
            run.error_code = "CHECK_EXECUTION_ERROR"
            run.active_dataset_key = None


def run_quality_check(
    dataset_id: int, expected_data_revision: int, *, expected_identity_key: str,
) -> QualityCheckResult:
    """Synchronously check the full expected dataset revision and return its run.

    A fresh session is owned per transaction. The caller must pass the immutable
    identity as well as the revision, and invoke this blocking function in its
    existing backend execution context (not a browser event-loop computation).
    QualityServiceError distinguishes invalid input, deletion, revision conflict,
    an active run, unsupported database, snapshot capacity and infrastructure
    failure. Unsupported science/reference resources produce completed blocked
    reports. An accepted attempt may be historical by the time it returns.
    """
    _target_input(dataset_id, expected_identity_key)
    _positive_integer(expected_data_revision, "Expected data revision")
    run_id: str | None = None
    try:
        metadata = _read_declaration(dataset_id, expected_data_revision, expected_identity_key)
        profile = select_validation_profile(metadata.variable_key)
        reference = load_reference_grid(metadata.grid_key)
        run_id, snapshot = _start(dataset_id, expected_data_revision, expected_identity_key, metadata, profile, reference.identity)
        report = validate_csv_snapshot(snapshot, profile, reference)
        completion_reference = load_reference_grid(snapshot.grid_key)
        _complete(run_id, snapshot, report, completion_reference.identity)
    except QualityServiceError:
        raise
    except Exception as error:
        # Do not log driver parameters, raw cells, host paths or exception text.
        logger.error("Quality execution failed for run %s (%s).", run_id, type(error).__name__)
        if run_id is None:
            raise _safe_system_error() from None
        try:
            _record_error(run_id, dataset_id, expected_identity_key)
        except QualityServiceError:
            raise
        except Exception as save_error:
            logger.error("Quality error status could not be saved for run %s (%s).", run_id, type(save_error).__name__)
            raise _safe_system_error(run_id) from None
    # Read failures after a committed report cannot rewrite it as an error or
    # imply that the caller should repeat the already completed check.
    return get_quality_check(cast(str, run_id))


def _result(
    run: QualityCheckRun, current_revision: int, current_profile: ValidationProfile,
    reference_identity: dict[str, JSONValue],
) -> QualityCheckResult:
    reasons: list[str] = []
    if run.checked_data_revision != current_revision:
        reasons.append("DATA_REVISION_CHANGED")
    if (run.ruleset_version, run.profile_key, run.profile_version) != (RULESET_VERSION, current_profile.key, current_profile.version):
        reasons.append("RULE_CONFIGURATION_CHANGED")
    stored_reference = json.loads(run.reference_identity_json)
    if stored_reference != reference_identity:
        reasons.append("REFERENCE_IDENTITY_CHANGED")
    is_current = not reasons
    status: QualityStatus
    if not is_current:
        status = "stale"
    elif run.run_status == "completed":
        status = cast(QualityStatus, run.outcome)
    else:
        status = cast(QualityStatus, run.run_status)
    return QualityCheckResult(
        run.id, run.dataset_id, run.dataset_identity_key, run.checked_data_revision,
        current_revision, run.attempt_number, run.ruleset_version, run.profile_key,
        run.profile_version, stored_reference, cast(RunStatus, run.run_status),
        cast(Outcome | None, run.outcome), is_current, status, tuple(reasons),
        _utc(run.started_at).isoformat(), _utc(run.completed_at).isoformat() if run.completed_at else None,
        _utc(run.lease_expires_at).isoformat(), run.scanned_row_count, run.snapshot_row_count,
        run.issue_count, run.warning_count, bool(run.report_complete), False,
        json.loads(run.summary_json), json.loads(run.rule_results_json), run.error_code,
    )


def _read_summary(dataset_id: int, identity_key: str, check_run_id: str | None = None) -> QualitySummary:
    with rx.session() as session, session.begin():
        dataset = _lock(session, dataset_id, identity_key)
        _recover_expired(session, identity_key, datetime.now(UTC))
        statement = select(QualityCheckRun).where(
            QualityCheckRun.dataset_id == dataset_id, QualityCheckRun.dataset_identity_key == identity_key,
        )
        if check_run_id is not None:
            statement = statement.where(QualityCheckRun.id == check_run_id)
        run = session.scalar(statement.order_by(QualityCheckRun.attempt_number.desc()).limit(1))
        if check_run_id is not None and run is None:
            raise QualityServiceError("CHECK_NOT_FOUND", "The requested check no longer exists.")
        revision = dataset.data_revision
        profile = select_validation_profile(dataset.variable_key)
        grid_key = dataset.grid_key
        # Detach scalar-only report metadata before leaving the short parent lock.
        if run is not None:
            session.expunge(run)
    if run is None:
        return QualitySummary(dataset_id, identity_key, revision, "not_checked", None)
    reference = load_reference_grid(grid_key)
    result = _result(run, revision, profile, reference.identity)
    return QualitySummary(dataset_id, identity_key, revision, result.quality_status, result)


def get_quality_summary(dataset_id: int, *, expected_identity_key: str) -> QualitySummary:
    """Return the most recently accepted attempt, ordered by attempt, not finish.

    Currentness compares revision, rule configuration and freshly read reference
    identity. Expired running attempts are recovered to error under the parent
    lock. No records gives not_checked. Does not increment data_revision.
    Raises QualityServiceError for invalid input, deletion or system failure.
    """
    _target_input(dataset_id, expected_identity_key)
    try:
        return _read_summary(dataset_id, expected_identity_key)
    except QualityServiceError:
        raise
    except Exception as error:
        logger.error("Quality summary failed for dataset %s (%s).", dataset_id, type(error).__name__)
        raise _safe_system_error() from None


def require_publication_check(
    session: Session, dataset: UploadedDataset, reference_identity: dict[str, JSONValue],
) -> QualityCheckResult:
    """Read complete evidence inside a caller-owned, already claimed parent.

    Publication uses this transaction collaborator both at snapshot capture and
    at final visibility commit. It never accepts a browser's quality label.
    Raises QualityServiceError when the latest accepted attempt cannot publish.
    """
    run = session.scalar(select(QualityCheckRun).where(
        QualityCheckRun.dataset_id == dataset.id,
        QualityCheckRun.dataset_identity_key == dataset.identity_key,
    ).order_by(QualityCheckRun.attempt_number.desc()).limit(1))
    if run is None:
        raise QualityServiceError("PUBLICATION_BLOCKED", "Run a complete scientific check before publication.")
    result = _result(run, dataset.data_revision, select_validation_profile(dataset.variable_key), reference_identity)
    metadata = asdict(_metadata(dataset))
    definitions = {rule.rule_code: asdict(rule) for rule in RULE_DEFINITIONS}
    rules = result.rule_results
    covered: set[str] = set()
    complete = True
    record_rules = {"CSV_STRUCTURE", "RECORD_COVERAGE", "FINITE_NUMBERS", "COORDINATE_RANGE", "UNIQUE_COORDINATES", "DECLARED_GRID"}
    for rule in rules:
        definition = rule.get("definition")
        if not isinstance(definition, dict):
            complete = False
            break
        code = str(definition.get("rule_code"))
        status = rule.get("status")
        if (code in covered or definition != definitions.get(code)
            or status not in {"passed", "not_applicable"}
            or (definition.get("required") and (status != "passed" or rule.get("issue_count") != 0))
            or (code in record_rules and rule.get("evaluated_row_count") != dataset.row_count)
            or (code == "MONTH_CONSISTENCY" and status == "passed" and rule.get("evaluated_row_count") != dataset.row_count)):
            complete = False
        covered.add(code)
    if (not result.is_current or result.run_status != "completed" or result.outcome != "passed"
        or not result.report_complete or result.issues_truncated
        or result.issue_count != result.warning_count or result.snapshot_row_count != dataset.row_count
        or result.scanned_row_count != dataset.row_count or dataset.row_count <= 0
        or result.summary.get("snapshot_metadata") != metadata
        or not complete or covered != set(definitions)
        or reference_identity.get("dependency_code") is not None
        or not reference_identity.get("file_sha256")
        or not all(metadata.values())):
        raise QualityServiceError("PUBLICATION_BLOCKED", "Publication requires the latest complete passing check of this revision, all applicable rules and the current available reference.")
    return result


def _run_target(check_run_id: str) -> tuple[int, str]:
    with rx.session() as session:
        target = session.execute(select(QualityCheckRun.dataset_id, QualityCheckRun.dataset_identity_key).where(QualityCheckRun.id == check_run_id)).one_or_none()
        if target is None:
            raise QualityServiceError("CHECK_NOT_FOUND", "The requested check no longer exists.")
        return target.dataset_id, target.dataset_identity_key


def get_quality_check(check_run_id: str) -> QualityCheckResult:
    """Read a specific run, including its original conclusion and currentness.

    An interrupted run is recovered on read; a deleted dataset's reports are
    deleted with it. QualityServiceError includes CHECK_NOT_FOUND or SYSTEM_ERROR.
    """
    _run_input(check_run_id)
    try:
        dataset_id, identity_key = _run_target(check_run_id)
        summary = _read_summary(dataset_id, identity_key, check_run_id)
        return cast(QualityCheckResult, summary.selected_check)
    except QualityServiceError:
        raise
    except Exception as error:
        logger.error("Quality status read failed for run %s (%s).", check_run_id, type(error).__name__)
        raise _safe_system_error(check_run_id) from None


def get_quality_checks(
    dataset_id: int, *, expected_identity_key: str, offset: int = 0, limit: int = 10,
) -> QualityCheckPage:
    """Read bounded history, newest accepted attempt first, with its full count.

    Identity is mandatory; offset is nonnegative and limit is 1..200. This
    listing does not derive quality conclusions. Read the selected run through
    get_quality_check. Invalid inputs/deletion/storage raise QualityServiceError.
    """
    _target_input(dataset_id, expected_identity_key)
    if isinstance(offset, bool) or not isinstance(offset, int) or not 0 <= offset <= 2**63 - 1:
        raise QualityServiceError("INVALID_INPUT", "History offset must be a nonnegative signed 64-bit integer.")
    _positive_integer(limit, "History page size")
    if limit > MAX_ISSUE_PAGE_SIZE:
        raise QualityServiceError("INVALID_INPUT", "Use a history page size of at most 200.")
    try:
        with rx.session() as session, session.begin():
            _lock(session, dataset_id, expected_identity_key)
            _recover_expired(session, expected_identity_key, datetime.now(UTC))
            filters = (
                QualityCheckRun.dataset_id == dataset_id,
                QualityCheckRun.dataset_identity_key == expected_identity_key,
            )
            total = session.scalar(select(func.count()).select_from(QualityCheckRun).where(*filters)) or 0
            runs = session.scalars(select(QualityCheckRun).where(*filters).order_by(
                QualityCheckRun.attempt_number.desc(), QualityCheckRun.id.desc(),
            ).offset(offset).limit(limit)).all()
            return QualityCheckPage(total, tuple(QualityCheckListItem(
                run.id, run.attempt_number, run.checked_data_revision, run.run_status,
                run.outcome, _utc(run.started_at).isoformat(),
            ) for run in runs))
    except QualityServiceError:
        raise
    except Exception as error:
        logger.error("Quality history read failed for dataset %s (%s).", dataset_id, type(error).__name__)
        raise _safe_system_error() from None


def get_quality_issues(
    check_run_id: str, *, offset: int = 0, limit: int = 50,
    severity: Severity | None = None, rule_code: str | None = None,
    order: Literal["asc", "desc"] = "asc",
) -> QualityIssuePage:
    """Read a stable database page and filtered/full counts in one locked view.

    Limit is 1..200, offset is nonnegative, order is asc/desc; severity and a
    bounded uppercase rule code are optional filters. Incomplete runs expose no
    issues and explicitly report report_complete=False. No truncation is used.
    Invalid input, deleted runs and system failures raise QualityServiceError.
    """
    _run_input(check_run_id)
    if isinstance(offset, bool) or not isinstance(offset, int) or not 0 <= offset <= 2**63 - 1:
        raise QualityServiceError("INVALID_INPUT", "Offset must be a nonnegative signed 64-bit integer.")
    _positive_integer(limit, "Page size")
    if limit > MAX_ISSUE_PAGE_SIZE or not isinstance(order, str) or order not in {"asc", "desc"}:
        raise QualityServiceError("INVALID_INPUT", "Use a page size of at most 200 and order asc or desc.")
    if severity is not None and (not isinstance(severity, str) or severity not in {"error", "warning", "info"}):
        raise QualityServiceError("INVALID_INPUT", "Severity must be error, warning or info.")
    if rule_code is not None and (not isinstance(rule_code, str) or re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", rule_code) is None):
        raise QualityServiceError("INVALID_INPUT", "Rule code must be a bounded uppercase identifier.")
    try:
        dataset_id, identity_key = _run_target(check_run_id)
        with rx.session() as session, session.begin():
            _lock(session, dataset_id, identity_key)
            _recover_expired(session, identity_key, datetime.now(UTC))
            run = session.get(QualityCheckRun, check_run_id)
            if run is None:
                raise QualityServiceError("CHECK_NOT_FOUND", "The requested check no longer exists.")
            if not run.report_complete:
                return QualityIssuePage(check_run_id, offset, limit, 0, 0, False, False, ())
            filters = [QualityIssue.check_run_id == check_run_id]
            if severity is not None:
                filters.append(QualityIssue.severity == severity)
            if rule_code is not None:
                filters.append(QualityIssue.rule_code == rule_code)
            total = session.scalar(select(func.count()).select_from(QualityIssue).where(*filters)) or 0
            sorting = (QualityIssue.issue_number.asc(), QualityIssue.id.asc()) if order == "asc" else (QualityIssue.issue_number.desc(), QualityIssue.id.desc())
            rows = session.scalars(select(QualityIssue).where(*filters).order_by(*sorting).offset(offset).limit(limit)).all()
            items = tuple(QualityIssueItem(
                issue.issue_number, issue.issue_code, issue.rule_code, cast(Severity, issue.severity),
                issue.snapshot_row_id, issue.snapshot_row_position, issue.field, issue.raw_value,
                bool(issue.raw_value_truncated), issue.message,
            ) for issue in rows)
            return QualityIssuePage(check_run_id, offset, limit, total, run.issue_count, True, False, items)
    except QualityServiceError:
        raise
    except Exception as error:
        logger.error("Quality issue page failed for run %s (%s).", check_run_id, type(error).__name__)
        raise _safe_system_error(check_run_id) from None
