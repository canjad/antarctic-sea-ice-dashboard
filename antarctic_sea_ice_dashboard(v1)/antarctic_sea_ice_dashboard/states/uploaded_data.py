"""Reflex orchestration for the shared, versioned CSV workspace."""

from collections.abc import AsyncIterator, Iterator
from datetime import UTC
import json
import logging
from typing import TypedDict
from uuid import uuid4

import reflex as rx
from sqlalchemy import select

from antarctic_sea_ice_dashboard.models import UploadedDataset, UploadedDatasetRow
from antarctic_sea_ice_dashboard.services.csv_import import (
    MAX_BYTES,
    MAX_CELL,
    MAX_COLUMNS,
    MAX_ROWS,
    parse_csv,
)
from antarctic_sea_ice_dashboard.services.dataset_metadata import DatasetMetadata
from antarctic_sea_ice_dashboard.services.uploaded_datasets import (
    DatasetConflict,
    DatasetRowLocation,
    create_uploaded_dataset,
    delete_dataset_row,
    delete_uploaded_dataset,
    rename_uploaded_dataset,
    save_dataset_row,
    update_dataset_metadata as save_dataset_metadata,
)


PAGE_SIZE = 25
logger = logging.getLogger(__name__)


def _quality_refresh_event() -> object:
    # Resolve the sibling state after both modules have been defined.
    from antarctic_sea_ice_dashboard.states.data_quality import DataQuality

    return DataQuality.refresh


class DatasetSummary(TypedDict):
    id: int
    name: str
    filename: str
    rows: int
    columns: int
    created: str


class PreviewRow(TypedDict):
    id: int
    number: int
    cells: list[str]


class EditorField(TypedDict):
    name: str
    label: str
    value: str


