# ERA5 Wind Data Processing Code

This directory contains code for processing the ERA5 10 m northward wind component (`v10`): cleaning data on the native grid, conservatively remapping it to a reconstructed NSIDC grid, and producing quality-check plots. Wind values are in m/s, with positive values indicating northward flow.

The GitHub version is intended to contain only the current source code, dependency lists, and this documentation. Raw data, complete monthly CSV exports, NetCDF files, weight caches, archives, and historical processing results remain local and are excluded from Git.

## Project Structure

```text
wind-processing/
├── .gitignore
├── README.md
├── requirements.txt
└── 10m v wind speed/
    ├── requirements-v-wind.txt
    ├── process_v_wind.py       # Clean and export native-grid data
    ├── regrid_v_wind.py        # Build weights and remap sample or full series
    ├── plot_v_wind_quality.py  # Compare native and reconstructed grids
    ├── wind_io.py             # Compatibility entry point for file writing
    └── wind_pipeline/         # Processing implementation modules
```

The `.gitignore` file uses an explicit allowlist for the files above and Python source files in `wind_pipeline/`. Update the allowlist when adding other source directories, configuration files, or documentation. Ignore rules apply only to untracked files; they do not delete local data or automatically remove files already committed to Git.

## Installation

Run the following commands from `wind-processing/`. Use Python 3.10 or later, with a version supported by the pinned dependencies. The top-level requirements file references the existing version pins in `10m v wind speed/requirements-v-wind.txt`.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Dependencies include NumPy, pandas, SciPy, pyproj, Shapely, and Matplotlib. The virtual environment directory is excluded from Git.

## Local Input Data

The code requires local input data, which is not included in the repository:

1. **ERA5 monthly mean `v10` NetCDF files.** Place these in `10m v wind speed/`, or select another directory with `--input-dir`. The code scans `.nc` files directly inside that directory without searching subdirectories. The current pipeline requires continuous monthly coverage from **March 1979 through December 2023 (538 months)**, with the same native grid across all usable inputs.
2. **Source file format.** The code reads NetCDF3 files using `scipy.io.netcdf_file`. Inputs must include `time`, `latitude`, `longitude`, and `v10`, together with the units, time metadata, and export history required by the validation code. NetCDF4 files require conversion; changing the extension is insufficient. Files containing `u10` without `v10` are excluded.
3. **Reference CSV files for remapping.** These are required for remapping only. The default directory is `10m v wind speed/monthly average csv NSIDC grid/`, with filenames matching `YYYYMM_v_wind_NSIDC_grid.csv`. These files supply the retained NSIDC grid coordinates, mask, and row order. Wind tables use the columns `Latitude,Longitude,v_wind`. This project uses 82,120 retained grid cells.

Inputs are locally supplied ERA5 files and existing NSIDC-grid CSV files. This repository does not include the original download requests or a complete set of download links. Obtain inputs from the data providers and record their sources, versions, and download parameters. The original mask version, conversion script, and weight matrix for the reference grid are unavailable, so remapped outputs are reconstructed products.

## Running the Pipeline

Start in `wind-processing/`. The examples use separate `processed_new/` and `weights_new/` directories to distinguish new outputs from existing local results. Processing and weight-building destination directories must not already exist; choose new names when rerunning.

```bash
cd "10m v wind speed"

# 1. Clean native-grid data and export monthly CSVs, NetCDF, and reports.
python process_v_wind.py --input-dir . --output-dir processed_new

# 2. Build conservative remapping weights from local reference CSVs.
python regrid_v_wind.py --build-weights \
  --native-file processed_new/v_wind_monthly_native_197903_202312.nc \
  --reference-dir "monthly average csv NSIDC grid" \
  --weights-dir weights_new

# 3. Remap selected sample months into a separate directory.
python regrid_v_wind.py --sample \
  --native-file processed_new/v_wind_monthly_native_197903_202312.nc \
  --reference-dir "monthly average csv NSIDC grid" \
  --weights-dir weights_new \
  --output-dir sample_new

# 4. Remap the full time series for subsequent plotting.
python regrid_v_wind.py --full \
  --native-file processed_new/v_wind_monthly_native_197903_202312.nc \
  --reference-dir "monthly average csv NSIDC grid" \
  --weights-dir weights_new \
  --output-dir processed_new/nsidc_reconstructed

# 5. Plot a comparison of native and reconstructed grids for one month.
python plot_v_wind_quality.py --data-dir processed_new --month 202302
```

Adjust `--input-dir` and `--reference-dir` if inputs are stored elsewhere. When changing output directories, update input paths in subsequent commands accordingly. Plotting requires a native grid with latitudes from -30 degrees to -90 degrees at 0.25-degree spacing, longitudes from -180 degrees to 179.75 degrees at 0.25-degree spacing, and the reconstructed grid of 82,120 retained cells described above.

Use `--help` with each entry script to view its options. Default sample months are `197903,198001,202302,202308,202309,202312`; select other months using `--months`. Full processing generates substantial local output, so allow sufficient disk space.

## Outputs and Method

Native-grid processing produces monthly CSVs, a combined NetCDF file, `source_inventory.json`, `summary.json`, `monthly_quality.csv`, and processing reports. Remapping produces weight caches, reconstructed-grid wind fields, and validation reports. All generated outputs are excluded from Git.

The pipeline preserves the signed monthly mean northward wind component, decodes fill values and scaling, resolves ERA5 version streams, and records source selection where inputs overlap. It does not apply additional temporal averaging, clipping, imputation, or standardization. Conservative remapping interprets native point values as representative cell means and performs coverage, constant-field, convergence, and integral checks. Reconstructed results should not be treated as an exact reproduction of the unavailable original conversion workflow.

## Before Uploading to GitHub

This directory can be used as a standalone repository or as the `wind-processing/` subdirectory of a larger repository. Its ignore rules apply within this directory. Run `git status --short` from the repository root before committing and review the files under `wind-processing/`. Only current source code, this README, dependency lists, and `.gitignore` should be included in the wind-processing upload. Follow the ignore rules when committing and pushing, and avoid force-adding ignored data. Manual uploads through the GitHub website do not automatically apply the local `.gitignore` rules.
