"""English descriptions of cleaning and remapping outputs."""

from __future__ import annotations

from pathlib import Path

from .constants import NETCDF_NAME, OUTPUT
from .storage import atomic_write_text


def render_report(summary: dict) -> str:
    return f"""# ERA5 10 m Northward Wind Cleaning

Coverage: {summary["start_month"]} to {summary["end_month"]}, {summary["months"]} months.
Each native month contains {summary["rows_per_native_month"]:,} cells.
Missing cleaned wind values: {summary["native_missing_values"]}.

The field is the signed geographic northward wind component in m/s. Original monthly means are retained. Raw fill codes are masked before decoding scale and offset; expver 1 takes precedence over expver 5. One source record is selected per month. No additional averaging, clipping, imputation, normalization or native land filtering is applied.

Outputs: monthly average csv/, {NETCDF_NAME}, monthly_quality.csv, source_inventory.json and summary.json. The quality table contains descriptive statistics. Source exclusions are recorded in the inventory.

Export readback, baseline regression and historical-value comparisons are not performed.
"""


def write_report(info: dict, summary: dict | None, output_dir: Path = OUTPUT) -> None:
    status = (
        "Conservative weights computed."
        if summary is None
        else f"{summary['mode'].capitalize()} remapping: {summary['processed_month_count']} months."
    )
    content = f"""# Reconstructed NSIDC-grid Northward Wind

{status}

The signed geographic northward component is remapped with positive physical overlap areas on the Hughes 1980 southern polar stereographic grid. Source point values are interpreted as representative cell means on midpoint bounds. Target cell order and the retained mask come from the existing NSIDC coordinate file. No vector rotation or additional temporal averaging is performed.

This is a reconstructed product. The original converter, original weight matrix and original mask version are unavailable. This processing does not establish equality with the original conversion.

Geometry: {info["source_arc_subdivisions"]} source-arc subdivisions; maximum projected segment length {info["maximum_geodesic_segment_m"]} m before physical area evaluation.
"""
    atomic_write_text(output_dir / "processing_report.md", content)
