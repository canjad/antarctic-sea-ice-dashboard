"""Immutable CSV publications, blocked source registrations and verified reads."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import csv
import io
import json
import logging
from typing import TypeVar, cast
from uuid import UUID, uuid4, uuid5

import reflex as rx
from sqlalchemy import select
from sqlalchemy.orm import Session

from antarctic_sea_ice_dashboard.models import ResearchProduct, UploadedDataset, UploadedDatasetRow
from antarctic_sea_ice_dashboard.services.csv_import import MAX_ROWS
from antarctic_sea_ice_dashboard.services.csv_validation import JSONValue
from antarctic_sea_ice_dashboard.services.data_quality import (
    MAX_SNAPSHOT_BYTES, QualityCheckResult, QualityServiceError, require_publication_check,
)
from antarctic_sea_ice_dashboard.services.dataset_metadata import GRID_DEFINITIONS
from antarctic_sea_ice_dashboard.services.product_contracts import (
    MANIFEST_SCHEMA_VERSION, OUTPUT_CONTRACT, SOURCE_OUTPUT_CONTRACT, ProductFile, ProductManifest,
    ProductMonthLocation, ProductOperation, ProductSummary, ProductVersion,
)
from antarctic_sea_ice_dashboard.services.product_storage import (
    ProductError, ProductStorage, canonical_json, digest_bytes, require_product_id,
    source_file, stream_digest,
)
from antarctic_sea_ice_dashboard.services.source_products import (
    inspect_source_product, source_root,
)
from antarctic_sea_ice_dashboard.services.uploaded_datasets import DatasetConflict, claim_uploaded_dataset
from antarctic_sea_ice_dashboard.services.validation_reference import load_reference_grid


logger = logging.getLogger(__name__)
PRODUCT_NAMESPACE = UUID("83253e23-57a4-4fe3-9588-917de34592c2")
MAX_CATALOGUE_PAGE = 50
Result = TypeVar("Result")


@dataclass(frozen=True)
class _CsvSnapshot:
    request: dict[str, JSONValue]
    headers: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]
    checks: dict[str, JSONValue]
    grid: dict[str, JSONValue]


def _utc(value: datetime) -> str:
    return (value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)).isoformat()


def _operation(record: ResearchProduct) -> ProductOperation:
    manifest = json.loads(record.manifest_json) if record.manifest_json else {}
    gaps = tuple(manifest.get("checks", {}).get("gaps", []))
    return ProductOperation(record.product_id, record.publication_status, record.source_kind, record.error_code, _utc(record.created_at), _utc(record.updated_at), gaps, record.manifest_digest, record.content_digest)


def _guard(action: Callable[[], Result], product_id: str | None = None) -> Result:
    """Sanitise all filesystem/driver failures at the public service seam."""
    try:
        return action()
    except ProductError:
        raise
    except (QualityServiceError, DatasetConflict) as error:
        raise ProductError(getattr(error, "code", "REVISION_CONFLICT"), str(error), product_id) from None
    except Exception as error:
        logger.error("Product operation failed for %s (%s).", product_id, type(error).__name__)
        raise ProductError("PRODUCT_UNAVAILABLE", "The product operation could not finish. Read its saved status before retrying; a committed product remains published.", product_id) from None


def _read_record(product_id: str) -> ResearchProduct:
    require_product_id(product_id)
    with rx.session() as session:
        record = session.get(ResearchProduct, product_id)
        if record is None:
            raise ProductError("PRODUCT_NOT_FOUND", "The requested product operation was not registered.", product_id)
        session.expunge(record)
        return record


def _claim_dataset(session: Session, dataset_id: int, revision: int, identity: str) -> UploadedDataset:
    if session.get_bind().dialect.name not in {"sqlite", "postgresql"}:
        raise ProductError("DATABASE_UNSUPPORTED", "Publication supports the documented SQLite/PostgreSQL parent-claim strategy.")
    return claim_uploaded_dataset(session, dataset_id, revision, identity)


def _check_identity(check: QualityCheckResult) -> dict[str, JSONValue]:
    return {
        "ruleset_version": check.ruleset_version, "profile_key": check.profile_key,
        "profile_version": check.profile_version, "reference_identity": check.reference_identity,
    }


def _capture_csv(dataset_id: int, revision: int, identity: str) -> _CsvSnapshot:
    with rx.session() as session, session.begin():
        dataset = _claim_dataset(session, dataset_id, revision, identity)
        reference = load_reference_grid(dataset.grid_key)
        check = require_publication_check(session, dataset, reference.identity)
        if dataset.row_count > MAX_ROWS:
            raise ProductError("SNAPSHOT_LIMIT", "The publication snapshot exceeds the supported record limit.")
        headers = json.loads(dataset.column_names_json)
        if not isinstance(headers, list) or not headers or any(not isinstance(cell, str) for cell in headers):
            raise ProductError("SNAPSHOT_INVALID", "Saved CSV headers are invalid.")
        size = len(dataset.column_names_json.encode("utf-8"))
        rows: list[tuple[str, ...]] = []
        records = session.scalars(select(UploadedDatasetRow.cell_values_json).where(
            UploadedDatasetRow.dataset_id == dataset_id,
        ).order_by(UploadedDatasetRow.row_position, UploadedDatasetRow.id).execution_options(yield_per=1000))
        for raw in records:
            size += len(raw.encode("utf-8"))
            if size > MAX_SNAPSHOT_BYTES or len(rows) >= MAX_ROWS:
                raise ProductError("SNAPSHOT_LIMIT", "The publication snapshot exceeds the 64 MiB or record limit.")
            cells = json.loads(raw)
            if not isinstance(cells, list) or len(cells) != len(headers) or any(not isinstance(cell, str) for cell in cells):
                raise ProductError("SNAPSHOT_INVALID", "Saved CSV cells differ from the ordered-string contract.")
            rows.append(tuple(cells))
        if len(rows) != dataset.row_count:
            raise ProductError("SNAPSHOT_INCOMPLETE", "The locked publication snapshot does not match the checked record count.")
        grid = GRID_DEFINITIONS[cast(str, dataset.grid_key)]
        return _CsvSnapshot({
            "source_kind": "uploaded_csv", "dataset_id": dataset_id, "dataset_identity_key": identity,
            "source_data_revision": revision, "name_at_capture": dataset.name,
            "original_filename": dataset.original_filename, "variable_key": dataset.variable_key,
            "unit": dataset.unit, "month": dataset.observation_month, "grid_key": dataset.grid_key,
            "output_contract": OUTPUT_CONTRACT, "manifest_schema_version": MANIFEST_SCHEMA_VERSION,
            "check_identity": _check_identity(check),
        }, tuple(headers), tuple(rows), cast(dict[str, JSONValue], asdict(check)), {
            "grid_key": dataset.grid_key, "definition": cast(dict[str, JSONValue], asdict(grid)),
            "reference_identity": reference.identity,
            "mask_identity": grid.coordinate_sha256,
        })


def _identity(request: dict[str, JSONValue]) -> tuple[str, str]:
    # Display labels and a particular recheck attempt do not change identity.
    identity = {key: value for key, value in request.items() if key not in {"name_at_capture", "original_filename", "dataset_id"}}
    key = digest_bytes(canonical_json(identity))
    return uuid5(PRODUCT_NAMESPACE, key).hex, key


def _begin(product_id: str, key: str, request: dict[str, JSONValue]) -> ResearchProduct:
    with rx.session() as session, session.begin():
        record = session.get(ResearchProduct, product_id)
        if record is None:
            now = datetime.now(UTC)
            record = ResearchProduct(
                product_id=product_id, idempotency_key=key, source_kind=str(request["source_kind"]),
                source_identity=str(request.get("dataset_identity_key") or request["source_id"]),
                source_data_revision=cast(int | None, request.get("source_data_revision")),
                publication_status="preparing", attempt_id=uuid4().hex,
                request_json=canonical_json(request).decode("ascii"), created_at=now, updated_at=now,
            )
            session.add(record)
            session.flush()
        elif record.idempotency_key != key:
            raise ProductError("IDENTITY_CONFLICT", "The saved publication identity conflicts with this request.", product_id)
        session.expunge(record)
        return record


def _reset_attempt(storage: ProductStorage, record: ResearchProduct, request: dict[str, JSONValue]) -> ResearchProduct:
    if storage.final_exists(record.product_id):
        raise ProductError("RECOVERY_REQUIRED", "A final directory exists. Recover the saved operation before retrying.", record.product_id)
    storage.discard_stage(record.product_id, record.attempt_id)
    with rx.session() as session, session.begin():
        current = session.get(ResearchProduct, record.product_id)
        current.attempt_id = uuid4().hex
        current.publication_status = "preparing"
        current.manifest_json = None
        current.manifest_digest = None
        current.content_digest = None
        current.error_code = None
        current.request_json = canonical_json(request).decode("ascii")
        current.updated_at = datetime.now(UTC)
        session.flush()
        session.expunge(current)
        return current


def _csv_readback(storage: ProductStorage, area_fd: int, name: str, expected_headers: tuple[str, ...] | None = None, expected_rows: tuple[tuple[str, ...], ...] | None = None) -> int:
    with storage.read_file(area_fd, name) as raw:
        with io.TextIOWrapper(raw, encoding="utf-8-sig", newline="") as text:
            reader = csv.reader(text, strict=True)
            headers = next(reader)
            if (not headers or len(set(headers)) != len(headers)
                or (expected_headers is not None and tuple(headers) != expected_headers)):
                raise ProductError("FORMAT_INVALID", "CSV readback headers do not match the output contract.")
            count = 0
            for row in reader:
                if len(row) != len(headers) or (expected_rows is not None and (count >= len(expected_rows) or tuple(row) != expected_rows[count])):
                    raise ProductError("FORMAT_INVALID", "CSV readback values or record width differ from the output contract.")
                count += 1
            if not count or (expected_rows is not None and count != len(expected_rows)):
                raise ProductError("FORMAT_INVALID", "CSV readback record coverage is incomplete.")
            return count


def _file_entry(storage: ProductStorage, area_fd: int, name: str, month: str | None, role: str, record_count: int | None) -> ProductFile:
    with storage.read_file(area_fd, name) as stream:
        size, digest = stream_digest(stream)
    return {"relative_path": name, "file_format": "csv" if name.endswith(".csv") else "json" if name.endswith(".json") else "markdown", "size_bytes": size, "sha256": digest, "record_count": record_count, "month": month, "role": role}


def _manifest(record: ResearchProduct, request: dict[str, JSONValue], files: list[ProductFile], grid: dict[str, JSONValue], checks: dict[str, JSONValue], method: str) -> ProductManifest:
    months = sorted({cast(str, item["month"]) for item in files if item["role"] == "data"})
    return {
        "product_id": record.product_id, "manifest_schema_version": MANIFEST_SCHEMA_VERSION,
        "output_contract": str(request["output_contract"]), "source_kind": record.source_kind,
        "source": request, "variable_key": str(request["variable_key"]), "unit": str(request["unit"]),
        "months": months, "grid": grid, "method": method,
        "files": files, "checks": checks, "created_at": _utc(record.created_at),
    }


def _save_prepared(storage: ProductStorage, record: ResearchProduct, manifest: ProductManifest) -> ResearchProduct:
    data = canonical_json(manifest)
    with storage.area(record.product_id, record.attempt_id) as area_fd:
        with storage.write_file(area_fd, "manifest.json") as stream:
            stream.write(data)
    storage.verify(record.product_id, manifest, record.attempt_id)
    # This commit precedes rename. Its manifest is the authority for recovery.
    with rx.session() as session, session.begin():
        current = session.get(ResearchProduct, record.product_id)
        current.manifest_json = data.decode("ascii")
        current.manifest_digest = digest_bytes(data)
        current.content_digest = digest_bytes(canonical_json({
            "request_identity": current.idempotency_key, "files": manifest["files"],
            "grid": manifest["grid"], "method": manifest["method"],
        }))
        current.publication_status = "publishing"
        current.updated_at = datetime.now(UTC)
        session.flush()
        session.expunge(current)
        return current


def _commit_visibility(record: ResearchProduct) -> None:
    request = json.loads(record.request_json)
    with rx.session() as session, session.begin():
        if record.source_kind == "uploaded_csv":
            # First SQL is the shared parent UPDATE: edits and delete cannot
            # interleave with this gate and the visibility commit.
            dataset = _claim_dataset(session, request["dataset_id"], request["source_data_revision"], request["dataset_identity_key"])
            reference = load_reference_grid(dataset.grid_key)
            check = require_publication_check(session, dataset, reference.identity)
            if _check_identity(check) != request["check_identity"]:
                raise ProductError("PUBLICATION_STALE", "Rules or reference identity changed after capture. Run a current check before retrying.", record.product_id)
        current = session.get(ResearchProduct, record.product_id)
        current.publication_status = "published" if record.source_kind == "uploaded_csv" else "blocked"
        current.error_code = None if record.source_kind == "uploaded_csv" else "SCIENTIFIC_EVIDENCE_INCOMPLETE"
        current.updated_at = datetime.now(UTC)


def _resume(storage: ProductStorage, record: ResearchProduct) -> None:
    if record.manifest_json is None:
        raise ProductError("PREPARATION_INTERRUPTED", "Preparation was interrupted. Explicitly repeat the original scoped request to capture it again.", record.product_id)
    manifest = cast(ProductManifest, json.loads(record.manifest_json))
    if digest_bytes(canonical_json(manifest)) != record.manifest_digest:
        raise ProductError("INTEGRITY_UNAVAILABLE", "The saved recovery manifest failed its digest check.", record.product_id)
    if storage.final_exists(record.product_id):
        storage.verify(record.product_id, manifest)
    else:
        storage.verify(record.product_id, manifest, record.attempt_id)
        storage.promote(record.product_id, record.attempt_id)
        storage.verify(record.product_id, manifest)
    storage.seal(record.product_id)
    _commit_visibility(record)


def _record_failure(product_id: str, error: Exception) -> None:
    try:
        with rx.session() as session, session.begin():
            record = session.get(ResearchProduct, product_id)
            if record is not None and record.publication_status not in {"published", "blocked"}:
                record.publication_status = "failed"
                record.error_code = error.code if isinstance(error, (ProductError, QualityServiceError)) else "PUBLICATION_INTERRUPTED"
                record.updated_at = datetime.now(UTC)
    except Exception as save_error:
        logger.error("Product failure status could not be saved for %s (%s).", product_id, type(save_error).__name__)


def _execute(request: dict[str, JSONValue], prepare: Callable[[ProductStorage, ResearchProduct], ProductManifest]) -> ProductOperation:
    product_id, key = _identity(request)
    return _guard(lambda: _execute_locked(product_id, key, request, prepare), product_id)


def _execute_locked(product_id: str, key: str, request: dict[str, JSONValue], prepare: Callable[[ProductStorage, ResearchProduct], ProductManifest]) -> ProductOperation:
    with ProductStorage() as storage, storage.lock(product_id):
        record = _begin(product_id, key, request)
        try:
            if record.publication_status in {"published", "blocked"}:
                storage.verify(product_id, cast(ProductManifest, json.loads(record.manifest_json)))
                return _operation(record)
            if record.manifest_json is not None and (record.publication_status != "failed" or storage.final_exists(product_id)):
                _resume(storage, record)
            else:
                record = _reset_attempt(storage, record, request)
                storage.create_stage(product_id, record.attempt_id)
                manifest = prepare(storage, record)
                record = _save_prepared(storage, record, manifest)
                _resume(storage, record)
        except Exception as error:
            logger.error("Publication interrupted for %s (%s).", product_id, type(error).__name__)
            _record_failure(product_id, error)
            if isinstance(error, ProductError):
                error.product_id = product_id
                raise
            raise ProductError(getattr(error, "code", "PUBLICATION_INTERRUPTED"), "Publication did not finish. Read or recover this product operation before retrying.", product_id) from None
        return _operation(_read_record(product_id))


def publish_csv_revision(dataset_id: int, expected_data_revision: int, *, expected_identity_key: str) -> ProductOperation:
    """Publish a complete current checked revision as UTF-8 ordered-string CSV.

    Captures cells/metadata/evidence under the workspace parent claim. Rechecks
    the gate in the visibility transaction after verified rename. Returns a
    stable product ID; ProductError carries it after operation acceptance.
    No scientific array conversion, cleanup, fill or row reordering occurs.
    """
    def action() -> ProductOperation:
        snapshot = _capture_csv(dataset_id, expected_data_revision, expected_identity_key)

        def prepare(storage: ProductStorage, record: ResearchProduct) -> ProductManifest:
            name = f"data-{snapshot.request['month']}.csv"
            with storage.area(record.product_id, record.attempt_id) as area_fd:
                with storage.write_file(area_fd, name) as raw:
                    text = io.TextIOWrapper(raw, encoding="utf-8", newline="", write_through=True)
                    try:
                        writer = csv.writer(text, lineterminator="\r\n")
                        writer.writerow(snapshot.headers)
                        writer.writerows(snapshot.rows)
                        text.flush()
                    finally:
                        text.detach()
                count = _csv_readback(storage, area_fd, name, snapshot.headers, snapshot.rows)
                entry = _file_entry(storage, area_fd, name, str(snapshot.request["month"]), "data", count)
            return _manifest(record, snapshot.request, [entry], snapshot.grid, snapshot.checks, "Working CSV snapshot; no scientific transformation")

        return _execute(snapshot.request, prepare)
    return cast(ProductOperation, _guard(action))


def register_source_product(source_id: str, months: tuple[str, ...]) -> ProductOperation:
    """Copy only the explicit historical CSV scope into a blocked registration.

    Source identity includes streaming hashes and evidence gaps. This adapter
    cannot promote historical reports to scientific acceptance. It copies bytes
    independently, verifies hashes and CSV width/readback, and preserves evidence.
    ProductError distinguishes invalid scope, missing resources and copy failure.
    """
    def action() -> ProductOperation:
        inventory = inspect_source_product(source_id, months)
        request = cast(dict[str, JSONValue], asdict(inventory))
        request.update({"source_kind": "checking_data", "output_contract": SOURCE_OUTPUT_CONTRACT, "manifest_schema_version": MANIFEST_SCHEMA_VERSION})

        def prepare(storage: ProductStorage, record: ResearchProduct) -> ProductManifest:
            files: list[ProductFile] = []
            with storage.area(record.product_id, record.attempt_id) as area_fd:
                for item in inventory.files:
                    with source_file(source_root(), item.resource) as stream:
                        storage.copy_file(area_fd, item.relative_path, stream, (item.size_bytes, item.sha256))
                    count = _csv_readback(storage, area_fd, item.relative_path, ("Latitude", "Longitude", "v_wind")) if item.role == "data" else None
                    entry = _file_entry(storage, area_fd, item.relative_path, item.month, item.role, count)
                    if (entry["size_bytes"], entry["sha256"]) != (item.size_bytes, item.sha256):
                        raise ProductError("INTEGRITY_UNAVAILABLE", "Copied file readback differs from the inventoried source bytes.", record.product_id)
                    files.append(entry)
            return _manifest(record, request, files, {
                "grid_key": inventory.grid_key, "mask_identity": inventory.historical_claims.get("regrid_metadata.json:coordinate_sha256"),
                "identity_status": "historical_claim_unaccepted",
            }, {"outcome": "blocked", "gaps": list(inventory.gaps), "historical_claims": inventory.historical_claims, "scientific_checks_executed": False}, inventory.method)

        return _execute(request, prepare)
    return cast(ProductOperation, _guard(action))


def get_product_operation(product_id: str) -> ProductOperation:
    """Read the saved lifecycle for a hexadecimal product ID, without recovery.

    ProductError reports INVALID_INPUT, PRODUCT_NOT_FOUND or a sanitised
    PRODUCT_UNAVAILABLE. A status read never establishes current file integrity.
    """
    return cast(ProductOperation, _guard(lambda: _operation(_read_record(product_id)), product_id))


def find_csv_product_operations(dataset_identity_key: str, source_data_revision: int, *, offset: int = 0) -> tuple[ProductOperation, ...]:
    """Find accepted operations after response loss, even after source deletion.

    Exact immutable source identity/revision is mandatory. Returns at most ten
    saved operations per page, including nonvisible work; never publishes it.
    ProductError reports INVALID_INPUT or PRODUCT_UNAVAILABLE; an empty page
    means no matching accepted journal entry, not a passing publication.
    """
    def action() -> tuple[ProductOperation, ...]:
        if (not isinstance(dataset_identity_key, str) or not 1 <= len(dataset_identity_key) <= 128
            or isinstance(source_data_revision, bool) or not isinstance(source_data_revision, int) or not 0 < source_data_revision <= 2**63 - 1
            or isinstance(offset, bool) or not isinstance(offset, int) or not 0 <= offset <= 2**63 - 1):
            raise ProductError("INVALID_INPUT", "Supply the exact source incarnation, positive revision and nonnegative operation offset.")
        with rx.session() as session:
            records = session.scalars(select(ResearchProduct).where(
                ResearchProduct.source_kind == "uploaded_csv",
                ResearchProduct.source_identity == dataset_identity_key,
                ResearchProduct.source_data_revision == source_data_revision,
            ).order_by(ResearchProduct.created_at.desc(), ResearchProduct.product_id).offset(offset).limit(10)).all()
            return tuple(_operation(record) for record in records)
    return _guard(action)


def recover_product(product_id: str) -> ProductOperation:
    """Under a nonblocking lock, verify and finish an already prepared operation.

    A published product is only verified. Interrupted preparation requires the
    original request; recovery never recaptures edited working data, deletes a
    final directory or trusts an unjournalled final manifest.
    ProductError reports INVALID_INPUT/PRODUCT_NOT_FOUND, OPERATION_BUSY,
    PREPARATION_INTERRUPTED (repeat the original scoped request), failed CSV
    revision/quality gates, INTEGRITY_UNAVAILABLE or PRODUCT_UNAVAILABLE.
    None of these failures authorises overwriting a final directory.
    """
    def action() -> ProductOperation:
        with ProductStorage() as storage, storage.lock(product_id):
            record = _read_record(product_id)
            try:
                if record.publication_status in {"published", "blocked"}:
                    storage.verify(product_id, cast(ProductManifest, json.loads(record.manifest_json)))
                else:
                    _resume(storage, record)
            except Exception as error:
                _record_failure(product_id, error)
                raise
        return _operation(_read_record(product_id))
    return cast(ProductOperation, _guard(action, product_id))


def _published_version(product_id: str) -> ProductVersion:
    record = _read_record(product_id)
    if record.publication_status != "published":
        raise ProductError("PRODUCT_NOT_PUBLISHED", "This product is not available for scientific consumption.", product_id)
    manifest = cast(ProductManifest, json.loads(record.manifest_json))
    if digest_bytes(canonical_json(manifest)) != record.manifest_digest:
        raise ProductError("INTEGRITY_UNAVAILABLE", "The saved manifest digest no longer matches.", product_id)
    with ProductStorage() as storage:
        storage.verify(product_id, manifest)
    if not record.manifest_digest or not record.content_digest:
        raise ProductError("INTEGRITY_UNAVAILABLE", "Published content identity is missing.", product_id)
    return ProductVersion(manifest, record.manifest_digest, record.content_digest)


def _published_manifest(product_id: str) -> ProductManifest:
    return _published_version(product_id).manifest


def read_product_version(product_id: str) -> ProductVersion:
    """Read a published ID with content/manifest digests and fresh integrity.

    This extends the Phase 4 read interface without changing publication or
    manifest schemas. ProductError has the same failures as read_product_manifest.
    No ORM record or filesystem path crosses this interface.
    """
    return cast(ProductVersion, _guard(lambda: _published_version(product_id), product_id))


def read_product_manifest(product_id: str) -> ProductManifest:
    """Return a verified published manifest, with controlled relative paths only.

    Every read hashes every listed file. Missing or modified files make the
    exact product unavailable; no fallback version or external path is used.
    ProductError reports INVALID_INPUT, PRODUCT_NOT_FOUND, PRODUCT_NOT_PUBLISHED,
    INTEGRITY_UNAVAILABLE or sanitised PRODUCT_UNAVAILABLE for unreadable storage.
    """
    return cast(ProductManifest, _guard(lambda: _published_manifest(product_id), product_id))


def list_available_products(*, offset: int = 0, limit: int = 20) -> tuple[ProductSummary, ...]:
    """List only published journal entries, with fresh integrity per product.

    Unavailable published entries retain their identity and an unavailable
    integrity status. Consumers must use read_product_manifest before access.
    Pagination covers journal entries, never exposes preparing/blocked records.
    ProductError reports INVALID_INPUT for invalid paging or PRODUCT_UNAVAILABLE
    for catalogue/database failure. Per-product integrity failures become an
    unavailable summary; they do not erase the committed product identity.
    """
    def action() -> tuple[ProductSummary, ...]:
        if (isinstance(offset, bool) or not isinstance(offset, int) or not 0 <= offset <= 2**63 - 1
            or isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_CATALOGUE_PAGE):
            raise ProductError("INVALID_INPUT", "Use a nonnegative offset and a catalogue page size from 1 to 50.")
        with rx.session() as session:
            records = session.scalars(select(ResearchProduct).where(ResearchProduct.publication_status == "published").order_by(
                ResearchProduct.created_at.desc(), ResearchProduct.product_id,
            ).offset(offset).limit(limit)).all()
            identities = [record.product_id for record in records]
        result: list[ProductSummary] = []
        for product_id in identities:
            record = _read_record(product_id)
            manifest = cast(ProductManifest, json.loads(record.manifest_json))
            try:
                _published_manifest(product_id)
                integrity = "verified"
            except (ProductError, OSError):
                integrity = "unavailable"
            result.append(ProductSummary(product_id, manifest["variable_key"], manifest["unit"], tuple(manifest["months"]), str(manifest["grid"].get("grid_key")), record.source_kind, integrity))
        return tuple(result)
    return cast(tuple[ProductSummary, ...], _guard(action))


def get_product_month_location(product_id: str, month: str) -> ProductMonthLocation:
    """Resolve an actual month to an opaque backend resource, never a host path.

    The resource ID pins product and relative filename. Read it through
    read_product_month_bytes; it is not a general filesystem or download URL.
    ProductError includes published-manifest read failures and MONTH_NOT_AVAILABLE
    when the exact product has no data file for the supplied YYYY-MM month.
    """
    manifest = read_product_manifest(product_id)
    for item in manifest["files"]:
        if item["role"] == "data" and item["month"] == month:
            return ProductMonthLocation(product_id, month, f"{product_id}/{item['relative_path']}", item["file_format"], item["sha256"], cast(int, item["record_count"]))
    raise ProductError("MONTH_NOT_AVAILABLE", "This product contains no data for the requested month.", product_id)


def read_product_month_bytes(product_id: str, month: str) -> bytes:
    """Read verified exact CSV bytes for a backend scientific-page consumer.

    The returned bytes are checked again after read, closing the verify/open
    interval. This is a bounded CSV interface, not a public static mount.
    ProductError includes exact-month/manifest failures, INTEGRITY_UNAVAILABLE
    for changed bytes and PRODUCT_UNAVAILABLE for unreadable storage.
    """
    def action() -> bytes:
        location = get_product_month_location(product_id, month)
        with ProductStorage() as storage, storage.area(product_id) as area_fd:
            with storage.read_file(area_fd, location.resource_id.split("/", 1)[1]) as stream:
                size, digest = stream_digest(stream)
                stream.seek(0)
                data = stream.read(size + 1)
        if len(data) != size or digest_bytes(data) != digest or digest != location.sha256:
            raise ProductError("INTEGRITY_UNAVAILABLE", "Product bytes changed during consumption.", product_id)
        return data
    return cast(bytes, _guard(action, product_id))
