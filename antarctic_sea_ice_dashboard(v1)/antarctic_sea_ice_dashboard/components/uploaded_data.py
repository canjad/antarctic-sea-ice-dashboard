import reflex as rx
from antarctic_sea_ice_dashboard.services.dataset_metadata import GRID_OPTIONS
from antarctic_sea_ice_dashboard.states.uploaded_data import (
    UploadedData as D,
    DatasetSummary,
    PreviewRow,
    EditorField,
)


BUTTON = "inline-flex min-h-9 items-center justify-center gap-2 rounded-md border border-slate-200 bg-white px-3 text-xs font-medium text-slate-600 hover:bg-slate-50 hover:text-sky-800 transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-sky-700 disabled:opacity-40 disabled:cursor-not-allowed"
PRIMARY = "inline-flex min-h-9 items-center justify-center gap-2 rounded-md border border-[#245f7e] bg-[#245f7e] px-3 text-xs font-medium text-white hover:bg-sky-900 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-sky-700 disabled:opacity-40 disabled:cursor-not-allowed"
DANGER = "inline-flex min-h-9 items-center justify-center gap-2 rounded-md border border-red-200 bg-white px-3 text-xs font-medium text-red-600 hover:bg-red-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-red-700 disabled:opacity-40 disabled:cursor-not-allowed"
INPUT = "w-full min-h-9 rounded-md border border-slate-200 bg-white px-3 py-2 text-xs text-slate-800 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-sky-700"


def import_zone() -> rx.Component:
    """Render CSV selection and import events; state reports validation failures."""
    return rx.el.section(
        rx.el.h2(
            "Import CSV", class_name="text-sm font-semibold text-[#172e40]"
        ),
        rx.el.p(
            "Select one file, then import it into this shared workspace.",
            class_name="mt-1 text-xs leading-relaxed text-slate-500",
        ),
        rx.upload.root(
            rx.el.div(
                rx.icon("file-up", class_name="h-6 w-6 text-[#397794]"),
                rx.el.span(
                    "Choose CSV or drop it here",
                    class_name="text-xs font-medium text-[#245f7e]",
                ),
                rx.el.span(
                    "One .csv · UTF-8 · max 5 MB per file",
                    class_name="text-[10px] text-slate-500",
                ),
                class_name="flex flex-col items-center gap-2 px-3 py-5",
            ),
            id="csv_import",
            multiple=False,
            disabled=D.busy,
            aria_label="Choose a CSV file to import",
            class_name="mt-4 w-full cursor-pointer rounded-lg border border-dashed border-slate-300 bg-[#f4f8fa] hover:border-sky-600 focus-visible:outline-2 focus-visible:outline-sky-700",
        ),
        rx.cond(
            rx.selected_files("csv_import").length() > 0,
            rx.el.div(
                rx.foreach(
                    rx.selected_files("csv_import"),
                    lambda filename: rx.el.p(
                        filename, class_name="break-all text-xs text-slate-700"
                    ),
                ),
                class_name="mt-3 rounded-md border border-slate-200 bg-slate-50 p-2",
            ),
            rx.el.p(
                "No file selected", class_name="mt-3 text-[11px] text-slate-500"
            ),
        ),
        rx.el.div(
            rx.el.button(
                rx.cond(
                    D.importing,
                    rx.icon("loader-circle", class_name="h-4 w-4 animate-spin"),
                    rx.icon("upload", class_name="h-4 w-4"),
                ),
                rx.cond(D.importing, "Validating & saving…", "Import CSV"),
                on_click=D.import_csv(
                    rx.upload_files(upload_id="csv_import"), D.import_token
                ),
                disabled=D.busy
                | (D.import_token == "")
                | (rx.selected_files("csv_import").length() == 0),
                class_name=PRIMARY,
            ),
            rx.el.button(
                "Clear",
                on_click=rx.clear_selected_files("csv_import"),
                disabled=D.busy,
                class_name=BUTTON,
            ),
            class_name="mt-3 flex flex-wrap gap-2",
        ),
        rx.el.p(
            "Up to 100,000 rows and 10 columns per CSV; the 5 MB per-file limit still applies. Unique nonempty headers; equal field counts. Header-only CSVs are supported. Values remain strings, including dates, formulas and leading zeros.",
            class_name="mt-3 text-[10px] leading-relaxed text-slate-500",
        ),
        rx.el.p(
            "Parsed contents are saved, not the raw file. Importing never changes maps, charts or model results.",
            class_name="mt-2 text-[10px] leading-relaxed text-slate-500",
        ),
        class_name="border-b border-slate-200 p-4",
    )


