"""Bounded quality views and request orchestration for the existing Data page."""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
import logging
from typing import TypedDict, cast
from uuid import uuid4

import reflex as rx

from antarctic_sea_ice_dashboard.services.csv_validation import Severity
from antarctic_sea_ice_dashboard.services.data_quality import (
    QualityCheckPage, QualityCheckResult, QualityIssuePage, QualityServiceError,
    QualitySummary, get_quality_check, get_quality_checks, get_quality_issues,
    get_quality_summary, run_quality_check,
)
from antarctic_sea_ice_dashboard.services.uploaded_datasets import (
    DatasetConflict, DatasetRowLocation, get_dataset_row_location,
)
from antarctic_sea_ice_dashboard.states.explorer import Explorer
from antarctic_sea_ice_dashboard.states.uploaded_data import UploadedData


ISSUE_PAGE_SIZE = 25
HISTORY_PAGE_SIZE = 10
POLL_INTERVAL_SECONDS = 3
MAX_POLL_READS = 610
logger = logging.getLogger(__name__)

STATUS_MESSAGES = {
    "not_checked": "No scientific check has been saved. Run a check for this data revision.",
    "running": "The backend reports a running check. No incremental progress is available.",
    "passed": "The named applicable rules passed. Review warnings and coverage; this does not validate the research method or original data provenance.",
    "failed": "Applicable rules found data problems. Review the issues, correct the working data and explicitly run another check.",
    "blocked": "Required metadata, an applicable validation profile or a reference grid is unavailable. Review blocked rule reasons.",
    "error": "Check execution failed or was interrupted. This is not a conclusion that the data failed a scientific rule. Refresh status before retrying.",
    "stale": "This historical assessment differs from the current revision, rules or reference. Review it as reference and run a new check of the working data.",
}


class QualityDetail(TypedDict):
    label: str
    value: str


class QualityRule(TypedDict):
    code: str
    status: str
    reason: str
    required: str
    scope: str
    severity: str
    source: str
    version: str
    evaluated: int
    issues: int


class QualityIssueView(TypedDict):
    number: int
    code: str
    rule: str
    severity: str
    field: str
    position: str
    raw_value: str
    raw_value_truncated: bool
    message: str
    row_id: int


class QualityHistoryItem(TypedDict):
    id: str
    label: str


@dataclass(frozen=True)
class _QualityView:
    latest: QualitySummary
    selected: QualityCheckResult | None
    history: QualityCheckPage
    issues: QualityIssuePage | None


def _read_view(
    target: tuple[int, str, int], check_id: str, history_page: int,
    issue_page: int, severity: str, rule_code: str,
) -> _QualityView:
    latest = get_quality_summary(target[0], expected_identity_key=target[1])
    selected = get_quality_check(check_id) if check_id else latest.selected_check
    if selected is not None and (selected.dataset_id, selected.dataset_identity_key) != target[:2]:
        raise QualityServiceError("CHECK_NOT_FOUND", "This check belongs to another dataset incarnation. Reload quality status.")
    history = get_quality_checks(target[0], expected_identity_key=target[1], offset=history_page * HISTORY_PAGE_SIZE, limit=HISTORY_PAGE_SIZE)
    issues = None if selected is None else get_quality_issues(
        selected.check_run_id, offset=issue_page * ISSUE_PAGE_SIZE, limit=ISSUE_PAGE_SIZE,
        severity=cast(Severity, severity) if severity else None,
        rule_code=rule_code or None,
    )
    if selected is not None:
        # A running report can finish between summary and issue reads. Re-read
        # its monotonic execution state so coverage and completeness agree.
        selected = get_quality_check(selected.check_run_id)
        if issues is not None and issues.report_complete != selected.report_complete:
            issues = get_quality_issues(
                selected.check_run_id, offset=issue_page * ISSUE_PAGE_SIZE, limit=ISSUE_PAGE_SIZE,
                severity=cast(Severity, severity) if severity else None, rule_code=rule_code or None,
            )
    return _QualityView(latest, selected, history, issues)


def _locate_record(target: tuple[int, str, int], row_id: int) -> DatasetRowLocation:
    with rx.session() as session:
        return get_dataset_row_location(session, target[0], target[2], row_id, expected_identity_key=target[1])


