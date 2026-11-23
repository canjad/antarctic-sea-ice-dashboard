"""Command-line adapters; scientific computation lives in workflow modules."""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np

from .constants import LEGACY, NATIVE, OUT, OUTPUT, ROOT
from .models import RemapOptions, WeightBuildOptions


def native_main(argv=None):
    from .native import process, rebuild_report

    parser = argparse.ArgumentParser(
        description="Publish a cleaned native ERA5 v10 product without overwriting an existing destination."
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=ROOT,
        help="Original NetCDF directory, with optional legacy CSVs.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Fresh processing destination, or existing destination for --report-only.",
    )
    parser.add_argument(
        "--report-only",
        action="store_true",
        help="Update only the report from an existing summary.json.",
    )
    args = parser.parse_args(argv)
    destination = (
        args.output_dir if args.output_dir is not None else args.input_dir / "processed"
    )
    try:
        if args.report_only:
            rebuild_report(destination)
        else:
            process(args.input_dir, destination)
    except (OSError, ValueError) as error:
        parser.exit(1, f"Error: {error}\n")


def regrid_main(argv=None) -> None:
    from .cache import build_weights
    from .netcdf import read_native_metadata
    from .remapping import process_months
    from .storage import file_hash

    parser = argparse.ArgumentParser(
        description="Build conservative weights or publish a verified remapped wind series."
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--build-weights",
        action="store_true",
        help="Compute conservative sparse weights; do not produce the full series.",
    )
    mode.add_argument(
        "--sample",
        action="store_true",
        help="Remap a few months using conservative weights.",
    )
    mode.add_argument(
        "--full",
        action="store_true",
        help="Remap all 538 native months using conservative weights.",
    )
    parser.add_argument(
        "--arc-subdivisions",
        type=int,
        default=8,
        help="Segments per 0.25-degree source latitude arc (default: 8).",
    )
    parser.add_argument(
        "--geodesic-segment-m",
        type=float,
        default=2500,
        help="Maximum projected segment length before physical-area evaluation (default: 2500 m).",
    )
    parser.add_argument(
        "--months",
        default="197903,198001,202302,202308,202309,202312",
        help="Comma-separated months for --sample.",
    )
    parser.add_argument(
        "--native-file",
        type=Path,
        default=NATIVE,
        help="Cleaned monthly native NetCDF input.",
    )
    parser.add_argument(
        "--reference-dir",
        type=Path,
        default=LEGACY,
        help="Existing NSIDC CSV directory supplying the retained-cell coordinates and descriptive comparisons.",
    )
    parser.add_argument(
        "--weights-dir",
        type=Path,
        default=OUTPUT,
        help="Conservative weight cache; --build-weights requires a fresh destination.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Fresh product directory; defaults to the historical full or sample location, which is refused when already present.",
    )
    args = parser.parse_args(argv)
    if (
        args.arc_subdivisions < 2
        or not np.isfinite(args.geodesic_segment_m)
        or args.geodesic_segment_m <= 0
    ):
        parser.error(
            "Use at least two arc subdivisions and a positive finite geodesic segment length."
        )
    for name in ["native_file", "reference_dir", "weights_dir"]:
        setattr(args, name, getattr(args, name).expanduser().resolve())
    args.output_dir = (
        (args.output_dir or (OUTPUT if args.full else OUTPUT / "sample"))
        .expanduser()
        .resolve()
    )
    destination = args.weights_dir if args.build_weights else args.output_dir
    if destination.exists():
        parser.error(
            f"Destination already exists; select a fresh directory: {destination}"
        )
    try:
        native = read_native_metadata(args.native_file)
        native["sha256"] = file_hash(args.native_file)
        if args.build_weights:
            options = WeightBuildOptions(
                args.weights_dir,
                args.reference_dir,
                args.arc_subdivisions,
                args.geodesic_segment_m,
            )
            build_weights(options, native)
        else:
            options = RemapOptions(
                args.output_dir,
                args.weights_dir,
                args.reference_dir,
                "full" if args.full else "sample",
                tuple(args.months.split(",")),
            )
            process_months(options, native)
    except (OSError, ValueError, KeyError) as error:
        parser.exit(1, f"Error: {error}\n")


def plot_main(argv=None) -> None:
    from .plotting import plot_month

    parser = argparse.ArgumentParser(
        description="Plot native and reconstructed wind with a shared signed scale."
    )
    parser.add_argument("--data-dir", type=Path, default=OUT)
    parser.add_argument(
        "--month", default="202302", help="Month to plot, formatted YYYYMM."
    )
    parser.add_argument(
        "--output-file",
        type=Path,
        help="Destination PNG; defaults to the reconstructed product folder.",
    )
    args = parser.parse_args(argv)
    try:
        plot_month(args.data_dir, args.month, args.output_file)
    except (OSError, ValueError, KeyError) as error:
        parser.exit(1, f"Error: {error}\n")