class UploadedData(rx.State):
    catalogue: list[DatasetSummary] = []
    selected_id: int = 0
    selected_name: str = ""
    original_filename: str = ""
    columns: list[str] = []
    rows: list[PreviewRow] = []
    row_count: int = 0
    data_revision: int = 0
    import_status: str = ""
    variable_key: str = ""
    observation_month: str = ""
    unit: str = ""
    grid_key: str = ""
    page_index: int = 0
    loading: bool = False
    importing: bool = False
    saving: bool = False
    refresh_required: bool = False
    import_token: str = ""
    error: str = ""
    success: str = ""
    editor_mode: str = ""
    editor_row_id: int = 0
    editor_row_number: int = 0
    editor_original: str = ""
    editor_fields: list[EditorField] = []
    form_revision: int = 0
    rename_open: bool = False
    metadata_open: bool = False
    metadata_form_revision: int = 0
    metadata_expected_data_revision: int = 0
    confirmation: str = ""
    confirmation_token: str = ""
    _editor_expected_data_revision: int = 0
    _rename_expected_data_revision: int = 0
    _rename_expected_name: str = ""
    _confirmation_dataset_id: int = 0
    _confirmation_data_revision: int = 0
    _confirmation_row_id: int = 0
    _confirmation_name: str = ""
    _selected_identity_key: str = ""
    _confirmation_identity_key: str = ""

    @rx.var
    def busy(self) -> bool:
        """Block mutations until pending work or refresh recovery finishes."""
        return self.loading or self.importing or self.saving or self.refresh_required

    @rx.var
    def page_count(self) -> int:
        """Return at least one page, including header-only datasets."""
        return max(1, (self.row_count + PAGE_SIZE - 1) // PAGE_SIZE)

    @rx.var
    def range_label(self) -> str:
        """Describe data records, independently of physical CSV line numbers."""
        if not self.row_count:
            return "0 records"
        start = self.page_index * PAGE_SIZE + 1
        end = min(self.row_count, start + PAGE_SIZE - 1)
        return f"Records {start}–{end} of {self.row_count:,}"

    @staticmethod
    def _parse_csv(filename: str, data: bytes) -> tuple[list[str], list[list[str]]]:
        """Preserve the existing parser interface; invalid input raises ValueError."""
        return parse_csv(filename, data)

    def _clear_confirmation(self) -> None:
        self.confirmation = ""
        self.confirmation_token = ""
        self._confirmation_dataset_id = 0
        self._confirmation_data_revision = 0
        self._confirmation_row_id = 0
        self._confirmation_name = ""
        self._confirmation_identity_key = ""

    def _clear_editor(self) -> None:
        self.editor_mode = ""
        self.editor_row_id = 0
        self.editor_row_number = 0
        self.editor_original = ""
        self.editor_fields = []
        self._editor_expected_data_revision = 0
        self._clear_confirmation()
        self.form_revision += 1

    def _clear_drafts(self) -> None:
        self._clear_editor()
        self.rename_open = False
        self._rename_expected_data_revision = 0
        self._rename_expected_name = ""
        self.metadata_open = False
        self.metadata_expected_data_revision = 0
        self.metadata_form_revision += 1

    def _clear_selection(self) -> None:
        self.selected_id = 0
        self._selected_identity_key = ""
        self.selected_name = ""
        self.original_filename = ""
        self.columns = []
        self.rows = []
        self.row_count = 0
        self.data_revision = 0
        self.import_status = ""
        self.variable_key = ""
        self.observation_month = ""
        self.unit = ""
        self.grid_key = ""
        self.page_index = 0
        self._clear_drafts()

    def _reload(self, *, require_selected: bool = False) -> None:
        # Build locally so a failed query cannot publish a partially refreshed
        # revision/preview. Verify revision after pagination on READ COMMITTED.
        with rx.session() as session:
            datasets = session.scalars(
                select(UploadedDataset).order_by(
                    UploadedDataset.created_at.desc(), UploadedDataset.id.desc()
                )
            ).all()
            catalogue: list[DatasetSummary] = []
            for dataset in datasets:
                created_at = dataset.created_at
                # SQLite may return naive values for the existing UTC field.
                if created_at.tzinfo is None:
                    created_at = created_at.replace(tzinfo=UTC)
                catalogue.append(
                    {
                        "id": dataset.id,
                        "name": dataset.name,
                        "filename": dataset.original_filename,
                        "rows": dataset.row_count,
                        "columns": len(json.loads(dataset.column_names_json)),
                        "created": created_at.astimezone(UTC).strftime(
                            "%d %b %Y · %H:%M UTC"
                        ),
                    }
                )
            selected = next((d for d in datasets if d.id == self.selected_id), None)
            if require_selected and (
                selected is None or selected.identity_key != self._selected_identity_key
            ):
                raise DatasetConflict("The selected dataset was deleted or replaced. Refresh the catalogue before locating another record.")
            if selected is None and datasets:
                selected = datasets[0]
                page_index = 0
            else:
                page_index = self.page_index
            if selected is None:
                self._clear_selection()
                self.catalogue = catalogue
                return
            page_index = min(
                page_index, max(0, (selected.row_count - 1) // PAGE_SIZE)
            )
            stored_rows = session.scalars(
                select(UploadedDatasetRow)
                .where(UploadedDatasetRow.dataset_id == selected.id)
                .order_by(UploadedDatasetRow.row_position, UploadedDatasetRow.id)
                .offset(page_index * PAGE_SIZE)
                .limit(PAGE_SIZE)
            ).all()
            rows: list[PreviewRow] = [
                {
                    "id": row.id,
                    "number": page_index * PAGE_SIZE + index + 1,
                    "cells": json.loads(row.cell_values_json),
                }
                for index, row in enumerate(stored_rows)
            ]
            current_version = session.execute(
                select(UploadedDataset.data_revision, UploadedDataset.identity_key).where(
                    UploadedDataset.id == selected.id
                )
            ).one_or_none()
            if current_version is None or tuple(current_version) != (
                selected.data_revision, selected.identity_key
            ):
                raise DatasetConflict(
                    "The dataset changed while loading. Refresh the catalogue."
                )
            self.selected_id = selected.id
            self._selected_identity_key = selected.identity_key or ""
            self.selected_name = selected.name
            self.original_filename = selected.original_filename
            self.columns = json.loads(selected.column_names_json)
            self.row_count = selected.row_count
            self.data_revision = selected.data_revision
            self.import_status = selected.status
            self.variable_key = selected.variable_key or ""
            self.observation_month = selected.observation_month or ""
            self.unit = selected.unit or ""
            self.grid_key = selected.grid_key or ""
            self.page_index = page_index
            self.rows = rows
            self.catalogue = catalogue

    def _read_failed(self, message: str) -> None:
        self.rows = []
        self._clear_drafts()
        self.refresh_required = True
        self.error = message

    def _after_write(self, message: str) -> None:
        # Every caller has left its committing session before entering here.
        self._clear_drafts()
        self.success = message
        try:
            self._reload()
            self.refresh_required = False
        except Exception:
            logger.exception("A committed workspace change could not be refreshed.")
            self._read_failed(
                "Your change was saved, but the preview could not refresh. "
                "Refresh the catalogue; do not repeat the change."
            )

    def _write_failed(self, error: Exception, message: str) -> None:
        logger.exception("Workspace transaction failed.")
        if isinstance(error, DatasetConflict):
            self._read_failed(str(error))
        else:
            self.error = str(error) if isinstance(error, ValueError) else message

    @rx.event
    def load_catalogue(self) -> Iterator[object]:
        """Refresh persisted state; recovery remains available after read failures."""
        if self.loading or self.importing or self.saving:
            return
        self.loading = True
        self.error = ""
        self._clear_drafts()
        if not self.import_token:
            self.import_token = uuid4().hex
        yield
        try:
            self._reload()
            self.refresh_required = False
        except Exception:
            logger.exception("Could not refresh the dataset catalogue.")
            self._read_failed(
                "Could not load saved datasets. Refresh the catalogue to retry."
            )
        finally:
            self.loading = False
        yield _quality_refresh_event()

    @rx.event
    async def import_csv(
        self, files: list[rx.UploadFile], import_token: str
    ) -> AsyncIterator[object]:
        """Import once per token; explicit later imports create new datasets."""
        if self.busy or not import_token or import_token != self.import_token:
            return
        self.error = ""
        self.success = ""
        # Consume before yielding/awaiting, including browser-queued requests.
        # Protection is scoped to the lifetime of this Reflex client state.
        self.import_token = uuid4().hex
        if len(files) != 1:
            self.error = "Select exactly one CSV, then choose Import CSV."
            return
        self.importing = True
        self._clear_drafts()
        yield
        try:
            upload = files[0]
            data = await upload.read(MAX_BYTES + 1)
            with rx.session() as session:
                result = create_uploaded_dataset(session, str(upload.name or ""), data)
        except Exception as error:
            self._write_failed(error, "Import failed. No dataset was saved. Please retry.")
        else:
            self.selected_id = result.dataset_id
            self.page_index = 0
            self._after_write(
                f"Imported {result.row_count:,} records at data revision 1. "
                "Saved in the shared workspace; scientific checks have not run."
            )
            # Clear even after failed post-commit refresh. Recovery reloads the
            # committed dataset rather than encouraging another import.
            yield rx.clear_selected_files("csv_import")
        finally:
            self.importing = False
        yield _quality_refresh_event()

    @rx.event
    def select_dataset(self, dataset_id: int) -> object:
        """Open a dataset's first page and discard drafts without changing data."""
        if self.busy:
            return
        self.selected_id = dataset_id
        self.page_index = 0
        self.success = ""
        self.error = ""
        self._clear_drafts()
        try:
            self._reload()
        except Exception:
            logger.exception("Could not open a dataset.")
            self._read_failed("Could not open this dataset. Refresh the catalogue and retry.")
        return _quality_refresh_event()

    @rx.event
    def change_page(self, delta: int) -> object:
        """Load one bounded preview page and discard unsaved drafts."""
        if self.busy:
            return
        self.page_index = max(0, min(self.page_count - 1, self.page_index + delta))
        self._clear_drafts()
        self.error = ""
        try:
            self._reload()
        except Exception:
            logger.exception("Could not load a preview page.")
            self._read_failed("Could not load this page. Refresh the catalogue and retry.")
        return _quality_refresh_event()

    def _open_located_row(self, location: DatasetRowLocation) -> None:
        """Load a resolved page and recheck its revision before opening a draft.

        The quality state holds the Reflex lock. The backend has already found
        the ordinal by stable ID; a later write must still pass the usual
        dataset-wide revision claim. A changed/deleted record raises conflict.
        """
        expected = (location.dataset_id, location.identity_key, location.data_revision)
        if self.busy or (self.selected_id, self._selected_identity_key, self.data_revision) != expected:
            raise DatasetConflict("The selected data changed before the record could open.")
        self.page_index = location.record_index // PAGE_SIZE
        self._clear_drafts()
        try:
            self._reload(require_selected=True)
            if (self.selected_id, self._selected_identity_key, self.data_revision) != expected:
                raise DatasetConflict("The data revision changed while locating the record.")
            if not any(row["id"] == location.row_id for row in self.rows):
                raise DatasetConflict("The identified record no longer exists on the resolved page.")
        except Exception:
            self._read_failed("Could not load the located record's page. Refresh saved data before editing.")
            raise
        self.edit_row(location.row_id)

    @rx.event
    def reload_quality_target(self, dataset_id: int, identity_key: str) -> Iterator[object]:
        """Recover a location conflict only while its dataset is still selected."""
        if (self.selected_id, self._selected_identity_key) == (dataset_id, identity_key):
            yield from self.load_catalogue()

    @rx.event
    def edit_row(self, row_id: int) -> None:
        """Capture the preview revision when opening the selected record draft."""
        if self.busy:
            return
        self.error = ""
        self.success = ""
        row = next((r for r in self.rows if r["id"] == row_id), None)
        if row is None:
            self.error = "This record is no longer in the preview. Refresh and retry."
            return
        self._clear_drafts()
        self.editor_mode = "edit"
        self.editor_row_id = row_id
        self.editor_row_number = row["number"]
        self.editor_original = json.dumps(row["cells"], ensure_ascii=True)
        self._editor_expected_data_revision = self.data_revision
        self.editor_fields = [
            {"name": f"cell_{index}", "label": column, "value": row["cells"][index]}
            for index, column in enumerate(self.columns)
        ]

    @rx.event
    def add_row(self) -> None:
        """Open a blank record draft; the backend rechecks the record limit."""
        if self.busy or not self.selected_id:
            return
        self.error = ""
        self.success = ""
        self._clear_drafts()
        if self.row_count >= MAX_ROWS:
            self.error = "The 100,000-record limit has been reached. Delete a record first."
            return
        self.editor_mode = "add"
        self._editor_expected_data_revision = self.data_revision
        self.editor_fields = [
            {"name": f"cell_{index}", "label": column, "value": ""}
            for index, column in enumerate(self.columns)
        ]

    @rx.event
    def cancel_edit(self) -> None:
        """Discard an unsaved record draft without changing the data revision."""
        if not self.busy:
            self._clear_editor()
            self.error = ""

    @rx.event
    def save_row(self, form_data: dict[str, str]) -> object:
        """Commit a current record form; stale or duplicate tokens are ignored."""
        if self.busy or form_data.get("form_revision") != str(self.form_revision):
            return
        if self.editor_mode not in ("add", "edit"):
            return
        self.form_revision += 1
        self.error = ""
        self.success = ""
        self.saving = True
        adding = self.editor_mode == "add"
        try:
            cells = [
                form_data.get(f"cell_{index}", "") for index in range(len(self.columns))
            ]
            if not adding:
                original_cells: list[str] = json.loads(self.editor_original)
                # Textarea API values normalise CRLF/CR to LF. Preserve each
                # original string when its visible text is unchanged, including
                # untouched cells when another column is genuinely edited.
                cells = [
                    original
                    if submitted == original.replace("\r\n", "\n").replace("\r", "\n")
                    else submitted
                    for original, submitted in zip(original_cells, cells, strict=True)
                ]
            with rx.session() as session:
                result = save_dataset_row(
                    session, self.selected_id, self._editor_expected_data_revision,
                    cells, None if adding else self.editor_row_id,
                    expected_identity_key=self._selected_identity_key,
                )
        except Exception as error:
            self._write_failed(error, "Could not save the record. No changes were saved; please retry.")
        else:
            if adding:
                self.page_index = (result.row_count - 1) // PAGE_SIZE
            self._after_write(
                "Record saved in the shared workspace."
                if result.changed else "No record values changed; the data revision is unchanged."
            )
        finally:
            self.saving = False
        return _quality_refresh_event()

    @rx.event
    def open_rename(self) -> None:
        """Toggle the display-name form, capturing its name and data revision."""
        if self.busy or not self.selected_id:
            return
        opening = not self.rename_open
        self._clear_drafts()
        self.rename_open = opening
        self._rename_expected_name = self.selected_name
        self._rename_expected_data_revision = self.data_revision

    @rx.event
    def rename_dataset(self, form_data: dict[str, str]) -> object:
        """Commit a current display-name form without changing data revision."""
        if (
            self.busy or not self.rename_open
            or form_data.get("form_revision") != str(self.form_revision)
        ):
            return
        self.form_revision += 1
        self.error = ""
        self.success = ""
        self.saving = True
        try:
            with rx.session() as session:
                rename_uploaded_dataset(
                    session, self.selected_id, self._rename_expected_data_revision,
                    self._rename_expected_name, form_data.get("dataset_name", ""),
                    expected_identity_key=self._selected_identity_key,
                )
        except Exception as error:
            self._write_failed(error, "Could not rename the dataset. No changes were saved.")
        else:
            self._after_write(
                "Dataset name saved. The data revision and original filename are unchanged."
            )
        finally:
            self.saving = False
        return _quality_refresh_event()

    @rx.event
    def open_metadata(self) -> None:
        """Toggle declarations, capturing the dataset's displayed revision."""
        if self.busy or not self.selected_id:
            return
        opening = not self.metadata_open
        self._clear_drafts()
        self.metadata_open = opening
        self.metadata_expected_data_revision = self.data_revision

    @rx.event
    def update_dataset_metadata(self, form_data: dict[str, str]) -> object:
        """Save optional scientific declarations without running scientific checks."""
        if (
            self.busy or not self.metadata_open
            or form_data.get("metadata_form_revision") != str(self.metadata_form_revision)
        ):
            return
        self.metadata_form_revision += 1
        self.error = ""
        self.success = ""
        self.saving = True
        try:
            metadata = DatasetMetadata(
                form_data.get("variable_key"), form_data.get("observation_month"),
                form_data.get("unit"), form_data.get("grid_key"),
            )
            with rx.session() as session:
                result = save_dataset_metadata(
                    session, self.selected_id, self.metadata_expected_data_revision, metadata,
                    expected_identity_key=self._selected_identity_key,
                )
        except Exception as error:
            self._write_failed(error, "Could not save metadata. No changes were saved.")
        else:
            self._after_write(
                "Scientific declarations saved. Scientific checks have not run."
                if result.changed else "No metadata changed; the data revision is unchanged."
            )
        finally:
            self.saving = False
        return _quality_refresh_event()

    @rx.event
    def request_delete(self, target: str) -> None:
        """Capture the record/dataset revision and name for explicit confirmation."""
        if self.busy:
            return
        if target == "dataset" and self.selected_id:
            revision = self.data_revision
        elif target == "row" and self.editor_mode == "edit":
            revision = self._editor_expected_data_revision
        else:
            return
        self.confirmation = target
        self.confirmation_token = uuid4().hex
        self._confirmation_dataset_id = self.selected_id
        self._confirmation_data_revision = revision
        self._confirmation_row_id = self.editor_row_id
        self._confirmation_name = self.selected_name
        self._confirmation_identity_key = self._selected_identity_key

    @rx.event
    def cancel_delete(self) -> None:
        """Discard a deletion confirmation without changing persisted data."""
        if not self.busy:
            self._clear_confirmation()

    @rx.event
    def confirm_delete(self, expected_confirmation_token: str) -> object:
        """Delete the captured target once; ignore stale confirmation tokens."""
        if (
            self.busy or not expected_confirmation_token
            or expected_confirmation_token != self.confirmation_token
        ):
            return
        target = self.confirmation
        if target not in ("dataset", "row"):
            return
        self.confirmation_token = ""
        self.error = ""
        self.success = ""
        self.saving = True
        try:
            with rx.session() as session:
                if target == "dataset":
                    delete_uploaded_dataset(
                        session, self._confirmation_dataset_id,
                        self._confirmation_data_revision, self._confirmation_name,
                        expected_identity_key=self._confirmation_identity_key,
                    )
                else:
                    delete_dataset_row(
                        session, self._confirmation_dataset_id,
                        self._confirmation_data_revision, self._confirmation_row_id,
                        expected_identity_key=self._confirmation_identity_key,
                    )
        except Exception as error:
            self._write_failed(error, "Could not delete the record. No changes were saved.")
            self._clear_confirmation()
        else:
            if target == "dataset":
                self._clear_selection()
            self._after_write(
                "Dataset and all its records deleted."
                if target == "dataset" else "Record deleted from the shared workspace."
            )
        finally:
            self.saving = False
        return _quality_refresh_event()
