"""Clean raw ERA5 northward wind and export one record per month."""

from __future__ import annotations

import json
from pathlib import Path

from scipy.io import netcdf_file

from .constants import NETCDF_NAME, OUT, ROOT
from .netcdf import create_native_variables
from .reports import render_report
from .sources import discover_sources, select_month, source_version
from .storage import atomic_write_json, atomic_write_text, staged_product
from .tables import export_csv, summarize, write_table


def process(input_dir: Path = ROOT, output_dir: Path = OUT) -> dict:
    input_dir, output_dir = Path(input_dir).resolve(), Path(output_dir).resolve()
    with staged_product(output_dir) as staging, discover_sources(input_dir) as catalog:
        csv_dir = staging / "monthly average csv"
        csv_dir.mkdir()
        quality = []
        with netcdf_file(str(staging / NETCDF_NAME), "w", version=2) as dataset:
            wind, source_id, version = create_native_variables(dataset, catalog)
            for index, month in enumerate(catalog.months):
                chosen, _ = select_month(catalog.records[month], month)
                source, values, counts = (
                    chosen["source"],
                    chosen["data"],
                    chosen["counts"],
                )
                wind[index] = values
                source_id[index] = source["item"]["source_id"]
                version[index] = source_version(counts)
                export_csv(csv_dir / f"{month}_v_wind.csv", catalog.coordinates, values)
                quality.append(
                    {
                        "month": month,
                        "selected_source": source["name"],
                        "selected_expver_1_cells": counts["1"],
                        "selected_expver_5_cells": counts["5"],
                        "selected_unspecified_stream_cells": counts["unspecified"],
                        **summarize(values),
                    }
                )
                if (
                    index == 0
                    or (index + 1) % 12 == 0
                    or index + 1 == len(catalog.months)
                ):
                    print(
                        f"Cleaned {index + 1}/{len(catalog.months)} months; latest {month}",
                        flush=True,
                    )
            del wind, source_id, version
        write_table(staging / "monthly_quality.csv", quality, list(quality[0]))
        summary = {
            "status": "native_grid_processing_complete",
            "start_month": catalog.months[0],
            "end_month": catalog.months[-1],
            "months": len(catalog.months),
            "rows_per_native_month": len(catalog.coordinates),
            "native_missing_values": sum(row["missing_values"] for row in quality),
            "output_csv_files": len(catalog.months),
            "included_source_files": len(catalog.sources),
            "excluded_source_files": catalog.excluded,
            "input_directory": str(input_dir),
            "output_directory": str(output_dir),
            "scientific_notes": "Signed geographic northward wind in m/s. Source monthly means are retained. Fill codes are masked before decoding scale and offset. Within a file expver 1 precedes 5. Overlapping exports are selected by completeness, fewer ERA5T cells, newer export and finer packing. No extra averaging, clipping, imputation, normalization or land filtering.",
        }
        atomic_write_json(
            staging / "source_inventory.json",
            {
                "included": [source["item"] for source in catalog.sources],
                "excluded": catalog.excluded,
            },
        )
        atomic_write_json(staging / "summary.json", summary)
        atomic_write_text(staging / "processing_report.md", render_report(summary))
    print(
        json.dumps(
            {key: summary[key] for key in ("status", "months", "native_missing_values")}
        ),
        flush=True,
    )
    return summary


def rebuild_report(output_dir: Path = OUT) -> None:
    output_dir = Path(output_dir)
    summary = json.loads((output_dir / "summary.json").read_text(encoding="utf-8"))
    atomic_write_text(output_dir / "processing_report.md", render_report(summary))
