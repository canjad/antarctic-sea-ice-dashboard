"""Published scientific month/grid adapter and exact original-cell queries."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import asdict, dataclass
import csv
import io
import logging
import math
from threading import Lock
from typing import TypeVar

from antarctic_sea_ice_dashboard.services.csv_validation import RULESET_VERSION, ReferenceGrid, select_validation_profile
from antarctic_sea_ice_dashboard.services.dataset_metadata import GRID_DEFINITIONS, GridDefinition, is_valid_observation_month
from antarctic_sea_ice_dashboard.services.map_raster import RASTER_VERSION, render_raster
from antarctic_sea_ice_dashboard.services.product_contracts import MANIFEST_SCHEMA_VERSION, OUTPUT_CONTRACT, ProductVersion
from antarctic_sea_ice_dashboard.services.product_storage import ProductError, canonical_json, digest_bytes
from antarctic_sea_ice_dashboard.services.research_products import list_available_products, read_product_month_bytes, read_product_version
from antarctic_sea_ice_dashboard.services.validation_reference import GEOGRAPHIC_PROJ, PROJECTED_PROJ, REFERENCE_ADAPTER_VERSION, load_reference_grid

from antarctic_sea_ice_dashboard.services.variables import VARIABLE_DEFINITIONS, VARIABLE_REGISTRY_VERSION, VariableDefinition


logger = logging.getLogger(__name__)
CATALOGUE_PAGE_SIZE = 20
MONTH_CACHE_SIZE = 2
RASTER_CACHE_SIZE = 4
GRID_ADAPTER_VERSION = "published-retained-1.1.0"
CacheKey = tuple[str, ...]
T = TypeVar("T")
_CACHE_LOCK = Lock()


@dataclass(frozen=True)
class FrameIdentity:
    product_id: str
    content_digest: str
    manifest_digest: str
    month: str
    grid_key: str
    frame_id: str


@dataclass(frozen=True)
class ScientificProduct:
    product_id: str
    content_digest: str
    variable_key: str
    label: str


@dataclass(frozen=True)
class ScientificCatalogue:
    products: tuple[ScientificProduct, ...] = ()
    has_more: bool = False
    code: str = ""
    message: str = ""


@dataclass(frozen=True)
class MonthFrame:
    identity: FrameIdentity
    available_months: tuple[str, ...]
    unit: str
    source: str
    image_src: str
    colour_limit: float
    bounds: str


@dataclass(frozen=True)
class FrameResult:
    frame: MonthFrame | None = None
    code: str = ""
    message: str = ""


@dataclass(frozen=True)
class PixelResult:
    status: str
    message: str
    identity: FrameIdentity
    grid_cell_id: int | None = None
    latitude: float | None = None
    longitude: float | None = None
    x_m: float | None = None
    y_m: float | None = None
    value: float | None = None
    unit: str = ""
    source: str = ""


@dataclass(frozen=True)
class ScientificMonth:
    """Backend-only canonical cells; never serialize its arrays into UI state."""

    variable: VariableDefinition
    original_unit: str
    identity: FrameIdentity
    months: tuple[str, ...]
    unit: str
    source: str
    definition: GridDefinition
    reference: ReferenceGrid
    values: tuple[float | None, ...]
    retained_positions: dict[int, int]


_MONTH_CACHE: OrderedDict[CacheKey, ScientificMonth] = OrderedDict()
_RASTER_CACHE: OrderedDict[CacheKey, str] = OrderedDict()


def _cached(cache: OrderedDict[CacheKey, T], key: CacheKey) -> T | None:
    with _CACHE_LOCK:
        value = cache.get(key)
        if value is not None:
            cache.move_to_end(key)
        return value


def _remember(cache: OrderedDict[CacheKey, T], key: CacheKey, value: T, maximum: int) -> T:
    with _CACHE_LOCK:
        cache[key] = value
        cache.move_to_end(key)
        while len(cache) > maximum:
            cache.popitem(last=False)
    return value


def _forget_product(product_id: str) -> None:
    with _CACHE_LOCK:
        for cache in (_MONTH_CACHE, _RASTER_CACHE):
            for key in tuple(cache):
                if key[0] == product_id:
                    del cache[key]


def _source(version: ProductVersion) -> str:
    manifest = version.manifest
    source = manifest["source"]
    if manifest["source_kind"] == "uploaded_csv":
        return f"Uploaded CSV: {source.get('original_filename', 'unnamed')}; working revision at publication: {source.get('source_data_revision', 'unknown')}. Original acquisition provenance is unverified."
    return f"{manifest['source_kind']}: {source.get('source_id', 'unidentified')}"


def _require_contract(version: ProductVersion) -> tuple[str, GridDefinition, tuple[str, ...]]:
    manifest = version.manifest
    variable = VARIABLE_DEFINITIONS.get(manifest["variable_key"])
    if variable is None:
        raise ProductError("VARIABLE_UNAVAILABLE", "This variable has no approved scientific reading contract.")
    profile = select_validation_profile(variable.variable_key)
    if manifest["unit"] not in variable.accepted_units:
        raise ProductError("UNIT_INCOMPATIBLE", "The product unit is incompatible with its approved variable contract.")
    checks = manifest["checks"]
    if (checks.get("ruleset_version"), checks.get("profile_key"), checks.get("profile_version")) != (RULESET_VERSION, profile.key, profile.version):
        raise ProductError("RULES_STALE", "The published scientific rules differ from the current contract. Check and publish a new version explicitly.")
    if (manifest["manifest_schema_version"] != MANIFEST_SCHEMA_VERSION
        or manifest["source_kind"] != "uploaded_csv" or manifest["output_contract"] != OUTPUT_CONTRACT):
        raise ProductError("PRODUCT_ADAPTER_UNAVAILABLE", "This product has no approved map reading adapter. Historical registrations remain blocked.")
    grid_key = manifest["grid"].get("grid_key")
    definition = GRID_DEFINITIONS.get(grid_key) if isinstance(grid_key, str) else None
    if definition is None:
        raise ProductError("GRID_UNSUPPORTED", "The product has no registered map grid.")
    if (canonical_json(manifest["grid"].get("definition")) != canonical_json(asdict(definition))
        or manifest["grid"].get("mask_identity") != definition.coordinate_sha256):
        raise ProductError("GRID_IDENTITY_MISMATCH", "The published grid definition or retained mask differs from the registered contract.")
    raw_months = manifest["months"]
    if any(not is_valid_observation_month(month) for month in raw_months) or len(set(raw_months)) != len(raw_months):
        raise ProductError("MONTH_INVENTORY_INVALID", "The manifest month inventory is invalid or duplicated.")
    months = tuple(sorted(raw_months))
    files = [item for item in manifest["files"] if item["role"] == "data"]
    if len(files) != len(months) or sorted(item["month"] or "" for item in files) != list(months):
        raise ProductError("MONTH_INVENTORY_INVALID", "Actual data files must match the manifest months exactly, one file per month.")
    if not months:
        raise ProductError("NO_MONTHS", "This published product has no actual months.")
    return str(grid_key), definition, months


def _require_reference(version: ProductVersion, grid_key: str, definition: GridDefinition) -> ReferenceGrid:
    reference = load_reference_grid(grid_key)
    if reference.dependency_code:
        raise ProductError(reference.dependency_code, reference.dependency_message or "The reference grid is unavailable.")
    if reference.identity != version.manifest["grid"].get("reference_identity"):
        raise ProductError("REFERENCE_IDENTITY_MISMATCH", "The configured reference differs from the published reference identity. The product is unavailable for mapping.")
    if len(reference.grid_cell_ids) != definition.retained_cell_count:
        raise ProductError("GRID_MAPPING_UNAVAILABLE", "The validated reference does not supply the retained cell identities.")
    return reference


def scientific_catalogue(page: int = 0) -> ScientificCatalogue:
    """Return one journal page filtered to verified supported v10 identities.

    Empty pages can have a next page: pagination follows the published journal,
    including unsupported entries. Failures are structured, path-free results.
    No working rows, blocked registrations or replacement versions are selected.
    """
    try:
        if isinstance(page, bool) or not isinstance(page, int) or not 0 <= page <= 100000:
            raise ProductError("INVALID_INPUT", "Catalogue page is outside the supported range.")
        summaries = list_available_products(offset=page * CATALOGUE_PAGE_SIZE, limit=CATALOGUE_PAGE_SIZE)
        products = []
        excluded_codes: set[str] = set()
        for summary in summaries:
            if summary.integrity_status != "verified" or summary.variable_key not in VARIABLE_DEFINITIONS:
                _forget_product(summary.product_id)
                if summary.integrity_status != "verified":
                    excluded_codes.add("INTEGRITY_UNAVAILABLE")
                continue
            try:
                version = read_product_version(summary.product_id)
                grid_key, definition, _ = _require_contract(version)
                _require_reference(version, grid_key, definition)
            except ProductError as error:
                _forget_product(summary.product_id)
                excluded_codes.add(error.code)
                continue
            products.append(ScientificProduct(summary.product_id, version.content_digest, summary.variable_key,
                f"{summary.product_id} · {summary.variable_key} · {summary.unit} · {summary.source} · {version.content_digest[:12]}"))
        message = "Some published products on this page are unavailable for mapping: " + ", ".join(sorted(excluded_codes)) if excluded_codes else ""
        return ScientificCatalogue(tuple(products), len(summaries) == CATALOGUE_PAGE_SIZE, message=message)
    except Exception as error:
        code, message = _failure(error)
        return ScientificCatalogue(code=code, message=message)


def _failure(error: Exception) -> tuple[str, str]:
    if isinstance(error, ProductError):
        return error.code, str(error)
    logger.error("Scientific product read failed (%s).", type(error).__name__)
    return "SCIENTIFIC_DATA_UNAVAILABLE", "The selected scientific data could not be read. Refresh this exact product after the operator resolves its resources."


def _read_month(product_id: str, expected_content_digest: str, requested_month: str) -> ScientificMonth:
    # Fresh file integrity and reference observation precede every cache hit.
    version = read_product_version(product_id)
    if version.content_digest != expected_content_digest:
        raise ProductError("CONTENT_VERSION_MISMATCH", "The selected product content identity differs. Refresh the catalogue and explicitly select a version.")
    grid_key, definition, months = _require_contract(version)
    month = requested_month or months[0]
    if month not in months:
        raise ProductError("MONTH_NOT_AVAILABLE", "This exact product contains no data for the requested month.")
    reference = _require_reference(version, grid_key, definition)
    variable = VARIABLE_DEFINITIONS[version.manifest["variable_key"]]
    original_unit = version.manifest["unit"]
    key = (product_id, version.content_digest, version.manifest_digest, month, variable.variable_key, grid_key,
        str(reference.identity["file_sha256"]), REFERENCE_ADAPTER_VERSION, GRID_ADAPTER_VERSION,
        VARIABLE_REGISTRY_VERSION, RULESET_VERSION, variable.profile_version, original_unit,
        variable.conversion_version, variable.internal_unit, variable.display_unit)
    cached = _cached(_MONTH_CACHE, key)
    if cached is not None:
        return cached
    item = next(item for item in version.manifest["files"] if item["role"] == "data" and item["month"] == month)
    if item["file_format"] != "csv" or item["record_count"] != definition.retained_cell_count:
        raise ProductError("MONTH_FORMAT_MISMATCH", "The monthly CSV format or retained-cell count differs from the map contract.")
    data = read_product_month_bytes(product_id, month)
    if digest_bytes(data) != item["sha256"]:
        raise ProductError("INTEGRITY_UNAVAILABLE", "The monthly bytes differ from the selected manifest.")
    try:
        reader = csv.reader(io.StringIO(data.decode("utf-8-sig"), newline=""), strict=True)
        headers = next(reader)
        required = ("Latitude", "Longitude", variable.value_field)
        if len(headers) != len(set(headers)) or any(headers.count(name) != 1 for name in required):
            raise ValueError("Required columns are missing or duplicated.")
        indices = [headers.index(name) for name in required]
        month_index = headers.index("observation_month") if "observation_month" in headers else None
        values: list[float | None] = [None] * (definition.shape[0] * definition.shape[1])
        count = 0
        for index, record in enumerate(reader):
            if index >= len(reference.coordinates) or len(record) != len(headers):
                raise ValueError("Monthly records do not match the retained grid.")
            latitude, longitude, value = (float(record[position]) for position in indices)
            if not all(math.isfinite(number) for number in (latitude, longitude, value)):
                raise ValueError("The published finite-number contract is violated.")
            if (latitude, longitude) != reference.coordinates[index]:
                raise ValueError("Coordinates or order differ from the retained reference.")
            if month_index is not None and record[month_index] != month:
                raise ValueError("The record month differs from the manifest month.")
            values[reference.grid_cell_ids[index]] = variable.normalise_value(value, original_unit)
            count += 1
        if count != definition.retained_cell_count:
            raise ValueError("Monthly records are incomplete.")
    except (ValueError, UnicodeError, csv.Error, StopIteration, OverflowError):
        raise ProductError("MONTH_DATA_INVALID", "The monthly CSV violates the exact coordinates, order, month or finite-value contract. No values were filled or repaired.") from None
    frame_id = digest_bytes(canonical_json(key))
    identity = FrameIdentity(product_id, version.content_digest, version.manifest_digest, month, grid_key, frame_id)
    result = ScientificMonth(variable, original_unit, identity, months, variable.display_unit, _source(version), definition, reference,
        tuple(values), {cell_id: index for index, cell_id in enumerate(reference.grid_cell_ids)})
    return _remember(_MONTH_CACHE, key, result, MONTH_CACHE_SIZE)


@dataclass(frozen=True)
class ProductSelection:
    product_id: str
    content_digest: str
    variable_key: str


@dataclass(frozen=True)
class ProductInventory:
    selection: ProductSelection
    months: tuple[str, ...]
    grid_key: str
    mask_identity: str


def scientific_inventory(selection: ProductSelection) -> ProductInventory:
    """Read exact published metadata; ProductError explains unavailable resources."""
    try:
        version = read_product_version(selection.product_id)
        if version.content_digest != selection.content_digest or version.manifest["variable_key"] != selection.variable_key:
            raise ProductError("PRODUCT_SELECTION_MISMATCH", "Select the exact published content version and variable again.")
        grid_key, definition, months = _require_contract(version)
        _require_reference(version, grid_key, definition)
        return ProductInventory(selection, months, grid_key, definition.coordinate_sha256)
    except Exception as error:
        code, message = _failure(error)
        raise ProductError(code, message) from None


def read_scientific_month(selection: ProductSelection, month: str) -> ScientificMonth:
    """Read one explicit actual month into backend memory, or raise ProductError.

    Accepts opaque product identities only. Arrays remain backend-owned; units,
    canonical cell identities and the complete publication contract are checked.
    """
    try:
        if not is_valid_observation_month(month):
            raise ProductError("MONTH_INVALID", "Select an explicit actual month in YYYY-MM format.")
        grid = _read_month(selection.product_id, selection.content_digest, month)
        if grid.variable.variable_key != selection.variable_key:
            raise ProductError("VARIABLE_MISMATCH", "The requested variable differs from the published product.")
        return grid
    except Exception as error:
        _forget_product(selection.product_id)
        code, message = _failure(error)
        raise ProductError(code, message) from None


def load_month_frame(product_id: str, content_digest: str, month: str) -> FrameResult:
    """Read one immutable month and render its canonical raster, or unavailable.

    Empty month chooses the earliest actual manifest month after explicit product
    selection. All failures clear the product caches; no synthetic fallback is
    supplied. The current-month range is symmetric, untruncated, and +/-1 for
    an all-zero field. Scientific arrays stay in the bounded backend cache.
    """
    try:
        grid = _read_month(product_id, content_digest, month)
        limit = max(abs(value) for value in grid.values if value is not None) or 1.0
        key = (product_id, grid.identity.frame_id, "v10", grid.identity.grid_key, RASTER_VERSION, limit.hex())
        image_src = _cached(_RASTER_CACHE, key)
        if image_src is None:
            image_src = _remember(_RASTER_CACHE, key, render_raster(grid.values, *grid.definition.shape, limit), RASTER_CACHE_SIZE)
        rows, columns = grid.definition.shape
        first_x, first_y = grid.definition.upper_left_cell_center_m
        half = grid.definition.cell_size_m / 2
        bounds = f"Hughes 1980 polar stereographic · x: {first_x - half:g} to {first_x + (columns - 1) * 2 * half + half:g} m · y: {first_y - (rows - 1) * 2 * half - half:g} to {first_y + half:g} m · image top is grid row 0"
        return FrameResult(MonthFrame(grid.identity, grid.months, grid.unit, grid.source, image_src, limit, bounds))
    except Exception as error:
        _forget_product(product_id)
        code, message = _failure(error)
        return FrameResult(code=code, message=message)


def query_pixel(identity: FrameIdentity, longitude: str, latitude: str) -> PixelResult:
    """Select the containing original cell in the same validated frame.

    Coordinates are Hughes 1980 geographic degrees, always longitude then
    latitude. Edges are half-open: west/north included, east/south excluded.
    There is no nearest-neighbour search or tolerance expansion. Reference
    centre validation uses the inherited 2 m per-axis tolerance, not hit testing.
    Missing resources return unavailable; invalid/outside/masked/missing values
    have distinct statuses, never a replacement cell or fabricated zero.
    """
    try:
        grid = _read_month(identity.product_id, identity.content_digest, identity.month)
        if grid.identity != identity:
            raise ProductError("FRAME_IDENTITY_MISMATCH", "The query frame no longer matches the displayed product, month and grid.")
        try:
            lon, lat = float(longitude), float(latitude)
        except (ValueError, TypeError, OverflowError):
            return PixelResult("invalid_position", "Enter finite longitude and latitude in geographic degrees.", identity)
        if not math.isfinite(lon) or not math.isfinite(lat) or not -180 <= lon <= 180 or not -90 <= lat <= 90:
            return PixelResult("invalid_position", "Longitude must be within [-180, 180] and latitude within [-90, 90].", identity)
        from pyproj import CRS, Transformer
        from pyproj.exceptions import ProjError
        transformer = Transformer.from_crs(CRS.from_proj4(GEOGRAPHIC_PROJ), CRS.from_proj4(PROJECTED_PROJ), always_xy=True)
        try:
            x_m, y_m = transformer.transform(lon, lat, errcheck=True)
        except ProjError:
            return PixelResult("outside_grid", "This position cannot be located on the product projection.", identity)
        if not math.isfinite(x_m) or not math.isfinite(y_m):
            return PixelResult("outside_grid", "This position has no finite location on the product projection.", identity)
        definition = grid.definition
        rows, columns = definition.shape
        first_x, first_y = definition.upper_left_cell_center_m
        half = definition.cell_size_m / 2
        column = math.floor((x_m - first_x + half) / definition.cell_size_m)
        row = math.floor((first_y + half - y_m) / definition.cell_size_m)
        if not 0 <= row < rows or not 0 <= column < columns:
            return PixelResult("outside_grid", "The selected position is outside the canonical grid extent.", identity)
        cell_id = row * columns + column
        position = grid.retained_positions.get(cell_id)
        if position is None:
            return PixelResult("outside_mask", "This original grid cell is outside the retained product mask.", identity, grid_cell_id=cell_id)
        cell_lat, cell_lon = grid.reference.coordinates[position]
        value = grid.values[cell_id]
        return PixelResult("value" if value is not None else "missing_value",
            "Original pixel value; no interpolation." if value is not None else "This retained pixel has no value.",
            identity, cell_id, cell_lat, cell_lon, first_x + column * definition.cell_size_m,
            first_y - row * definition.cell_size_m, value, grid.unit, grid.source)
    except Exception as error:
        _forget_product(identity.product_id)
        code, message = _failure(error)
        return PixelResult("unavailable", f"{code}: {message}", identity)
