"""Quality report presentation beside the persisted CSV workspace."""

import reflex as rx

from antarctic_sea_ice_dashboard.components.uploaded_data import BUTTON, INPUT, PRIMARY, metadata_value
from antarctic_sea_ice_dashboard.states.data_quality import (
    DataQuality as Q, QualityDetail, QualityHistoryItem, QualityIssueView, QualityRule,
)
from antarctic_sea_ice_dashboard.states.uploaded_data import UploadedData as D
from antarctic_sea_ice_dashboard.components.research_products import publication_control


TEXT = "text-xs leading-relaxed text-slate-600"


def detail(item: QualityDetail) -> rx.Component:
    return rx.el.div(
        rx.el.dt(item["label"], class_name="text-[11px] font-medium text-slate-500"),
        rx.el.dd(item["value"], class_name="mt-1 whitespace-pre-wrap break-words text-xs text-slate-700"),
        class_name="min-w-0",
    )


def feedback(message: rx.Var[str], kind: str = "error") -> rx.Component:
    return rx.cond(
        message != "",
        rx.el.p(
            message, role="alert" if kind == "error" else "status",
            class_name="my-3 whitespace-pre-wrap break-words rounded-md border p-3 text-xs leading-relaxed " + (
                "border-red-200 bg-red-50 text-red-700" if kind == "error" else "border-sky-200 bg-sky-50 text-sky-900"
            ),
        ),
    )


def rule_item(rule: QualityRule) -> rx.Component:
    return rx.el.div(
        rx.el.div(
            rx.el.strong(rule["code"]), rx.el.span(rule["status"]),
            class_name="flex flex-wrap justify-between gap-2 text-xs text-slate-800",
        ),
        rx.el.p(rule["reason"], class_name="mt-1 " + TEXT),
        rx.el.p(f"{rule['required']} · {rule['scope']} · severity {rule['severity']} · evaluated records {rule['evaluated']} · issues {rule['issues']}", class_name="mt-1 " + TEXT),
        rx.el.p(f"Source: {rule['source']} · configuration {rule['version']}", class_name="mt-1 break-words text-[11px] text-slate-500"),
        class_name="rounded-md border border-slate-200 p-3",
    )


def issue_item(issue: QualityIssueView) -> rx.Component:
    return rx.el.article(
        rx.el.div(
            rx.el.h4(f"#{issue['number']} · {issue['code']}", class_name="break-words text-xs font-semibold text-slate-800"),
            rx.el.span(issue["severity"], class_name="text-xs font-medium text-slate-600"),
            class_name="flex flex-wrap items-start justify-between gap-2",
        ),
        rx.el.p(issue["message"], class_name="mt-2 whitespace-pre-wrap break-words " + TEXT),
        rx.el.p(f"Rule: {issue['rule']} · Field: {issue['field']}", class_name="mt-2 " + TEXT),
        rx.el.p(issue["position"], class_name="mt-1 " + TEXT),
        rx.el.p(f"Snapshot record ID: {issue['row_id']}", class_name="mt-1 " + TEXT, hidden=issue["row_id"] == 0),
        rx.el.p("Original value excerpt", class_name="mt-2 text-[11px] font-medium text-slate-500"),
        rx.el.pre(issue["raw_value"], class_name="mt-1 max-h-28 overflow-auto whitespace-pre-wrap break-all rounded bg-slate-50 p-2 text-xs text-slate-700"),
        rx.cond(issue["raw_value_truncated"], rx.el.p("This excerpt was truncated by the backend. The saved working value is intact.", class_name="mt-1 text-[11px] text-amber-800")),
        rx.cond(
            issue["row_id"] > 0,
            rx.el.div(
                rx.cond(
                    Q.checked_data_revision == D.data_revision,
                    rx.el.button(
                        rx.cond(Q.locating, "Locating record…", "Locate and edit record"),
                        on_click=Q.locate_issue(issue["number"], Q.request_generation),
                        disabled=D.busy | Q.loading | Q.locating | Q.workspace_outdated | (Q.read_error != ""),
                        class_name=BUTTON,
                    ),
                    rx.el.p("Historical record reference only. Reload working data and run a new check before locating it.", class_name=TEXT),
                ),
                class_name="mt-3",
            ),
            rx.el.p("This dataset-level issue has no record to open. Review scientific metadata or the rule's dependency reason.", class_name="mt-3 " + TEXT),
        ),
        class_name="min-w-0 rounded-lg border border-slate-200 bg-white p-4",
    )


def history_option(item: QualityHistoryItem) -> rx.Component:
    return rx.el.option(item["label"], value=item["id"])


