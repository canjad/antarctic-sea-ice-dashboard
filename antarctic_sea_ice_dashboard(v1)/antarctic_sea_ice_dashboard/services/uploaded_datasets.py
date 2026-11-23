"""Atomic CSV workspace writes with dataset-wide optimistic concurrency."""

from dataclasses import dataclass
import json
from uuid import uuid4

from sqlalchemy import and_, delete, func, insert, or_, select, update
from sqlalchemy.orm import Session

from antarctic_sea_ice_dashboard.models import (
    QualityCheckRun,
    QualityIssue,
    UploadedDataset,
    UploadedDatasetRow,
)
from antarctic_sea_ice_dashboard.services.csv_import import MAX_CELL, MAX_ROWS, parse_csv
from antarctic_sea_ice_dashboard.services.dataset_metadata import (
    DatasetMetadata,
    validate_dataset_metadata,
)


class DatasetConflict(ValueError):
    """The selected dataset was deleted or changed since the caller read it."""


@dataclass(frozen=True)
class DatasetWriteResult:
    """Committed identity, revision, record count and whether content changed."""

    dataset_id: int
    data_revision: int
    row_count: int
    changed: bool


@dataclass(frozen=True)
class DatasetRowLocation:
    """Current zero-based ordinal of an identified record at one data revision."""

    dataset_id: int
    identity_key: str
    data_revision: int
    row_id: int
    record_index: int


def get_dataset_row_location(
    session: Session, dataset_id: int, expected_data_revision: int, row_id: int,
    *, expected_identity_key: str,
) -> DatasetRowLocation:
    """Find a live record's ordinal using the preview's position/ID ordering.

    Owns a fresh transaction. The expected incarnation and revision must match
    before lookup, including for historical issues: SQLite can reuse deleted
    row IDs. DatasetConflict rejects missing records or changed data. No cells
    are returned and no content/revision changes are made.
    """
    if isinstance(row_id, bool) or not isinstance(row_id, int) or row_id <= 0:
        raise ValueError("Select an identified data record before locating it.")
    with session.begin():
        dataset = claim_uploaded_dataset(
            session, dataset_id, expected_data_revision, expected_identity_key,
        )
        row = _find_row(session, dataset_id, row_id)
        record_index = session.scalar(select(func.count()).select_from(UploadedDatasetRow).where(
            UploadedDatasetRow.dataset_id == dataset_id,
            or_(
                UploadedDatasetRow.row_position < row.row_position,
                and_(UploadedDatasetRow.row_position == row.row_position, UploadedDatasetRow.id < row.id),
            ),
        )) or 0
        return DatasetRowLocation(dataset_id, expected_identity_key, dataset.data_revision, row_id, record_index)


