"""Apply conservative weights and export reconstructed target-grid wind."""

from __future__ import annotations

import shutil
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from scipy.io import netcdf_file
from scipy.sparse import csr_matrix

from .cache import copy_cache, load_weights
from .models import RemapOptions
from .netcdf import create_output_netcdf
from .reports import write_report
from .storage import atomic_write_json, staged_product
from .tables import export_month_csv, summarize, write_table


def remap_month(source_values: np.ndarray, matrix: csr_matrix) -> np.ndarray:
    return matrix @ source_values


def process_months(args: RemapOptions, native: dict) -> dict:
    matrix, target, info = load_weights(args.weights_dir)
    coverage = pd.read_csv(args.weights_dir / "weight_coverage.csv")
    requested = native["months"] if args.full else list(args.months)
    if (
        not requested
        or requested != sorted(set(requested))
        or any(month not in native["months"] for month in requested)
    ):
        raise ValueError(
            "Requested months must be unique, chronological months in the native series."
        )
    indices = [native["months"].index(month) for month in requested]
    selected = dict(native)
    selected["months"] = requested
    for name in ("time", "source_file_id", "source_expver"):
        selected[name] = native[name][indices]
    filename = (
        f"v_wind_monthly_nsidc_reconstructed_{requested[0]}_{requested[-1]}.nc"
        if args.full
        else "v_wind_monthly_nsidc_sample.nc"
    )
    with staged_product(args.output_dir) as stage:
        info = copy_cache(args.weights_dir, stage, info)
        inventory = native["path"].parent / "source_inventory.json"
        if inventory.exists():
            shutil.copyfile(inventory, stage / "source_inventory.json")
        csv_dir = stage / "monthly average csv"
        csv_dir.mkdir()
        quality = []
        with create_output_netcdf(
            stage / filename, selected, target, info, coverage
        ) as output:
            with netcdf_file(native["path"], "r", mmap=True) as source:
                for position, (month, index) in enumerate(zip(requested, indices)):
                    values = remap_month(
                        np.array(
                            source.variables["v10"].data[index], dtype=np.float64
                        ).ravel(),
                        matrix,
                    )
                    output.variables["v10"][position] = values
                    export_month_csv(csv_dir, month, target, values)
                    quality.append({"month": month, **summarize(values)})
                    if (
                        position == 0
                        or (position + 1) % 12 == 0
                        or position + 1 == len(requested)
                    ):
                        print(
                            f"Remapped {position + 1}/{len(requested)} months; latest {month}",
                            flush=True,
                        )
        write_table(stage / "monthly_quality.csv", quality, list(quality[0]))
        summary = {
            "status": "complete",
            "mode": args.mode,
            "completed_utc": datetime.now(timezone.utc).isoformat(),
            "processed_months": requested,
            "processed_month_count": len(requested),
            "target_cells_per_month": len(target),
            "missing_values": sum(row["missing_values"] for row in quality),
            "output_netcdf": filename,
        }
        atomic_write_json(stage / "summary.json", summary)
        write_report(info, summary, stage)
    return summary