def check_history() -> rx.Component:
    return rx.el.div(
        rx.el.label(
            rx.el.span("Check record", class_name="mb-1 block text-xs font-medium text-slate-600"),
            rx.el.select(
                rx.el.option("Latest accepted attempt", value=""),
                rx.cond(
                    (Q.selected_check_id != "") & ~Q.history.pluck("id").contains(Q.selected_check_id),
                    rx.el.option("Selected historical attempt", value=Q.selected_check_id),
                ),
                rx.foreach(Q.history, history_option),
                value=Q.selected_check_id,
                on_change=lambda value: Q.select_check(value, Q.request_generation),
                disabled=Q.loading,
                class_name=INPUT,
            ),
            class_name="block min-w-0 flex-1",
        ),
        rx.el.div(
            rx.el.button("Newer attempts", on_click=Q.change_page("history", -1, Q.request_generation), disabled=Q.loading | (Q.history_page == 0), class_name=BUTTON),
            rx.el.span(f"History page {Q.history_page + 1} of {Q.history_page_count} · {Q.history_total} attempts", class_name=TEXT),
            rx.el.button("Older attempts", on_click=Q.change_page("history", 1, Q.request_generation), disabled=Q.loading | (Q.history_page + 1 >= Q.history_page_count), class_name=BUTTON),
            class_name="mt-2 flex flex-wrap items-center gap-2",
        ),
        class_name="mt-4 min-w-0",
    )


def issue_list() -> rx.Component:
    return rx.el.section(
        rx.el.h3("Quality issues", class_name="text-sm font-semibold text-[#172e40]"),
        rx.el.div(
            rx.el.label(
                "Severity",
                rx.el.select(
                    rx.el.option("All severities", value=""),
                    rx.el.option("Error", value="error"), rx.el.option("Warning", value="warning"), rx.el.option("Info", value="info"),
                    value=Q.severity, on_change=lambda value: Q.change_filter("severity", value, Q.request_generation),
                    disabled=Q.loading, class_name=INPUT,
                ),
                class_name="text-xs text-slate-600",
            ),
            rx.el.label(
                "Rule",
                rx.el.select(
                    rx.el.option("All rules", value=""),
                    rx.foreach(Q.rule_options, lambda code: rx.el.option(code, value=code)),
                    value=Q.rule_code, on_change=lambda value: Q.change_filter("rule_code", value, Q.request_generation),
                    disabled=Q.loading, class_name=INPUT,
                ),
                class_name="min-w-0 text-xs text-slate-600",
            ),
            class_name="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2",
        ),
        rx.el.p(Q.issue_range, class_name="mt-3 " + TEXT),
        rx.cond(~Q.report_complete, rx.el.p("The report is incomplete. Available counts are not a complete assessment and an empty page does not establish that there are no problems.", class_name="mt-3 text-xs text-amber-800")),
        rx.cond(Q.issues_truncated, rx.el.p("The backend marked this report as truncated. The issue list does not contain every finding.", class_name="mt-3 text-xs text-amber-800")),
        rx.cond(
            Q.loading,
            rx.el.p("Loading the requested issue page…", role="status", class_name="mt-3 " + TEXT),
            rx.cond(
                Q.issues.length() > 0,
                rx.el.div(rx.foreach(Q.issues, issue_item), class_name="mt-3 grid gap-3"),
                rx.cond(
                    Q.report_complete & (Q.read_error == ""),
                    rx.el.p(rx.cond(Q.issue_total == 0, "The completed report contains no issues. Review rule coverage and its conclusion above.", "No issues match these filters."), class_name="mt-3 " + TEXT),
                ),
            ),
        ),
        rx.el.div(
            rx.el.button("Previous issues", on_click=Q.change_page("issues", -1, Q.request_generation), disabled=Q.loading | (Q.issue_page == 0), class_name=BUTTON),
            rx.el.span(f"Page {Q.issue_page + 1} of {Q.issue_page_count}", class_name=TEXT),
            rx.el.button("Next issues", on_click=Q.change_page("issues", 1, Q.request_generation), disabled=Q.loading | (Q.issue_page + 1 >= Q.issue_page_count), class_name=BUTTON),
            class_name="mt-4 flex flex-wrap items-center gap-2",
        ),
        class_name="mt-5 border-t border-slate-200 pt-4",
    )