def catalogue_item(dataset: DatasetSummary) -> rx.Component:
    """Render one persisted dataset summary and its selection event."""
    return rx.el.button(
        rx.el.div(
            rx.icon(
                "file-spreadsheet", class_name="h-4 w-4 shrink-0 text-[#397794]"
            ),
            rx.el.span(
                dataset["name"],
                class_name="min-w-0 flex-1 break-words text-left text-xs font-medium",
            ),
            rx.cond(
                D.selected_id == dataset["id"],
                rx.icon(
                    "check", class_name="h-3.5 w-3.5 shrink-0 text-sky-700"
                ),
            ),
            class_name="flex items-start gap-2",
        ),
        rx.el.p(
            f"{dataset['rows']:,} rows · {dataset['columns']} columns",
            class_name="mt-2 text-left text-[10px] text-slate-500",
        ),
        rx.el.p(
            dataset["created"],
            class_name="mt-1 text-left text-[10px] text-slate-500",
        ),
        on_click=D.select_dataset(dataset["id"]),
        disabled=D.busy,
        aria_pressed=D.selected_id == dataset["id"],
        key=dataset["id"],
        class_name=rx.cond(
            D.selected_id == dataset["id"],
            "w-full rounded-md border border-sky-200 bg-[#edf5f8] p-3 text-[#235b78] focus-visible:outline-2 focus-visible:outline-sky-700 disabled:opacity-40",
            "w-full rounded-md border border-slate-200 bg-white p-3 text-slate-700 hover:bg-slate-50 focus-visible:outline-2 focus-visible:outline-sky-700 disabled:opacity-40",
        ),
    )


def catalogue() -> rx.Component:
    """Render saved summaries and a refresh action available during recovery."""
    return rx.el.aside(
        import_zone(),
        rx.el.section(
            rx.el.div(
                rx.el.h2(
                    "Saved datasets",
                    class_name="text-sm font-semibold text-[#172e40]",
                ),
                rx.el.span(
                    D.catalogue.length(),
                    class_name="w-fit rounded bg-slate-100 px-2 py-1 text-[10px] text-slate-600",
                ),
                class_name="flex items-center justify-between",
            ),
            rx.el.p(
                "Shared by everyone using this prototype.",
                class_name="mt-1 text-[11px] text-slate-500",
            ),
            rx.el.button(
                rx.icon("refresh-cw", class_name="h-3.5 w-3.5"),
                "Refresh catalogue",
                on_click=D.load_catalogue,
                disabled=D.loading | D.importing | D.saving,
                class_name=BUTTON,
            ),
            rx.cond(
                D.loading,
                rx.el.div(
                    "Loading saved datasets…",
                    role="status",
                    class_name="animate-pulse rounded-md bg-slate-100 p-4 text-xs text-slate-500",
                ),
                rx.cond(
                    D.catalogue.length() > 0,
                    rx.el.div(
                        rx.foreach(D.catalogue, catalogue_item),
                        class_name="flex max-h-[480px] flex-col gap-2 overflow-y-auto",
                    ),
                    rx.el.div(
                        rx.icon(
                            "database", class_name="mb-2 h-5 w-5 text-slate-400"
                        ),
                        rx.el.p(
                            "No saved datasets yet",
                            class_name="text-xs font-medium text-slate-600",
                        ),
                        rx.el.p(
                            "Import a non-confidential CSV to create the first record.",
                            class_name="mt-2 text-[11px] leading-relaxed text-slate-500",
                        ),
                        class_name="rounded-md border border-dashed border-slate-200 bg-slate-50 p-4",
                    ),
                ),
            ),
            class_name="flex flex-col gap-3 p-4",
        ),
        class_name="min-w-0 rounded-lg border border-slate-200 bg-white",
    )


