"""Product names, supported time span and scientific validation limits."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "processed"
OUTPUT = OUT / "nsidc_reconstructed"
NETCDF_NAME = "v_wind_monthly_native_197903_202312.nc"
TARGET_NETCDF_NAME = "v_wind_monthly_nsidc_reconstructed_197903_202312.nc"
NATIVE = OUT / NETCDF_NAME
LEGACY = ROOT / "monthly average csv NSIDC grid"
MONTHS = pd.period_range("1979-03", "2023-12", freq="M").strftime("%Y%m").tolist()
CSV_FIELDS = ["Latitude", "Longitude", "v_wind"]
DOC_URL = "https://confluence.ecmwf.int/pages/viewpage.action?pageId=179741903"
PARAM_URL = "https://codes.ecmwf.int/grib/param-db/166"
CACHE_SCHEMA_VERSION = 2
METHOD_VERSION = "hughes-polar-conservative-v1"
CACHE_FILES = (
    "conservative_weights.npz",
    "source_overlap_areas.npz",
    "target_grid.csv",
    "weight_coverage.csv",
    "regrid_metadata.json",
)
COVERAGE_COLUMNS = [
    "cell",
    "target_physical_area_m2",
    "overlap_projected_area_m2",
    "overlap_physical_area_m2",
    "projected_coverage_ratio",
    "physical_coverage_ratio",
    "source_cell_count",
    "normalized_weight_row_sum",
]
OVERLAP_FIELDS = [
    "month",
    "selected_file",
    "other_file",
    "compared_cells",
    "max_abs_difference_m_s",
    "rmse_m_s",
    "packing_tolerance_m_s",
    "cells_beyond_packing_tolerance",
    "other_ERA5T_cells",
]
GRID_FIELDS = [
    "month",
    "file",
    "rows",
    "missing_values",
    "minimum_m_s",
    "maximum_m_s",
    "mean_m_s",
    "std_m_s",
    "coordinate_match",
    "value_regridding_verified",
]
LIMITS = {
    "grid_fit_max_m": 2.0,
    "projected_coverage_max_relative_error": 2e-9,
    "physical_coverage_max_relative_error": 5e-6,
    "weight_row_sum_max_error": 1e-12,
    "constant_field_max_error": 1e-12,
    "convergence_weight_l1_max": 1e-4,
    "convergence_wind_max_absolute_m_s": 2e-4,
}