def selected_report() -> rx.Component:
    return rx.el.div(
        rx.el.div(
            rx.el.h3(rx.cond(Q.selected_check_id == "", "Latest check", "Historical check"), class_name="text-sm font-semibold text-[#172e40]"),
            rx.el.span(Q.quality_status, class_name="rounded border border-slate-200 bg-slate-50 px-2 py-1 text-xs font-semibold text-slate-800"),
            class_name="flex flex-wrap items-center justify-between gap-2",
        ),
        rx.el.p(Q.status_message, role="status", class_name="mt-2 " + TEXT),
        rx.cond(
            Q.check_run_id != "",
            rx.el.div(
                rx.el.p(f"Checked data revision: {Q.checked_data_revision} · current data revision: {Q.current_data_revision}", class_name=TEXT),
                rx.el.p(f"Saved execution state: {Q.run_status} · original conclusion: {Q.original_outcome}", class_name="mt-1 " + TEXT),
                rx.el.p(f"Warnings: {Q.warning_count} · total issues: {Q.issue_total}", class_name="mt-1 " + TEXT),
                rx.cond(Q.stale_reasons != "", rx.el.p(f"Stale reasons: {Q.stale_reasons}", class_name="mt-2 text-xs text-amber-800")),
                rx.cond(Q.error_code != "", rx.el.p(f"Execution error code: {Q.error_code}", class_name="mt-2 text-xs text-red-700")),
                rx.el.dl(rx.foreach(Q.details, detail), class_name="mt-4 grid grid-cols-1 gap-3 md:grid-cols-2"),
                rx.cond(Q.statistics.length() > 0, rx.el.details(
                    rx.el.summary("Snapshot statistics and evidence", class_name="cursor-pointer text-xs font-medium text-slate-700"),
                    rx.el.dl(rx.foreach(Q.statistics, detail), class_name="mt-3 grid grid-cols-1 gap-3 md:grid-cols-2"),
                    class_name="mt-4",
                )),
                rx.el.details(
                    rx.el.summary("Rule coverage, dependencies and sources", class_name="cursor-pointer text-xs font-medium text-slate-700"),
                    rx.cond(
                        Q.rules.length() > 0,
                        rx.el.div(rx.foreach(Q.rules, rule_item), class_name="mt-3 grid gap-2"),
                        rx.el.p("No completed rule coverage is available for this attempt. The saved execution state above explains whether it is still running or ended in an error.", class_name="mt-3 " + TEXT),
                    ),
                    class_name="mt-4",
                ),
                issue_list(),
                class_name="mt-3",
            ),
        ),
        class_name="mt-5 rounded-lg border border-slate-200 p-4",
    )


def data_quality() -> rx.Component:
    """Render one selected dataset's quality area within the existing Data page."""
    return rx.el.section(
        rx.el.h2("Data quality", class_name="font-['IBM_Plex_Sans'] text-xl font-medium text-[#172e40]"),
        rx.el.p("Check a saved CSV, inspect findings, edit its working records above and explicitly recheck. Checks never modify the working data.", class_name="mt-1 " + TEXT),
        rx.cond(
            D.selected_id == 0,
            rx.el.p("Select or import a CSV to view scientific quality checks.", class_name="mt-4 " + TEXT),
            rx.el.div(
                rx.el.h3(D.selected_name, class_name="mt-4 break-words text-sm font-semibold text-slate-800"),
                rx.el.p(f"Working data revision: {D.data_revision}", class_name="mt-1 " + TEXT),
                rx.el.dl(
                    metadata_value("Working variable", D.variable_key),
                    metadata_value("Working month", D.observation_month),
                    metadata_value("Working unit", D.unit),
                    metadata_value("Working grid", D.grid_key),
                    class_name="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2",
                ),
                rx.cond(
                    D.refresh_required,
                    rx.el.p("Refresh saved data above before viewing quality or starting a check. Any successful save remains saved.", class_name="mt-4 text-xs text-amber-800"),
                    rx.cond(
                        (Q.dataset_id == D.selected_id) & (Q.workspace_revision == D.data_revision),
                        rx.el.div(
                            rx.el.div(
                                rx.el.button(
                                    rx.cond(Q.run_busy, "Check request in progress…", "Run check on working revision"),
                                    on_click=Q.start_check(Q.start_token),
                                    disabled=D.busy | Q.loading | Q.run_busy | Q.workspace_outdated | (Q.read_error != "") | (Q.latest_run_status == "running"),
                                    class_name=PRIMARY,
                                ),
                                rx.el.button("Refresh quality status", on_click=Q.refresh, disabled=Q.loading, class_name=BUTTON),
                                class_name="mt-4 flex flex-wrap gap-2",
                            ),
                            rx.cond(Q.run_busy, rx.el.p("A synchronous backend check request is pending. Saved execution state is read separately below; no progress percentage is reported.", role="status", class_name="mt-2 " + TEXT)),
                            rx.cond(Q.workspace_outdated, rx.el.div(
                                rx.el.p(f"The backend now reports data revision {Q.current_data_revision}. Reload the saved workspace before starting a check or editing an issue.", class_name=TEXT),
                                rx.el.button("Reload saved workspace", on_click=D.load_catalogue, disabled=D.loading | D.importing | D.saving, class_name=BUTTON),
                                class_name="mt-3 rounded-md border border-amber-200 bg-amber-50 p-3",
                            )),
                            feedback(Q.run_error), feedback(Q.read_error), feedback(Q.location_error),
                            feedback(Q.location_notice, "info"), feedback(Q.polling_notice, "info"),
                            check_history(),
                            rx.cond(Q.quality_status != "", selected_report(), rx.el.p(rx.cond(Q.read_error != "", "Quality status is unavailable. Refresh quality status to retry.", "Loading quality status…"), role="status", class_name="mt-4 " + TEXT)),
                            publication_control(),
                        ),
                        rx.el.p("Refreshing quality for the selected working revision…", role="status", class_name="mt-4 " + TEXT),
                    ),
                ),
            ),
        ),
        on_mount=Q.refresh,
        on_unmount=Q.leave,
        aria_busy=Q.loading | Q.locating | Q.run_busy,
        class_name="mb-6 min-w-0 rounded-lg border border-slate-200 bg-white p-4",
    )
