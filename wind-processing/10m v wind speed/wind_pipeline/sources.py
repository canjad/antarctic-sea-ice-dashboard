"""Raw ERA5 validation, packed-value decoding and monthly source selection."""

from __future__ import annotations

import re
from collections import defaultdict
from contextlib import ExitStack, contextmanager
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.io import netcdf_file

from .models import SourceCatalog
from .storage import file_hash
from .time_axis import read_dates, text, validate_month_dates


def scalar_packing(var, attribute, default, name):
    values = np.asarray(getattr(var, attribute, default))
    if values.size != 1:
        raise ValueError(f"{attribute} must be scalar in {name}.")
    try:
        value = float(values.item())
    except (ValueError, TypeError) as error:
        raise ValueError(f"Invalid {attribute} in {name}.") from error
    if not np.isfinite(value):
        raise ValueError(f"{attribute} must be finite in {name}.")
    return value


def validate_expver(ds, name):
    if "expver" not in ds.variables:
        raise ValueError(f"Missing expver coordinate in {name}.")
    var = ds.variables["expver"]
    versions = np.array(var.data, copy=True)
    if var.dimensions != ("expver",) or versions.ndim != 1 or not len(versions):
        raise ValueError(f"Unexpected expver coordinate dimensions in {name}.")
    if not np.isin(versions, [1, 5]).all():
        raise ValueError(f"Unsupported expver in {name}: {versions.tolist()}.")
    if len(np.unique(versions)) != len(versions):
        raise ValueError(
            f"Duplicate expver coordinates in {name}: {versions.tolist()}."
        )
    return versions


def validate_wind_shape(ds, dates, latitude, longitude, name):
    var = ds.variables["v10"]
    expected = (len(dates), len(latitude), len(longitude))
    versions = None
    if var.dimensions == ("time", "expver", "latitude", "longitude"):
        versions = validate_expver(ds, name)
        expected = (len(dates), len(versions), len(latitude), len(longitude))
    elif var.dimensions != ("time", "latitude", "longitude"):
        raise ValueError(f"Unexpected v10 dimensions in {name}: {var.dimensions}.")
    if var.data.shape != expected:
        raise ValueError(
            f"Unexpected v10 shape in {name}: {var.data.shape}; expected {expected}."
        )
    return versions


def validate_variable(ds, dates, name):
    var = ds.variables["v10"]
    if text(getattr(var, "units", "")) not in {"m s**-1", "m s-1", "m/s"}:
        raise ValueError(f"Unexpected wind units in {name}.")
    if text(getattr(var, "_Unsigned", "false")).lower() == "true":
        raise ValueError(f"Unsigned packed wind is unsupported in {name}.")
    axes = []
    for axis, bounds in (("latitude", (-90, 90)), ("longitude", (-180, 360))):
        if axis not in ds.variables:
            raise ValueError(f"Missing {axis} coordinate in {name}.")
        var_axis = ds.variables[axis]
        values = np.array(var_axis.data, dtype=np.float64, copy=True)
        if var_axis.dimensions != (axis,) or values.ndim != 1 or not len(values):
            raise ValueError(f"Unexpected {axis} coordinate dimensions in {name}.")
        if not np.isfinite(values).all() or np.any(
            (values < bounds[0]) | (values > bounds[1])
        ):
            raise ValueError(f"Invalid {axis} coordinates in {name}.")
        if len(np.unique(values)) != len(values):
            raise ValueError(f"Duplicate {axis} coordinates in {name}.")
        if len(values) > 1 and not (
            np.all(np.diff(values) > 0) or np.all(np.diff(values) < 0)
        ):
            raise ValueError(
                f"{axis} coordinates must be strictly monotonic in {name}."
            )
        axes.append(values)
    latitude, longitude = axes
    versions = validate_wind_shape(ds, dates, latitude, longitude, name)
    return (
        latitude,
        longitude,
        scalar_packing(var, "scale_factor", 1, name),
        scalar_packing(var, "add_offset", 0, name),
        versions,
    )