def claim_uploaded_dataset(
    session: Session,
    dataset_id: int,
    expected_data_revision: int | None,
    expected_identity_key: str,
    *,
    allow_any_revision: bool = False,
) -> UploadedDataset:
    """Lock the parent as the first SQL statement in an owned transaction.

    Writes require an expected revision. Quality report reads/finalisation must
    explicitly set allow_any_revision to pass None without rejecting later edits.
    No revision is incremented. Missing/stale targets raise DatasetConflict.
    """
    if not expected_identity_key:
        raise ValueError(
            "This dataset has no identity key. Ask the workspace operator to "
            "complete the dataset identity migration before editing."
        )
    if (
        isinstance(dataset_id, bool) or not isinstance(dataset_id, int) or dataset_id <= 0
        or (expected_data_revision is None and not allow_any_revision)
        or (expected_data_revision is not None and (
            isinstance(expected_data_revision, bool)
            or not isinstance(expected_data_revision, int)
            or expected_data_revision <= 0
        ))
    ):
        raise ValueError("Select a saved dataset and refresh before editing.")
    # This must be the transaction's first SQL statement. A matched UPDATE
    # locks the parent on PostgreSQL and obtains a write transaction on SQLite,
    # including no-op saves. FOR UPDATE alone has no effect on SQLite. The
    # immutable key rejects a different dataset reusing a deleted numeric ID.
    statement = update(UploadedDataset).where(
        UploadedDataset.id == dataset_id,
        UploadedDataset.identity_key == expected_identity_key,
    )
    if expected_data_revision is not None:
        statement = statement.where(UploadedDataset.data_revision == expected_data_revision)
    result = session.execute(
        statement
        .values(data_revision=UploadedDataset.data_revision)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        raise DatasetConflict(
            "This dataset changed or was deleted. Refresh the catalogue, "
            "review the latest revision and retry; no changes were saved."
        )
    dataset = session.get(UploadedDataset, dataset_id, populate_existing=True)
    if dataset is None:
        raise DatasetConflict("This dataset was deleted. Refresh the catalogue.")
    return dataset


def _bump_revision(session: Session, dataset_id: int) -> None:
    session.execute(
        update(UploadedDataset)
        .where(UploadedDataset.id == dataset_id)
        .values(data_revision=UploadedDataset.data_revision + 1)
        .execution_options(synchronize_session=False)
    )


def _find_row(session: Session, dataset_id: int, row_id: int) -> UploadedDatasetRow:
    row = session.scalar(
        select(UploadedDatasetRow).where(
            UploadedDatasetRow.id == row_id,
            UploadedDatasetRow.dataset_id == dataset_id,
        )
    )
    if row is None:
        raise DatasetConflict("This record was deleted. Refresh before editing.")
    return row


def create_uploaded_dataset(
    session: Session,
    filename: str,
    data: bytes,
    metadata: DatasetMetadata = DatasetMetadata(),
) -> DatasetWriteResult:
    """Validate CSV bytes and declarations, then commit all records at revision 1.

    The session must have no active transaction. Returns only after commit;
    ValueError rejects input and database errors propagate after rollback.
    Reimporting the same file deliberately creates another dataset.
    """
    filename = filename.replace("\\", "/").rsplit("/", 1)[-1]
    headers, records = parse_csv(filename, data)
    metadata = validate_dataset_metadata(metadata)
    with session.begin():
        dataset = UploadedDataset(
            name=filename.rsplit(".", 1)[0],
            original_filename=filename,
            column_names_json=json.dumps(headers, ensure_ascii=True),
            row_count=len(records),
            data_revision=1,
            identity_key=uuid4().hex,
            variable_key=metadata.variable_key,
            observation_month=metadata.observation_month,
            unit=metadata.unit,
            grid_key=metadata.grid_key,
            source="uploaded_csv",
            status="ready",
        )
        session.add(dataset)
        session.flush()
        dataset_id = dataset.id
        for batch_start in range(0, len(records), 1000):
            session.execute(
                insert(UploadedDatasetRow.__table__),
                [
                    {
                        "dataset_id": dataset_id,
                        "row_position": batch_start + offset,
                        "cell_values_json": json.dumps(cells, ensure_ascii=True),
                    }
                    for offset, cells in enumerate(records[batch_start : batch_start + 1000])
                ],
            )
    return DatasetWriteResult(dataset_id, 1, len(records), True)


def save_dataset_row(
    session: Session,
    dataset_id: int,
    expected_data_revision: int,
    cells: list[str],
    row_id: int | None = None,
    *,
    expected_identity_key: str,
) -> DatasetWriteResult:
    """Append a record or replace the identified record's strings atomically.

    Uses a fresh session transaction and rejects stale dataset revisions,
    including edits to different records. Identical values leave the revision
    unchanged. ValueError rejects input/conflicts; database failures roll back.
    """
    with session.begin():
        dataset = claim_uploaded_dataset(
            session, dataset_id, expected_data_revision, expected_identity_key
        )
        if len(cells) != len(json.loads(dataset.column_names_json)):
            raise ValueError("The column count changed. Refresh before editing.")
        if any(not isinstance(cell, str) or len(cell) > MAX_CELL for cell in cells):
            raise ValueError("Use string cells of at most 32,768 characters.")
        changed = True
        if row_id is None:
            if dataset.row_count >= MAX_ROWS:
                raise ValueError("The 100,000-record limit has been reached.")
            last_position = session.scalar(
                select(func.max(UploadedDatasetRow.row_position)).where(
                    UploadedDatasetRow.dataset_id == dataset_id
                )
            )
            session.add(
                UploadedDatasetRow(
                    dataset_id=dataset_id,
                    row_position=0 if last_position is None else last_position + 1,
                    cell_values_json=json.dumps(cells, ensure_ascii=True),
                )
            )
            dataset.row_count += 1
        else:
            row = _find_row(session, dataset_id, row_id)
            changed = json.loads(row.cell_values_json) != cells
            if changed:
                row.cell_values_json = json.dumps(cells, ensure_ascii=True)
        if changed:
            _bump_revision(session, dataset_id)
        row_count = dataset.row_count
    return DatasetWriteResult(
        dataset_id, expected_data_revision + int(changed), row_count, changed
    )


def update_dataset_metadata(
    session: Session,
    dataset_id: int,
    expected_data_revision: int,
    metadata: DatasetMetadata,
    *,
    expected_identity_key: str,
) -> DatasetWriteResult:
    """Commit validated declarations and one revision only when they differ.

    Requires a fresh session; returns committed revision/count. Unknown values
    remain None. ValueError reports format errors/conflicts, never scientific
    validity; database failures roll back metadata and revision together.
    """
    metadata = validate_dataset_metadata(metadata)
    with session.begin():
        dataset = claim_uploaded_dataset(
            session, dataset_id, expected_data_revision, expected_identity_key
        )
        current = DatasetMetadata(
            dataset.variable_key, dataset.observation_month, dataset.unit, dataset.grid_key
        )
        changed = metadata != current
        if changed:
            dataset.variable_key = metadata.variable_key
            dataset.observation_month = metadata.observation_month
            dataset.unit = metadata.unit
            dataset.grid_key = metadata.grid_key
            _bump_revision(session, dataset_id)
        row_count = dataset.row_count
    return DatasetWriteResult(
        dataset_id, expected_data_revision + int(changed), row_count, changed
    )


def rename_uploaded_dataset(
    session: Session,
    dataset_id: int,
    expected_data_revision: int,
    expected_name: str,
    name: str,
    *,
    expected_identity_key: str,
) -> None:
    """Commit a display name without changing the data revision.

    Requires a fresh session and the name/revision seen when opening the form.
    ValueError rejects invalid names or stale drafts; database errors roll back.
    """
    name = name.strip()
    if not name or len(name) > 200 or "\x00" in name:
        raise ValueError("Use a nonempty name of at most 200 characters without null characters.")
    with session.begin():
        dataset = claim_uploaded_dataset(
            session, dataset_id, expected_data_revision, expected_identity_key
        )
        if dataset.name != expected_name:
            raise DatasetConflict("Someone renamed this dataset. Refresh before renaming.")
        dataset.name = name


def delete_dataset_row(
    session: Session,
    dataset_id: int,
    expected_data_revision: int,
    row_id: int,
    *,
    expected_identity_key: str,
) -> DatasetWriteResult:
    """Delete one record, decrement the count and advance the revision together.

    Requires a fresh session. ValueError rejects missing/stale records or
    datasets; database errors roll back all changes. Stable positions may gap.
    """
    with session.begin():
        dataset = claim_uploaded_dataset(
            session, dataset_id, expected_data_revision, expected_identity_key
        )
        session.delete(_find_row(session, dataset_id, row_id))
        dataset.row_count -= 1
        _bump_revision(session, dataset_id)
        row_count = dataset.row_count
    return DatasetWriteResult(dataset_id, expected_data_revision + 1, row_count, True)


def delete_uploaded_dataset(
    session: Session,
    dataset_id: int,
    expected_data_revision: int,
    expected_name: str,
    *,
    expected_identity_key: str,
) -> None:
    """Delete the confirmed dataset and its records in one fresh transaction.

    The confirmed name and revision must still match. ValueError rejects stale
    confirmations; database errors roll back. Explicit child deletion also
    preserves behaviour when SQLite foreign-key enforcement is disabled.
    """
    with session.begin():
        dataset = claim_uploaded_dataset(
            session, dataset_id, expected_data_revision, expected_identity_key
        )
        if dataset.name != expected_name:
            raise DatasetConflict("This dataset was renamed. Refresh and confirm deletion again.")
        # Explicit deletion also handles SQLite installations without FK enforcement.
        run_ids = select(QualityCheckRun.id).where(QualityCheckRun.dataset_id == dataset_id)
        session.execute(delete(QualityIssue).where(QualityIssue.check_run_id.in_(run_ids)))
        session.execute(delete(QualityCheckRun).where(QualityCheckRun.dataset_id == dataset_id))
        session.execute(
            delete(UploadedDatasetRow).where(UploadedDatasetRow.dataset_id == dataset_id)
        )
        session.delete(dataset)