def table_row(row: PreviewRow) -> rx.Component:
    """Render a preview record using its stable row ID for editing."""
    return rx.el.tr(
        rx.el.td(
            rx.el.button(
                rx.icon("pencil", class_name="h-3 w-3"),
                f"Record {row['number']}",
                on_click=D.edit_row(row["id"]),
                aria_label=f"Edit record {row['number']}",
                disabled=D.busy,
                class_name=BUTTON,
            ),
            class_name="sticky left-0 border-b border-slate-100 bg-white px-3 py-2",
        ),
        rx.foreach(
            row["cells"],
            lambda cell: rx.el.td(
                rx.el.div(
                    cell,
                    class_name="max-h-28 min-w-36 max-w-80 overflow-auto whitespace-pre-wrap break-words text-xs text-slate-700",
                ),
                class_name="border-b border-slate-100 px-3 py-2 align-top",
            ),
        ),
        key=row["id"],
        class_name=rx.cond(
            D.editor_row_id == row["id"],
            "bg-sky-50 text-slate-700",
            "bg-white text-slate-700 even:bg-slate-50 hover:bg-[#f0f6f9]",
        ),
    )


def preview() -> rx.Component:
    """Render the current database page and state-controlled page navigation."""
    return rx.el.section(
        rx.el.div(
            rx.el.div(
                rx.el.h3(
                    "Table preview",
                    class_name="text-sm font-semibold text-[#172e40]",
                ),
                rx.el.p(
                    "Choose a record to edit its string values below. Record numbers exclude the header and differ from CSV physical lines when quoted values contain newlines.",
                    class_name="mt-1 text-[11px] text-slate-500",
                ),
            ),
            rx.el.span(
                f"{D.row_count:,} rows · {D.columns.length()} columns",
                class_name="text-xs font-medium text-[#245f7e]",
            ),
            class_name="flex flex-wrap items-center justify-between gap-3 p-4",
        ),
        rx.cond(
            D.row_count > 0,
            rx.el.div(
                rx.el.div(
                    rx.el.table(
                        rx.el.thead(
                            rx.el.tr(
                                rx.el.th(
                                    rx.el.div(
                                        rx.icon(
                                            "list-ordered", class_name="h-3 w-3"
                                        ),
                                        "Record",
                                        class_name="flex items-center gap-2",
                                    ),
                                    scope="col",
                                    class_name="sticky left-0 z-10 bg-[#f4f8fa] px-3 py-3 text-left text-[11px] font-medium text-slate-600",
                                ),
                                rx.foreach(
                                    D.columns,
                                    lambda column: rx.el.th(
                                        rx.el.div(
                                            rx.icon(
                                                "columns-3",
                                                class_name="h-3 w-3 shrink-0 text-slate-400",
                                            ),
                                            rx.el.span(
                                                column,
                                                class_name="whitespace-pre-wrap break-words",
                                            ),
                                            class_name="flex min-w-36 max-w-80 items-start gap-2",
                                        ),
                                        scope="col",
                                        class_name="bg-[#f4f8fa] px-3 py-3 text-left text-[11px] font-medium text-slate-600",
                                    ),
                                ),
                            ),
                            class_name="sticky top-0 z-20",
                        ),
                        rx.el.tbody(rx.foreach(D.rows, table_row)),
                        class_name="table-auto w-full border-collapse",
                    ),
                    tab_index=0,
                    aria_label="Uploaded dataset table; scroll horizontally for more columns",
                    class_name="max-h-[440px] w-full overflow-auto focus-visible:outline-2 focus-visible:outline-sky-700",
                ),
                class_name="w-full overflow-hidden border-y border-slate-200",
            ),
            rx.el.div(
                rx.icon("rows-3", class_name="mb-2 h-6 w-6 text-slate-400"),
                rx.el.p(
                    "This dataset has headers but no rows.",
                    class_name="text-xs text-slate-600",
                ),
                rx.el.p(
                    "Choose Add row to enter the first record.",
                    class_name="mt-1 text-[11px] text-slate-500",
                ),
                rx.el.p(
                    D.columns.join(" · "),
                    class_name="mt-3 whitespace-pre-wrap break-words text-xs text-slate-700",
                ),
                class_name="border-y border-slate-200 bg-slate-50 p-5",
            ),
        ),
        rx.el.div(
            rx.el.span(D.range_label, class_name="text-[11px] text-slate-500"),
            rx.el.div(
                rx.el.button(
                    rx.icon("chevron-left", class_name="h-3.5 w-3.5"),
                    "Previous",
                    on_click=D.change_page(-1),
                    disabled=(D.page_index == 0) | D.busy,
                    class_name=BUTTON,
                ),
                rx.el.span(
                    f"Page {D.page_index + 1} / {D.page_count}",
                    class_name="text-[11px] text-slate-600",
                ),
                rx.el.button(
                    "Next",
                    rx.icon("chevron-right", class_name="h-3.5 w-3.5"),
                    on_click=D.change_page(1),
                    disabled=(D.page_index + 1 >= D.page_count) | D.busy,
                    class_name=BUTTON,
                ),
                class_name="flex items-center gap-2",
            ),
            class_name="flex flex-wrap items-center justify-between gap-3 p-4",
        ),
    )


