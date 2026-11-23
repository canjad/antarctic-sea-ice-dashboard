"""Read-only inventory adapters for explicitly selected historical wind CSVs."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re

from antarctic_sea_ice_dashboard.services.dataset_metadata import is_valid_observation_month
from antarctic_sea_ice_dashboard.services.product_storage import (
    ProductError, digest_bytes, directory, source_file, stream_digest,
)


SOURCE_ROOT_ENV = "SEA_ICE_CHECKING_DATA_ROOT"
SOURCE_ADAPTER_VERSION = "historical-wind-inventory-1.0.0"
MAX_REGISTRATION_MONTHS = 12
MAX_REGISTRATION_BYTES = 1024 * 1024 * 1024
MAX_EVIDENCE_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True)
class SourceDefinition:
    source_id: str
    label: str
    directory: str
    monthly_directory: str
    filename_suffix: str
    grid_key: str | None
    method: str


SOURCES = (
    SourceDefinition("wind-v2-reconstructed", "Wind v2 · reconstructed NSIDC", "processed_v2/nsidc_reconstructed", "monthly average csv", "_v_wind_NSIDC_reconstructed.csv", "nsidc_sh_25km_hughes_1980_retained_82120_v1", "hughes-polar-conservative-v1; reconstructed conservative remapping"),
    SourceDefinition("wind-legacy-reconstructed", "Wind legacy · reconstructed NSIDC", "processed/nsidc_reconstructed", "monthly average csv", "_v_wind_NSIDC_reconstructed.csv", "nsidc_sh_25km_hughes_1980_retained_82120_v1", "historical reconstructed remapping; method evidence requires review"),
    SourceDefinition("wind-v3-native", "Wind v3 · native ERA5", "processed_v3", "monthly average csv", "_v_wind.csv", None, "native-grid extraction; not remapped"),
)


@dataclass(frozen=True)
class SourceFile:
    resource: str
    relative_path: str
    size_bytes: int
    sha256: str
    month: str | None
    role: str


@dataclass(frozen=True)
class SourceInventory:
    source_id: str
    adapter_version: str
    variable_key: str
    unit: str
    grid_key: str | None
    method: str
    months: tuple[str, ...]
    files: tuple[SourceFile, ...]
    gaps: tuple[str, ...]
    historical_claims: dict[str, str | int | float | bool | None]


def source_root() -> Path:
    """Read the operator root or raise ProductError(SOURCE_NOT_CONFIGURED)."""
    value = os.environ.get(SOURCE_ROOT_ENV)
    if not value or not Path(value).is_absolute():
        raise ProductError("SOURCE_NOT_CONFIGURED", "The operator must configure SEA_ICE_CHECKING_DATA_ROOT as a read-only absolute wind resource root.")
    return Path(value)


def source_definition(source_id: str) -> SourceDefinition:
    """Resolve a fixed backend identity or raise ProductError(INVALID_SOURCE)."""
    for definition in SOURCES:
        if source_id == definition.source_id:
            return definition
    raise ProductError("INVALID_SOURCE", "Select a backend-listed source product identity.")


def available_source_months(source_id: str) -> tuple[str, ...]:
    """Enumerate actual monthly filenames only, never fill intervening months.

    A listed month is an inventory candidate, not a scientific conclusion.
    Registration subsequently opens and copies each selected regular file.
    Invalid/configuration inputs raise ProductError; unavailable or unsafe
    source directories raise OSError for the caller to sanitise.
    """
    definition = source_definition(source_id)
    pattern = re.compile(r"([0-9]{6})" + re.escape(definition.filename_suffix))
    with directory(source_root() / definition.directory / definition.monthly_directory) as descriptor:
        names = os.listdir(descriptor)
    months = set()
    for name in names:
        match = pattern.fullmatch(name)
        if match:
            compact = match[1]
            month = compact[:4] + "-" + compact[4:]
            if is_valid_observation_month(month):
                months.add(month)
    return tuple(sorted(months))


def inspect_source_product(source_id: str, months: tuple[str, ...]) -> SourceInventory:
    """Hash only an explicit 1..12-month scope and bounded evidence files.

    Historical claims cannot confer publication eligibility. No method-validation
    adapter is currently approved for these sources, so every registration is
    blocked. Missing/unsafe resources fail with ProductError or OSError, which
    the publication boundary sanitises. No source file is written or imported.
    """
    definition = source_definition(source_id)
    if (not isinstance(months, tuple) or not 1 <= len(months) <= MAX_REGISTRATION_MONTHS
        or any(not is_valid_observation_month(month) for month in months)
        or len(set(months)) != len(months)):
        raise ProductError("INVALID_SCOPE", "Select 1 to 12 distinct actual months in YYYY-MM format.")
    selected = tuple(sorted(months))
    if not set(selected).issubset(available_source_months(source_id)):
        raise ProductError("SOURCE_MONTH_MISSING", "One or more requested months have no source file; no missing month is inferred or filled.")
    root = source_root()
    files: list[SourceFile] = []
    total = 0
    for month in selected:
        resource = f"{definition.directory}/{definition.monthly_directory}/{month.replace('-', '')}{definition.filename_suffix}"
        with source_file(root, resource) as stream:
            size, digest = stream_digest(stream)
        total += size
        if total > MAX_REGISTRATION_BYTES:
            raise ProductError("RESOURCE_LIMIT", "The selected scope exceeds the 1 GiB registration limit.")
        files.append(SourceFile(resource, f"data-{month}.csv", size, digest, month, "data"))
    gaps = ["HISTORICAL_RULE_COVERAGE_UNACCEPTED", "METHOD_VALIDATION_ADAPTER_UNAVAILABLE", "SOURCE_PROVENANCE_NOT_ATTESTED"]
    if definition.grid_key is not None:
        gaps.extend(("ORIGINAL_MASK_VERSION_UNAVAILABLE", "ORIGINAL_RESEARCH_WEIGHTS_UNAVAILABLE"))
    else:
        gaps.append("NATIVE_GRID_CONSUMPTION_CONTRACT_UNAVAILABLE")
    claims: dict[str, str | int | float | bool | None] = {}
    for filename in ("summary.json", "regrid_metadata.json", "cache_manifest.json", "source_inventory.json", "processing_report.md"):
        resource = f"{definition.directory}/{filename}"
        try:
            with source_file(root, resource) as stream:
                size, digest = stream_digest(stream, maximum=MAX_EVIDENCE_BYTES)
                stream.seek(0)
                data = stream.read(MAX_EVIDENCE_BYTES + 1)
            if len(data) != size or digest_bytes(data) != digest:
                raise ProductError("SOURCE_CHANGED", "Historical evidence changed while being inventoried.")
        except FileNotFoundError:
            gaps.append("MISSING_" + filename.replace(".", "_").upper())
            continue
        files.append(SourceFile(resource, "evidence-" + filename, size, digest, None, "historical_evidence"))
        if filename in {"summary.json", "regrid_metadata.json"}:
            try:
                value = json.loads(data)
                if not isinstance(value, dict):
                    raise ValueError("Expected an object.")
                for key in ("status", "schema_version", "method_version", "coordinate_sha256", "reference_sha256", "source_netcdf_sha256", "weights_sha256", "selection_mask_definition", "product_status", "wind_component"):
                    item = value.get(key)
                    if isinstance(item, (str, int, float, bool)) or item is None:
                        claims[f"{filename}:{key}"] = item[:2000] if isinstance(item, str) else item
            except (UnicodeError, ValueError):
                gaps.append("INVALID_" + filename.replace(".", "_").upper())
    return SourceInventory(source_id, SOURCE_ADAPTER_VERSION, "v10", "m/s", definition.grid_key, definition.method, selected, tuple(files), tuple(gaps), claims)
