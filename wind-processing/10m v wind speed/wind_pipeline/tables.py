"""CSV export and descriptive monthly statistics, without export readback."""

from __future__ import annotations

import csv
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

import numpy as np
import pandas as pd


def summarize(values: np.ndarray) -> dict:
    finite = values[np.isfinite(values)]
    return {
        "rows": int(values.size),
        "missing_values": int(values.size - finite.size),
        "minimum_m_s": float(finite.min()) if finite.size else None,
        "maximum_m_s": float(finite.max()) if finite.size else None,
        "mean_m_s": float(finite.mean()) if finite.size else None,
        "std_m_s": float(finite.std()) if finite.size else None,
    }


def write_table(path: Path, records: Iterable[Mapping], fields: Sequence[str]) -> None:
    with Path(path).open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(records)


def export_csv(path: Path, coordinates: np.ndarray, values: np.ndarray) -> None:
    pd.DataFrame(
        {
            "Latitude": coordinates[:, 0],
            "Longitude": coordinates[:, 1],
            "v_wind": values.ravel(),
        }
    ).to_csv(path, index=False, float_format="%.15g")


def export_month_csv(
    destination: Path, month: str, target: pd.DataFrame, values: np.ndarray
) -> None:
    frame = target[["Latitude", "Longitude"]].copy()
    frame["v_wind"] = values
    frame.to_csv(
        destination / f"{month}_v_wind_NSIDC_reconstructed.csv",
        index=False,
        float_format="%.17g",
    )
