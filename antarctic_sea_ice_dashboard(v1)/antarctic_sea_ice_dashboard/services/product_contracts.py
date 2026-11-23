"""Versioned, path-free product boundary structures."""

from dataclasses import dataclass
from typing import TypedDict

from antarctic_sea_ice_dashboard.services.csv_validation import JSONValue


MANIFEST_SCHEMA_VERSION = "1.0.0"
OUTPUT_CONTRACT = "ordered-string-csv-1.0.0"
SOURCE_OUTPUT_CONTRACT = "historical-wind-csv-copy-1.0.0"


class ProductFile(TypedDict):
    relative_path: str
    file_format: str
    size_bytes: int
    sha256: str
    record_count: int | None
    month: str | None
    role: str


class ProductManifest(TypedDict):
    product_id: str
    manifest_schema_version: str
    output_contract: str
    source_kind: str
    source: dict[str, JSONValue]
    variable_key: str
    unit: str
    months: list[str]
    grid: dict[str, JSONValue]
    method: str
    files: list[ProductFile]
    checks: dict[str, JSONValue]
    created_at: str


@dataclass(frozen=True)
class ProductOperation:
    product_id: str
    publication_status: str
    source_kind: str
    error_code: str | None
    created_at: str
    updated_at: str
    block_reasons: tuple[str, ...]
    manifest_digest: str | None
    content_digest: str | None


@dataclass(frozen=True)
class ProductVersion:
    """Detached published identity and its freshly verified manifest."""

    manifest: ProductManifest
    manifest_digest: str
    content_digest: str


@dataclass(frozen=True)
class ProductSummary:
    product_id: str
    variable_key: str
    unit: str
    months: tuple[str, ...]
    grid_key: str
    source: str
    integrity_status: str


@dataclass(frozen=True)
class ProductMonthLocation:
    product_id: str
    month: str
    resource_id: str
    file_format: str
    sha256: str
    record_count: int
