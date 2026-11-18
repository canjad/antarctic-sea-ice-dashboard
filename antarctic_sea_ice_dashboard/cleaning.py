"""Shared rules for turning stored CSV strings into clean numeric observations.

Every page that reads uploaded rows (Map Explorer, Compare, Model Insights and
the Data row editor) goes through these helpers so the same cell is always
judged the same way.
"""

import math
import re
from collections.abc import Iterable

# Cells that mean "no value" rather than "bad value". They are skipped quietly.
MISSING_TOKENS = frozenset(
    {"", "na", "n/a", "nan", "null", "none", "-", "--", "?", "missing"}
)
_NUMBER = re.compile(
    r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?"
)
# Only unambiguous thousands separators ("1,234" or "12,345.6"). A decimal
# comma such as "0,5" is rejected instead of being silently read as 5.
_THOUSANDS = re.compile(r"[+-]?[1-9][0-9]{0,2}(?:,[0-9]{3})+(?:\.[0-9]*)?")

# Fields whose values are percentages but may be stored as 0–1 fractions.
PERCENT_FIELDS = frozenset({"sea_ice_concentration", "prediction"})

# Plausible physical limits after unit scaling. Values outside are treated as
# fill values or entry errors (for example -999 or 9999).
PHYSICAL_RANGES: dict[str, tuple[float, float]] = {
    "sea_ice_concentration": (0.0, 100.0),
    "prediction": (0.0, 100.0),
    "sea_surface_temperature": (-3.0, 40.0),
    "wind_speed": (0.0, 100.0),
    "air_temperature": (-95.0, 60.0),
}

VALUE_SCALE_CHOICES = ("auto", "fraction", "percent")
PREDICTION_TYPE_CHOICES = ("concentration", "extent_probability")
# Sea-ice extent is conventionally the area with at least 15% concentration.
EXTENT_THRESHOLD = 15.0


def is_missing(value: object) -> bool:
    return isinstance(value, str) and value.strip().casefold() in MISSING_TOKENS


def parse_number(value: object) -> float | None:
    """Return a finite float for a numeric cell, or None for missing/invalid."""
    if not isinstance(value, str):
        return None
    raw = value.strip()
    if raw.casefold() in MISSING_TOKENS:
        return None
    if "," in raw:
        if not _THOUSANDS.fullmatch(raw):
            return None
        raw = raw.replace(",", "")
    if not _NUMBER.fullmatch(raw):
        return None
    try:
        number = float(raw)
    except (ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def normalize_longitude(longitude: float) -> float | None:
    """Accept -180…360 longitudes and return them in the -180…180 range."""
    if not -180 <= longitude <= 360:
        return None
    if longitude >= 180:
        longitude -= 360
    return longitude


def parse_coordinates(
    latitude_cell: object, longitude_cell: object
) -> tuple[float, float] | None:
    """Return (latitude, longitude) with longitude normalized, or None."""
    latitude = parse_number(latitude_cell)
    longitude = parse_number(longitude_cell)
    if latitude is None or longitude is None or not -90 <= latitude <= 90:
        return None
    longitude = normalize_longitude(longitude)
    if longitude is None:
        return None
    return latitude, longitude


def percent_factor(values: Iterable[float], setting: str = "auto") -> float:
    """Return 100 when values are 0–1 fractions, otherwise 1.

    "auto" looks at the shape of the data instead of only its maximum, so one
    stray value above 1 in a fraction file does not flip the whole file to
    percent (and a percent file with few ice cells is not taken as fractions).
    """
    if setting == "fraction":
        return 100.0
    if setting == "percent":
        return 1.0
    inner = above = in_range = 0
    for value in values:
        if not 0 <= value <= 100:
            continue
        in_range += 1
        if 0 < value < 1:
            inner += 1
        elif value > 1:
            above += 1
    if not in_range:
        return 1.0
    if above == 0:
        return 100.0
    return 100.0 if inner > above else 1.0


def in_physical_range(measurement_key: str, value: float) -> bool:
    bounds = PHYSICAL_RANGES.get(measurement_key)
    return bounds is None or bounds[0] <= value <= bounds[1]