def editor_field(field: EditorField) -> rx.Component:
    """Render a named string field from the current row draft."""
    return rx.el.label(
        rx.el.span(
            field["label"],
            class_name="block whitespace-pre-wrap break-words text-[11px] font-medium text-slate-600",
        ),
        rx.el.textarea(
            name=field["name"],
            aria_label=field["label"],
            default_value=field["value"],
            rows=2,
            max_length=32768,
            disabled=D.busy,
            class_name="mt-1 w-full resize-y rounded-md border border-slate-200 bg-white px-3 py-2 text-xs text-slate-800 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-sky-700",
        ),
        key=field["name"],
        class_name="block min-w-0",
    )


def row_editor() -> rx.Component:
    """Render a versioned row form; state handles stale or invalid submissions."""
    return rx.el.section(
        rx.cond(
            D.editor_mode != "",
            rx.el.div(
                rx.el.h3(
                    rx.cond(
                        D.editor_mode == "add",
                        "Add a new row",
                        f"Edit record {D.editor_row_number}",
                    ),
                    class_name="text-sm font-semibold text-[#172e40]",
                ),
                rx.el.p(
                    "Blank values are allowed. Save commits this row to the shared workspace. Unsaved drafts are discarded when you change dataset, page or row, or refresh.",
                    class_name="mt-1 text-[11px] leading-relaxed text-slate-500",
                ),
                rx.el.form(
                    rx.el.input(
                        type="hidden",
                        name="form_revision",
                        value=D.form_revision.to(str),
                    ),
                    rx.el.div(
                        rx.foreach(D.editor_fields, editor_field),
                        class_name="mt-4 grid max-h-[420px] grid-cols-1 gap-3 overflow-y-auto p-1 md:grid-cols-2",
                    ),
                    rx.el.div(
                        rx.el.button(
                            rx.icon("save", class_name="h-3.5 w-3.5"),
                            "Save row",
                            type="submit",
                            disabled=D.busy,
                            class_name=PRIMARY,
                        ),
                        rx.el.button(
                            "Cancel",
                            type="button",
                            on_click=D.cancel_edit,
                            disabled=D.busy,
                            class_name=BUTTON,
                        ),
                        rx.cond(
                            D.editor_mode == "edit",
                            rx.el.button(
                                rx.icon("trash-2", class_name="h-3.5 w-3.5"),
                                "Delete row…",
                                type="button",
                                on_click=D.request_delete("row"),
                                disabled=D.busy,
                                class_name=DANGER,
                            ),
                        ),
                        class_name="mt-4 flex flex-wrap gap-2",
                    ),
                    key=D.form_revision,
                    on_submit=D.save_row,
                ),
            ),
            rx.el.div(
                rx.icon(
                    "mouse-pointer-2",
                    class_name="h-4 w-4 shrink-0 text-slate-400",
                ),
                rx.el.p(
                    "Select a row in the preview, or choose Add row. The original synthetic fixtures below are not editable records.",
                    class_name="text-xs leading-relaxed text-slate-500",
                ),
                class_name="flex gap-2",
            ),
        ),
        class_name="border-t border-slate-200 p-4",
    )