def _text(value: object) -> str:
    return "Not available" if value is None else str(value)


def _connected(client_token: str) -> bool:
    # Import only during an event, after application construction is complete.
    from antarctic_sea_ice_dashboard.antarctic_sea_ice_dashboard import app

    return client_token in app.event_namespace.token_to_sid


class DataQuality(rx.State):
    dataset_id: int = 0
    workspace_revision: int = 0
    current_data_revision: int = 0
    quality_status: str = ""
    latest_status: str = ""
    latest_run_status: str = ""
    check_run_id: str = ""
    selected_check_id: str = ""
    checked_data_revision: int = 0
    run_status: str = ""
    original_outcome: str = ""
    status_message: str = ""
    stale_reasons: str = ""
    error_code: str = ""
    details: list[QualityDetail] = []
    statistics: list[QualityDetail] = []
    rules: list[QualityRule] = []
    rule_options: list[str] = []
    issues: list[QualityIssueView] = []
    history: list[QualityHistoryItem] = []
    issue_total: int = 0
    matching_total: int = 0
    warning_count: int = 0
    history_total: int = 0
    issue_page: int = 0
    history_page: int = 0
    severity: str = ""
    rule_code: str = ""
    report_complete: bool = False
    issues_truncated: bool = False
    loading: bool = False
    locating: bool = False
    read_error: str = ""
    run_error: str = ""
    location_error: str = ""
    location_notice: str = ""
    polling_notice: str = ""
    request_generation: int = 0
    start_token: str = ""
    _identity_key: str = ""
    _executing_keys: list[str] = []
    _run_feedback_token: str = ""

    @rx.var
    def run_busy(self) -> bool:
        """Describe a local request without claiming backend execution status."""
        return bool(self._identity_key and self._identity_key in self._executing_keys)

    @rx.var
    def workspace_outdated(self) -> bool:
        return self.current_data_revision != self.workspace_revision

    @rx.var
    def issue_page_count(self) -> int:
        return max(1, (self.matching_total + ISSUE_PAGE_SIZE - 1) // ISSUE_PAGE_SIZE)

    @rx.var
    def history_page_count(self) -> int:
        return max(1, (self.history_total + HISTORY_PAGE_SIZE - 1) // HISTORY_PAGE_SIZE)

    @rx.var
    def issue_range(self) -> str:
        if not self.matching_total:
            return "0 matching issues"
        start = self.issue_page * ISSUE_PAGE_SIZE + 1
        end = min(self.matching_total, start + ISSUE_PAGE_SIZE - 1)
        return f"Issues {start}–{end} of {self.matching_total:,} matching · {self.issue_total:,} total"

    @staticmethod
    def _target(uploaded: UploadedData) -> tuple[int, str, int]:
        return uploaded.selected_id, uploaded._selected_identity_key, uploaded.data_revision

    def _clear_report(self) -> None:
        self.quality_status = ""
        self.latest_status = ""
        self.latest_run_status = ""
        self.check_run_id = ""
        self.checked_data_revision = 0
        self.run_status = ""
        self.original_outcome = ""
        self.status_message = ""
        self.stale_reasons = ""
        self.error_code = ""
        self.details = []
        self.statistics = []
        self.rules = []
        self.rule_options = []
        self.issues = []
        self.issue_total = 0
        self.matching_total = 0
        self.warning_count = 0
        self.report_complete = False
        self.issues_truncated = False

    def _invalidate(self) -> None:
        self.request_generation += 1
        self.issues = []
        self.read_error = ""
        self.polling_notice = ""
        self.locating = False
        self.loading = True

    def _publish(self, view: _QualityView) -> None:
        self._clear_report()
        self.current_data_revision = view.selected.current_data_revision if view.selected else view.latest.current_data_revision
        self.latest_status = view.latest.quality_status
        self.latest_run_status = view.latest.selected_check.run_status if view.latest.selected_check else ""
        self.history_total = view.history.total
        self.history = [{"id": item.check_run_id, "label": f"Attempt {item.attempt_number} · revision {item.checked_data_revision} · {item.run_status} / {item.outcome or 'no conclusion'} · {item.started_at}"} for item in view.history.items]
        run = view.selected
        self.quality_status = run.quality_status if run else "not_checked"
        self.status_message = STATUS_MESSAGES[self.quality_status]
        if run is None:
            return
        self.check_run_id = run.check_run_id
        self.checked_data_revision = run.checked_data_revision
        self.run_status = run.run_status
        self.original_outcome = run.outcome or "No completed conclusion"
        self.stale_reasons = ", ".join(run.stale_reasons)
        self.error_code = run.error_code or ""
        self.issue_total = run.issue_count
        self.warning_count = run.warning_count
        self.report_complete = run.report_complete
        self.issues_truncated = run.issues_truncated
        self.details = [
            {"label": "Check ID", "value": run.check_run_id},
            {"label": "Started (UTC)", "value": run.started_at},
            {"label": "Completed (UTC)", "value": run.completed_at or "Not completed"},
            {"label": "Ruleset version", "value": run.ruleset_version},
            {"label": "Validation profile", "value": f"{run.profile_key} / {run.profile_version}"},
            {"label": "Snapshot records", "value": str(run.snapshot_row_count)},
        ]
        if run.run_status == "completed":
            self.details.append({"label": "Scanned records", "value": str(run.scanned_row_count)})
        metadata = run.summary.get("snapshot_metadata")
        for key, label in (("variable_key", "Checked variable"), ("observation_month", "Checked month"), ("unit", "Checked unit"), ("grid_key", "Checked grid")):
            self.details.append({"label": label, "value": _text(metadata.get(key)) if isinstance(metadata, dict) else "Unavailable in this saved report"})
        for key, value in run.reference_identity.items():
            self.details.append({"label": f"Reference: {key}", "value": _text(value)})
        for key, label in (("value_field", "Value field"), ("valid_numeric_count", "Finite samples"), ("missing_numeric_count", "Missing samples"), ("invalid_numeric_count", "Invalid samples"), ("unavailable_numeric_count", "Unavailable samples"), ("minimum", "Minimum"), ("maximum", "Maximum"), ("statistics_basis", "Statistics basis"), ("month_evidence", "Month evidence"), ("missing_value_policy", "Missing-value policy"), ("longitude_convention", "Longitude convention")):
            if key in run.summary:
                self.statistics.append({"label": label, "value": _text(run.summary[key])})
        for result in run.rule_results:
            definition = result["definition"]
            if not isinstance(definition, dict):
                continue
            code = str(definition["rule_code"])
            self.rules.append({
                "code": code, "status": str(result["status"]), "reason": str(result["reason"]),
                "required": "Required" if definition["required"] else "Optional",
                "scope": str(definition["scope"]), "severity": str(definition["severity"]),
                "source": str(definition["source"]), "version": str(definition["configuration_version"]),
                "evaluated": int(cast(int, result["evaluated_row_count"])), "issues": int(cast(int, result["issue_count"])),
            })
            self.rule_options.append(code)
        page = view.issues
        if page is None:
            return
        self.matching_total = page.total
        self.issue_total = page.total_issue_count
        self.report_complete = page.report_complete
        self.issues_truncated = page.issues_truncated
        self.issues = [{
            "number": item.issue_number, "code": item.issue_code, "rule": item.rule_code,
            "severity": item.severity, "field": item.field or "Dataset",
            "position": f"Snapshot record position {item.snapshot_row_position} (zero-based)" if item.snapshot_row_position is not None else "Dataset-level issue; no record position",
            "raw_value": item.raw_value if item.raw_value is not None else "No raw value",
            "raw_value_truncated": item.raw_value_truncated, "message": item.message,
            "row_id": item.snapshot_row_id or 0,
        } for item in page.items]

    @rx.event
    def leave(self) -> None:
        """Release page reads while any accepted backend execution continues."""
        self.request_generation += 1
        self.loading = False
        self.locating = False
        self._run_feedback_token = ""

    @rx.event
    def observe_visibility(self, visible: bool, generation: int) -> None:
        """Pause page reads on a hidden document without cancelling execution."""
        if generation == self.request_generation and not visible:
            self.leave()
            self.polling_notice = "Status reads paused while this page was hidden. Refresh quality status when you return; the backend check continues."

    @rx.event(background=True)
    async def refresh(self) -> AsyncIterator[object]:
        """Read real status and one page; discard results after context changes."""
        async with self:
            uploaded = await self.get_state(UploadedData)
            explorer = await self.get_state(Explorer)
            self._invalidate()
            generation = self.request_generation
            target = self._target(uploaded)
            if explorer.page != "Data" or not target[0] or uploaded.refresh_required:
                self._clear_report()
                self.loading = False
                return
            if (self.dataset_id, self._identity_key, self.workspace_revision) != target:
                if (self.dataset_id, self._identity_key) != target[:2]:
                    self.location_error = ""
                self.location_notice = ""
                self.dataset_id, self._identity_key, self.workspace_revision = target
                self.current_data_revision = target[2]
                self.selected_check_id = ""
                self.issue_page = 0
                self.history_page = 0
                self.severity = ""
                self.rule_code = ""
                self.run_error = ""
                self._run_feedback_token = ""
                self._clear_report()
                self.start_token = uuid4().hex
            if not self.start_token:
                self.start_token = uuid4().hex
        for read_number in range(MAX_POLL_READS):
            async with self:
                uploaded = await self.get_state(UploadedData)
                explorer = await self.get_state(Explorer)
                if generation != self.request_generation or target != self._target(uploaded) or explorer.page != "Data" or uploaded.refresh_required:
                    return
                client_token = self.router.session.client_token
                if not _connected(client_token):
                    self.loading = False
                    return
                selection = (self.selected_check_id, self.history_page, self.issue_page, self.severity, self.rule_code)
            yield rx.call_script(
                "document.visibilityState === 'visible'",
                callback=lambda visible: DataQuality.observe_visibility(visible, generation),
            )
            try:
                view = await asyncio.to_thread(_read_view, target, *selection)
            except Exception as error:
                logger.error("Quality view read failed (%s).", type(error).__name__)
                async with self:
                    uploaded = await self.get_state(UploadedData)
                    explorer = await self.get_state(Explorer)
                    if generation == self.request_generation and target == self._target(uploaded) and explorer.page == "Data":
                        self.read_error = str(error) if isinstance(error, QualityServiceError) else "Could not refresh quality status. Refresh to retry; no data was changed."
                        self.loading = False
                return
            async with self:
                uploaded = await self.get_state(UploadedData)
                explorer = await self.get_state(Explorer)
                if generation != self.request_generation or target != self._target(uploaded) or explorer.page != "Data" or uploaded.refresh_required:
                    return
                self._publish(view)
                self.loading = False
                active = view.latest.selected_check
                keep_reading = self.run_busy or (active is not None and active.run_status == "running")
                if not keep_reading:
                    return
                if read_number == MAX_POLL_READS - 1:
                    self.polling_notice = "Automatic status reads stopped after the bounded observation window. Refresh status to continue; the backend check was not cancelled."
            await asyncio.sleep(POLL_INTERVAL_SECONDS)

    @rx.event
    def select_check(self, check_id: str, generation: int) -> object:
        """Choose a saved attempt or follow the latest accepted attempt."""
        if generation != self.request_generation or (check_id and check_id not in {item["id"] for item in self.history}):
            return None
        self._invalidate()
        self.selected_check_id = check_id
        self.issue_page = 0
        self.rule_code = ""
        self.run_error = ""
        self._run_feedback_token = ""
        self.location_error = ""
        self.location_notice = ""
        self._clear_report()
        return DataQuality.refresh

    @rx.event
    def change_filter(self, field: str, value: str, generation: int) -> object:
        """Restart backend pagination with filters captured in the request."""
        if generation != self.request_generation:
            return None
        if field == "severity" and value in {"", "error", "warning", "info"}:
            self.severity = value
        elif field == "rule_code" and (not value or value in self.rule_options):
            self.rule_code = value
        else:
            return None
        self._invalidate()
        self.issue_page = 0
        self.run_error = ""
        self._run_feedback_token = ""
        return DataQuality.refresh

    @rx.event
    def change_page(self, collection: str, delta: int, generation: int) -> object:
        """Keep history and issue pages independently bounded."""
        if generation != self.request_generation or self.loading:
            return None
        if collection == "issues":
            self.issue_page = max(0, min(self.issue_page_count - 1, self.issue_page + delta))
        elif collection == "history":
            self.history_page = max(0, min(self.history_page_count - 1, self.history_page + delta))
        else:
            return None
        self._invalidate()
        self.run_error = ""
        self._run_feedback_token = ""
        return DataQuality.refresh

    @rx.event(background=True)
    async def start_check(self, expected_token: str) -> AsyncIterator[object]:
        """Run the synchronous backend in a thread; refresh never replays it."""
        async with self:
            uploaded = await self.get_state(UploadedData)
            explorer = await self.get_state(Explorer)
            target = self._target(uploaded)
            if (explorer.page != "Data" or uploaded.busy or self.loading or self.run_busy
                or self.read_error or self.workspace_outdated or self.latest_run_status == "running"
                or not expected_token or expected_token != self.start_token
                or target != (self.dataset_id, self._identity_key, self.workspace_revision)):
                return
            self.start_token = uuid4().hex
            self._executing_keys = [*self._executing_keys, target[1]]
            self.run_error = ""
            self.location_error = ""
            self.location_notice = ""
            self.selected_check_id = ""
            self.issue_page = 0
            self.history_page = 0
            self._invalidate()
            feedback_token = uuid4().hex
            self._run_feedback_token = feedback_token
        yield DataQuality.refresh
        failure = ""
        try:
            await asyncio.to_thread(run_quality_check, target[0], target[2], expected_identity_key=target[1])
        except Exception as error:
            logger.error("Quality start request failed (%s).", type(error).__name__)
            failure = str(error) if isinstance(error, QualityServiceError) else "The check request could not finish. Refresh its saved status before explicitly retrying."
        finally:
            async with self:
                self._executing_keys = [key for key in self._executing_keys if key != target[1]]
                uploaded = await self.get_state(UploadedData)
                explorer = await self.get_state(Explorer)
                same_target = target == self._target(uploaded) and explorer.page == "Data"
                if same_target and feedback_token == self._run_feedback_token:
                    self.run_error = failure
        if same_target:
            yield DataQuality.refresh

    @rx.event(background=True)
    async def locate_issue(self, issue_number: int, generation: int) -> AsyncIterator[object]:
        """Resolve a stable row ID only at the checked revision, then open editing."""
        async with self:
            uploaded = await self.get_state(UploadedData)
            explorer = await self.get_state(Explorer)
            if generation != self.request_generation or self.loading or self.locating or uploaded.busy or explorer.page != "Data":
                return
            issue = next((item for item in self.issues if item["number"] == issue_number), None)
            if issue is None or not issue["row_id"]:
                return
            target = self._target(uploaded)
            if target != (self.dataset_id, self._identity_key, self.checked_data_revision):
                self.location_error = "This issue belongs to another data revision. Historical row IDs may have been reused. Refresh the working data and run a new check before locating records."
                return
            self.locating = True
            self.location_error = ""
            self.location_notice = ""
            row_id = issue["row_id"]
        reload_workspace = False
        try:
            location = await asyncio.to_thread(_locate_record, target, row_id)
            async with self:
                uploaded = await self.get_state(UploadedData)
                explorer = await self.get_state(Explorer)
                if generation != self.request_generation or target != self._target(uploaded) or explorer.page != "Data" or uploaded.busy:
                    return
                uploaded._open_located_row(location)
                self.location_notice = "Opened the current record in the CSV editor above. Review the values, save changes, then explicitly run a new check."
        except Exception as error:
            conflict = isinstance(error, DatasetConflict)
            logger.error("Quality record location failed (%s).", type(error).__name__)
            async with self:
                uploaded = await self.get_state(UploadedData)
                explorer = await self.get_state(Explorer)
                if generation == self.request_generation and target[:2] == self._target(uploaded)[:2] and explorer.page == "Data":
                    self.location_error = "The record was deleted or the dataset revision changed. Reloading the saved data; no replacement record was opened." if conflict else "Could not locate the record. Refresh saved data and retry; no data was changed."
                    reload_workspace = conflict
        finally:
            async with self:
                if generation == self.request_generation:
                    self.locating = False
        if reload_workspace:
            yield UploadedData.reload_quality_target(target[0], target[1])
