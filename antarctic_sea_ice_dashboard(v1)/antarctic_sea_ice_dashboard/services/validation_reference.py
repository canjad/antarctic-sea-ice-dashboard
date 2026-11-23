"""Read a backend-configured grid without importing the scientific pipeline."""

from __future__ import annotations

import csv
import hashlib
import io
import math
import os
from pathlib import Path
import struct

from antarctic_sea_ice_dashboard.services.csv_validation import JSONValue, ReferenceGrid
from antarctic_sea_ice_dashboard.services.dataset_metadata import GRID_DEFINITIONS


REFERENCE_GRID_ENV = "SEA_ICE_REFERENCE_GRID_CSV"
REFERENCE_MAX_BYTES = 64 * 1024 * 1024
REFERENCE_ADAPTER_VERSION = "retained-grid-1.0.0"
_REQUIRED_FIELDS = ("Latitude", "Longitude", "grid_row", "grid_column", "grid_cell_id", "x_m", "y_m")
GEOGRAPHIC_PROJ = "+proj=longlat +a=6378273.0 +b=6356889.449 +no_defs"
PROJECTED_PROJ = "+proj=stere +lat_0=-90 +lat_ts=-70 +lon_0=0 +a=6378273.0 +b=6356889.449 +units=m +no_defs"


def load_reference_grid(grid_key: str | None) -> ReferenceGrid:
    """Return validated coordinates or a sanitised, identified dependency block.

    Only the server environment supplies a path. Read and hash the same bounded
    bytes. Identity includes the file digest, pinned coordinate digest, grid key
    and adapter version; no host paths are returned to callers.
    """
    identity: dict[str, JSONValue] = {
        "grid_key": grid_key,
        "adapter_version": REFERENCE_ADAPTER_VERSION,
        "coordinate_sha256": None,
        "file_sha256": None,
    }

    def blocked(code: str, message: str) -> ReferenceGrid:
        return ReferenceGrid({**identity, "dependency_code": code}, dependency_code=code, dependency_message=message)

    definition = GRID_DEFINITIONS.get(grid_key or "")
    if definition is None:
        return blocked("GRID_DECLARATION_REQUIRED" if grid_key is None else "GRID_UNSUPPORTED", "Declare a registered, versioned grid; no grid is inferred from row counts or filenames.")
    identity["coordinate_sha256"] = definition.coordinate_sha256
    path_value = os.environ.get(REFERENCE_GRID_ENV)
    if not path_value:
        return blocked("REFERENCE_GRID_NOT_CONFIGURED", "The operator must configure SEA_ICE_REFERENCE_GRID_CSV for the registered retained grid.")
    try:
        with Path(path_value).open("rb") as stream:
            data = stream.read(REFERENCE_MAX_BYTES + 1)
    except (OSError, ValueError):
        return blocked("REFERENCE_GRID_UNAVAILABLE", "The configured reference grid cannot be read.")
    if len(data) > REFERENCE_MAX_BYTES:
        return blocked("REFERENCE_GRID_TOO_LARGE", "The configured reference grid exceeds the 64 MiB resource limit.")
    identity["file_sha256"] = hashlib.sha256(data).hexdigest()
    try:
        reader = csv.reader(io.StringIO(data.decode("utf-8-sig"), newline=""), strict=True)
        headers = next(reader)
        if len(set(headers)) != len(headers) or any(name not in headers for name in _REQUIRED_FIELDS):
            raise ValueError("Reference schema is invalid.")
        indices = [headers.index(name) for name in _REQUIRED_FIELDS]
        coordinates: list[tuple[float, float]] = []
        projected_centres: list[tuple[float, float]] = []
        cell_ids: list[int] = []
        previous_cell = -1
        for record in reader:
            if len(record) != len(headers) or len(coordinates) >= definition.retained_cell_count:
                raise ValueError("Reference record count or width is invalid.")
            latitude, longitude, row, column, cell, x_m, y_m = [float(record[i]) for i in indices]
            if not all(math.isfinite(value) for value in (latitude, longitude, row, column, cell, x_m, y_m)):
                raise ValueError("Reference numbers must be finite.")
            if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
                raise ValueError("Reference geographic coordinates are invalid.")
            if not all(value.is_integer() for value in (row, column, cell)):
                raise ValueError("Reference indices must be integers.")
            if not (0 <= row < definition.shape[0] and 0 <= column < definition.shape[1]):
                raise ValueError("Reference indices are out of range.")
            if cell != row * definition.shape[1] + column or cell <= previous_cell:
                raise ValueError("Reference cell identity or canonical order is invalid.")
            first_x, first_y = definition.upper_left_cell_center_m
            if x_m != first_x + definition.cell_size_m * column or y_m != first_y - definition.cell_size_m * row:
                raise ValueError("Reference projected centres are not canonical.")
            coordinates.append((latitude, longitude))
            projected_centres.append((x_m, y_m))
            cell_ids.append(int(cell))
            previous_cell = int(cell)
        if len(coordinates) != definition.retained_cell_count or len(set(coordinates)) != len(coordinates):
            raise ValueError("Reference retained coordinates are incomplete or duplicated.")
        digest = hashlib.sha256()
        # Match wind_pipeline/storage.py: latitude float64 bytes, then longitude.
        for axis in (0, 1):
            for coordinate in coordinates:
                digest.update(struct.pack("<d", coordinate[axis]))
        if digest.hexdigest() != definition.coordinate_sha256:
            return blocked("REFERENCE_GRID_IDENTITY_MISMATCH", "Reference coordinate bytes differ from the registered retained mask and order.")
    except (UnicodeError, csv.Error, ValueError, OverflowError, StopIteration):
        return blocked("REFERENCE_GRID_INVALID", "The reference grid schema, coordinates or canonical cell mapping is invalid.")

    try:
        from pyproj import CRS, Transformer
        from pyproj.exceptions import ProjError
    except ImportError:
        return blocked("REFERENCE_PROJECTION_UNAVAILABLE", "The pyproj dependency is required to validate the registered Hughes 1980 grid.")
    try:
        # Match wind_pipeline/projection.py ellipsoid and target_array's 2 m
        # maximum axis residual. EPSG:4326 would silently change the ellipsoid.
        geographic = CRS.from_proj4(GEOGRAPHIC_PROJ)
        projected = CRS.from_proj4(PROJECTED_PROJ)
        transformer = Transformer.from_crs(geographic, projected, always_xy=True)
        x_values, y_values = transformer.transform(
            [pair[1] for pair in coordinates], [pair[0] for pair in coordinates], errcheck=True,
        )
        for x_m, y_m, (expected_x, expected_y) in zip(x_values, y_values, projected_centres):
            if not math.isfinite(x_m) or not math.isfinite(y_m) or max(abs(x_m - expected_x), abs(y_m - expected_y)) > 2.0:
                return blocked("REFERENCE_GRID_PROJECTION_MISMATCH", "Reference coordinates do not fit canonical Hughes 1980 cell centres within 2 metres per axis.")
    except (ProjError, ValueError, OverflowError):
        return blocked("REFERENCE_GRID_PROJECTION_INVALID", "The registered reference projection could not be evaluated.")
    return ReferenceGrid({**identity, "dependency_code": None}, tuple(coordinates), grid_cell_ids=tuple(cell_ids))