def export_timestamp(history, name):
    match = re.match(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", history)
    if match is None:
        raise ValueError(f"Missing export timestamp at the start of history in {name}.")
    try:
        datetime.fromisoformat(match.group(1))
    except ValueError as error:
        raise ValueError(f"Invalid export timestamp in {name}.") from error
    return match.group(1)


@contextmanager
def discover_sources(input_dir):
    """Validate all sources; this context owns their NetCDF handles."""
    input_dir = Path(input_dir)
    if not input_dir.is_dir():
        raise FileNotFoundError(f"Input directory not found: {input_dir}.")
    paths = sorted(input_dir.glob("*.nc"))
    if not paths:
        raise ValueError(f"No source NetCDF files found in {input_dir}.")
    sources, excluded, records = [], [], defaultdict(list)
    with ExitStack() as stack:
        for path in paths:
            ds = stack.enter_context(netcdf_file(str(path), "r", mmap=True))
            dates = read_dates(ds)
            history = text(getattr(ds, "history", ""))
            item = {
                "name": path.name,
                "size_bytes": path.stat().st_size,
                "sha256": file_hash(path),
                "actual_start": dates[0].isoformat(),
                "actual_end": dates[-1].isoformat(),
                "records": len(dates),
                "variables": list(ds.variables),
                "history": history,
            }
            if "v10" not in ds.variables:
                reason = (
                    "No v10 variable; contains u10 (eastward wind), not v10."
                    if "u10" in ds.variables
                    else "No v10 variable; unsuitable for northward wind processing."
                )
                nominal = re.search(r"_(\d{6})_(\d{6})\.nc$", path.name)
                if nominal and (
                    nominal.group(1) < dates[0].strftime("%Y%m")
                    or nominal.group(2) > dates[-1].strftime("%Y%m")
                ):
                    reason += " Filename also overstates actual time coverage."
                item["excluded_reason"] = reason
                excluded.append(item)
                continue
            months = validate_month_dates(dates, path.name)
            lat, lon, scale, offset, versions = validate_variable(ds, dates, path.name)
            if sources and (
                not np.array_equal(lat, sources[0]["latitude"])
                or not np.array_equal(lon, sources[0]["longitude"])
            ):
                raise ValueError(
                    "Raw grids are inconsistent; regridding is required before combining."
                )
            item.update(
                {
                    "source_id": len(sources),
                    "scale_factor": scale,
                    "add_offset": offset,
                    "dimensions": list(ds.variables["v10"].dimensions),
                    "expver": versions.tolist() if versions is not None else None,
                }
            )
            source = {
                "name": path.name,
                "handle": ds,
                "item": item,
                "dates": dates,
                "latitude": lat,
                "longitude": lon,
                "scale": scale,
                "offset": offset,
                "export_time": export_timestamp(history, path.name),
            }
            for index, month in enumerate(months):
                records[month].append((source, index))
            sources.append(source)
        if not sources:
            raise ValueError("No usable v10 sources were found.")
        ordered = sorted(records)
        expected = (
            pd.period_range("1979-03", "2023-12", freq="M").strftime("%Y%m").tolist()
        )
        if ordered != expected:
            raise ValueError(
                "v10 monthly coverage is not continuous from 1979-03 to 2023-12."
            )
        lat, lon = sources[0]["latitude"], sources[0]["longitude"]
        coordinates = np.column_stack(
            [np.repeat(lat, len(lon)), np.tile(lon, len(lat))]
        )
        yield SourceCatalog(
            sources, excluded, dict(records), ordered, lat, lon, coordinates
        )


def decode(source, index):
    """Mask raw fill codes, scale once, then prefer ERA5 to ERA5T per cell."""
    versions = validate_wind_shape(
        source["handle"],
        source["dates"],
        source["latitude"],
        source["longitude"],
        source["name"],
    )
    if not np.isfinite(source["scale"]) or not np.isfinite(source["offset"]):
        raise ValueError(f"Packing parameters must be finite in {source['name']}.")
    var = source["handle"].variables["v10"]
    raw = np.array(var.data[index], copy=True)
    fill = np.zeros(raw.shape, dtype=bool)
    for attribute in ("_FillValue", "missing_value"):
        if hasattr(var, attribute):
            for code in np.asarray(getattr(var, attribute)).reshape(-1):
                fill |= raw == code
    values = raw.astype(np.float64) * source["scale"] + source["offset"]
    values[fill] = np.nan
    if versions is None:
        return values, {"1": 0, "5": 0, "unspecified": int(np.isfinite(values).sum())}
    combined = np.full(values.shape[1:], np.nan)
    counts = {"1": 0, "5": 0, "unspecified": 0}
    for version in (1, 5):
        for j in np.flatnonzero(versions == version):
            take = np.isnan(combined) & np.isfinite(values[j])
            combined[take] = values[j][take]
            counts[str(version)] += int(take.sum())
    return combined, counts


def candidate_key(candidate):
    source, counts = candidate["source"], candidate["counts"]
    return (
        int(np.isfinite(candidate["data"]).sum()),
        -counts["5"],
        source["export_time"],
        -abs(source["scale"]),
    )


def choose_candidate(candidates, month):
    if not candidates:
        raise ValueError(f"No source candidates for {month}.")
    chosen = max(candidates, key=candidate_key)
    if not np.isfinite(chosen["data"]).all():
        raise ValueError(
            f"Missing v10 values remain in {month}; no fabricated filling is allowed."
        )
    return chosen


def select_month(records, month):
    candidates = []
    for source, index in records:
        values, counts = decode(source, index)
        candidates.append({"source": source, "data": values, "counts": counts})
    return choose_candidate(candidates, month), candidates


def source_version(counts):
    used = [label for label, count in counts.items() if count]
    return {"1": 1, "5": 5, "unspecified": 0}[used[0]] if len(used) == 1 else -1
