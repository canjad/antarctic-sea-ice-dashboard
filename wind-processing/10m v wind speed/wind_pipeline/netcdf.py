"""NetCDF schemas and native input reading for both wind products."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.io import netcdf_file

from .constants import METHOD_VERSION, NATIVE
from .projection import MAJOR_AXIS, MINOR_AXIS, PROJECTED_CRS
from .time_axis import read_dates, validate_month_dates


def create_native_variables(target, catalog):
    target.createDimension("time", len(catalog.months))
    target.createDimension("latitude", len(catalog.latitude))
    target.createDimension("longitude", len(catalog.longitude))
    t = target.createVariable("time", "i", ("time",))
    t.units = "hours since 1900-01-01 00:00:00"
    t.calendar = "gregorian"
    t.standard_name = "time"
    t.axis = "T"
    t[:] = [
        (datetime.strptime(m, "%Y%m") - datetime(1900, 1, 1)).days * 24
        for m in catalog.months
    ]
    y = target.createVariable("latitude", "d", ("latitude",))
    y.units, y.standard_name, y.axis = "degrees_north", "latitude", "Y"
    y[:] = catalog.latitude
    x = target.createVariable("longitude", "d", ("longitude",))
    x.units, x.standard_name, x.axis = "degrees_east", "longitude", "X"
    x[:] = catalog.longitude
    wind = target.createVariable("v10", "d", ("time", "latitude", "longitude"))
    wind.units, wind.standard_name = "m s-1", "northward_wind"
    wind.long_name = "Monthly mean northward wind component at 10 metres"
    wind.cell_methods = "time: mean"
    wind._FillValue = np.float64(np.nan)
    sid = target.createVariable("source_file_id", "i", ("time",))
    sid.long_name = "Source file ID; mapping in source_inventory.json"
    ev = target.createVariable("source_expver", "i", ("time",))
    ev.long_name = "1=ERA5, 5=ERA5T, 0=unspecified single stream, -1=mixed"
    target.Conventions = "CF-1.8"
    target.title = "Decoded, deduplicated monthly ERA5 10m v wind on the original grid"
    target.time_coverage_start, target.time_coverage_end = "1979-03-01", "2023-12-01"
    target.source = "User-supplied ERA5 NetCDF files; see source_inventory.json"
    target.history = "Decoded fill values and packing once; expver 1 preferred to 5 within a file. Across files: completeness, absence of ERA5T, newest export, then finer packing. No additional temporal averaging or spatial interpolation."
    return wind, sid, ev


def read_native_metadata(path: Path = NATIVE) -> dict:
    with netcdf_file(path, "r", mmap=True) as dataset:
        latitude = np.array(dataset.variables["latitude"].data, dtype=np.float64)
        longitude = np.array(dataset.variables["longitude"].data, dtype=np.float64)
        times = np.array(dataset.variables["time"].data, dtype=np.float64)
        source_ids = np.array(
            dataset.variables["source_file_id"].data, dtype=np.float64
        )
        source_expver = np.array(
            dataset.variables["source_expver"].data, dtype=np.float64
        )
        units = dataset.variables["time"].units.decode("ascii")
        dates = read_dates(dataset)
        calendar = dataset.variables["time"]._attributes.get("calendar", b"gregorian")
        if dataset.variables["v10"].dimensions != ("time", "latitude", "longitude"):
            raise ValueError(
                "Native wind dimensions must be time, latitude, longitude in that order."
            )
        if dataset.variables["v10"].shape != (
            len(times),
            len(latitude),
            len(longitude),
        ):
            raise ValueError(
                "Native wind dimensions do not match its coordinate lengths."
            )
        if dataset.variables["v10"]._attributes.get("units") != b"m s-1":
            raise ValueError("Native northward wind units must be m s-1.")
        provenance = {
            name: dict(dataset.variables[name]._attributes)
            for name in ("source_file_id", "source_expver")
        }
        source_description = dataset._attributes.get(
            "source", b"Cleaned user-supplied ERA5 files"
        )
    if units != "hours since 1900-01-01 00:00:00":
        raise ValueError(f"Unsupported native time units: {units}")
    if calendar not in (b"gregorian", b"standard", b"proleptic_gregorian"):
        raise ValueError("Native time calendar must be Gregorian.")
    if latitude.shape != (241,) or longitude.shape != (1440,):
        raise ValueError("Unexpected native ERA5 grid dimensions.")
    if times.ndim != 1 or not len(times) or not np.isfinite(times).all():
        raise ValueError("Native time coordinates must be a nonempty finite vector.")
    if source_ids.shape != times.shape or source_expver.shape != times.shape:
        raise ValueError("Native provenance vectors must match the time coordinate.")
    for name, values in [
        ("source_file_id", source_ids),
        ("source_expver", source_expver),
    ]:
        if (
            not np.isfinite(values).all()
            or not np.array_equal(values, np.floor(values))
            or np.any(np.abs(values) > np.iinfo(np.int32).max)
        ):
            raise ValueError(
                f"Native provenance must contain finite representable integers: {name}."
            )
    if np.any(source_ids < 0) or not np.isin(source_expver, [-1, 0, 1, 5]).all():
        raise ValueError("Native source IDs or experiment-version flags are invalid.")
    source_ids = source_ids.astype(np.int32)
    source_expver = source_expver.astype(np.int32)
    if not np.allclose(latitude, np.arange(-30.0, -90.01, -0.25), rtol=0, atol=1e-12):
        raise ValueError(
            "Native latitude centers must descend from -30 to -90 by 0.25 degrees."
        )
    if not np.allclose(longitude, np.arange(-180.0, 180.0, 0.25), rtol=0, atol=1e-12):
        raise ValueError(
            "Native longitude centers must run from -180 to 179.75 by 0.25 degrees."
        )
    months = validate_month_dates(dates, str(path))
    periods = pd.period_range(months[0], months[-1], freq="M").strftime("%Y%m").tolist()
    if months != periods or len(set(months)) != len(months):
        raise ValueError("Native months are not unique and continuous.")
    return {
        "latitude": latitude,
        "longitude": longitude,
        "time": times,
        "months": months,
        "time_units": units,
        "source_file_id": source_ids,
        "source_expver": source_expver,
        "path": path,
        "provenance_attributes": provenance,
        "source_description": source_description,
    }


def create_output_netcdf(
    path: Path, native: dict, target: pd.DataFrame, info: dict, coverage: pd.DataFrame
):
    dataset = netcdf_file(path, "w", version=2)
    dataset.createDimension("time", len(native["months"]))
    dataset.createDimension("cell", len(target))
    dataset.Conventions = "CF-1.8"
    dataset.title = "Monthly ERA5 geographic northward 10 m wind conservatively remapped to reconstructed NSIDC retained cells"
    dataset.product_status = (
        "New remapping product; original project weight matrix unavailable"
    )
    dataset.history = "See regrid_metadata.json and processing_report.md for source bounds, geometry and area evaluation."
    dataset.wind_component_interpretation = (
        "Signed geographic northward component; no vector rotation"
    )
    dataset.weights_sha256 = info["weights_sha256"]
    dataset.method_version = METHOD_VERSION
    dataset.source = native["source_description"]
    dataset.source_native_netcdf_sha256 = native["sha256"]
    dataset.source_inventory_reference = (
        "source_inventory.json"
        if (native["path"].parent / "source_inventory.json").exists()
        else "Source inventory unavailable; see source provenance variable attributes."
    )
    time_var = dataset.createVariable("time", "d", ("time",))
    time_var[:] = native["time"]
    time_var.units = native["time_units"]
    time_var.calendar = "gregorian"
    time_var.standard_name = "time"
    for field, code, values in [
        ("latitude", "d", target.Latitude),
        ("longitude", "d", target.Longitude),
        ("x", "d", target.x_m),
        ("y", "d", target.y_m),
        ("grid_row", "i", target.grid_row),
        ("grid_column", "i", target.grid_column),
        ("grid_cell_id", "i", target.grid_cell_id),
    ]:
        variable = dataset.createVariable(field, code, ("cell",))
        variable[:] = np.asarray(values)
        if field == "latitude":
            variable.units = "degrees_north"
            variable.standard_name = "latitude"
        elif field == "longitude":
            variable.units = "degrees_east"
            variable.standard_name = "longitude"
        elif field in ("x", "y"):
            variable.units = "m"
            variable.standard_name = (
                "projection_x_coordinate" if field == "x" else "projection_y_coordinate"
            )
    area = dataset.createVariable("cell_area", "d", ("cell",))
    area[:] = coverage.target_physical_area_m2.to_numpy()
    area.units = "m2"
    area.long_name = "Hughes 1980 ellipsoidal area of each retained target cell"
    mapping = dataset.createVariable("crs", "i", ())
    mapping[...] = 0
    mapping.grid_mapping_name = "polar_stereographic"
    mapping.latitude_of_projection_origin = np.float64(-90.0)
    mapping.straight_vertical_longitude_from_pole = np.float64(0.0)
    mapping.standard_parallel = np.float64(-70.0)
    mapping.false_easting = np.float64(0.0)
    mapping.false_northing = np.float64(0.0)
    mapping.semi_major_axis = np.float64(MAJOR_AXIS)
    mapping.semi_minor_axis = np.float64(MINOR_AXIS)
    mapping.epsg_code = "EPSG:3412"
    mapping.crs_wkt = PROJECTED_CRS.to_wkt()
    for name in ["source_file_id", "source_expver"]:
        variable = dataset.createVariable(name, "i", ("time",))
        variable[:] = native[name]
        for attribute, value in native["provenance_attributes"][name].items():
            setattr(variable, attribute, value)
    wind = dataset.createVariable("v10", "d", ("time", "cell"))
    wind.units = "m s-1"
    wind.standard_name = "northward_wind"
    wind.long_name = "Monthly mean geographic northward wind component at 10 metres"
    wind.cell_methods = "time: mean area: mean"
    wind.coordinates = "latitude longitude x y"
    wind.grid_mapping = "crs"
    wind.cell_measures = "area: cell_area"
    wind._FillValue = np.float64(np.nan)
    return dataset
