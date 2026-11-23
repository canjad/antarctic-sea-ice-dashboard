"""Gregorian CF decoding and exact monthly timestamp validation."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import numpy as np


def text(value):
    return value.decode() if isinstance(value, bytes) else str(value)


def read_dates(ds):
    """Read supported Gregorian CF times; also used by the plotting script."""
    if "time" not in ds.variables:
        raise ValueError("Missing time coordinate.")
    var = ds.variables["time"]
    calendar = text(getattr(var, "calendar", "standard")).lower()
    if calendar not in {"gregorian", "standard", "proleptic_gregorian"}:
        raise ValueError(f"Unsupported time calendar: {calendar}.")
    if var.dimensions != ("time",):
        raise ValueError(f"Unexpected time coordinate dimensions: {var.dimensions}.")
    values = np.array(var.data, dtype=np.float64, copy=True)
    if values.ndim != 1 or not len(values) or not np.isfinite(values).all():
        raise ValueError(
            "Time coordinates must be nonempty, finite and one-dimensional."
        )
    units = text(getattr(var, "units", ""))
    match = re.fullmatch(r"(hours|seconds|days) since (.+)", units)
    if match is None:
        raise ValueError(f"Unsupported time units: {units!r}.")
    try:
        base = datetime.fromisoformat(match.group(2))
        if base.tzinfo is not None:
            base = base.astimezone(timezone.utc).replace(tzinfo=None)
        multiplier = {"hours": 3600, "seconds": 1, "days": 86400}[match.group(1)]
        dates = [
            base + timedelta(seconds=float(value) * multiplier) for value in values
        ]
    except (ValueError, OverflowError) as error:
        raise ValueError(f"Invalid time origin or coordinate: {units!r}.") from error
    if calendar != "proleptic_gregorian" and min([base, *dates]) < datetime(
        1582, 10, 15
    ):
        raise ValueError(
            "Mixed Gregorian/Julian dates before 1582-10-15 are unsupported."
        )
    return dates


def validate_month_dates(dates, name):
    if not dates:
        raise ValueError(f"No time records in {name}.")
    previous = None
    for date in dates:
        if date != datetime(date.year, date.month, 1):
            raise ValueError(
                f"Expected exact month-start midnight in {name}: {date.isoformat()}."
            )
        if previous is not None and date <= previous:
            raise ValueError(
                f"Monthly dates must be unique and strictly increasing in {name}."
            )
        previous = date
    return [date.strftime("%Y%m") for date in dates]
