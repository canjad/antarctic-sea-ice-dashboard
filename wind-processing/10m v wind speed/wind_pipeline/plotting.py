"""Validated placement and shared signed colour scale for quality figures."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.io import netcdf_file

from .constants import CSV_FIELDS
from .projection import CELL_SIZE, FIRST_X, FIRST_Y, FORWARD, GRID_COLUMNS, GRID_ROWS
from .time_axis import read_dates


def target_array(frame: pd.DataFrame, grid: pd.DataFrame) -> np.ndarray:
    """Place retained values using saved, validated canonical grid indices."""
    if frame.columns.tolist() != CSV_FIELDS or not len(frame):
        raise ValueError(
            "Wind CSV must contain Latitude, Longitude, v_wind and at least one row."
        )
    required = [
        "Latitude",
        "Longitude",
        "grid_row",
        "grid_column",
        "grid_cell_id",
        "x_m",
        "y_m",
    ]
    if any(name not in grid.columns for name in required) or len(grid) != len(frame):
        raise ValueError("Target grid schema or row count does not match the wind CSV.")
    if not np.isfinite(frame.to_numpy(dtype=np.float64)).all():
        raise ValueError("Wind CSV contains missing or nonfinite values.")
    if not np.isfinite(grid[required].to_numpy(dtype=np.float64)).all():
        raise ValueError(
            "Target grid contains missing or nonfinite coordinates or indices."
        )
    if not np.array_equal(
        frame[["Latitude", "Longitude"]].to_numpy(),
        grid[["Latitude", "Longitude"]].to_numpy(),
    ):
        raise ValueError(
            "Wind CSV coordinates or row order differ from the saved target grid."
        )
    if frame.duplicated(["Latitude", "Longitude"]).any():
        raise ValueError("Wind CSV contains duplicate coordinate pairs.")
    indices = grid[["grid_row", "grid_column", "grid_cell_id"]].to_numpy(
        dtype=np.float64
    )
    if not np.array_equal(indices, np.rint(indices)):
        raise ValueError("Target grid indices must be integers.")
    rows, columns, cell_ids = indices.astype(np.int64).T
    if np.any(
        (rows < 0) | (rows >= GRID_ROWS) | (columns < 0) | (columns >= GRID_COLUMNS)
    ):
        raise ValueError("Target grid indices are outside the canonical grid.")
    if not np.array_equal(cell_ids, rows * GRID_COLUMNS + columns):
        raise ValueError(
            "Target grid cell IDs do not match the saved row and column indices."
        )
    if len(np.unique(cell_ids)) != len(cell_ids) or np.any(np.diff(cell_ids) <= 0):
        raise ValueError(
            "Target cells must be unique and in canonical row-major order."
        )
    if not np.array_equal(grid.x_m.to_numpy(), FIRST_X + CELL_SIZE * columns):
        raise ValueError("Target x coordinates differ from the canonical grid.")
    if not np.array_equal(grid.y_m.to_numpy(), FIRST_Y - CELL_SIZE * rows):
        raise ValueError("Target y coordinates differ from the canonical grid.")
    tx, ty = FORWARD.transform(frame.Longitude.to_numpy(), frame.Latitude.to_numpy())
    residual = np.maximum(
        np.abs(tx - grid.x_m.to_numpy()), np.abs(ty - grid.y_m.to_numpy())
    )
    if not np.isfinite(residual).all() or residual.max() > 2.0:
        raise ValueError(
            "Geographic coordinates do not fit the saved target grid within 2 metres."
        )
    result = np.full((GRID_ROWS, GRID_COLUMNS), np.nan)
    result[rows, columns] = frame.v_wind.to_numpy()
    return result


def symmetric_limit(*arrays: np.ndarray) -> float:
    """Use a shared signed scale, including a neutral scale for an all-zero field."""
    if not arrays or any(
        not values.size or not np.isfinite(values).all() for values in arrays
    ):
        raise ValueError("Colour-scale inputs must be nonempty and finite.")
    maximum = max(float(np.abs(values).max()) for values in arrays)
    return maximum if maximum > 0 else 1.0


def plot_month(data_dir: Path, month: str, output_file: Path | None = None) -> Path:
    """Validate inputs before rendering and atomically replace the finished figure."""
    if len(month) != 6 or not month.isdigit() or not 1 <= int(month[4:]) <= 12:
        raise ValueError("Month must be a valid YYYYMM value.")
    data_dir = Path(data_dir).expanduser().resolve()
    target_dir = data_dir / "nsidc_reconstructed"
    frame = pd.read_csv(
        target_dir / "monthly average csv" / f"{month}_v_wind_NSIDC_reconstructed.csv",
        float_precision="round_trip",
    )
    grid_path = target_dir / "target_grid.csv"
    grid = pd.read_csv(grid_path, float_precision="round_trip")
    if len(grid) != 82120:
        raise ValueError("Expected 82,120 retained target cells.")
    target = target_array(frame, grid)
    with netcdf_file(
        str(data_dir / "v_wind_monthly_native_197903_202312.nc"), "r", mmap=True
    ) as source:
        months = [date.strftime("%Y%m") for date in read_dates(source)]
        if months.count(month) != 1:
            raise ValueError(
                f"Native product must contain exactly one record for {month}."
            )
        native = np.array(source.variables["v10"].data[months.index(month)], copy=True)
        latitude = np.array(source.variables["latitude"].data, copy=True)
        longitude = np.array(source.variables["longitude"].data, copy=True)
    if native.shape != (241, 1440) or not np.array_equal(
        latitude, np.arange(-30, -90.01, -0.25)
    ):
        raise ValueError("Unexpected native latitude grid or wind shape.")
    if not np.array_equal(longitude, np.arange(-180, 180, 0.25)):
        raise ValueError("Unexpected native longitude grid.")
    limit = symmetric_limit(native, frame.v_wind.to_numpy())
    lat_edges = np.r_[latitude[0] + 0.125, latitude - 0.125]
    lat_edges[-1] = -90.0
    lon_edges = np.r_[longitude - 0.125, longitude[-1] + 0.125]
    lon_mesh, lat_mesh = np.meshgrid(lon_edges, lat_edges)
    native_x, native_y = FORWARD.transform(lon_mesh, lat_mesh)
    target_x = (FIRST_X + CELL_SIZE * (np.arange(GRID_COLUMNS + 1) - 0.5)) / 1000
    target_y = (FIRST_Y - CELL_SIZE * (np.arange(GRID_ROWS + 1) - 0.5)) / 1000

    cache_root = Path(tempfile.gettempdir()) / "v-wind-plot-cache"
    os.environ.setdefault("MPLCONFIGDIR", str(cache_root / "matplotlib"))
    os.environ.setdefault("XDG_CACHE_HOME", str(cache_root))
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import TwoSlopeNorm

    norm = TwoSlopeNorm(vmin=-limit, vcenter=0.0, vmax=limit)
    fig, axes = plt.subplots(1, 2, figsize=(11, 6), constrained_layout=True)
    temporary = None
    output = (
        Path(output_file) if output_file else target_dir / f"v_wind_quality_{month}.png"
    )
    output = output.expanduser().resolve()
    try:
        axes[0].pcolormesh(
            native_x / 1000,
            native_y / 1000,
            native,
            shading="flat",
            cmap="RdBu_r",
            norm=norm,
            rasterized=True,
        )
        image = axes[1].pcolormesh(
            target_x,
            target_y,
            target,
            shading="flat",
            cmap="RdBu_r",
            norm=norm,
            rasterized=True,
        )
        axes[0].set_title("Native ERA5 grid\nLand and ocean cells")
        axes[1].set_title("Reconstructed NSIDC grid\n82,120 retained cells")
        for axis in axes:
            axis.set_aspect("equal")
            axis.set_xlim(target_x[0], target_x[-1])
            axis.set_ylim(target_y[-1], target_y[0])
            axis.set_xlabel("Polar stereographic x (km)")
            axis.set_ylabel("Polar stereographic y (km)")
            axis.grid(alpha=0.15)
        fig.colorbar(image, ax=axes, shrink=0.8, label="Northward wind component (m/s)")
        fig.suptitle(f"{month[:4]}-{month[4:]} | Monthly mean northward wind at 10 m")
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            dir=output.parent, prefix=".wind-figure-", suffix=".png", delete=False
        ) as stream:
            temporary = Path(stream.name)
        fig.savefig(temporary, dpi=180, format="png")
        os.replace(temporary, output)
    finally:
        plt.close(fig)
        if temporary is not None and temporary.exists():
            temporary.unlink()
    print(f"Saved wind quality figure: {output}", flush=True)
    return output
