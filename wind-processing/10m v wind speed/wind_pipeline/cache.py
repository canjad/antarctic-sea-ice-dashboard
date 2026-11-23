"""Build, load and copy conservative-remapping weights."""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.sparse import csr_matrix, load_npz, save_npz

from .constants import CACHE_FILES, CACHE_SCHEMA_VERSION, METHOD_VERSION, OUTPUT
from .geometry import compute_weights, reconstruct_target, source_polygons
from .models import WeightBuildOptions
from .reports import write_report
from .storage import atomic_write_json, file_hash, staged_product


def build_weights(args: WeightBuildOptions, native: dict) -> None:
    target, info = reconstruct_target(args.reference_dir)
    polygons, source_ids = source_polygons(native, args.arc_subdivisions)
    matrix, coverage = compute_weights(
        target,
        polygons,
        source_ids,
        len(native["latitude"]) * len(native["longitude"]),
        args.geodesic_segment_m,
    )
    info.update(
        {
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "cache_schema_version": CACHE_SCHEMA_VERSION,
            "method_version": METHOD_VERSION,
            "source_arc_subdivisions": args.arc_subdivisions,
            "maximum_geodesic_segment_m": args.geodesic_segment_m,
            "product_status": "Reconstructed conservative remapping; original converter unavailable",
            "source_bounds": "Centers +/-0.125 degrees, cyclic longitude, clipped South Pole bounds",
            "wind_component": "Signed geographic northward 10 m wind in m/s; no vector rotation",
        }
    )
    with staged_product(args.weights_dir) as stage:
        save_npz(stage / "conservative_weights.npz", matrix)
        source_areas = np.asarray(
            matrix.T @ coverage.overlap_physical_area_m2.to_numpy()
        ).ravel()
        np.savez_compressed(
            stage / "source_overlap_areas.npz", physical_area_m2=source_areas
        )
        target.to_csv(stage / "target_grid.csv", index=False, float_format="%.15g")
        coverage.to_csv(
            stage / "weight_coverage.csv", index=False, float_format="%.15g"
        )
        info["weights_sha256"] = file_hash(stage / "conservative_weights.npz")
        atomic_write_json(stage / "regrid_metadata.json", info)
        write_report(info, None, stage)
    print(f"Computed conservative weights: {args.weights_dir}", flush=True)


def load_weights(weights_dir: Path = OUTPUT) -> tuple[csr_matrix, pd.DataFrame, dict]:
    matrix = load_npz(weights_dir / "conservative_weights.npz").tocsr()
    target = pd.read_csv(weights_dir / "target_grid.csv")
    info = json.loads(
        (weights_dir / "regrid_metadata.json").read_text(encoding="utf-8")
    )
    return matrix, target, info


def copy_cache(source: Path, destination: Path, info: dict) -> dict:
    for name in CACHE_FILES:
        if name != "regrid_metadata.json":
            shutil.copyfile(source / name, destination / name)
    metadata = dict(info)
    metadata["cache_origin"] = str(source)
    atomic_write_json(destination / "regrid_metadata.json", metadata)
    return metadata