def delete_confirmation() -> rx.Component:
    """Render deletion confirmation bound to the captured state token."""
    return rx.cond(
        D.confirmation != "",
        rx.el.div(
            rx.el.h3(
                rx.cond(
                    D.confirmation == "dataset",
                    f"Delete “{D.selected_name}” and all its rows?",
                    f"Delete record {D.editor_row_number}?",
                ),
                class_name="text-sm font-semibold text-red-700",
            ),
            rx.el.p(
                "This permanently removes the record from the shared workspace for everyone. This action cannot be undone.",
                class_name="mt-2 text-xs leading-relaxed text-slate-600",
            ),
            rx.el.div(
                rx.el.button(
                    "Confirm permanent deletion",
                    on_click=D.confirm_delete(D.confirmation_token),
                    disabled=D.busy,
                    class_name=DANGER,
                ),
                rx.el.button(
                    "Keep record",
                    on_click=D.cancel_delete,
                    disabled=D.busy,
                    class_name=BUTTON,
                ),
                class_name="mt-3 flex flex-wrap gap-2",
            ),
            role="alert",
            class_name="m-4 rounded-lg border border-red-200 bg-red-50 p-4",
        ),
    )


def metadata_value(label: str, value: rx.Var[str]) -> rx.Component:
    """Render one persisted metadata value, displaying unknown values explicitly."""
    return rx.el.div(
        rx.el.dt(label, class_name="text-[11px] font-medium text-slate-500"),
        rx.el.dd(
            rx.cond(value != "", value, "Not set"),
            class_name="mt-1 break-words text-xs text-slate-700",
        ),
        class_name="min-w-0",
    )


def metadata_field(
    label: str,
    name: str,
    value: rx.Var[str],
    placeholder: str,
) -> rx.Component:
    """Render an optional named metadata input with its persisted initial value."""
    return rx.el.label(
        rx.el.span(
            label,
            class_name="block text-[11px] font-medium text-slate-600",
        ),
        rx.el.input(
            name=name,
            default_value=value,
            placeholder=placeholder,
            max_length=100,
            disabled=D.busy,
            class_name=INPUT,
        ),
        class_name="block min-w-0",
    )


def dataset_metadata() -> rx.Component:
    """Render declarations and a versioned form; state reports invalid metadata."""
    return rx.el.section(
        rx.el.div(
            rx.el.h3(
                "Scientific metadata",
                class_name="text-sm font-semibold text-[#172e40]",
            ),
            rx.el.button(
                rx.icon("pencil", class_name="h-3.5 w-3.5"),
                rx.cond(D.metadata_open, "Close metadata", "Edit metadata"),
                on_click=D.open_metadata,
                disabled=D.busy,
                class_name=BUTTON,
            ),
            class_name="flex flex-wrap items-center justify-between gap-3",
        ),
        rx.el.dl(
            metadata_value(
                "Variable key",
                rx.cond(
                    D.variable_key == "v10",
                    "v10 · 10 m northward wind component",
                    D.variable_key,
                ),
            ),
            metadata_value("Observation month", D.observation_month),
            metadata_value("Unit", D.unit),
            metadata_value("Grid identity", D.grid_key),
            class_name="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2",
        ),
        rx.el.p(
            "These optional declarations describe the data. Saving them does not perform scientific checks.",
            class_name="mt-3 text-[11px] leading-relaxed text-slate-500",
        ),
        rx.cond(
            D.metadata_open,
            rx.el.form(
                rx.el.input(
                    type="hidden",
                    name="metadata_form_revision",
                    value=D.metadata_form_revision.to(str),
                ),
                rx.el.div(
                    metadata_field(
                        "Variable key",
                        "variable_key",
                        D.variable_key,
                        "Not set",
                    ),
                    rx.el.label(
                        rx.el.span(
                            "Observation month (YYYY-MM)",
                            class_name="block text-[11px] font-medium text-slate-600",
                        ),
                        rx.el.input(
                            name="observation_month",
                            default_value=D.observation_month,
                            placeholder="YYYY-MM",
                            pattern="[0-9]{4}-(0[1-9]|1[0-2])",
                            max_length=7,
                            disabled=D.busy,
                            class_name=INPUT,
                        ),
                        class_name="block min-w-0",
                    ),
                    metadata_field("Unit", "unit", D.unit, "Not set"),
                    rx.el.label(
                        rx.el.span(
                            "Grid identity",
                            class_name="block text-[11px] font-medium text-slate-600",
                        ),
                        rx.el.select(
                            rx.el.option("Not set", value=""),
                            *[
                                rx.el.option(label, value=key)
                                for key, label in GRID_OPTIONS.items()
                            ],
                            name="grid_key",
                            default_value=D.grid_key,
                            disabled=D.busy,
                            class_name=INPUT,
                        ),
                        class_name="block min-w-0",
                    ),
                    class_name="grid grid-cols-1 gap-3 sm:grid-cols-2",
                ),
                rx.el.p(
                    "Use an established variable key. v10 means the 10 m northward wind component, not wind speed. The month describes the observations, not the import date. Leave unknown values blank and choose Not set for an unknown grid.",
                    class_name="mt-3 text-[11px] leading-relaxed text-slate-500",
                ),
                rx.el.div(
                    rx.el.button(
                        "Save metadata",
                        type="submit",
                        disabled=D.busy,
                        class_name=PRIMARY,
                    ),
                    rx.el.button(
                        "Cancel",
                        type="button",
                        on_click=D.open_metadata,
                        disabled=D.busy,
                        class_name=BUTTON,
                    ),
                    class_name="mt-3 flex flex-wrap gap-2",
                ),
                key=D.metadata_form_revision,
                on_submit=D.update_dataset_metadata,
                class_name="mt-4 rounded-md border border-slate-200 bg-slate-50 p-3",
            ),
        ),
        class_name="border-b border-slate-200 p-4",
    )


