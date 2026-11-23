"""Approved scientific variable contracts and explicit unavailable candidates."""

from dataclasses import dataclass
from types import MappingProxyType
from collections.abc import Mapping
import math


VARIABLE_REGISTRY_VERSION = "variables-1.0.0"


@dataclass(frozen=True)
class VariableDefinition:
    """One approved decoded-value contract shared by checking and reading."""

    variable_key: str
    display_name: str
    value_field: str
    accepted_units: tuple[str, ...]
    internal_unit: str
    display_unit: str
    conversion_version: str
    profile_key: str
    profile_version: str
    missing_policy: str
    evidence: str
    dependencies: tuple[str, ...]
    display_strategy: str

    def normalise_value(self, value: float, original_unit: str) -> float:
        """Accept finite decoded values in explicit equivalent units, or fail.

        The CSV contract contains physical values, not packed NetCDF integers.
        No scale, offset, finite fill sentinel or numeric range is inferred.
        """
        if original_unit not in self.accepted_units:
            raise ValueError("The original unit is incompatible with this variable.")
        if not math.isfinite(value):
            raise ValueError("The approved contract requires finite observations.")
        return value


VARIABLE_DEFINITIONS: Mapping[str, VariableDefinition] = MappingProxyType({
    "v10": VariableDefinition(
        variable_key="v10", display_name="10 m northward wind component",
        value_field="v_wind", accepted_units=("m/s", "m s-1", "m s**-1"),
        internal_unit="m/s", display_unit="m/s",
        conversion_version="decoded-equivalent-wind-units-1.0.0",
        profile_key="v10-csv", profile_version="1.0.0",
        missing_policy="Empty cells and all nonfinite numbers fail; no finite fill codes are assumed.",
        evidence="Existing v10 CSV profile; wind_pipeline/sources.py accepted units; plotting.py:target_array finite-value contract.",
        dependencies=("Current complete scientific check", "Published ordered-string CSV", "Matching retained reference grid"),
        display_strategy="signed_symmetric",
    ),
})

# These are inventory entries, not accepted variable definitions or aliases.
UNAVAILABLE_VARIABLES: Mapping[str, str] = MappingProxyType({
    "Sea ice concentration": "No approved source product, concentration scale, fill/range policy or publication profile is available.",
    "Sea-surface temperature": "No approved source product, unit/packing/fill policy or publication profile is available.",
})
