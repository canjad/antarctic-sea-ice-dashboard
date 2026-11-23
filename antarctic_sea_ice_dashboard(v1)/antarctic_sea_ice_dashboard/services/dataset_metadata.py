"""Scientific declarations and explicit grid identities for uploaded datasets."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from types import MappingProxyType


MAX_METADATA_LENGTH = 100
_IDENTIFIER_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9_.:-]{0,99}")
_MONTH_PATTERN = re.compile(r"[0-9]{4}-(?:0[1-9]|1[0-2])")


@dataclass(frozen=True)
class DatasetMetadata:
    """Optional declarations; missing values do not imply a scientific type."""

    variable_key: str | None = None
    observation_month: str | None = None
    unit: str | None = None
    grid_key: str | None = None


@dataclass(frozen=True)
class GridDefinition:
    """An explicit grid definition, including the retained coordinate identity."""

    label: str
    crs: str
    shape: tuple[int, int]
    cell_size_m: float
    upper_left_cell_center_m: tuple[float, float]
    retained_cell_count: int
    coordinate_sha256: str
    coordinate_digest_definition: str
    selection_mask_definition: str


# This registry records a declaration target, not a claim about uploaded rows.
# The original mask product/version is unavailable in the reference pipeline.
GRID_DEFINITIONS: Mapping[str, GridDefinition] = MappingProxyType(
    {
        "nsidc_sh_25km_hughes_1980_retained_82120_v1": GridDefinition(
            label="Reconstructed NSIDC southern 25 km grid (82,120 retained cells)",
            crs="EPSG:3412 (Hughes 1980)",
            shape=(332, 316),
            cell_size_m=25000.0,
            upper_left_cell_center_m=(-3937500.0, 4337500.0),
            retained_cell_count=82120,
            coordinate_sha256=(
                "64960e466ec387d5a6f8964d9886581c26a0f8d555f409f6d83287fca70ce2e7"
            ),
            coordinate_digest_definition=(
                "SHA-256 of contiguous little-endian float64 latitude values, "
                "then longitude values, in the retained reference row order. "
                "Coordinates preserve the original five-decimal reference values."
            ),
            selection_mask_definition=(
                "Exactly the 82,120 coordinate rows retained in the reference CSV, "
                "in ascending canonical row-major order. Original mask product "
                "and version are unavailable."
            ),
        ),
    }
)

GRID_OPTIONS: dict[str, str] = {
    key: definition.label for key, definition in GRID_DEFINITIONS.items()
}


def _normalise_optional(value: str | None, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{label} must be text or left unset.")
    normalised = value.strip()
    if not normalised:
        return None
    if len(normalised) > MAX_METADATA_LENGTH:
        raise ValueError(
            f"{label} must contain at most {MAX_METADATA_LENGTH} characters."
        )
    if any(
        unicodedata.category(character) in {"Cc", "Cf", "Cs"}
        for character in value
    ):
        raise ValueError(f"{label} cannot contain null or control characters.")
    return normalised


def _validate_identifier(value: str | None, label: str) -> str | None:
    normalised = _normalise_optional(value, label)
    if normalised is not None and _IDENTIFIER_PATTERN.fullmatch(normalised) is None:
        raise ValueError(
            f"{label} must start with an ASCII letter and use only ASCII letters, "
            "digits, underscores, full stops, colons or hyphens."
        )
    return normalised


def is_valid_observation_month(value: str | None) -> bool:
    """Check Gregorian YYYY-MM without normalising stored scientific values."""
    if not isinstance(value, str) or _MONTH_PATTERN.fullmatch(value) is None:
        return False
    try:
        date(int(value[:4]), int(value[5:]), 1)
    except ValueError:
        return False
    return True


def validate_dataset_metadata(metadata: DatasetMetadata) -> DatasetMetadata:
    """Return normalised declarations or raise ValueError for invalid input.

    Blank values become None. Month values use Gregorian YYYY-MM semantics.
    Grid keys must identify a registered definition. These format checks do not
    inspect CSV cells or establish scientific validity, units or grid membership.
    """
    variable_key = _validate_identifier(metadata.variable_key, "Variable key")
    observation_month = _normalise_optional(
        metadata.observation_month, "Observation month"
    )
    if observation_month is not None and not is_valid_observation_month(observation_month):
        raise ValueError(
            "Observation month must be a valid Gregorian month in YYYY-MM format, "
            "using a year from 0001 to 9999."
        )
    unit = _normalise_optional(metadata.unit, "Unit")
    grid_key = _validate_identifier(metadata.grid_key, "Grid key")
    if grid_key is not None and grid_key not in GRID_DEFINITIONS:
        raise ValueError("Select a registered grid definition or leave the grid unset.")
    return DatasetMetadata(variable_key, observation_month, unit, grid_key)