def dataset_editor() -> rx.Component:
    """Render the selected dataset and its metadata, row and management events."""
    return rx.el.section(
        rx.cond(
            D.selected_id > 0,
            rx.el.div(
                rx.el.div(
                    rx.el.span(
                        "UPLOADED CSV · STORED RECORD",
                        class_name="text-[9px] tracking-wider text-[#397794]",
                    ),
                    rx.el.h2(
                        D.selected_name,
                        class_name="mt-2 break-words font-['IBM_Plex_Sans'] text-xl font-medium text-[#172e40]",
                    ),
                    rx.el.p(
                        f"Original file: {D.original_filename}",
                        class_name="mt-1 break-all text-[11px] text-slate-500",
                    ),
                    rx.el.div(
                        rx.el.span(f"Data revision: {D.data_revision}"),
                        rx.el.span(
                            "Import status: ",
                            rx.cond(
                                D.import_status == "ready",
                                "Ready",
                                D.import_status,
                            ),
                        ),
                        rx.el.span("Scientific checks: see Data quality below"),
                        class_name="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-slate-600",
                    ),
                    rx.el.div(
                        rx.el.button(
                            rx.icon("plus", class_name="h-3.5 w-3.5"),
                            "Add row",
                            on_click=D.add_row,
                            disabled=(D.row_count >= 100000) | D.busy,
                            class_name=PRIMARY,
                        ),
                        rx.el.button(
                            rx.icon("pencil", class_name="h-3.5 w-3.5"),
                            "Rename",
                            on_click=D.open_rename,
                            disabled=D.busy,
                            class_name=BUTTON,
                        ),
                        rx.el.button(
                            rx.icon("trash-2", class_name="h-3.5 w-3.5"),
                            "Delete dataset…",
                            on_click=D.request_delete("dataset"),
                            disabled=D.busy,
                            class_name=DANGER,
                        ),
                        class_name="mt-4 flex flex-wrap gap-2",
                    ),
                    rx.cond(
                        D.row_count >= 100000,
                        rx.el.p(
                            "100,000-row limit reached. Delete a row before adding another.",
                            class_name="mt-2 text-[11px] text-slate-500",
                        ),
                    ),
                    rx.cond(
                        D.rename_open,
                        rx.el.form(
                            rx.el.input(
                                type="hidden",
                                name="form_revision",
                                value=D.form_revision.to(str),
                            ),
                            rx.el.label(
                                "Dataset name",
                                rx.el.input(
                                    name="dataset_name",
                                    default_value=D.selected_name,
                                    key=D.selected_name,
                                    required=True,
                                    max_length=200,
                                    disabled=D.busy,
                                    class_name=INPUT,
                                ),
                                class_name="flex-1 text-[11px] font-medium text-slate-600",
                            ),
                            rx.el.button(
                                "Save name",
                                type="submit",
                                disabled=D.busy,
                                class_name=PRIMARY,
                            ),
                            rx.el.button(
                                "Cancel",
                                type="button",
                                on_click=D.open_rename,
                                disabled=D.busy,
                                class_name=BUTTON,
                            ),
                            on_submit=D.rename_dataset,
                            key=D.form_revision,
                            class_name="mt-4 flex flex-wrap items-end gap-2 rounded-md border border-slate-200 bg-slate-50 p-3",
                        ),
                    ),
                    class_name="border-b border-slate-200 p-4",
                ),
                dataset_metadata(),
                preview(),
                row_editor(),
                delete_confirmation(),
            ),
            rx.el.div(
                rx.icon("table-2", class_name="h-9 w-9 text-[#397794]"),
                rx.el.h2(
                    "Your CSV workspace",
                    class_name="mt-4 font-['IBM_Plex_Sans'] text-xl font-medium text-[#172e40]",
                ),
                rx.el.p(
                    "Import a CSV or select a saved dataset to preview columns and edit rows.",
                    class_name="mt-2 text-xs leading-relaxed text-slate-500",
                ),
                rx.el.p(
                    "All saved datasets are shared. Use only non-confidential data.",
                    class_name="mt-3 text-xs text-slate-600",
                ),
                class_name="flex min-h-80 flex-col items-center justify-center p-8 text-center",
            ),
        ),
        aria_busy=D.loading | D.importing | D.saving,
        class_name="min-w-0 w-full rounded-lg border border-slate-200 bg-white",
    )


