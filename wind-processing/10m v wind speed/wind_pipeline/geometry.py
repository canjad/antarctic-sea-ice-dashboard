"""Hughes 1980 projection, recovered mask and physical polygon areas."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import shapely
from scipy.sparse import coo_matrix, csr_matrix
from shapely.strtree import STRtree

from .constants import LEGACY, LIMITS
from .projection import (
    CELL_SIZE,
    FIRST_X,
    FIRST_Y,
    FORWARD,
    GEOD,
    GRID_COLUMNS,
    GRID_ROWS,
    INVERSE,
    PROJECTED_CRS,
)
from .storage import array_digest, file_hash, say


def reconstruct_target(reference_dir: Path = LEGACY) -> tuple[pd.DataFrame, dict]:
    examples = sorted(reference_dir.glob("*_v_wind_NSIDC_grid.csv"))
    if not examples:
        raise ValueError(
            "An existing NSIDC coordinate file is required to reconstruct the retained-cell mask."
        )
    reference = examples[0]
    frame = pd.read_csv(reference, usecols=["Latitude", "Longitude"])
    if (
        len(frame) != 82120
        or frame.duplicated().any()
        or not np.isfinite(frame.to_numpy()).all()
    ):
        raise ValueError(
            "The reference must contain 82,120 unique finite coordinate pairs."
        )
    x, y = FORWARD.transform(frame.Longitude.to_numpy(), frame.Latitude.to_numpy())
    columns = np.rint((x - FIRST_X) / CELL_SIZE).astype(np.int32)
    rows = np.rint((FIRST_Y - y) / CELL_SIZE).astype(np.int32)
    exact_x = FIRST_X + CELL_SIZE * columns
    exact_y = FIRST_Y - CELL_SIZE * rows
    residual = np.maximum(np.abs(x - exact_x), np.abs(y - exact_y))
    if residual.max() > LIMITS["grid_fit_max_m"]:
        raise ValueError(
            f"Coordinates do not fit the canonical grid: {residual.max():.6f} m residual."
        )
    if np.any(
        (rows < 0) | (rows >= GRID_ROWS) | (columns < 0) | (columns >= GRID_COLUMNS)
    ):
        raise ValueError("Reference coordinates fall outside the canonical NSIDC grid.")
    ids = rows * GRID_COLUMNS + columns
    if len(np.unique(ids)) != len(ids) or not np.all(np.diff(ids) > 0):
        raise ValueError(
            "Reference cells must be unique and in ascending canonical row-major order."
        )
    exact_lon, exact_lat = INVERSE.transform(exact_x, exact_y)
    target = frame.copy()
    target.insert(0, "cell", np.arange(len(frame), dtype=np.int32))
    target["grid_row"] = rows
    target["grid_column"] = columns
    target["grid_cell_id"] = ids
    target["x_m"] = exact_x
    target["y_m"] = exact_y
    target["canonical_latitude"] = exact_lat
    target["canonical_longitude"] = exact_lon
    info = {
        "reference_file": str(reference),
        "reference_sha256": file_hash(reference),
        "coordinate_sha256": array_digest(
            frame.Latitude.to_numpy(), frame.Longitude.to_numpy()
        ),
        "reference_cell_count": len(target),
        "grid_fit_max_axis_residual_m": float(residual.max()),
        "grid_fit_median_axis_residual_m": float(np.median(residual)),
        "crs": "EPSG:3412 (Hughes 1980)",
        "projected_crs_wkt": PROJECTED_CRS.to_wkt(),
        "grid_shape": [GRID_ROWS, GRID_COLUMNS],
        "nominal_cell_size_m": CELL_SIZE,
        "upper_left_cell_center_m": [FIRST_X, FIRST_Y],
        "selection_mask_definition": "Exactly the coordinate rows retained in the existing reference CSV; original mask product/version unavailable.",
        "csv_coordinates": "Original five-decimal reference coordinates, preserved in the existing row order.",
    }
    return target, info


def source_polygons(
    metadata: dict, arc_subdivisions: int, indices: np.ndarray | None = None
):
    """Build source cells with shared densified latitude arcs and exact radial meridians.

    The -180-degree cell straddles the longitude seam continuously in projection.
    Native centers are interpreted as area-representative values on half-step
    bounds. Latitude bounds are clipped at the South Pole. This interpretation
    defines the new product; it is not evidence about the missing original matrix.
    """
    nlon = len(metadata["longitude"])
    if indices is None:
        indices = np.arange(len(metadata["latitude"]) * nlon, dtype=np.int32)
    indices = np.asarray(indices, dtype=np.int32)
    center_lat = metadata["latitude"][indices // nlon]
    center_lon = metadata["longitude"][indices % nlon]
    north = np.minimum(center_lat + 0.125, 90.0)
    south = np.maximum(center_lat - 0.125, -90.0)
    fraction = np.linspace(-0.125, 0.125, arc_subdivisions + 1)
    north_lon = center_lon[:, None] + fraction[None, :]
    south_lon = north_lon[:, ::-1]
    all_lon = np.concatenate([north_lon, south_lon], axis=1)
    all_lat = np.concatenate(
        [
            np.broadcast_to(north[:, None], north_lon.shape),
            np.broadcast_to(south[:, None], south_lon.shape),
        ],
        axis=1,
    )
    x, y = FORWARD.transform(all_lon, all_lat)
    vertices = np.stack([x, y], axis=-1)
    polygons = shapely.polygons(vertices)
    if not shapely.is_valid(polygons).all() or not (shapely.area(polygons) > 0).all():
        raise ValueError(
            "A reconstructed source polygon is invalid or has zero projected area."
        )
    return polygons, indices


def target_polygons(target: pd.DataFrame):
    half = CELL_SIZE / 2
    x, y = target.x_m.to_numpy(), target.y_m.to_numpy()
    return shapely.box(x - half, y - half, x + half, y + half)


def physical_areas(geometries, maximum_segment_m: float) -> np.ndarray:
    """Evaluate Hughes ellipsoidal polygon areas after bounding projected segment length.

    A projected straight target edge is generally curved on the ellipsoid. It is
    therefore densified before inverse projection, and Geod joins those vertices
    with short geodesics. Finer-segmentation comparisons quantify this approximation.
    """
    geometries = shapely.segmentize(geometries, maximum_segment_m)
    result = np.zeros(len(geometries), dtype=np.float64)
    polygon_mask = shapely.get_type_id(geometries) == 3
    no_holes = shapely.get_num_interior_rings(geometries) == 0
    ordinary = np.flatnonzero(polygon_mask & no_holes)
    if len(ordinary):
        coordinates, owners = shapely.get_coordinates(
            geometries[ordinary], return_index=True
        )
        longitude, latitude = INVERSE.transform(coordinates[:, 0], coordinates[:, 1])
        boundaries = np.concatenate(
            [[0], np.flatnonzero(np.diff(owners)) + 1, [len(owners)]]
        )
        for local, start, end in zip(
            range(len(ordinary)), boundaries[:-1], boundaries[1:]
        ):
            area, _ = GEOD.polygon_area_perimeter(
                longitude[start:end], latitude[start:end]
            )
            result[ordinary[local]] = abs(area)
    for index in np.flatnonzero(~(polygon_mask & no_holes)):
        geometry = geometries[index]
        if geometry.is_empty:
            continue
        if geometry.geom_type == "Polygon":
            shells = [geometry]
        elif geometry.geom_type == "MultiPolygon":
            shells = list(geometry.geoms)
        elif geometry.geom_type in (
            "LineString",
            "Point",
            "MultiLineString",
            "MultiPoint",
        ):
            continue
        else:
            raise ValueError(f"Unexpected overlap geometry: {geometry.geom_type}")
        area_sum = 0.0
        for polygon in shells:
            lon, lat = INVERSE.transform(*np.asarray(polygon.exterior.coords).T)
            shell_area, _ = GEOD.polygon_area_perimeter(lon, lat)
            polygon_area = abs(shell_area)
            for interior in polygon.interiors:
                lon, lat = INVERSE.transform(*np.asarray(interior.coords).T)
                hole_area, _ = GEOD.polygon_area_perimeter(lon, lat)
                polygon_area -= abs(hole_area)
            area_sum += polygon_area
        result[index] = area_sum
    return result


def compute_weights(
    target: pd.DataFrame,
    polygons,
    source_ids: np.ndarray,
    source_count: int,
    maximum_segment_m: float,
    block_size: int = 1024,
    quiet: bool = False,
) -> tuple[csr_matrix, pd.DataFrame]:
    tree = STRtree(polygons)
    targets = target_polygons(target)
    areas = physical_areas(targets, maximum_segment_m)
    row_ids, column_ids, overlap_areas = [], [], []
    projected_coverage = np.zeros(len(target), dtype=np.float64)
    physical_coverage = np.zeros(len(target), dtype=np.float64)
    for start in range(0, len(target), block_size):
        stop = min(start + block_size, len(target))
        pairs = tree.query(targets[start:stop], predicate="intersects")
        local_rows, source_local = pairs
        intersections = shapely.intersection(
            targets[start:stop][local_rows], polygons[source_local]
        )
        projected = shapely.area(intersections)
        positive = projected > 0
        local_rows = local_rows[positive]
        source_local = source_local[positive]
        projected = projected[positive]
        intersections = intersections[positive]
        physical = physical_areas(intersections, maximum_segment_m)
        if not np.isfinite(physical).all() or not (physical > 0).all():
            raise ValueError(
                "A positive-area overlap has a nonpositive or nonfinite physical area."
            )
        projected_coverage[start:stop] = np.bincount(
            local_rows, weights=projected, minlength=stop - start
        )
        physical_coverage[start:stop] = np.bincount(
            local_rows, weights=physical, minlength=stop - start
        )
        row_ids.append(local_rows + start)
        column_ids.append(source_ids[source_local])
        overlap_areas.append(physical)
        if not quiet and (
            start == 0 or stop == len(target) or (start // block_size) % 10 == 0
        ):
            say(f"Computed overlap areas for {stop:,}/{len(target):,} target cells.")
    matrix = coo_matrix(
        (
            np.concatenate(overlap_areas),
            (np.concatenate(row_ids), np.concatenate(column_ids)),
        ),
        shape=(len(target), source_count),
    ).tocsr()
    matrix.sum_duplicates()
    raw_sums = np.asarray(matrix.sum(axis=1)).ravel()
    if np.any(raw_sums <= 0):
        raise ValueError("At least one target cell has no source coverage.")
    matrix.data /= np.repeat(raw_sums, np.diff(matrix.indptr))
    coverage = pd.DataFrame(
        {
            "cell": target.cell.to_numpy(),
            "target_physical_area_m2": areas,
            "overlap_projected_area_m2": projected_coverage,
            "overlap_physical_area_m2": physical_coverage,
            "projected_coverage_ratio": projected_coverage / CELL_SIZE**2,
            "physical_coverage_ratio": physical_coverage / areas,
            "source_cell_count": np.diff(matrix.indptr),
            "normalized_weight_row_sum": np.asarray(matrix.sum(axis=1)).ravel(),
        }
    )
    return matrix, coverage