def uploaded_workspace() -> rx.Component:
    """Render CSV workspace messages, recovery action and persisted data views."""
    return rx.el.div(
        rx.el.div(
            rx.icon("users", class_name="h-5 w-5 shrink-0 text-[#36758b]"),
            rx.el.div(
                rx.el.p(
                    "Shared prototype workspace · non-confidential CSVs only",
                    class_name="text-sm font-medium text-[#172e40]",
                ),
                rx.el.p(
                    "Uploaded datasets and edits are stored and visible to everyone using this prototype. There is no sign-in or private access. Do not upload confidential or personal data. Checked and published products can be selected in Overview, Map and Compare. Model Insights contains synthetic illustrations.",
                    class_name="mt-1 text-xs leading-relaxed text-slate-600",
                ),
            ),
            class_name="mb-4 flex gap-3 rounded-lg border border-slate-200 bg-[#edf4f7] p-4",
        ),
        rx.cond(
            D.error != "",
            rx.el.div(
                rx.icon("circle-alert", class_name="h-4 w-4 shrink-0"),
                rx.el.p(D.error),
                role="alert",
                class_name="mb-4 flex items-start gap-2 rounded-lg border border-red-200 bg-red-50 p-3 text-xs leading-relaxed text-red-700",
            ),
        ),
        rx.cond(
            D.success != "",
            rx.el.div(
                rx.icon("circle-check", class_name="h-4 w-4 shrink-0"),
                rx.el.p(D.success),
                role="status",
                class_name="mb-4 flex items-start gap-2 rounded-lg border border-green-200 bg-green-50 p-3 text-xs leading-relaxed text-green-700",
            ),
        ),
        rx.cond(
            D.refresh_required,
            rx.el.div(
                rx.el.p(
                    "Refresh the saved data before making another change. Do not repeat a change that was already saved.",
                    class_name="text-xs leading-relaxed text-amber-800",
                ),
                rx.el.button(
                    rx.icon("refresh-cw", class_name="h-3.5 w-3.5"),
                    "Refresh saved data",
                    on_click=D.load_catalogue,
                    disabled=D.loading | D.importing | D.saving,
                    class_name=BUTTON,
                ),
                role="status",
                class_name="mb-4 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-amber-200 bg-amber-50 p-3",
            ),
        ),
        rx.el.div(
            catalogue(),
            dataset_editor(),
            class_name="grid w-full grid-cols-1 items-start gap-4 xl:grid-cols-[300px_minmax(0,1fr)]",
        ),
        class_name="mb-6 w-full min-w-0",
    )
